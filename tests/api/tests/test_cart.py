"""Shopping-cart API behaviour.

The cart is where a customer's intent is turned into money, so the properties
asserted here are the ones that would cost real revenue or real trust if they
regressed: prices always resolved server-side, availability checked against the
*combined* quantity rather than the delta, and one cart per customer with no
leakage between accounts.

Every product these tests mutate is created by the test itself. Sharing a
seeded product would make two workers fight over the same stock row and turn a
green suite red for reasons unrelated to the code under test.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import allure
import pytest

from tests.api.clients import AdminClient, CartClient
from tests.database.queries.queries import DatabaseQueries
from tests.fixtures.users import TestUser
from tests.test_data.factories import INVALID_QUANTITIES
from tests.utilities.http import ApiResponse, HttpClient

pytestmark = [pytest.mark.api, allure.epic("Shopping cart")]

TOTALS_KEYS = ("subtotal", "discount_total", "tax", "shipping_fee", "total", "currency")
MONEY_KEYS = ("subtotal", "discount_total", "tax", "shipping_fee", "total")

ProductFactory = Callable[..., dict[str, Any]]


def assert_money(value: Any, *, label: str) -> float:
    """Money must cross the wire as a 2dp JSON number, never as a string.

    A string amount silently breaks every client that does arithmetic on it,
    and the breakage surfaces in the checkout page rather than at the API.
    """
    assert isinstance(value, int | float) and not isinstance(
        value, bool
    ), f"{label} must be a JSON number, got {type(value).__name__} {value!r}"
    numeric = float(value)
    assert round(numeric, 2) == numeric, f"{label} must be rounded to cents, got {value!r}"
    return numeric


def assert_totals_consistent(totals: dict[str, Any]) -> None:
    """total == subtotal - discount + tax + shipping, on whatever the server sent."""
    for key in MONEY_KEYS:
        assert_money(totals[key], label=f"totals.{key}")
    expected = round(
        totals["subtotal"] - totals["discount_total"] + totals["tax"] + totals["shipping_fee"], 2
    )
    assert totals["total"] == expected, (
        f"total {totals['total']} does not equal subtotal - discount + tax + shipping "
        f"({expected}); totals were {totals}"
    )


def assert_error_envelope(response: ApiResponse) -> None:
    """Every failure must use the one documented envelope, with no extra keys."""
    assert isinstance(response.body, dict), f"Expected a JSON error body: {response.raw_text[:200]}"
    assert set(response.body) == {
        "error",
        "message",
        "details",
    }, f"Error envelope must be exactly error/message/details, got {sorted(response.body)}"


def line_for(cart: dict[str, Any], product_id: int) -> dict[str, Any]:
    matches = [item for item in cart["items"] if item["product_id"] == product_id]
    assert len(matches) == 1, (
        f"Expected exactly one cart line for product {product_id}, found {len(matches)}: "
        f"{cart['items']}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Shape and pricing
# ---------------------------------------------------------------------------
@allure.feature("Cart contents")
@allure.story("Empty cart")
@allure.severity(allure.severity_level.CRITICAL)
def test_new_customer_sees_a_priced_empty_cart(cart_client: CartClient) -> None:
    """A first-time visitor must get a renderable cart, not a 404 or a null body.

    The storefront reads totals unconditionally; an empty cart that omitted them
    would blank the mini-cart for every new customer.
    """
    response = cart_client.get()
    response.assert_status(200).assert_faster_than()
    response.assert_has_keys(
        "id", "items", "item_count", "distinct_item_count", "totals", "issues", "is_checkout_ready"
    )

    body = response.body
    assert body["id"] is None, "A cart row must not be created merely by looking at the cart"
    assert body["items"] == []
    assert body["item_count"] == 0
    assert body["distinct_item_count"] == 0
    assert body["issues"] == []
    assert body["is_checkout_ready"] is False, "An empty cart can never be checked out"

    totals = body["totals"]
    assert set(totals) == set(TOTALS_KEYS)
    for key in MONEY_KEYS:
        assert assert_money(totals[key], label=f"totals.{key}") == 0.0
    assert totals["currency"] == "USD"


@allure.feature("Cart contents")
@allure.story("Add item")
@allure.severity(allure.severity_level.BLOCKER)
def test_adding_an_item_returns_201_with_a_server_priced_line(
    cart_client: CartClient, product_factory: ProductFactory
) -> None:
    """The line total is the number the customer is asked to pay per product.

    If line_total ever drifts from unit_price * quantity the shop either gives
    stock away or overcharges, and neither is visible until money has moved.
    """
    product = product_factory(price=25.00, stock_quantity=5)

    response = cart_client.add_item(product["id"], 2)
    response.assert_status(201)

    body = response.body
    line = line_for(body, product["id"])
    assert line["sku"] == product["sku"]
    assert line["name"] == product["name"]
    assert assert_money(line["unit_price"], label="unit_price") == 25.00
    assert line["quantity"] == 2
    assert assert_money(line["line_total"], label="line_total") == 50.00
    assert line["available_stock"] == 5
    assert line["exceeds_stock"] is False
    assert line["is_active"] is True

    assert body["item_count"] == 2
    assert body["distinct_item_count"] == 1
    assert body["is_checkout_ready"] is True

    # $50.00 basket: 8% tax on $50.00 is $4.00, and $50.00 is under the $100.00
    # free-shipping threshold so the $9.99 flat fee applies.
    assert body["totals"] == {
        "subtotal": 50.00,
        "discount_total": 0.00,
        "tax": 4.00,
        "shipping_fee": 9.99,
        "total": 63.99,
        "currency": "USD",
    }


@allure.feature("Cart pricing")
@allure.story("Totals")
@allure.severity(allure.severity_level.BLOCKER)
def test_totals_are_computed_server_side_and_internally_consistent(
    cart_client: CartClient, product_factory: ProductFactory
) -> None:
    """Tax, shipping and total must agree with the lines they were derived from.

    Uneven prices are used deliberately: per-line rounding errors only show up
    when the amounts do not divide cleanly, and a subtotal that disagrees with
    the sum of its lines is the classic symptom.
    """
    cheap = product_factory(price=19.99, stock_quantity=10)
    odd = product_factory(price=7.45, stock_quantity=10)

    cart_client.add_item(cheap["id"], 3).assert_status(201)
    response = cart_client.add_item(odd["id"], 2)
    response.assert_status(201)

    body = response.body
    assert line_for(body, cheap["id"])["line_total"] == 59.97
    assert line_for(body, odd["id"])["line_total"] == 14.90

    # 59.97 + 14.90 = 74.87 subtotal; 8% tax = 5.99 (5.9896 rounded half-up);
    # under $100.00 so shipping is 9.99; total 90.85.
    assert body["totals"]["subtotal"] == 74.87
    assert body["totals"]["tax"] == 5.99
    assert body["totals"]["shipping_fee"] == 9.99
    assert body["totals"]["total"] == 90.85
    assert_totals_consistent(body["totals"])

    summed_lines = round(sum(item["line_total"] for item in body["items"]), 2)
    assert body["totals"]["subtotal"] == summed_lines
    assert body["item_count"] == 5
    assert body["distinct_item_count"] == 2


@allure.feature("Cart pricing")
@allure.story("Free-shipping threshold")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize(
    ("price", "expected_shipping", "expected_total"),
    [
        pytest.param(99.99, 9.99, 117.98, id="one-cent-below-threshold"),
        pytest.param(100.00, 0.00, 108.00, id="exactly-at-threshold"),
        pytest.param(150.00, 0.00, 162.00, id="above-threshold"),
    ],
)
def test_free_shipping_applies_from_the_threshold_upwards(
    cart_client: CartClient,
    product_factory: ProductFactory,
    price: float,
    expected_shipping: float,
    expected_total: float,
) -> None:
    """The boundary is a promise made on the product page, so it must be exact.

    An off-by-one-cent boundary charges shipping to customers who were told it
    was free - a support ticket every time, and a chargeback risk.
    """
    product = product_factory(price=price, stock_quantity=3)
    response = cart_client.add_item(product["id"], 1)
    response.assert_status(201)

    totals = response.body["totals"]
    assert totals["subtotal"] == price
    assert totals["shipping_fee"] == expected_shipping
    assert totals["total"] == expected_total
    assert_totals_consistent(totals)


@allure.feature("Cart pricing")
@allure.story("Price tampering")
@allure.severity(allure.severity_level.BLOCKER)
def test_prices_in_the_request_body_are_ignored(
    cart_client: CartClient, product_factory: ProductFactory
) -> None:
    """A client that posts its own price must not be able to set what it pays.

    This is the cheapest possible attack on an e-commerce API: add the field the
    server would have computed and hope it is trusted. The line must come back
    priced from the catalogue.
    """
    product = product_factory(price=129.99, stock_quantity=4)

    response = cart_client.add_item_raw(
        {
            "product_id": product["id"],
            "quantity": 1,
            "unit_price": 0.01,
            "price": 0,
            "line_total": 0,
            "subtotal": 0,
            "total": 0,
            "discount_total": 999,
        }
    )
    response.assert_status(201)

    line = line_for(response.body, product["id"])
    assert line["unit_price"] == 129.99, "unit_price was taken from the request body"
    assert line["line_total"] == 129.99, "line_total was taken from the request body"
    assert response.body["totals"]["subtotal"] == 129.99
    assert response.body["totals"]["discount_total"] == 0.00, "A client-supplied discount applied"
    # 129.99 + 10.40 tax, shipping free above the $100.00 threshold.
    assert response.body["totals"]["total"] == 140.39


# ---------------------------------------------------------------------------
# Mutating a cart
# ---------------------------------------------------------------------------
@allure.feature("Cart contents")
@allure.story("Add item")
@allure.severity(allure.severity_level.CRITICAL)
def test_adding_the_same_product_twice_merges_into_one_line(
    cart_client: CartClient,
    product_factory: ProductFactory,
    customer: TestUser,
    db: DatabaseQueries,
) -> None:
    """Duplicate lines for one product break quantity edits and stock checks.

    A cart showing "Widget x2" twice lets a customer edit one line and leave the
    other, and makes every availability check look at half the real demand.
    """
    product = product_factory(price=10.00, stock_quantity=20)

    cart_client.add_item(product["id"], 2).assert_status(201)
    response = cart_client.add_item(product["id"], 3)
    response.assert_status(201)

    body = response.body
    assert body["distinct_item_count"] == 1, f"Expected one merged line, got {body['items']}"
    assert body["item_count"] == 5
    line = line_for(body, product["id"])
    assert line["quantity"] == 5
    assert line["line_total"] == 50.00

    persisted = [
        row for row in db.cart_items_for_user(customer.id) if row["product_id"] == product["id"]
    ]
    assert len(persisted) == 1, f"The database holds duplicate cart rows: {persisted}"
    assert persisted[0]["quantity"] == 5


@allure.feature("Inventory rules")
@allure.story("Combined availability")
@allure.severity(allure.severity_level.BLOCKER)
def test_availability_is_checked_against_the_combined_quantity(
    cart_client: CartClient, product_with_stock: ProductFactory
) -> None:
    """Repeated small additions must not accumulate past the stock level.

    Validating only the increment is the classic oversell bug: two "add 2"
    requests each look affordable against 3 units and the shop sells 4.
    """
    product = product_with_stock(3)

    cart_client.add_item(product["id"], 2).assert_status(201)

    response = cart_client.add_item(product["id"], 2)
    response.assert_error("INSUFFICIENT_INVENTORY", 409)
    assert_error_envelope(response)
    assert response.details["available"] == 3
    assert response.details["requested"] == 4, "The check must count the 2 already in the cart"

    # The rejected addition must not have been partially applied.
    after = cart_client.get()
    after.assert_status(200)
    assert line_for(after.body, product["id"])["quantity"] == 2


@allure.feature("Cart contents")
@allure.story("Update quantity")
@allure.severity(allure.severity_level.CRITICAL)
def test_updating_a_line_sets_an_absolute_quantity(
    cart_client: CartClient, product_factory: ProductFactory
) -> None:
    """PATCH sets the quantity, it does not add to it.

    A delta interpretation would make the quantity stepper in the basket double
    every value a customer types.
    """
    product = product_factory(price=10.00, stock_quantity=20)
    cart_client.add_item(product["id"], 2).assert_status(201)

    response = cart_client.update_item(product["id"], 5)
    response.assert_status(200)

    line = line_for(response.body, product["id"])
    assert line["quantity"] == 5, "Quantity was treated as a delta rather than an absolute value"
    assert line["line_total"] == 50.00
    assert response.body["item_count"] == 5


@allure.feature("Inventory rules")
@allure.story("Update quantity")
@allure.severity(allure.severity_level.CRITICAL)
def test_updating_a_line_above_available_stock_is_rejected(
    cart_client: CartClient, product_with_stock: ProductFactory
) -> None:
    """The stock ceiling has to hold on edit, not only on the first add."""
    product = product_with_stock(3)
    cart_client.add_item(product["id"], 1).assert_status(201)

    response = cart_client.update_item(product["id"], 9)
    response.assert_error("INSUFFICIENT_INVENTORY", 409)
    assert response.details["available"] == 3
    assert response.details["requested"] == 9

    unchanged = cart_client.get()
    assert line_for(unchanged.body, product["id"])["quantity"] == 1


@allure.feature("Cart contents")
@allure.story("Remove item")
@allure.severity(allure.severity_level.NORMAL)
def test_removing_a_line_leaves_the_rest_of_the_cart_intact(
    cart_client: CartClient, product_factory: ProductFactory
) -> None:
    """Removing one product must not disturb the others or their pricing."""
    keep = product_factory(price=10.00, stock_quantity=5)
    drop = product_factory(price=20.00, stock_quantity=5)
    cart_client.add_item(keep["id"], 1).assert_status(201)
    cart_client.add_item(drop["id"], 1).assert_status(201)

    response = cart_client.remove_item(drop["id"])
    response.assert_status(200)

    remaining = [item["product_id"] for item in response.body["items"]]
    assert remaining == [keep["id"]]
    assert response.body["distinct_item_count"] == 1
    assert response.body["totals"]["subtotal"] == 10.00


@allure.feature("Cart contents")
@allure.story("Remove item")
@allure.severity(allure.severity_level.NORMAL)
def test_removing_a_product_that_is_not_in_the_cart_is_404(
    cart_client: CartClient, product_factory: ProductFactory
) -> None:
    """A stale basket tab must get a clear 404, not a silent success."""
    product = product_factory(stock_quantity=5)

    response = cart_client.remove_item(product["id"])
    response.assert_error("CART_ITEM_NOT_FOUND", 404)
    assert_error_envelope(response)
    assert response.details["product_id"] == product["id"]


@allure.feature("Cart contents")
@allure.story("Clear cart")
@allure.severity(allure.severity_level.NORMAL)
def test_clearing_the_cart_is_idempotent(
    cart_client: CartClient, product_factory: ProductFactory
) -> None:
    """Emptying the cart is a button users double-click; click two must be safe."""
    product = product_factory(stock_quantity=5)
    cart_client.add_item(product["id"], 2).assert_status(201)

    first = cart_client.clear()
    first.assert_status(200)
    assert first.body["items"] == []
    assert first.body["item_count"] == 0
    assert first.body["totals"]["total"] == 0.00

    second = cart_client.clear()
    second.assert_status(200)
    assert second.body["items"] == []
    assert second.body["totals"] == first.body["totals"]


# ---------------------------------------------------------------------------
# Rejected input
# ---------------------------------------------------------------------------
@allure.feature("Input validation")
@allure.story("Quantity")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.parametrize(
    ("label", "quantity"),
    INVALID_QUANTITIES,
    ids=[label for label, _ in INVALID_QUANTITIES],
)
def test_invalid_quantities_are_rejected(
    cart_client: CartClient, product_factory: ProductFactory, label: str, quantity: Any
) -> None:
    """Anything that is not a positive whole number must be refused at the edge.

    A quantity that slips through is not cosmetic: zero and negative values
    reach the pricing code and can produce negative line totals, and a JSON
    boolean silently becomes one unit the customer never asked for and will be
    charged for.
    """
    product = product_factory(stock_quantity=10)

    response = cart_client.add_item_raw({"product_id": product["id"], "quantity": quantity})

    response.assert_status(422)
    assert_error_envelope(response)
    assert response.error_code in {
        "VALIDATION_ERROR",
        "INVALID_QUANTITY",
    }, f"Unexpected error code for the {label!r} quantity: {response.error_code}"


@allure.feature("Inventory rules")
@allure.story("Add item")
@allure.severity(allure.severity_level.BLOCKER)
def test_adding_more_units_than_are_in_stock_reports_what_is_available(
    cart_client: CartClient, product_with_stock: ProductFactory
) -> None:
    """The customer has to be told how many they *can* have, not just "no".

    `details.available` is what the storefront renders as "Only 3 left"; without
    it the only recovery is trial and error.
    """
    product = product_with_stock(3)

    response = cart_client.add_item(product["id"], 5)
    response.assert_error("INSUFFICIENT_INVENTORY", 409)
    assert_error_envelope(response)
    assert response.details["product_id"] == product["id"]
    assert response.details["requested"] == 5
    assert response.details["available"] == 3


@allure.feature("Inventory rules")
@allure.story("Add item")
@allure.severity(allure.severity_level.CRITICAL)
def test_out_of_stock_product_cannot_be_added(
    cart_client: CartClient, out_of_stock_product: dict[str, Any]
) -> None:
    """Selling a zero-stock product creates an order that can never be fulfilled."""
    response = cart_client.add_item(out_of_stock_product["id"], 1)
    response.assert_error("INSUFFICIENT_INVENTORY", 409)
    assert response.details["available"] == 0


@allure.feature("Input validation")
@allure.story("Add item")
@allure.severity(allure.severity_level.NORMAL)
def test_adding_a_product_that_does_not_exist_is_404(cart_client: CartClient) -> None:
    """A guessed or stale product id must not create a phantom cart line."""
    response = cart_client.add_item(99_999_999, 1)
    response.assert_error("PRODUCT_NOT_FOUND", 404)
    assert_error_envelope(response)


@allure.feature("Inventory rules")
@allure.story("Deactivated products")
@allure.severity(allure.severity_level.CRITICAL)
def test_adding_a_deactivated_product_is_rejected_as_unavailable(
    cart_client: CartClient, product_factory: ProductFactory, admin_client: AdminClient
) -> None:
    """A withdrawn product must be refused with the code the contract promises.

    PRODUCT_UNAVAILABLE(409) tells the storefront "this existed but you can no
    longer buy it", which is a different message and a different recovery path
    from "this id is not a product". Collapsing the two into 404 makes a
    withdrawn item indistinguishable from a broken link, and leaves customers
    who bookmarked it with no explanation.
    """
    product = product_factory(stock_quantity=5)
    admin_client.deactivate_product(product["id"]).assert_status_in(200, 204)

    response = cart_client.add_item(product["id"], 1)
    response.assert_error("PRODUCT_UNAVAILABLE", 409)
    assert_error_envelope(response)


@allure.feature("Inventory rules")
@allure.story("Deactivated products")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_product_deactivated_after_being_added_blocks_checkout(
    cart_client: CartClient, product_factory: ProductFactory, admin_client: AdminClient
) -> None:
    """A cart is re-priced on every read, so withdrawal must surface immediately.

    Without this the customer reaches the payment step before discovering the
    item cannot be sold - the most expensive possible moment to find out.
    """
    product = product_factory(price=30.00, stock_quantity=5)
    cart_client.add_item(product["id"], 1).assert_status(201)

    admin_client.deactivate_product(product["id"]).assert_status_in(200, 204)

    response = cart_client.get()
    response.assert_status(200)
    line = line_for(response.body, product["id"])
    assert line["is_active"] is False
    assert response.body["issues"], "A withdrawn product must be reported in issues"
    assert response.body["is_checkout_ready"] is False


# ---------------------------------------------------------------------------
# Access control and isolation
# ---------------------------------------------------------------------------
@allure.feature("Access control")
@allure.story("Authentication")
@allure.severity(allure.severity_level.BLOCKER)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        pytest.param("GET", "/cart", None, id="read-cart"),
        pytest.param("POST", "/cart/items", {"product_id": 1, "quantity": 1}, id="add-item"),
        pytest.param("PATCH", "/cart/items/1", {"quantity": 1}, id="update-item"),
        pytest.param("DELETE", "/cart/items/1", None, id="remove-item"),
        pytest.param("DELETE", "/cart", None, id="clear-cart"),
    ],
)
def test_cart_endpoints_reject_anonymous_callers(
    http: HttpClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """A cart belongs to an account; there is no anonymous cart to reach.

    An endpoint that fell back to a shared or implicit cart would let one
    visitor read or empty another's basket.
    """
    response = http.request(method, path, json_body=body, authenticate=False)
    response.assert_error("INVALID_TOKEN", 401)
    assert_error_envelope(response)


@allure.feature("Access control")
@allure.story("Per-user isolation")
@allure.severity(allure.severity_level.BLOCKER)
def test_each_customer_has_an_independent_cart(
    cart_client: CartClient,
    product_factory: ProductFactory,
    second_customer: TestUser,
) -> None:
    """Cross-account cart leakage would expose one customer's basket to another.

    Both customers act over the same connection here, which is exactly the
    condition under which a cart keyed on anything but the authenticated user
    would leak.
    """
    mine = product_factory(price=10.00, stock_quantity=5)
    theirs = product_factory(price=20.00, stock_quantity=5)

    cart_client.add_item(mine["id"], 1).assert_status(201)
    cart_client.add_item(theirs["id"], 2, token=second_customer.token).assert_status(201)

    my_cart = cart_client.get()
    my_cart.assert_status(200)
    assert [item["product_id"] for item in my_cart.body["items"]] == [mine["id"]]
    assert my_cart.body["totals"]["subtotal"] == 10.00

    their_cart = cart_client.get(token=second_customer.token)
    their_cart.assert_status(200)
    assert [item["product_id"] for item in their_cart.body["items"]] == [theirs["id"]]
    assert their_cart.body["totals"]["subtotal"] == 40.00

    # Emptying one cart must leave the other untouched.
    cart_client.clear().assert_status(200)
    still_there = cart_client.get(token=second_customer.token)
    assert still_there.body["item_count"] == 2
