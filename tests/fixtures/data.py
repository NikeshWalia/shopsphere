"""Fixtures that provide test data and database access.

Two kinds of product fixture, used for different purposes:

* ``seeded_product`` - an existing catalogue product, read-only. Cheap, and
  right for tests that only need "some product that exists".
* ``product_factory`` / ``product_with_stock`` - products created by this test,
  with an exact stock level. Necessary for anything that *mutates* stock, since
  a shared product would make two concurrent tests interfere.

Products created here are deactivated at teardown rather than deleted:
``order_items.product_id`` is ``ON DELETE RESTRICT``, because an order must
always resolve to a real product row. Deactivation is what the application
itself does, so cleanup exercises the same path a real admin would.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.api.clients import AdminClient, ProductClient
from tests.database.queries.queries import DatabaseQueries
from tests.test_data.factories import product_payload


@pytest.fixture(scope="session")
def db() -> Iterator[DatabaseQueries]:
    """Read-only database access, shared for the session.

    Session-scoped because the connection is stateless as far as tests are
    concerned - it is opened in autocommit mode and only ever reads.
    """
    queries = DatabaseQueries()
    queries.connect()
    yield queries
    queries.close()


@pytest.fixture
def fresh_db() -> Iterator[DatabaseQueries]:
    """A dedicated connection.

    Needed by the concurrency tests, where several threads must each observe
    the database independently rather than sharing one connection.
    """
    queries = DatabaseQueries()
    queries.connect()
    yield queries
    queries.close()


@pytest.fixture(scope="session")
def seeded_product(product_client: ProductClient) -> dict[str, Any]:
    """Any seeded product with stock. Treat as read-only."""
    return product_client.first_in_stock(min_stock=5)


@pytest.fixture(scope="session")
def seeded_categories(product_client: ProductClient) -> list[dict[str, Any]]:
    response = product_client.categories()
    response.assert_status(200)
    return list(response.body)


@pytest.fixture
def product_factory(admin_client: AdminClient) -> Iterator[Callable[..., dict[str, Any]]]:
    """Create products owned by this test, cleaned up afterwards."""
    created: list[int] = []

    def _create(**overrides: Any) -> dict[str, Any]:
        response = admin_client.create_product(product_payload(**overrides))
        assert (
            response.status_code == 201
        ), f"Could not create test product: {response.status_code} {response.raw_text[:300]}"
        product = dict(response.body)
        created.append(int(product["id"]))
        return product

    yield _create

    for product_id in created:
        # Best-effort: a test that already deactivated the product, or that
        # failed before creating it, must not turn teardown into a second
        # failure that masks the real one.
        with contextlib.suppress(Exception):
            admin_client.deactivate_product(product_id)


@pytest.fixture
def product_with_stock(
    product_factory: Callable[..., dict[str, Any]]
) -> Callable[[int], dict[str, Any]]:
    """Create a product with an exact number of units in stock."""

    def _create(stock: int, **overrides: Any) -> dict[str, Any]:
        return product_factory(stock_quantity=stock, **overrides)

    return _create


@pytest.fixture
def out_of_stock_product(product_factory: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """A product created with zero stock.

    Created rather than found: the seeded out-of-stock products are shared, and
    a test that restocked one would break every other test relying on it.
    """
    return product_factory(stock_quantity=0)


@pytest.fixture
def cart_with_items(cart_client, product_client: ProductClient) -> dict[str, Any]:
    """A cart holding two distinct products, for checkout tests."""
    cart_client.clear()
    products = product_client.list(in_stock=True, page_size=10, sort="price_asc")
    products.assert_status(200)
    chosen = [item for item in products.body["items"] if item["stock_quantity"] >= 3][:2]
    assert len(chosen) >= 2, "The seeded catalogue needs at least two products with stock"

    for product in chosen:
        cart_client.add_item(product["id"], 1).assert_status(201)

    response = cart_client.get()
    response.assert_status(200)
    return dict(response.body)


@pytest.fixture
def placed_order(order_client, cart_with_items, customer_with_address) -> dict[str, Any]:
    """A successfully paid order belonging to this test's customer."""
    _, address_id = customer_with_address
    return order_client.place_successful_order(address_id=address_id)
