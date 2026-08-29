"""Fixtures providing HTTP clients and API client objects.

Scoping is deliberate. The raw HTTP connection pool is session-scoped because
opening a TCP connection per test is pure waste. Everything carrying *identity*
is function-scoped, so no test can inherit another test's session.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.api.clients import (
    AddressClient,
    AdminClient,
    AuthClient,
    CartClient,
    OrderClient,
    PaymentMockClient,
    ProductClient,
)
from tests.configuration.settings import settings
from tests.utilities.http import HttpClient


@pytest.fixture(scope="session")
def api_url() -> str:
    return settings.api_url


@pytest.fixture(scope="session")
def anonymous_http() -> Iterator[HttpClient]:
    """Unauthenticated client, shared for the session.

    Safe to share because it holds no identity - only a connection pool.
    """
    client = HttpClient(settings.api_url)
    yield client
    client.close()


@pytest.fixture
def http() -> Iterator[HttpClient]:
    """A fresh, unauthenticated client for one test."""
    client = HttpClient(settings.api_url)
    yield client
    client.close()


@pytest.fixture(scope="session")
def payment_mock() -> Iterator[PaymentMockClient]:
    client = HttpClient(settings.payment_mock_url)
    yield PaymentMockClient(client)
    client.close()


# ---------------------------------------------------------------------------
# Anonymous clients - no identity, safe to share for the session
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def auth_client(anonymous_http: HttpClient) -> AuthClient:
    return AuthClient(anonymous_http)


@pytest.fixture(scope="session")
def product_client(anonymous_http: HttpClient) -> ProductClient:
    return ProductClient(anonymous_http)


# ---------------------------------------------------------------------------
# Authenticated clients
#
# Each is function-scoped and built from the calling test's own user fixture, so
# two tests never share a session. Sharing an authenticated client is the single
# most common cause of order-dependent test suites: one test logs out, changes a
# password or deactivates the account, and every later test using that session
# fails for reasons unrelated to what it was testing.
# ---------------------------------------------------------------------------
@pytest.fixture
def customer_http(customer) -> Iterator[HttpClient]:
    client = HttpClient(settings.api_url, token=customer.token)
    yield client
    client.close()


@pytest.fixture
def admin_http(admin_token: str) -> Iterator[HttpClient]:
    client = HttpClient(settings.api_url, token=admin_token)
    yield client
    client.close()


@pytest.fixture
def cart_client(customer_http: HttpClient) -> CartClient:
    return CartClient(customer_http)


@pytest.fixture
def address_client(customer_http: HttpClient) -> AddressClient:
    return AddressClient(customer_http)


@pytest.fixture
def order_client(customer_http: HttpClient) -> OrderClient:
    return OrderClient(customer_http)


@pytest.fixture
def admin_client(admin_http: HttpClient) -> AdminClient:
    return AdminClient(admin_http)


@pytest.fixture
def admin_product_client(admin_http: HttpClient) -> ProductClient:
    """Catalogue client authenticated as an admin.

    Needed for the cases where admins see more than the public does - a
    deactivated product, or `include_inactive` on the listing.
    """
    return ProductClient(admin_http)
