"""Domain enumerations shared by models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class RoleName(StrEnum):
    CUSTOMER = "customer"
    ADMIN = "admin"


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @classmethod
    def cancellable(cls) -> frozenset[OrderStatus]:
        """Statuses from which a customer may still cancel.

        Once an order has shipped it is physically in transit, so cancellation
        becomes a returns problem rather than an inventory problem.
        """
        return frozenset({cls.PENDING, cls.CONFIRMED, cls.PROCESSING})

    @classmethod
    def terminal(cls) -> frozenset[OrderStatus]:
        return frozenset({cls.DELIVERED, cls.CANCELLED})


# Admin-driven status transitions. Encoded as data rather than as a chain of
# ``if`` statements so that both the service and its unit tests read from one
# source of truth.
ORDER_STATUS_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.CONFIRMED, OrderStatus.CANCELLED}),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.PROCESSING, OrderStatus.CANCELLED}),
    OrderStatus.PROCESSING: frozenset({OrderStatus.SHIPPED, OrderStatus.CANCELLED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class PromotionType(StrEnum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class ProductSort(StrEnum):
    """Whitelisted sort keys.

    User-supplied sort input is mapped through this enum before it reaches the
    query builder, so a request can never inject an arbitrary ORDER BY clause.
    """

    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    RATING_DESC = "rating_desc"
    NEWEST = "newest"
    NAME_ASC = "name_asc"
    RELEVANCE = "relevance"
