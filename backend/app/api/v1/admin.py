"""Administrative endpoints.

Every route here depends on ``CurrentAdmin``. An anonymous caller gets 401 and
an authenticated customer gets 403 - the distinction the security suite asserts
on. The dependency is declared on the router so a new route cannot accidentally
be added without the gate.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import func, select

from app.api.responses import AUTH_ERRORS, errors
from app.api.v1.orders import _to_order_response
from app.api.v1.products import _to_summary
from app.core.deps import CurrentAdmin, DbSession, get_current_admin
from app.core.errors import PermissionDeniedError, UserNotFoundError
from app.models.catalog import Product
from app.models.enums import OrderStatus, PaymentStatus
from app.models.order import Order
from app.repositories import catalog as catalog_repo
from app.repositories import order as order_repo
from app.repositories import user as user_repo
from app.schemas.auth import UserResponse
from app.schemas.catalog import (
    InventoryResponse,
    ProductCreateRequest,
    ProductDetailResponse,
    ProductUpdateRequest,
    StockUpdateRequest,
)
from app.schemas.common import Page
from app.schemas.order import OrderResponse, OrderStatusUpdateRequest, OrderSummaryResponse
from app.services import catalog as catalog_service
from app.services import order as order_service
from app.services.auth import to_user_response

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(get_current_admin)],
    responses=AUTH_ERRORS,
)


def _to_detail(product: Product) -> ProductDetailResponse:
    return ProductDetailResponse(
        **_to_summary(product).model_dump(),
        description=product.description,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
@router.post(
    "/products",
    response_model=ProductDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product",
    description="Creates the product and its inventory row in one transaction.",
    responses=errors(401, 403, 404, 409, 422),
)
def create_product(
    payload: ProductCreateRequest, db: DbSession, admin: CurrentAdmin
) -> ProductDetailResponse:
    return _to_detail(catalog_service.create_product(db, payload))


@router.patch(
    "/products/{product_id}",
    response_model=ProductDetailResponse,
    summary="Update a product",
    description="Partial update: fields absent from the body are left unchanged.",
    responses=errors(401, 403, 404, 422),
)
def update_product(
    payload: ProductUpdateRequest,
    db: DbSession,
    admin: CurrentAdmin,
    product_id: int = Path(ge=1),
) -> ProductDetailResponse:
    return _to_detail(catalog_service.update_product(db, product_id, payload))


@router.delete(
    "/products/{product_id}",
    response_model=ProductDetailResponse,
    summary="Deactivate a product",
    description=(
        "Products are deactivated, never hard-deleted: `order_items` references them with "
        "ON DELETE RESTRICT because past orders must always resolve to a real product row. "
        "A deactivated product disappears from the public catalogue immediately."
    ),
    responses=errors(401, 403, 404),
)
def deactivate_product(
    db: DbSession, admin: CurrentAdmin, product_id: int = Path(ge=1)
) -> ProductDetailResponse:
    return _to_detail(catalog_service.deactivate_product(db, product_id))


@router.put(
    "/products/{product_id}/stock",
    response_model=InventoryResponse,
    summary="Set stock level",
    description=(
        "Sets an absolute quantity. The inventory row is locked first, so an adjustment "
        "cannot silently overwrite a decrement from a checkout committing at the same moment."
    ),
    responses=errors(401, 403, 404, 422),
)
def set_stock(
    payload: StockUpdateRequest,
    db: DbSession,
    admin: CurrentAdmin,
    product_id: int = Path(ge=1),
) -> InventoryResponse:
    inventory = catalog_service.set_stock(db, product_id, payload.quantity)
    product = catalog_repo.get_product(db, product_id, include_inactive=True)
    assert product is not None  # noqa: S101 - set_stock already raised if missing
    return InventoryResponse(
        product_id=product.id,
        sku=product.sku,
        name=product.name,
        quantity=inventory.quantity,
        updated_at=inventory.updated_at,
    )


@router.get(
    "/inventory",
    response_model=Page[InventoryResponse],
    summary="Stock levels across the catalogue",
    description="Supports `low_stock_threshold` to surface products that need restocking.",
)
def list_inventory(
    db: DbSession,
    admin: CurrentAdmin,
    low_stock_threshold: Annotated[int | None, Query(ge=0)] = None,
    search: Annotated[str | None, Query(max_length=160, description="SKU or name contains")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[InventoryResponse]:
    from app.models.catalog import Inventory

    stmt = select(Product, Inventory).join(Inventory, Inventory.product_id == Product.id)
    if low_stock_threshold is not None:
        stmt = stmt.where(Inventory.quantity <= low_stock_threshold)
    if search and (needle := search.strip()):
        # Without a search, finding one product means paging through a table
        # sorted by stock level. That is fine for 60 products and useless for
        # 6,000.
        pattern = f"%{needle.lower()}%"
        stmt = stmt.where(
            func.lower(Product.sku).like(pattern) | func.lower(Product.name).like(pattern)
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(Inventory.quantity.asc(), Product.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [
        InventoryResponse(
            product_id=product.id,
            sku=product.sku,
            name=product.name,
            quantity=inventory.quantity,
            updated_at=inventory.updated_at,
        )
        for product, inventory in rows
    ]
    return Page.build(items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
@router.get(
    "/orders",
    response_model=Page[OrderSummaryResponse],
    summary="All orders",
    description="Unlike `/orders`, this is not scoped to one customer. Admins only.",
)
def list_orders(
    db: DbSession,
    admin: CurrentAdmin,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    payment_status: Annotated[PaymentStatus | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=40, description="Order number contains")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[OrderSummaryResponse]:
    orders, total = order_repo.list_orders(
        db,
        status=status_filter,
        payment_status=payment_status,
        search=search,
        page=page,
        page_size=page_size,
    )
    items = [
        OrderSummaryResponse(
            id=order.id,
            order_number=order.order_number,
            status=order.status,
            payment_status=order.payment_status,
            total=order.total,
            currency=order.currency,
            item_count=order.item_count,
            created_at=order.created_at,
        )
        for order in orders
    ]
    return Page.build(items, total=total, page=page, page_size=page_size)


@router.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Any order's detail",
    responses=errors(401, 403, 404),
)
def get_order(db: DbSession, admin: CurrentAdmin, order_id: int = Path(ge=1)) -> OrderResponse:
    return _to_order_response(order_service.get_order_for_user(db, order_id=order_id, user_id=None))


@router.patch(
    "/orders/{order_id}/status",
    response_model=OrderResponse,
    summary="Advance an order's status",
    description=(
        "Transitions are validated against a state machine: pending to confirmed to "
        "processing to shipped to delivered, with cancellation allowed up to (but not "
        "including) shipped. An illegal transition returns 409 listing what *is* allowed. "
        "Moving an order to `cancelled` restores its stock."
    ),
    responses=errors(401, 403, 404, 409, 422),
)
def update_order_status(
    payload: OrderStatusUpdateRequest,
    db: DbSession,
    admin: CurrentAdmin,
    order_id: int = Path(ge=1),
) -> OrderResponse:
    return _to_order_response(
        order_service.update_status(db, order_id=order_id, new_status=payload.status)
    )


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@router.get("/users", response_model=Page[UserResponse], summary="List users")
def list_users(
    db: DbSession,
    admin: CurrentAdmin,
    search: Annotated[str | None, Query(max_length=120)] = None,
    role: Annotated[str | None, Query(max_length=32)] = None,
    is_active: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[UserResponse]:
    users, total = user_repo.list_users(
        db, search=search, role=role, is_active=is_active, page=page, page_size=page_size
    )
    return Page.build(
        [to_user_response(user) for user in users], total=total, page=page, page_size=page_size
    )


@router.patch(
    "/users/{user_id}/active",
    response_model=UserResponse,
    summary="Activate or deactivate a user",
    description=(
        "Deactivation takes effect on the very next request, because the user record is "
        "re-read on every authenticated call rather than trusted from the token.\n\n"
        "An admin cannot deactivate their own account - that would be an easy way to lock "
        "the last administrator out of the system."
    ),
    responses=errors(401, 403, 404, 409),
)
def set_user_active(
    db: DbSession,
    admin: CurrentAdmin,
    user_id: int = Path(ge=1),
    is_active: bool = Query(description="Target state"),
) -> UserResponse:
    if user_id == admin.id:
        raise PermissionDeniedError(
            "You cannot change your own account's active state.", details={"user_id": user_id}
        )

    user = user_repo.get_user(db, user_id)
    if user is None:
        raise UserNotFoundError(details={"user_id": user_id})

    user.is_active = is_active
    db.commit()
    db.refresh(user)
    return to_user_response(user)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get(
    "/stats",
    summary="Headline counts for the admin dashboard",
    description="Aggregates computed in SQL rather than by loading rows into Python.",
)
def stats(db: DbSession, admin: CurrentAdmin) -> dict[str, object]:
    from app.models.catalog import Inventory
    from app.models.user import User

    orders_by_status: dict[OrderStatus, int] = {
        row[0]: row[1]
        for row in db.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        ).all()
    }
    revenue = db.execute(
        select(func.coalesce(func.sum(Order.total), 0)).where(
            Order.payment_status == PaymentStatus.PAID
        )
    ).scalar_one()

    return {
        "products_total": db.execute(select(func.count(Product.id))).scalar_one(),
        "products_active": db.execute(
            select(func.count(Product.id)).where(Product.is_active.is_(True))
        ).scalar_one(),
        "out_of_stock": db.execute(
            select(func.count(Inventory.id)).where(Inventory.quantity == 0)
        ).scalar_one(),
        "users_total": db.execute(select(func.count(User.id))).scalar_one(),
        "orders_total": db.execute(select(func.count(Order.id))).scalar_one(),
        "orders_by_status": {str(key): value for key, value in orders_by_status.items()},
        "paid_revenue": float(revenue),
    }
