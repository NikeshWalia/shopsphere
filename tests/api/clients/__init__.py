"""Reusable API clients.

One client per bounded context, all sharing :class:`BaseClient`. Tests import
these rather than constructing URLs, so an endpoint change is a one-line fix.
"""

from tests.api.clients.admin import AdminClient, product_payload, unique_sku
from tests.api.clients.auth import AuthClient
from tests.api.clients.catalog import ProductClient
from tests.api.clients.commerce import (
    CARD_APPROVED,
    CARD_DECLINED_CVC,
    CARD_DECLINED_EXPIRED,
    CARD_DECLINED_FUNDS,
    CARD_DECLINED_GENERIC,
    CARD_INVALID_LUHN,
    CARD_PROVIDER_ERROR,
    CARD_TIMEOUT,
    AddressClient,
    CartClient,
    OrderClient,
    default_address,
    payment_payload,
)
from tests.api.clients.payment import PaymentMockClient

__all__ = [
    "CARD_APPROVED",
    "CARD_DECLINED_CVC",
    "CARD_DECLINED_EXPIRED",
    "CARD_DECLINED_FUNDS",
    "CARD_DECLINED_GENERIC",
    "CARD_INVALID_LUHN",
    "CARD_PROVIDER_ERROR",
    "CARD_TIMEOUT",
    "AddressClient",
    "AdminClient",
    "AuthClient",
    "CartClient",
    "OrderClient",
    "PaymentMockClient",
    "ProductClient",
    "default_address",
    "payment_payload",
    "product_payload",
    "unique_sku",
]
