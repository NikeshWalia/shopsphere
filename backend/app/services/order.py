"""Checkout, order lifecycle and payment reconciliation.

The checkout sequence is the highest-risk path in the application, so the
ordering of its steps is deliberate and worth stating explicitly:

    1.  Validate the cart, the address and the promotion code.
    2.  Lock every relevant inventory row (``FOR UPDATE``, ordered by product id).
    3.  Re-validate availability against the *locked* rows.
    4.  Compute totals server-side from catalogue prices.
    5.  Create the order (pending/pending), its items, and decrement stock.
    6.  **Commit.** Stock is now reserved and the order exists durably.
    7.  Call the payment provider - outside the transaction, holding no locks.
    8.  Record the outcome in a second, short transaction.

Steps 5-6 happen *before* the charge so that a slow provider cannot leave the
last unit of stock sellable to somebody else while the first customer is paying.
Step 7 is outside the transaction because a provider that takes eight seconds
must not hold inventory row locks for eight seconds - that would serialise every
checkout in the shop behind one slow card.

Outcome handling in step 8 distinguishes what we *know* from what we do not:

* **Approved**  - order confirmed, payment paid, cart cleared.
* **Declined / provider error / invalid request** - we know no money moved, so
  stock is returned and the order is cancelled.
* **Timeout** - we genuinely do not know whether the charge succeeded. The order
  stays ``pending``/``pending`` with stock still reserved rather than being
  guessed either way. See "Known limitations" in the README: a production system
  would reconcile these against the provider on a schedule.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    AddressNotFoundError,
    EmptyCartError,
    InvalidOrderStateError,
    OrderNotFoundError,
    PaymentDeclinedError,
    PaymentProviderError,
    PaymentProviderTimeoutError,
    PromotionInvalidError,
)
from app.core.logging import get_logger, log_business_event
from app.models.enums import ORDER_STATUS_TRANSITIONS, OrderStatus, PaymentStatus
from app.models.order import Order, OrderItem, Payment
from app.models.promotion import Promotion
from app.models.user import Address
from app.repositories import order as order_repo
from app.repositories import user as user_repo
from app.schemas.order import CheckoutRequest, QuoteResponse
from app.services import inventory as inventory_service
from app.services.cart import PricedCart, price_cart
from app.services.payment import ChargeOutcome, ChargeResult, PaymentGateway
from app.services.pricing import OrderTotals, PromotionRule, compute_totals, promotion_error

logger = get_logger(__name__)


def promotion_rule_from_model(promotion: Promotion) -> PromotionRule:
    """Convert an ORM row into the framework-free rule the pricing module uses."""
    return PromotionRule(
        code=promotion.code,
        discount_type=promotion.discount_type,
        value=Decimal(promotion.value),
        min_subtotal=Decimal(promotion.min_subtotal),
        max_discount=(
            Decimal(promotion.max_discount) if promotion.max_discount is not None else None
        ),
        is_active=promotion.is_active,
        valid_from=promotion.valid_from,
        valid_to=promotion.valid_to,
        usage_limit=promotion.usage_limit,
        times_used=promotion.times_used,
        description=promotion.description,
    )


def resolve_promotion(
    db: Session, code: str | None, subtotal: Decimal
) -> tuple[Promotion | None, PromotionRule | None]:
    """Look up and validate a promotion code, or raise a descriptive 422."""
    if not code:
        return None, None

    promotion = order_repo.get_promotion(db, code)
    if promotion is None:
        raise PromotionInvalidError(
            f"Promotion code {code.upper()} was not recognised.", details={"promo_code": code}
        )

    rule = promotion_rule_from_model(promotion)
    if (reason := promotion_error(rule, subtotal)) is not None:
        raise PromotionInvalidError(reason, details={"promo_code": promotion.code})
    return promotion, rule


def _totals_for(priced: PricedCart, rule: PromotionRule | None) -> OrderTotals:
    return compute_totals(
        priced.lines,
        tax_rate=settings.tax_rate,
        shipping_flat_fee=settings.shipping_flat_fee,
        free_shipping_threshold=settings.free_shipping_threshold,
        promotion=rule,
        currency=settings.currency,
    )


def quote(db: Session, user_id: int, promo_code: str | None) -> QuoteResponse:
    """Preview the totals for the current cart without placing an order.

    Uses the same :func:`compute_totals` call as checkout, so what the customer
    is shown and what they are charged cannot drift apart.
    """
    priced = price_cart(db, order_repo.get_cart(db, user_id))
    subtotal = sum((line.line_total for line in priced.lines), Decimal("0.00"))

    promotion: Promotion | None = None
    rule: PromotionRule | None = None
    issues = list(priced.issues)
    if promo_code:
        try:
            promotion, rule = resolve_promotion(db, promo_code, subtotal)
        except PromotionInvalidError as exc:
            # A quote reports a bad code as an issue rather than failing: the
            # customer still needs to see their basket.
            issues.append(exc.message)

    totals = _totals_for(priced, rule)
    return QuoteResponse(
        subtotal=totals.subtotal,
        discount_total=totals.discount_total,
        tax=totals.tax,
        shipping_fee=totals.shipping_fee,
        total=totals.total,
        currency=totals.currency,
        promo_code=promotion.code if promotion else None,
        promo_description=promotion.description if promotion else None,
        item_count=sum(row.quantity for row in priced.item_rows),
        issues=issues,
        is_checkout_ready=bool(priced.item_rows) and not issues,
    )


def checkout(
    db: Session,
    *,
    user_id: int,
    request: CheckoutRequest,
    gateway: PaymentGateway,
    idempotency_key: str | None = None,
) -> Order:
    """Place an order. See the module docstring for the full sequence."""
    if (
        idempotency_key
        and (existing := order_repo.find_by_idempotency_key(db, user_id, idempotency_key))
        is not None
    ):
        logger.info(
            "Replaying idempotent checkout",
            extra={"order_id": existing.id, "idempotency_key": idempotency_key},
        )
        return existing

    address = user_repo.get_address(db, request.address_id, user_id=user_id)
    if address is None:
        # Scoped by user_id, so another customer's address id is indistinguishable
        # from one that does not exist.
        raise AddressNotFoundError(details={"address_id": request.address_id})

    cart = order_repo.get_cart(db, user_id)
    priced = price_cart(db, cart)
    if not priced.lines:
        raise EmptyCartError()
    if priced.issues:
        raise InvalidOrderStateError(priced.issues[0], details={"issues": priced.issues})

    subtotal = sum((line.line_total for line in priced.lines), Decimal("0.00"))
    promotion, rule = resolve_promotion(db, request.promo_code, subtotal)

    order, replayed = _reserve_and_create_order(
        db,
        user_id=user_id,
        priced=priced,
        rule=rule,
        promotion=promotion,
        address=address,
        idempotency_key=idempotency_key,
    )
    if replayed:
        # A concurrent request with the same key won the race and has already
        # been charged; returning its order is the whole point of the key.
        return order

    result = gateway.charge(
        amount=Decimal(order.total),
        currency=order.currency,
        card_number=request.payment.card_number,
        card_holder=request.payment.card_holder,
        expiry_month=request.payment.expiry_month,
        expiry_year=request.payment.expiry_year,
        cvv=request.payment.cvv,
        reference=order.order_number,
    )
    return _apply_payment_result(db, order=order, result=result, cart_user_id=user_id)


def _reserve_and_create_order(
    db: Session,
    *,
    user_id: int,
    priced: PricedCart,
    rule: PromotionRule | None,
    promotion: Promotion | None,
    address: Address,
    idempotency_key: str | None,
) -> tuple[Order, bool]:
    """Steps 2-6: lock stock, create the order, decrement, commit.

    Returns the order and whether it was an existing one recovered after losing
    an idempotency-key race (in which case it must not be charged again).
    """
    product_ids = [line.product_id for line in priced.lines]
    locked = inventory_service.lock_inventory_rows(db, product_ids)

    # Re-validate against the locked rows. The earlier check in price_cart used
    # an unlocked read that another checkout may have invalidated since.
    inventory_service.validate_all(
        [
            inventory_service.StockRequest(
                product_id=line.product_id,
                product_name=line.name,
                requested=line.quantity,
                available=locked[line.product_id].quantity if line.product_id in locked else 0,
            )
            for line in priced.lines
        ]
    )

    totals = _totals_for(priced, rule)
    order = Order(
        order_number=order_repo.generate_order_number(),
        user_id=user_id,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        subtotal=totals.subtotal,
        discount_total=totals.discount_total,
        tax=totals.tax,
        shipping_fee=totals.shipping_fee,
        total=totals.total,
        currency=totals.currency,
        promo_code=promotion.code if promotion else None,
        shipping_address_id=address.id,
        shipping_full_name=address.full_name,
        shipping_line1=address.line1,
        shipping_line2=address.line2,
        shipping_city=address.city,
        shipping_state=address.state,
        shipping_postal_code=address.postal_code,
        shipping_country=address.country,
        shipping_phone=address.phone,
        idempotency_key=idempotency_key,
    )
    db.add(order)
    db.flush()

    for line in priced.lines:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=line.product_id,
                product_name=line.name,
                sku=line.sku,
                unit_price=line.unit_price,
                quantity=line.quantity,
                line_total=line.line_total,
            )
        )
        inventory_service.decrement(locked[line.product_id], line.quantity, product_name=line.name)

    if promotion is not None:
        promotion.times_used += 1

    try:
        db.commit()
    except IntegrityError:
        # Two checkouts raced with the same Idempotency-Key. The unique
        # constraint let exactly one through; this transaction (including its
        # stock decrement) is rolled back wholesale, and we return the winner.
        db.rollback()
        if idempotency_key:
            winner = order_repo.find_by_idempotency_key(db, user_id, idempotency_key)
            if winner is not None:
                logger.info(
                    "Concurrent checkout collapsed onto existing order",
                    extra={"order_id": winner.id, "idempotency_key": idempotency_key},
                )
                return winner, True
        raise

    db.refresh(order)
    log_business_event(
        "order.created",
        order_id=order.id,
        order_number=order.order_number,
        user_id=user_id,
        total=str(order.total),
        item_count=len(priced.lines),
    )
    return order, False


def _apply_payment_result(
    db: Session, *, order: Order, result: ChargeResult, cart_user_id: int
) -> Order:
    """Step 8: persist the charge attempt and move the order to its next state."""
    attempt = order_repo.next_payment_attempt(db, order.id)
    payment = Payment(
        order_id=order.id,
        provider_reference=result.provider_reference,
        amount=order.total,
        currency=order.currency,
        method="card",
        card_last4=result.card_last4,
        card_brand=result.card_brand,
        attempt=attempt,
    )

    if result.approved:
        payment.status = PaymentStatus.PAID
        order.payment_status = PaymentStatus.PAID
        order.status = OrderStatus.CONFIRMED
        db.add(payment)

        cart = order_repo.get_cart(db, cart_user_id)
        if cart is not None:
            order_repo.clear_cart_items(db, cart)

        db.commit()
        db.refresh(order)
        log_business_event(
            "order.paid",
            order_id=order.id,
            order_number=order.order_number,
            total=str(order.total),
            provider_reference=result.provider_reference,
        )
        return order

    if result.outcome is ChargeOutcome.TIMEOUT:
        # Unknown outcome. Record the attempt, leave the order pending and keep
        # the stock reserved rather than guessing. The 504 tells the client the
        # order exists and is awaiting confirmation.
        payment.status = PaymentStatus.PENDING
        payment.failure_code = result.failure_code
        payment.failure_message = result.failure_message
        db.add(payment)
        db.commit()
        log_business_event(
            "order.payment_timeout",
            order_id=order.id,
            order_number=order.order_number,
            total=str(order.total),
        )
        raise PaymentProviderTimeoutError(
            "The payment provider did not respond in time. Your order is pending confirmation.",
            details={
                "order_id": order.id,
                "order_number": order.order_number,
                "order_status": OrderStatus.PENDING.value,
                "payment_status": PaymentStatus.PENDING.value,
            },
        )

    # Declined, provider error or invalid request: no money moved, so unwind.
    payment.status = PaymentStatus.FAILED
    payment.failure_code = result.failure_code
    payment.failure_message = result.failure_message
    db.add(payment)

    order.payment_status = PaymentStatus.FAILED
    order.status = OrderStatus.CANCELLED
    order.cancelled_reason = result.failure_message or "Payment failed"
    _restore_inventory(db, order)
    db.commit()
    db.refresh(order)

    log_business_event(
        "order.payment_failed",
        order_id=order.id,
        order_number=order.order_number,
        outcome=result.outcome.value,
        failure_code=result.failure_code,
    )

    details = {
        "order_id": order.id,
        "order_number": order.order_number,
        "order_status": order.status.value,
        "payment_status": order.payment_status.value,
        "failure_code": result.failure_code,
    }
    if result.outcome is ChargeOutcome.DECLINED:
        raise PaymentDeclinedError(
            result.failure_message or "The card was declined.", details=details
        )
    raise PaymentProviderError(
        result.failure_message or "The payment provider returned an error.", details=details
    )


def _restore_inventory(db: Session, order: Order) -> None:
    """Return every line's stock to the shelf, under row locks."""
    locked = inventory_service.lock_inventory_rows(db, [item.product_id for item in order.items])
    for item in order.items:
        if (row := locked.get(item.product_id)) is not None:
            inventory_service.increment(row, item.quantity)


def cancel_order(db: Session, *, order_id: int, user_id: int | None, reason: str | None) -> Order:
    """Cancel an order and return its stock.

    ``user_id`` is ``None`` for admin cancellations and set for customer ones,
    which is what stops a customer cancelling somebody else's order.
    """
    order = order_repo.get_order(db, order_id, user_id=user_id)
    if order is None:
        raise OrderNotFoundError(details={"order_id": order_id})

    if order.status not in OrderStatus.cancellable():
        raise InvalidOrderStateError(
            f"An order with status '{order.status.value}' can no longer be cancelled.",
            details={"order_id": order.id, "status": order.status.value},
        )

    was_paid = order.payment_status is PaymentStatus.PAID
    _restore_inventory(db, order)

    order.status = OrderStatus.CANCELLED
    order.cancelled_reason = reason or "Cancelled by customer"
    if was_paid:
        # The mock provider has no real settlement, so the refund is recorded
        # rather than requested. A real integration would call the provider and
        # only mark REFUNDED once it acknowledged.
        order.payment_status = PaymentStatus.REFUNDED
        db.add(
            Payment(
                order_id=order.id,
                provider_reference=(
                    order.latest_payment.provider_reference if order.latest_payment else None
                ),
                amount=order.total,
                currency=order.currency,
                status=PaymentStatus.REFUNDED,
                method="card",
                attempt=order_repo.next_payment_attempt(db, order.id),
            )
        )

    db.commit()
    db.refresh(order)
    log_business_event(
        "order.cancelled",
        order_id=order.id,
        order_number=order.order_number,
        refunded=was_paid,
        restored_items=len(order.items),
    )
    return order


def update_status(db: Session, *, order_id: int, new_status: OrderStatus) -> Order:
    """Admin status transition, validated against the allowed state machine."""
    order = order_repo.get_order(db, order_id)
    if order is None:
        raise OrderNotFoundError(details={"order_id": order_id})

    allowed = ORDER_STATUS_TRANSITIONS[order.status]
    if new_status not in allowed:
        raise InvalidOrderStateError(
            f"Cannot move an order from '{order.status.value}' to '{new_status.value}'.",
            details={
                "order_id": order.id,
                "current_status": order.status.value,
                "requested_status": new_status.value,
                "allowed": sorted(status.value for status in allowed),
            },
        )

    if new_status is OrderStatus.CANCELLED:
        return cancel_order(db, order_id=order_id, user_id=None, reason="Cancelled by admin")

    previous = order.status
    order.status = new_status
    order.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(order)
    log_business_event(
        "order.status_changed",
        order_id=order.id,
        order_number=order.order_number,
        previous_status=previous.value,
        new_status=new_status.value,
    )
    return order


def get_order_for_user(db: Session, *, order_id: int, user_id: int | None) -> Order:
    order = order_repo.get_order(db, order_id, user_id=user_id)
    if order is None:
        raise OrderNotFoundError(details={"order_id": order_id})
    return order


def order_to_response_kwargs(order: Order) -> dict:
    """Flatten the snapshotted shipping columns back into a nested object."""
    return {
        "shipping_address": {
            "full_name": order.shipping_full_name,
            "line1": order.shipping_line1,
            "line2": order.shipping_line2,
            "city": order.shipping_city,
            "state": order.shipping_state,
            "postal_code": order.shipping_postal_code,
            "country": order.shipping_country,
            "phone": order.shipping_phone,
        }
    }
