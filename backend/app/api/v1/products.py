"""Public catalogue endpoints: search, filter, sort, browse."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.responses import NOT_FOUND, errors
from app.core.deps import DbSession, OptionalUser
from app.core.errors import NotFoundError, ProductNotFoundError
from app.models.catalog import Product
from app.models.enums import RoleName
from app.repositories import catalog as catalog_repo
from app.schemas.catalog import (
    BrandResponse,
    CategoryResponse,
    CategoryWithCountResponse,
    ProductDetailResponse,
    ProductFilters,
    ProductSummaryResponse,
)
from app.schemas.common import Page

router = APIRouter(tags=["Catalogue"])


def _to_summary(product: Product) -> ProductSummaryResponse:
    return ProductSummaryResponse(
        id=product.id,
        sku=product.sku,
        name=product.name,
        price=product.price,
        brand=product.brand,
        rating=product.rating,
        image_url=product.image_url,
        is_active=product.is_active,
        in_stock=product.in_stock,
        stock_quantity=product.stock_quantity,
        category=CategoryResponse.model_validate(product.category),
    )


@router.get(
    "/products",
    response_model=Page[ProductSummaryResponse],
    summary="Search, filter and sort products",
    description=(
        "All filters combine with AND. Search is case-insensitive and matches partial "
        "words across name, description, brand and SKU.\n\n"
        "Sorting is restricted to a fixed set of keys and every sort carries a unique "
        "tiebreaker, so pagination is stable: page 2 never repeats or skips a row that "
        "page 1 already returned.\n\n"
        "`include_inactive` is honoured only for administrators; other callers always "
        "receive active products."
    ),
    responses=errors(422),
)
def list_products(
    db: DbSession,
    user: OptionalUser,
    # Binding the whole filter model as query parameters (rather than listing
    # them out and constructing it in the body) means its cross-field rules -
    # such as min_price <= max_price - are evaluated during request parsing.
    # They therefore surface as a normal 422 through the validation handler
    # instead of raising a ValidationError mid-handler and becoming a 500.
    filters: Annotated[ProductFilters, Query()] = None,  # type: ignore[assignment]
) -> Page[ProductSummaryResponse]:
    is_admin = user is not None and user.role_name == RoleName.ADMIN
    if filters.include_inactive and not is_admin:
        # Silently downgraded rather than rejected: a non-admin passing the flag
        # simply sees the public catalogue.
        filters = filters.model_copy(update={"include_inactive": False})

    products, total = catalog_repo.search_products(db, filters)
    return Page.build(
        [_to_summary(product) for product in products],
        total=total,
        page=filters.page,
        page_size=filters.page_size,
    )


@router.get(
    "/products/brands",
    response_model=list[BrandResponse],
    summary="Brand facet",
    description="Every brand with at least one active product, for populating filter controls.",
)
def list_brands(db: DbSession) -> list[BrandResponse]:
    return [
        BrandResponse(brand=brand, product_count=count)
        for brand, count in catalog_repo.list_brands(db)
    ]


@router.get(
    "/products/{product_id}",
    response_model=ProductDetailResponse,
    summary="Product detail",
    description="Administrators additionally see deactivated products; everyone else gets 404.",
    responses=NOT_FOUND,
)
def get_product(product_id: int, db: DbSession, user: OptionalUser) -> ProductDetailResponse:
    is_admin = user is not None and user.role_name == RoleName.ADMIN
    product = catalog_repo.get_product(db, product_id, include_inactive=is_admin)
    if product is None:
        raise ProductNotFoundError(details={"product_id": product_id})

    return ProductDetailResponse(
        **_to_summary(product).model_dump(),
        description=product.description,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


@router.get(
    "/categories",
    response_model=list[CategoryWithCountResponse],
    summary="Categories with active-product counts",
)
def list_categories(db: DbSession) -> list[CategoryWithCountResponse]:
    return [
        CategoryWithCountResponse(
            id=category.id,
            name=category.name,
            slug=category.slug,
            description=category.description,
            product_count=count,
        )
        for category, count in catalog_repo.list_categories_with_counts(db)
    ]


@router.get(
    "/categories/{slug}",
    response_model=CategoryResponse,
    summary="Category by slug",
    responses=NOT_FOUND,
)
def get_category(slug: str, db: DbSession) -> CategoryResponse:
    category = catalog_repo.get_category_by_slug(db, slug)
    if category is None:
        raise NotFoundError("Category not found.", details={"slug": slug})
    return CategoryResponse.model_validate(category)
