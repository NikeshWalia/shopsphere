"""SQLAlchemy models.

Every model is imported here so that ``Base.metadata`` is fully populated by a
single ``import app.models``. Alembic autogenerate and the test-database
bootstrap both rely on that.
"""

from app.db.base import Base
from app.models.cart import Cart, CartItem
from app.models.catalog import Category, Inventory, Product
from app.models.enums import (
    ORDER_STATUS_TRANSITIONS,
    OrderStatus,
    PaymentStatus,
    ProductSort,
    PromotionType,
    RoleName,
)
from app.models.order import Order, OrderItem, Payment
from app.models.promotion import Promotion
from app.models.user import Address, Role, User

__all__ = [
    "ORDER_STATUS_TRANSITIONS",
    "Address",
    "Base",
    "Cart",
    "CartItem",
    "Category",
    "Inventory",
    "Order",
    "OrderItem",
    "OrderStatus",
    "Payment",
    "PaymentStatus",
    "Product",
    "ProductSort",
    "Promotion",
    "PromotionType",
    "Role",
    "RoleName",
    "User",
]
