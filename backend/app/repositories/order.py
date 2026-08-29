"""Cart, order, payment and promotion queries."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.cart import Cart, CartItem
from app.models.enums import OrderStatus, PaymentStatus
from app.models.order import Order, OrderItem, Payment
from app.models.promotion import Promotion


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
def get_cart(db: Session, user_id: int) -> Cart | None:
    return (
        db.execute(
            select(Cart)
            .where(Cart.user_id == user_id)
            .options(selectinload(Cart.items).joinedload(CartItem.product))
        )
        .unique()
        .scalar_one_or_none()
    )


def get_or_create_cart(db: Session, user_id: int) -> Cart:
    """Return the user's cart, creating it on first use.

    ``flush`` (not ``commit``) so the new cart participates in whatever
    transaction the caller is running - adding the first item must be atomic
    with creating the cart that holds it.

    The IntegrityError branch is not defensive padding. ``carts.user_id`` is
    UNIQUE, and a customer whose first action is several simultaneous "add to
    cart" clicks produces exactly this race: every request finds no cart, every
    request tries to create one, and all but the first violate the constraint.
    Left unhandled that surfaced as a 500 for four out of five clicks. Catching
    it and re-reading turns the race into the correct outcome - one cart, every
    request served.
    """
    cart = get_cart(db, user_id)
    if cart is not None:
        return cart

    savepoint = db.begin_nested()
    try:
        cart = Cart(user_id=user_id)
        db.add(cart)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        # A concurrent request won. Roll back only the failed INSERT - a plain
        # rollback here would discard the caller's whole transaction.
        savepoint.rollback()
        cart = get_cart(db, user_id)
        if cart is None:  # pragma: no cover - the row must exist if we lost the race
            raise
    return cart


def get_cart_item(db: Session, cart_id: int, product_id: int) -> CartItem | None:
    return db.execute(
        select(CartItem).where(CartItem.cart_id == cart_id, CartItem.product_id == product_id)
    ).scalar_one_or_none()


def clear_cart_items(db: Session, cart: Cart) -> int:
    removed = len(cart.items)
    for item in list(cart.items):
        db.delete(item)
    cart.items.clear()
    return removed


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
def generate_order_number(now: datetime | None = None) -> str:
    """Human-friendly order reference, e.g. ``SS-20260827-7K3QF1``.

    The random suffix uses ``secrets`` and 6 base-32 characters (~1 in a billion
    collision chance per day). A sequential counter would leak order volume, and
    a bare UUID is unreadable over the phone.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d")
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike 0/O, 1/I
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"SS-{stamp}-{suffix}"


_ORDER_LOADERS = (
    selectinload(Order.items).joinedload(OrderItem.product),
    selectinload(Order.payments),
)


def get_order(db: Session, order_id: int, *, user_id: int | None = None) -> Order | None:
    """Fetch an order, optionally constrained to its owner.

    Customer-facing endpoints always pass ``user_id``. Ownership is therefore a
    WHERE clause, not a comparison the handler could omit - which is what makes
    "read another customer's order" return 404 rather than their data.
    """
    stmt = select(Order).where(Order.id == order_id).options(*_ORDER_LOADERS)
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    return db.execute(stmt).unique().scalar_one_or_none()


def get_order_by_number(
    db: Session, order_number: str, *, user_id: int | None = None
) -> Order | None:
    stmt = select(Order).where(Order.order_number == order_number).options(*_ORDER_LOADERS)
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    return db.execute(stmt).unique().scalar_one_or_none()


def find_by_idempotency_key(db: Session, user_id: int, key: str) -> Order | None:
    """Look up a previous checkout with the same key from the same user.

    Scoped by user on purpose: idempotency keys are client-generated, and one
    customer must never be able to retrieve another's order by guessing a key.
    """
    return (
        db.execute(
            select(Order)
            .where(Order.user_id == user_id, Order.idempotency_key == key)
            .options(*_ORDER_LOADERS)
        )
        .unique()
        .scalar_one_or_none()
    )


def _order_list_stmt(
    *,
    user_id: int | None,
    status: OrderStatus | None,
    payment_status: PaymentStatus | None,
    search: str | None,
) -> Select:
    stmt = select(Order)
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    if payment_status is not None:
        stmt = stmt.where(Order.payment_status == payment_status)
    if search:
        stmt = stmt.where(Order.order_number.ilike(f"%{search.strip()}%"))
    return stmt


def list_orders(
    db: Session,
    *,
    user_id: int | None = None,
    status: OrderStatus | None = None,
    payment_status: PaymentStatus | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Order], int]:
    stmt = _order_list_stmt(
        user_id=user_id, status=status, payment_status=payment_status, search=search
    )
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = (
        db.execute(
            stmt.options(selectinload(Order.items))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .unique()
        .scalars()
        .all()
    )
    return list(rows), total


def lock_order(db: Session, order_id: int) -> Order | None:
    """Fetch an order under a row lock.

    Used by cancel and by admin status changes so two concurrent transitions
    cannot both read "confirmed" and both act on it.
    """
    return db.execute(
        select(Order).where(Order.id == order_id).with_for_update()
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
def next_payment_attempt(db: Session, order_id: int) -> int:
    current = db.execute(
        select(func.coalesce(func.max(Payment.attempt), 0)).where(Payment.order_id == order_id)
    ).scalar_one()
    return int(current) + 1


def list_payments(db: Session, order_id: int) -> list[Payment]:
    return list(
        db.execute(select(Payment).where(Payment.order_id == order_id).order_by(Payment.id))
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Promotions
# ---------------------------------------------------------------------------
def get_promotion(db: Session, code: str) -> Promotion | None:
    return db.execute(
        select(Promotion).where(func.upper(Promotion.code) == code.strip().upper())
    ).scalar_one_or_none()


def list_active_promotions(db: Session) -> list[Promotion]:
    now = datetime.now(UTC)
    return list(
        db.execute(
            select(Promotion)
            .where(
                Promotion.is_active.is_(True),
                (Promotion.valid_from.is_(None)) | (Promotion.valid_from <= now),
                (Promotion.valid_to.is_(None)) | (Promotion.valid_to >= now),
            )
            .order_by(Promotion.code)
        )
        .scalars()
        .all()
    )
