"""Shopping cart.

Cart rows deliberately store only ``product_id`` and ``quantity``. Prices are
resolved server-side on every read and again at checkout, which is what makes
it structurally impossible for a client to dictate what it pays.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, intpk

if TYPE_CHECKING:
    from app.models.catalog import Product
    from app.models.user import User


class Cart(Base, TimestampMixin):
    __tablename__ = "carts"

    id: Mapped[intpk]
    # One cart per user, enforced by the database rather than by convention.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="cart")
    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart",
        cascade="all, delete-orphan",
        order_by="CartItem.id",
    )

    def __repr__(self) -> str:
        return f"<Cart {self.id} user={self.user_id} items={len(self.items)}>"


class CartItem(Base, TimestampMixin):
    __tablename__ = "cart_items"

    id: Mapped[intpk]
    cart_id: Mapped[int] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    cart: Mapped[Cart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")

    __table_args__ = (
        # Adding the same product twice increments the existing line instead of
        # creating a second one; the constraint guarantees that invariant.
        UniqueConstraint("cart_id", "product_id", name="uq_cart_items_cart_id_product_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index("ix_cart_items_cart_id", "cart_id"),
    )

    def __repr__(self) -> str:
        return f"<CartItem cart={self.cart_id} product={self.product_id} qty={self.quantity}>"
