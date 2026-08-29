"""Cart endpoints.

Every response is priced server-side from the catalogue. No request body on any
of these routes accepts a price, a line total or an order total.
"""

from __future__ import annotations

from fastapi import APIRouter, Path, status

from app.api.responses import AUTH_ERRORS, errors
from app.core.deps import CurrentUser, DbSession
from app.schemas.cart import AddCartItemRequest, CartResponse, UpdateCartItemRequest
from app.services import cart as cart_service

router = APIRouter(prefix="/cart", tags=["Cart"])

_ITEM_ERRORS = errors(401, 403, 404, 409, 422)


@router.get(
    "",
    response_model=CartResponse,
    summary="Current user's cart",
    description=(
        "Prices, line totals and order totals are recomputed from the catalogue on every "
        "read, so a cart always reflects the current price and stock. Lines whose stock has "
        "fallen short, or whose product has been deactivated, are reported in `issues` and "
        "set `is_checkout_ready` to false."
    ),
    responses=AUTH_ERRORS,
)
def get_cart(user: CurrentUser, db: DbSession) -> CartResponse:
    return cart_service.get_cart_response(db, user.id)


@router.post(
    "/items",
    response_model=CartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a product to the cart",
    description=(
        "Adding a product already in the cart increases the existing line. Availability is "
        "checked against the **combined** quantity, so repeated small additions cannot "
        "accumulate past the stock level."
    ),
    responses=_ITEM_ERRORS,
)
def add_item(payload: AddCartItemRequest, user: CurrentUser, db: DbSession) -> CartResponse:
    return cart_service.add_item(db, user.id, payload.product_id, payload.quantity)


@router.patch(
    "/items/{product_id}",
    response_model=CartResponse,
    summary="Set the quantity of a cart line",
    description="Sets an absolute quantity. Use DELETE to remove a line; quantity 0 is rejected.",
    responses=_ITEM_ERRORS,
)
def update_item(
    payload: UpdateCartItemRequest,
    user: CurrentUser,
    db: DbSession,
    product_id: int = Path(ge=1),
) -> CartResponse:
    return cart_service.update_item(db, user.id, product_id, payload.quantity)


@router.delete(
    "/items/{product_id}",
    response_model=CartResponse,
    summary="Remove a line from the cart",
    responses=errors(401, 403, 404),
)
def remove_item(user: CurrentUser, db: DbSession, product_id: int = Path(ge=1)) -> CartResponse:
    return cart_service.remove_item(db, user.id, product_id)


@router.delete(
    "",
    response_model=CartResponse,
    summary="Empty the cart",
    description="Idempotent: emptying an already-empty cart succeeds and returns the empty cart.",
    responses=AUTH_ERRORS,
)
def clear_cart(user: CurrentUser, db: DbSession) -> CartResponse:
    return cart_service.clear(db, user.id)
