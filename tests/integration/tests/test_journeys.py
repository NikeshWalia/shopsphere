"""Integration tests: complete business journeys across API, database and provider.

Each test is one coherent story, asserted at *both* boundaries — what the API
returned and what was actually persisted. A response can be right while the
database is wrong; only checking both catches that.
"""

from __future__ import annotations

from typing import Any

import allure
import pytest

from tests.api.clients import (
    CARD_APPROVED,
    CARD_DECLINED_FUNDS,
    CARD_TIMEOUT,
    AdminClient,
    AuthClient,
    CartClient,
    OrderClient,
    ProductClient,
)
from tests.database.queries.queries import DatabaseQueries
from tests.test_data.factories import DEFAULT_PASSWORD, unique_email
from tests.utilities.http import HttpClient

pytestmark = [allure.epic("Business journeys")]


@allure.feature("Journey 1: browse to confirmed order")
@allure.severity(allure.severity_level.BLOCKER)
def test_register_search_buy_and_confirm(
    auth_client: AuthClient,
    product_client: ProductClient,
    admin_client: AdminClient,
    db: DatabaseQueries,
    http: HttpClient,
) -> None:
    """The complete happy path, from having no account to owning an order.

    Deliberately built from scratch rather than from fixtures: this is the one
    test that proves a brand-new customer can get all the way through without
    anything having been prepared for them.
    """
    with allure.step("Register a brand-new customer"):
        email = unique_email("journey1")
        registered = auth_client.register(
            email=email, password=DEFAULT_PASSWORD, full_name="Journey One"
        )
        registered.assert_status(201)
        token = registered.body["access_token"]
        user_id = registered.body["user"]["id"]

        stored = db.user_by_email(email)
        assert stored is not None and stored["role"] == "customer"

    with allure.step("Sign in with those credentials"):
        auth_client.login(email, DEFAULT_PASSWORD).assert_status(200)

    with allure.step("Admin publishes a product the customer can find"):
        product = admin_client.create_product_with_stock(8, name="Journey Widget", price=45.00)

    with allure.step("Search the catalogue and open the product"):
        found = product_client.search("Journey Widget")
        found.assert_status(200)
        assert any(item["id"] == product["id"] for item in found.body["items"])

        detail = product_client.get(product["id"]).assert_status(200)
        assert detail.body["stock_quantity"] == 8

    with allure.step("Add two units to the cart"):
        cart = HttpClient(http.base_url, token=token)
        added = cart.post("/cart/items", json_body={"product_id": product["id"], "quantity": 2})
        added.assert_status(201)
        assert added.body["totals"]["subtotal"] == 90.00

    with allure.step("Add a shipping address"):
        address = cart.post(
            "/addresses",
            json_body={
                "full_name": "Journey One",
                "line1": "1 Journey Way",
                "city": "Austin",
                "state": "TX",
                "postal_code": "73301",
                "country": "US",
            },
        )
        address.assert_status(201)

    with allure.step("Preview the totals"):
        quote = cart.post("/checkout/quote", json_body={"promo_code": None}).assert_status(200)
        assert quote.body["subtotal"] == 90.00
        assert quote.body["tax"] == 7.20
        assert quote.body["shipping_fee"] == 9.99
        assert quote.body["total"] == 107.19

    with allure.step("Place the order"):
        import uuid

        placed = cart.post(
            "/orders",
            json_body={
                "address_id": address.body["id"],
                "payment": {
                    "card_number": CARD_APPROVED,
                    "card_holder": "Journey One",
                    "expiry_month": 12,
                    "expiry_year": 2032,
                    "cvv": "123",
                },
            },
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        placed.assert_status(201)
        order = placed.body
        assert order["status"] == "confirmed"
        assert order["payment_status"] == "paid"
        # The charged amount must equal the previewed amount, to the cent.
        assert order["total"] == quote.body["total"]

    with allure.step("Verify what was actually written to the database"):
        row = db.order_by_id(order["id"])
        assert row is not None
        assert row["user_id"] == user_id
        assert row["status"] == "confirmed"
        assert row["payment_status"] == "paid"
        assert float(row["total"]) == 107.19

        lines = db.order_items(order["id"])
        assert len(lines) == 1
        assert lines[0]["quantity"] == 2
        assert float(lines[0]["unit_price"]) == 45.00

        payments = db.payments_for_order(order["id"])
        assert len(payments) == 1 and payments[0]["status"] == "paid"
        assert payments[0]["card_last4"] == CARD_APPROVED[-4:]

        assert db.stock_for(product["id"]) == 6, "Stock was not decremented by exactly 2"
        assert db.cart_items_for_user(user_id) == [], "The cart was not cleared"


@allure.feature("Journey 2: a cart edited before checkout")
def test_totals_track_every_cart_change(
    cart_client: CartClient,
    order_client: OrderClient,
    admin_client: AdminClient,
    customer_with_address,
    db: DatabaseQueries,
) -> None:
    """Totals must be recomputed at every step, not cached from the first read.

    A stale total is how a customer ends up charged for a quantity they changed.
    """
    _, address_id = customer_with_address
    cheap = admin_client.create_product_with_stock(20, price=10.00, name="Cheap Item")
    pricey = admin_client.create_product_with_stock(20, price=60.00, name="Pricey Item")

    with allure.step("Add two products"):
        cart_client.add_item(cheap["id"], 1).assert_status(201)
        state = cart_client.add_item(pricey["id"], 1).assert_status(201)
        assert state.body["totals"]["subtotal"] == 70.00

    with allure.step("Increase the cheaper line to 3"):
        state = cart_client.update_item(cheap["id"], 3).assert_status(200)
        assert state.body["totals"]["subtotal"] == 90.00
        assert state.body["totals"]["shipping_fee"] == 9.99  # still under 100

    with allure.step("Increase again, crossing the free-shipping threshold"):
        state = cart_client.update_item(cheap["id"], 5).assert_status(200)
        assert state.body["totals"]["subtotal"] == 110.00
        assert state.body["totals"]["shipping_fee"] == 0.0

    with allure.step("Remove the expensive line"):
        state = cart_client.remove_item(pricey["id"]).assert_status(200)
        assert state.body["totals"]["subtotal"] == 50.00
        assert state.body["totals"]["shipping_fee"] == 9.99, "Shipping was not reinstated"

    with allure.step("Check out and confirm the order matches the final cart"):
        final = cart_client.get().assert_status(200).body
        order = order_client.checkout(address_id=address_id).assert_status(201).body

        assert order["subtotal"] == final["totals"]["subtotal"]
        assert order["total"] == final["totals"]["total"]
        assert len(order["items"]) == 1
        assert order["items"][0]["quantity"] == 5

        assert float(db.sum_of_order_items(order["id"])) == order["subtotal"]


@allure.feature("Journey 3: order history")
def test_an_order_appears_in_history_and_opens_correctly(
    order_client: OrderClient,
    cart_client: CartClient,
    admin_client: AdminClient,
    customer_with_address,
) -> None:
    _, address_id = customer_with_address
    product = admin_client.create_product_with_stock(10, price=33.00)

    with allure.step("Place an order"):
        cart_client.add_item(product["id"], 2).assert_status(201)
        placed = order_client.checkout(address_id=address_id).assert_status(201).body

    with allure.step("It appears in the customer's history"):
        history = order_client.list().assert_status(200)
        row = next((r for r in history.body["items"] if r["id"] == placed["id"]), None)
        assert row is not None, "The order is missing from history"
        assert row["order_number"] == placed["order_number"]
        assert row["total"] == placed["total"]
        assert row["item_count"] == 2

    with allure.step("Opening it shows the same order"):
        detail = order_client.get(placed["id"]).assert_status(200).body
        assert detail["order_number"] == placed["order_number"]
        assert detail["total"] == placed["total"]
        assert detail["items"][0]["sku"] == product["sku"]
        assert detail["shipping_address"]["city"]


@allure.feature("Journey 4: payment failure")
@allure.severity(allure.severity_level.BLOCKER)
def test_a_failed_payment_leaves_nothing_marked_paid(
    order_client: OrderClient,
    cart_client: CartClient,
    admin_client: AdminClient,
    customer_with_address,
    db: DatabaseQueries,
) -> None:
    """Business rule 5, asserted in the API *and* the database.

    The single most damaging failure this application could have is an order
    recorded as paid when no money moved. Checking only the HTTP response would
    miss a bug that writes the wrong row.
    """
    _, address_id = customer_with_address
    product = admin_client.create_product_with_stock(6, price=50.00)

    with allure.step("Fill a cart"):
        cart_client.add_item(product["id"], 2).assert_status(201)
        assert db.stock_for(product["id"]) == 6

    with allure.step("Pay with a card that will be declined"):
        response = order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_FUNDS)
        response.assert_error("PAYMENT_DECLINED", 402)
        order_id = response.details["order_id"]

    with allure.step("The database shows a cancelled, unpaid order"):
        row = db.order_by_id(order_id)
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["payment_status"] == "failed", "A declined charge produced a paid order"

        payments = db.payments_for_order(order_id)
        assert payments and payments[-1]["status"] == "failed"
        assert payments[-1]["failure_code"] == "insufficient_funds"
        assert all(payment["status"] != "paid" for payment in payments)

    with allure.step("Stock was returned"):
        assert db.stock_for(product["id"]) == 6, "A declined payment did not restore stock"

    with allure.step("No paid order anywhere lacks a successful payment"):
        assert db.paid_orders_without_payment() == []


@allure.feature("Journey 5: insufficient inventory")
@allure.severity(allure.severity_level.CRITICAL)
def test_stock_disappearing_mid_session_blocks_checkout(
    cart_client: CartClient,
    order_client: OrderClient,
    admin_client: AdminClient,
    customer_with_address,
    db: DatabaseQueries,
) -> None:
    """The gap between filling a cart and paying is where overselling lives.

    The customer's cart is valid when built and invalid by the time they pay.
    The cart must report it, and checkout must refuse.
    """
    _, address_id = customer_with_address
    product = admin_client.create_product_with_stock(2, price=25.00)

    with allure.step("Add both available units"):
        cart_client.add_item(product["id"], 2).assert_status(201)
        assert cart_client.get().body["is_checkout_ready"] is True

    with allure.step("An admin reduces stock to one while the cart is open"):
        admin_client.set_stock(product["id"], 1).assert_status(200)

    with allure.step("The cart now reports the shortfall and blocks checkout"):
        cart = cart_client.get().assert_status(200).body
        assert cart["is_checkout_ready"] is False
        assert cart["issues"], "The cart did not report the shortfall"
        assert cart["items"][0]["exceeds_stock"] is True
        assert cart["items"][0]["available_stock"] == 1

    with allure.step("Checkout is refused"):
        response = order_client.checkout(address_id=address_id)
        response.assert_status(409)

    with allure.step("Nothing was sold and stock is untouched"):
        assert db.stock_for(product["id"]) == 1
        assert db.negative_stock_rows() == []

    with allure.step("Reducing the cart to what is available lets it through"):
        cart_client.update_item(product["id"], 1).assert_status(200)
        order = order_client.checkout(address_id=address_id).assert_status(201).body
        assert order["payment_status"] == "paid"
        assert db.stock_for(product["id"]) == 0


@allure.feature("Journey 6: admin restock")
def test_an_admin_restock_is_immediately_visible_to_customers(
    admin_client: AdminClient,
    product_client: ProductClient,
    cart_client: CartClient,
    db: DatabaseQueries,
) -> None:
    """No caching between the admin console and the storefront.

    A customer looking at a stale "out of stock" is a lost sale; one looking at
    a stale "in stock" is an oversell.
    """
    product = admin_client.create_product_with_stock(0, price=15.00)

    with allure.step("The product starts out of stock and cannot be added"):
        assert product_client.get(product["id"]).body["in_stock"] is False
        cart_client.add_item(product["id"], 1).assert_error("INSUFFICIENT_INVENTORY", 409)

    with allure.step("An admin restocks it"):
        updated = admin_client.set_stock(product["id"], 7).assert_status(200)
        assert updated.body["quantity"] == 7
        assert db.stock_for(product["id"]) == 7

    with allure.step("The customer sees the new availability at once"):
        detail = product_client.get(product["id"]).assert_status(200).body
        assert detail["in_stock"] is True
        assert detail["stock_quantity"] == 7

    with allure.step("...and can buy up to that quantity, but no more"):
        cart_client.add_item(product["id"], 7).assert_status(201)
        cart_client.add_item(product["id"], 1).assert_error("INSUFFICIENT_INVENTORY", 409)


@allure.feature("Journey 7: privilege boundaries")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_customer_is_refused_everywhere_an_admin_is_allowed(
    customer_http: HttpClient, admin_http: HttpClient, http: HttpClient
) -> None:
    """The same requests, three identities, three different outcomes."""
    endpoints = [
        ("GET", "/admin/users"),
        ("GET", "/admin/orders"),
        ("GET", "/admin/inventory"),
        ("GET", "/admin/stats"),
    ]

    for method, path in endpoints:
        with allure.step(f"{method} {path}"):
            anonymous = http.request(method, path, authenticate=False)
            customer = customer_http.request(method, path)
            admin = admin_http.request(method, path)

            assert anonymous.status_code == 401, f"{path}: anonymous got {anonymous.status_code}"
            assert customer.status_code == 403, f"{path}: customer got {customer.status_code}"
            assert admin.status_code == 200, f"{path}: admin got {admin.status_code}"


@allure.feature("Journey 8: payment timeout")
@allure.severity(allure.severity_level.CRITICAL)
@pytest.mark.slow
def test_a_provider_timeout_leaves_the_order_pending_and_stock_reserved(
    order_client: OrderClient,
    cart_client: CartClient,
    admin_client: AdminClient,
    customer_with_address,
    db: DatabaseQueries,
) -> None:
    """The case where the correct answer is "we do not know".

    A declined charge is known not to have taken money, so stock is returned. A
    timeout carries no such knowledge: the charge may well have succeeded.
    Marking it paid could ship goods for free; marking it failed and releasing
    stock could take money for a cancelled order. So the order stays pending
    with stock still held, and a human resolves it.

    Marked slow because the eight-second wait is a genuine socket timeout - the
    behaviour under test, not an arbitrary sleep.
    """
    _, address_id = customer_with_address
    product = admin_client.create_product_with_stock(5, price=30.00)

    with allure.step("Fill a cart"):
        cart_client.add_item(product["id"], 2).assert_status(201)
        assert db.stock_for(product["id"]) == 5

    with allure.step("Pay with the card that makes the provider stall"):
        response = order_client.checkout(address_id=address_id, card_number=CARD_TIMEOUT)
        response.assert_error("PAYMENT_PROVIDER_TIMEOUT", 504)
        order_id = response.details["order_id"]
        assert response.details["order_status"] == "pending"
        assert response.details["payment_status"] == "pending"

    with allure.step("The order is pending, never paid"):
        row = db.order_by_id(order_id)
        assert row is not None
        assert row["status"] == "pending"
        assert row["payment_status"] == "pending", "A timeout was resolved to a definite outcome"

        payments = db.payments_for_order(order_id)
        assert payments and payments[-1]["status"] == "pending"
        assert all(payment["status"] != "paid" for payment in payments)

    with allure.step("Stock stays reserved, because the order may yet be paid"):
        assert (
            db.stock_for(product["id"]) == 3
        ), "Stock was released for an order whose payment outcome is unknown"

    with allure.step("An admin can resolve it by cancelling, which returns the stock"):
        admin_client.set_order_status(order_id, "cancelled").assert_status(200)
        assert db.stock_for(product["id"]) == 5


@allure.feature("Journey 9: cancellation")
def test_cancelling_a_paid_order_refunds_and_restocks(
    order_client: OrderClient,
    cart_client: CartClient,
    admin_client: AdminClient,
    customer_with_address,
    db: DatabaseQueries,
) -> None:
    _, address_id = customer_with_address
    product = admin_client.create_product_with_stock(10, price=40.00)

    with allure.step("Buy three units"):
        cart_client.add_item(product["id"], 3).assert_status(201)
        order = order_client.checkout(address_id=address_id).assert_status(201).body
        assert db.stock_for(product["id"]) == 7

    with allure.step("Cancel the order"):
        cancelled = order_client.cancel(order["id"], "No longer needed").assert_status(200).body
        assert cancelled["status"] == "cancelled"
        assert cancelled["payment_status"] == "refunded"

    with allure.step("The database records the refund and the restock"):
        row = db.order_by_id(order["id"])
        assert row is not None and row["payment_status"] == "refunded"

        payments = db.payments_for_order(order["id"])
        assert any(
            payment["status"] == "refunded" for payment in payments
        ), "No refund row was written - the audit trail is incomplete"
        # The original successful charge is preserved, not overwritten.
        assert any(payment["status"] == "paid" for payment in payments)

        assert db.stock_for(product["id"]) == 10


@allure.feature("Journey 10: admin order lifecycle")
def test_an_order_moves_through_its_lifecycle(
    order_client: OrderClient,
    cart_client: CartClient,
    admin_client: AdminClient,
    customer_with_address,
    db: DatabaseQueries,
) -> None:
    """Each transition must be permitted only from the right previous state."""
    _, address_id = customer_with_address
    product = admin_client.create_product_with_stock(5, price=20.00)
    cart_client.add_item(product["id"], 1).assert_status(201)
    order = order_client.checkout(address_id=address_id).assert_status(201).body

    with allure.step("Skipping ahead is refused"):
        refused = admin_client.set_order_status(order["id"], "delivered")
        refused.assert_error("INVALID_ORDER_STATE", 409)
        assert "allowed" in refused.details, "The error does not say what IS allowed"

    for target in ("processing", "shipped", "delivered"):
        with allure.step(f"Advance to {target}"):
            updated = admin_client.set_order_status(order["id"], target).assert_status(200)
            assert updated.body["status"] == target
            assert db.order_by_id(order["id"])["status"] == target

    with allure.step("A delivered order is terminal"):
        admin_client.set_order_status(order["id"], "shipped").assert_status(409)
        order_client.cancel(order["id"]).assert_error("INVALID_ORDER_STATE", 409)


@allure.feature("Journey 11: data isolation between customers")
@allure.severity(allure.severity_level.CRITICAL)
def test_two_customers_shopping_at_once_never_see_each_other(
    make_customer,
    admin_client: AdminClient,
    http: HttpClient,
    db: DatabaseQueries,
) -> None:
    """Interleaved sessions must stay completely separate.

    Run as one test rather than two so the operations genuinely interleave -
    the failure mode being guarded against is state leaking between concurrent
    sessions, which sequential tests would never expose.
    """
    alice = make_customer()
    bob = make_customer()
    product = admin_client.create_product_with_stock(20, price=25.00)

    alice_http = HttpClient(http.base_url, token=alice.token)
    bob_http = HttpClient(http.base_url, token=bob.token)

    with allure.step("Both fill carts, interleaved"):
        alice_http.post("/cart/items", json_body={"product_id": product["id"], "quantity": 2})
        bob_http.post("/cart/items", json_body={"product_id": product["id"], "quantity": 5})
        alice_http.patch(f"/cart/items/{product['id']}", json_body={"quantity": 3})

    with allure.step("Each sees only their own cart"):
        assert alice_http.get("/cart").body["item_count"] == 3
        assert bob_http.get("/cart").body["item_count"] == 5
        assert db.cart_items_for_user(alice.id)[0]["quantity"] == 3
        assert db.cart_items_for_user(bob.id)[0]["quantity"] == 5

    with allure.step("Both check out"):
        import uuid

        payment: dict[str, Any] = {
            "card_number": CARD_APPROVED,
            "card_holder": "Test",
            "expiry_month": 12,
            "expiry_year": 2032,
            "cvv": "123",
        }
        for client, person in ((alice_http, alice), (bob_http, bob)):
            address = client.post(
                "/addresses",
                json_body={
                    "full_name": person.full_name,
                    "line1": "1 Way",
                    "city": "Austin",
                    "state": "TX",
                    "postal_code": "73301",
                    "country": "US",
                },
            )
            address.assert_status(201)
            client.post(
                "/orders",
                json_body={"address_id": address.body["id"], "payment": payment},
                headers={"Idempotency-Key": uuid.uuid4().hex},
            ).assert_status(201)

    with allure.step("Stock reflects both orders exactly once"):
        assert db.stock_for(product["id"]) == 12  # 20 - 3 - 5

    with allure.step("Neither history contains the other's order"):
        alice_orders = {row["id"] for row in alice_http.get("/orders").body["items"]}
        bob_orders = {row["id"] for row in bob_http.get("/orders").body["items"]}
        assert not (alice_orders & bob_orders), "Order history leaked between customers"

    alice_http.close()
    bob_http.close()
