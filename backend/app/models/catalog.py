"""Product catalogue and stock levels."""

from __future__ import annotations

# Imported at runtime, not merely for type checking: SQLAlchemy resolves the
# string annotations inside Mapped[...] at class-definition time.
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Money, TimestampMixin, intpk


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[intpk]
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    products: Mapped[list[Product]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<Category {self.slug}>"


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[intpk]
    sku: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    price: Mapped[Money] = mapped_column(nullable=False)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False
    )
    brand: Mapped[str] = mapped_column(String(80), nullable=False)
    rating: Mapped[Decimal] = mapped_column(Numeric(2, 1), default=0, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512))
    # Products are deactivated, never hard-deleted: order history must keep
    # pointing at a real row.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped[Category] = relationship(back_populates="products", lazy="joined")
    inventory: Mapped[Inventory] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False, lazy="joined"
    )

    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("rating >= 0 AND rating <= 5", name="rating_within_range"),
        Index("ix_products_category_id", "category_id"),
        Index("ix_products_brand", "brand"),
        Index("ix_products_price", "price"),
        Index("ix_products_rating", "rating"),
        Index("ix_products_created_at", "created_at"),
        # Listing endpoints always filter on is_active first; the composite
        # index lets the common "active products in a category" query be served
        # without touching inactive rows.
        Index("ix_products_is_active_category_id", "is_active", "category_id"),
        # Case-insensitive search hits lower(name); the expression index makes
        # prefix matches (name ILIKE 'laptop%') index-eligible.
        Index("ix_products_lower_name", func.lower(name)),
    )

    @property
    def stock_quantity(self) -> int:
        return self.inventory.quantity if self.inventory else 0

    @property
    def in_stock(self) -> bool:
        return self.stock_quantity > 0

    def __repr__(self) -> str:
        return f"<Product {self.sku} {self.name!r}>"


class Inventory(Base):
    """Stock level, kept in its own table.

    Separating stock from the product row means the hot, frequently-updated
    counter is locked independently of catalogue reads: ``SELECT ... FOR UPDATE``
    during checkout blocks other checkouts without blocking product browsing.
    """

    __tablename__ = "inventory"

    id: Mapped[intpk]
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="inventory")

    __table_args__ = (
        # The last line of defence against overselling. Even if application
        # logic is bypassed or races, the database refuses to go negative.
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<Inventory product={self.product_id} qty={self.quantity}>"
