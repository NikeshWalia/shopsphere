"""Orders, order lines and payment records."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Money, TimestampMixin, intpk
from app.models.enums import OrderStatus, PaymentStatus

if TYPE_CHECKING:
    from app.models.catalog import Product
    from app.models.user import User

# native_enum=False renders these as VARCHAR + CHECK rather than a PostgreSQL
# ENUM type. Postgres enums cannot have values added inside a transaction and
# make Alembic downgrades awkward; a CHECK constraint is equally safe and
# migrates cleanly.
_order_status = Enum(
    OrderStatus,
    name="order_status",
    native_enum=False,
    length=20,
    values_callable=lambda enum: [member.value for member in enum],
)
_payment_status = Enum(
    PaymentStatus,
    name="payment_status",
    native_enum=False,
    length=20,
    values_callable=lambda enum: [member.value for member in enum],
)


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[intpk]
    # Human-facing reference (SS-20260827-A1B2C3). Exposed in the UI and in
    # emails; the numeric id stays an internal detail.
    order_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[OrderStatus] = mapped_column(
        _order_status, default=OrderStatus.PENDING, nullable=False
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        _payment_status, default=PaymentStatus.PENDING, nullable=False
    )

    subtotal: Mapped[Money] = mapped_column(nullable=False)
    discount_total: Mapped[Money] = mapped_column(nullable=False, default=0)
    tax: Mapped[Money] = mapped_column(nullable=False)
    shipping_fee: Mapped[Money] = mapped_column(nullable=False, default=0)
    total: Mapped[Money] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    promo_code: Mapped[str | None] = mapped_column(String(32))

    # The address is snapshotted onto the order. Addresses can be edited or
    # deleted afterwards, and an order must always show where it was actually
    # shipped, so a bare foreign key would not be enough.
    shipping_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("addresses.id", ondelete="SET NULL")
    )
    shipping_full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    shipping_line1: Mapped[str] = mapped_column(String(160), nullable=False)
    shipping_line2: Mapped[str | None] = mapped_column(String(160))
    shipping_city: Mapped[str] = mapped_column(String(80), nullable=False)
    shipping_state: Mapped[str] = mapped_column(String(80), nullable=False)
    shipping_postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    shipping_country: Mapped[str] = mapped_column(String(2), nullable=False)
    shipping_phone: Mapped[str | None] = mapped_column(String(32))

    # Client-supplied Idempotency-Key. The unique constraint below is what
    # actually prevents a double-clicked "Place order" button from creating two
    # orders, even when both requests are in flight simultaneously.
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    cancelled_reason: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.id"
    )
    payments: Mapped[list[Payment]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="Payment.id"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_orders_user_id_idempotency_key"),
        CheckConstraint("subtotal >= 0", name="subtotal_non_negative"),
        CheckConstraint("total >= 0", name="total_non_negative"),
        CheckConstraint("discount_total >= 0", name="discount_non_negative"),
        Index("ix_orders_user_id_created_at", "user_id", "created_at"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_payment_status", "payment_status"),
    )

    @property
    def latest_payment(self) -> Payment | None:
        return self.payments[-1] if self.payments else None

    @property
    def item_count(self) -> int:
        return sum(item.quantity for item in self.items)

    def __repr__(self) -> str:
        return f"<Order {self.order_number} {self.status} {self.payment_status}>"


class OrderItem(Base):
    """A purchased line.

    Name, SKU and unit price are copied from the product at purchase time. If
    the catalogue price changes tomorrow the order must still show what the
    customer was actually charged.
    """

    __tablename__ = "order_items"

    id: Mapped[intpk]
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_price: Mapped[Money] = mapped_column(nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total: Mapped[Money] = mapped_column(nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
        Index("ix_order_items_order_id", "order_id"),
        Index("ix_order_items_product_id", "product_id"),
    )

    def __repr__(self) -> str:
        return f"<OrderItem order={self.order_id} {self.sku} x{self.quantity}>"


class Payment(Base, TimestampMixin):
    """One charge attempt against the mock provider.

    Multiple rows per order are expected: a declined attempt followed by a
    successful retry leaves an audit trail rather than overwriting history.
    """

    __tablename__ = "payments"

    id: Mapped[intpk]
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    provider_reference: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[Money] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        _payment_status, default=PaymentStatus.PENDING, nullable=False
    )
    method: Mapped[str] = mapped_column(String(20), default="card", nullable=False)
    # Only the last four digits are ever persisted; the full PAN never leaves
    # the request body.
    card_last4: Mapped[str | None] = mapped_column(String(4))
    card_brand: Mapped[str | None] = mapped_column(String(20))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    failure_message: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    order: Mapped[Order] = relationship(back_populates="payments")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        Index("ix_payments_order_id", "order_id"),
        Index("ix_payments_provider_reference", "provider_reference"),
    )

    def __repr__(self) -> str:
        return f"<Payment order={self.order_id} {self.status} {self.amount}>"
