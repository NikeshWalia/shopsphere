"""Catalogue administration: product creation, updates and stock changes."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    DuplicateSkuError,
    NotFoundError,
    ProductNotFoundError,
)
from app.core.logging import get_logger, log_business_event
from app.models.catalog import Category, Inventory, Product
from app.repositories import catalog as catalog_repo
from app.schemas.catalog import ProductCreateRequest, ProductUpdateRequest

logger = get_logger(__name__)


def _require_category(db: Session, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise NotFoundError("Category not found.", details={"category_id": category_id})
    return category


def create_product(db: Session, payload: ProductCreateRequest) -> Product:
    """Create a product together with its inventory row.

    Products and stock are created in one transaction: a product without an
    inventory row would report zero stock forever and could never be sold.
    """
    _require_category(db, payload.category_id)

    if catalog_repo.get_product_by_sku(db, payload.sku) is not None:
        raise DuplicateSkuError(details={"sku": payload.sku})

    product = Product(
        sku=payload.sku,
        name=payload.name,
        description=payload.description,
        price=payload.price,
        category_id=payload.category_id,
        brand=payload.brand,
        rating=payload.rating,
        image_url=payload.image_url,
        is_active=payload.is_active,
    )
    db.add(product)
    try:
        db.flush()
        db.add(Inventory(product_id=product.id, quantity=payload.stock_quantity))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateSkuError(details={"sku": payload.sku}) from exc

    db.refresh(product)
    log_business_event(
        "product.created", product_id=product.id, sku=product.sku, stock=payload.stock_quantity
    )
    return product


def update_product(db: Session, product_id: int, payload: ProductUpdateRequest) -> Product:
    product = catalog_repo.get_product(db, product_id, include_inactive=True)
    if product is None:
        raise ProductNotFoundError(details={"product_id": product_id})

    # exclude_unset keeps PATCH semantics honest: a field the caller did not
    # send is left alone rather than being reset to its schema default.
    changes = payload.model_dump(exclude_unset=True)
    if "category_id" in changes and changes["category_id"] is not None:
        _require_category(db, changes["category_id"])

    for field, value in changes.items():
        setattr(product, field, value)

    db.commit()
    db.refresh(product)
    log_business_event("product.updated", product_id=product.id, fields=sorted(changes))
    return product


def deactivate_product(db: Session, product_id: int) -> Product:
    """Soft-delete a product.

    Hard deletion is not offered: ``order_items.product_id`` is ``ON DELETE
    RESTRICT``, because a past order must always resolve to a real product row.
    """
    product = catalog_repo.get_product(db, product_id, include_inactive=True)
    if product is None:
        raise ProductNotFoundError(details={"product_id": product_id})

    product.is_active = False
    db.commit()
    db.refresh(product)
    log_business_event("product.deactivated", product_id=product.id, sku=product.sku)
    return product


def set_stock(db: Session, product_id: int, quantity: int) -> Inventory:
    """Set an absolute stock level.

    The row is locked first so an admin adjustment cannot silently overwrite a
    decrement made by a checkout committing at the same moment.
    """
    product = catalog_repo.get_product(db, product_id, include_inactive=True)
    if product is None:
        raise ProductNotFoundError(details={"product_id": product_id})

    locked = _lock_inventory(db, product_id)
    previous = locked.quantity
    locked.quantity = quantity
    db.commit()
    db.refresh(locked)

    log_business_event(
        "inventory.adjusted",
        product_id=product_id,
        sku=product.sku,
        previous_quantity=previous,
        new_quantity=quantity,
    )
    return locked


def _lock_inventory(db: Session, product_id: int) -> Inventory:
    from app.services.inventory import lock_inventory_rows

    rows = lock_inventory_rows(db, [product_id])
    if product_id not in rows:
        # Only reachable for a product created outside create_product().
        inventory = Inventory(product_id=product_id, quantity=0)
        db.add(inventory)
        db.flush()
        return inventory
    return rows[product_id]
