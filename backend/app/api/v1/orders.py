"""Checkout and order endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.responses import AUTH_ERRORS, errors
from app.core.deps import CurrentUser, DbSession, IdempotencyKey
from app.models.enums import OrderStatus, PaymentStatus
from app.models.order import Order
from app.repositories import order as order_repo
from app.schemas.common import Page
from app.schemas.order import (
    CancelOrderRequest,
    CheckoutRequest,
    OrderResponse,
    OrderSummaryResponse,
    QuoteRequest,
    QuoteResponse,
)
from app.services import order as order_service
from app.services.payment import PaymentGateway, get_payment_gateway

router = APIRouter(tags=["Orders"])

Gateway = Annotated[PaymentGateway, Depends(get_payment_gateway)]


def _to_order_response(order: Order) -> OrderResponse:
    return OrderResponse.model_validate(
        {
            **{column.name: getattr(order, column.name) for column in order.__table__.columns},
            "items": order.items,
            "payments": order.payments,
            **order_service.order_to_response_kwargs(order),
        }
    )


@router.post(
    "/checkout/quote",
    response_model=QuoteResponse,
    summary="Preview checkout totals",
    description=(
        "Computes tax, shipping and any discount for the current cart **without** placing an "
        "order. It calls the same total-calculation code as checkout, so the preview a "
        "customer sees can never disagree with the amount they are charged.\n\n"
        "An invalid promotion code is reported in `issues` rather than failing the request, "
        "so the basket still renders."
    ),
    responses=AUTH_ERRORS,
)
def quote(payload: QuoteRequest, user: CurrentUser, db: DbSession) -> QuoteResponse:
    return order_service.quote(db, user.id, payload.promo_code)


@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place an order (checkout)",
    description=(
        "Reserves stock, creates the order, then charges the payment provider.\n\n"
        "**Idempotency.** Send an `Idempotency-Key` header. Repeating the request with the "
        "same key returns the original order instead of creating a second one, which is what "
        "makes a double-clicked 'Place order' button safe. Without the header no such "
        "protection is possible and two submissions create two orders.\n\n"
        "**Totals** are computed entirely server-side from catalogue prices; the request "
        "body carries no monetary values at all.\n\n"
        "**Failure handling.** A declined card or a provider error returns the stock and "
        "cancels the order. A provider *timeout* is different: the outcome is genuinely "
        "unknown, so the order is left `pending` with stock still reserved and a 504 is "
        "returned - it is never optimistically marked paid."
    ),
    responses=errors(401, 402, 403, 404, 409, 422, 502, 504),
)
def checkout(
    payload: CheckoutRequest,
    user: CurrentUser,
    db: DbSession,
    gateway: Gateway,
    idempotency_key: IdempotencyKey,
) -> OrderResponse:
    order = order_service.checkout(
        db, user_id=user.id, request=payload, gateway=gateway, idempotency_key=idempotency_key
    )
    return _to_order_response(order)


@router.get(
    "/orders",
    response_model=Page[OrderSummaryResponse],
    summary="Order history for the current user",
    description="Scoped to the authenticated user; there is no way to widen it to other accounts.",
    responses=AUTH_ERRORS,
)
def list_orders(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[OrderStatus | None, Query(alias="status")] = None,
    payment_status: Annotated[PaymentStatus | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[OrderSummaryResponse]:
    orders, total = order_repo.list_orders(
        db,
        user_id=user.id,
        status=status_filter,
        payment_status=payment_status,
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
    summary="Order detail",
    description=(
        "Ownership is enforced as part of the database query. Another customer's order id "
        "returns `ORDER_NOT_FOUND`, identical to an id that does not exist, so this endpoint "
        "cannot be used to discover which orders exist."
    ),
    responses=errors(401, 403, 404),
)
def get_order(user: CurrentUser, db: DbSession, order_id: int = Path(ge=1)) -> OrderResponse:
    order = order_service.get_order_for_user(db, order_id=order_id, user_id=user.id)
    return _to_order_response(order)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Cancel an order",
    description=(
        "Allowed while the order is pending, confirmed or processing. Cancelling returns "
        "every line's stock to the catalogue, and an order that was already paid moves to "
        "`refunded`. Shipped and delivered orders cannot be cancelled."
    ),
    responses=errors(401, 403, 404, 409),
)
def cancel_order(
    payload: CancelOrderRequest,
    user: CurrentUser,
    db: DbSession,
    order_id: int = Path(ge=1),
) -> OrderResponse:
    order = order_service.cancel_order(
        db, order_id=order_id, user_id=user.id, reason=payload.reason
    )
    return _to_order_response(order)
