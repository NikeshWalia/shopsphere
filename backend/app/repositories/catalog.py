"""Catalogue queries: search, filtering, sorting and pagination."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.catalog import Category, Inventory, Product
from app.models.enums import ProductSort

if TYPE_CHECKING:
    from app.schemas.catalog import ProductFilters

# Every sort ends with a unique tiebreaker on ``Product.id``.
#
# Without it, rows with an equal sort key (three products at $19.99, dozens at
# rating 4.5) come back in whatever order the planner chooses, which can differ
# between two executions of the same query. Page 2 would then repeat or skip
# rows. A deterministic tiebreaker is the difference between a paginated API
# that can be tested and one that is intermittently wrong.
_SORT_CLAUSES = {
    ProductSort.PRICE_ASC: (Product.price.asc(), Product.id.asc()),
    ProductSort.PRICE_DESC: (Product.price.desc(), Product.id.asc()),
    ProductSort.RATING_DESC: (Product.rating.desc(), Product.id.asc()),
    ProductSort.NEWEST: (Product.created_at.desc(), Product.id.desc()),
    ProductSort.NAME_ASC: (Product.name.asc(), Product.id.asc()),
}


def _escape_like(term: str) -> str:
    """Neutralise LIKE wildcards in user input.

    Without this a search for ``100%`` matches everything beginning with 100,
    and ``_`` silently becomes "any character". The value is still passed as a
    bound parameter, so this is about correctness of the *match*, not injection.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_search(stmt: Select, term: str) -> Select:
    """Case-insensitive partial match across the fields a shopper would try."""
    pattern = f"%{_escape_like(term)}%"
    return stmt.where(
        or_(
            Product.name.ilike(pattern, escape="\\"),
            Product.description.ilike(pattern, escape="\\"),
            Product.brand.ilike(pattern, escape="\\"),
            Product.sku.ilike(pattern, escape="\\"),
        )
    )


def _apply_filters(stmt: Select, filters: ProductFilters) -> Select:
    if not filters.include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    if filters.q:
        stmt = _apply_search(stmt, filters.q)
    if filters.category_id is not None:
        stmt = stmt.where(Product.category_id == filters.category_id)
    if filters.category:
        # Accept either the slug or the display name so a URL like
        # /products?category=Laptops behaves the same as ?category=laptops.
        needle = filters.category.strip().lower()
        stmt = stmt.join(Category, Product.category_id == Category.id).where(
            or_(func.lower(Category.slug) == needle, func.lower(Category.name) == needle)
        )
    if filters.brand:
        stmt = stmt.where(func.lower(Product.brand) == filters.brand.strip().lower())
    if filters.min_price is not None:
        stmt = stmt.where(Product.price >= filters.min_price)
    if filters.max_price is not None:
        stmt = stmt.where(Product.price <= filters.max_price)
    if filters.min_rating is not None:
        stmt = stmt.where(Product.rating >= filters.min_rating)
    if filters.in_stock is not None:
        stmt = stmt.join(Inventory, Inventory.product_id == Product.id)
        stmt = stmt.where(Inventory.quantity > 0 if filters.in_stock else Inventory.quantity <= 0)
    return stmt


def _apply_sort(stmt: Select, filters: ProductFilters) -> Select:
    if filters.sort is ProductSort.RELEVANCE:
        if filters.q:
            # Cheap relevance: a hit in the name beats a hit in the description.
            # Not BM25, but it makes "laptop" surface laptops rather than
            # accessories that merely mention one - and it is deterministic.
            pattern = f"%{_escape_like(filters.q)}%"
            name_hit = func.coalesce(Product.name.ilike(pattern, escape="\\"), False)
            return stmt.order_by(name_hit.desc(), Product.rating.desc(), Product.id.asc())
        return stmt.order_by(Product.created_at.desc(), Product.id.desc())
    # SQLAlchemy's order_by() overloads do not cover a star-unpacked tuple, so
    # mypy widens the element type to object. The clauses themselves are
    # correctly typed where they are declared above.
    return stmt.order_by(*_SORT_CLAUSES[filters.sort])  # type: ignore[arg-type]


def search_products(db: Session, filters: ProductFilters) -> tuple[list[Product], int]:
    """Return one page of products plus the total number of matches.

    The count is computed from the same filtered statement (minus ordering and
    limits) so the pagination metadata can never disagree with the results.
    """
    base = select(Product)
    filtered = _apply_filters(base, filters)

    count_stmt = select(func.count()).select_from(filtered.order_by(None).subquery())
    total = db.execute(count_stmt).scalar_one()

    stmt = _apply_sort(filtered, filters).options(
        joinedload(Product.category), joinedload(Product.inventory)
    )
    offset = (filters.page - 1) * filters.page_size
    rows = db.execute(stmt.offset(offset).limit(filters.page_size)).unique().scalars().all()
    return list(rows), total


def get_product(db: Session, product_id: int, *, include_inactive: bool = False) -> Product | None:
    stmt = select(Product).where(Product.id == product_id)
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    return db.execute(stmt).unique().scalar_one_or_none()


def get_product_by_sku(db: Session, sku: str) -> Product | None:
    return (
        db.execute(select(Product).where(func.upper(Product.sku) == sku.strip().upper()))
        .unique()
        .scalar_one_or_none()
    )


def get_products_by_ids(db: Session, product_ids: list[int]) -> dict[int, Product]:
    """Bulk-load products for a cart or order in a single round trip."""
    if not product_ids:
        return {}
    rows = (
        db.execute(
            select(Product)
            .where(Product.id.in_(set(product_ids)))
            .options(joinedload(Product.inventory), joinedload(Product.category))
        )
        .unique()
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


def list_categories(db: Session) -> list[Category]:
    return list(db.execute(select(Category).order_by(Category.name)).scalars().all())


def list_categories_with_counts(db: Session) -> list[tuple[Category, int]]:
    """Categories plus their active-product counts, in one query.

    An outer join keeps empty categories in the result: a category that shows
    up with a count of zero is more useful to a UI than one that vanishes.
    """
    stmt = (
        select(Category, func.count(Product.id))
        .outerjoin(Product, (Product.category_id == Category.id) & Product.is_active.is_(True))
        .group_by(Category.id)
        .order_by(Category.name)
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def get_category_by_slug(db: Session, slug: str) -> Category | None:
    return db.execute(
        select(Category).where(func.lower(Category.slug) == slug.strip().lower())
    ).scalar_one_or_none()


def list_brands(db: Session) -> list[tuple[str, int]]:
    """Brand facet: every brand with at least one active product."""
    stmt = (
        select(Product.brand, func.count(Product.id))
        .where(Product.is_active.is_(True))
        .group_by(Product.brand)
        .order_by(Product.brand)
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def get_inventory(db: Session, product_id: int) -> Inventory | None:
    return db.execute(
        select(Inventory).where(Inventory.product_id == product_id)
    ).scalar_one_or_none()
