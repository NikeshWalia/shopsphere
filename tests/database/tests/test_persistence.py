"""What the API actually writes to the database.

The API is the only way state is created here - nothing is inserted behind the
application's back, because a row manufactured by a test would not have passed
through password hashing, stock locking or total calculation, and asserting on
it would prove nothing about the real code path.

What these tests add over the HTTP-level suites is everything the response body
cannot show. A checkout response says the order is paid; only the database can
say whether inventory was decremented by exactly the quantity ordered, whether
a payment row was written with the right last-four digits, whether a declined
charge really returned the stock it reserved, and whether a replayed
Idempotency-Key created a second order that nobody will ever see but that
accounting will eventually find.

Every test creates the product it mutates, so stock assertions are exact
("7 became 4") rather than relative, and two workers running in parallel cannot
interfere with each other.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import uuid4

import allure
import pytest

from tests.api.clients import (
    CARD_APPROVED,
    CARD_DECLINED_FUNDS,
    AddressClient,
    AdminClient,
    AuthClient,
    CartClient,
    OrderClient,
)
from tests.database.queries.queries import DatabaseQueries

# Imported under another name: pytest would try to collect anything called
# ``Test*`` at module level as a test class.
from tests.fixtures.users import TestUser as Customer
from tests.test_data.factories import unique_email, unique_suffix

pytestmark = [pytest.mark.database, allure.epic("Database")]

# Every monetary expectation below is written out as the exact amount the shop
# must store, not recomputed from the tax and shipping rules. Recomputing them
# would make the test agree with the application by construction and pass even
# if the pricing rule itself were wrong.
UNIT_PRICE = Decimal("49.99")

BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def _unique_password() -> str:
    """A password no other row in the database can contain by chance.

    Uniqueness is what makes the "stored nowhere in plaintext" scan meaningful:
    a shared password could match a row this test did not create.
    """
    return f"Pw{unique_suffix()}Aa1!"


@allure.feature("Persistence")
@allure.story("Registration")
class TestRegistrationPersistence:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_password_is_stored_as_a_bcrypt_hash_and_never_in_plaintext(
        self, auth_client: AuthClient, db: DatabaseQueries
    ) -> None:
        """A leaked database must not be a leaked password list.

        Customers reuse passwords across sites, so plaintext (or a fast digest)
        in ``users`` turns one breach of this shop into account takeovers
        everywhere else. The scan covers every column of the row, not just
        ``password_hash``, because the realistic regression is a debug field or
        an audit column quietly capturing the submitted value.
        """
        email = unique_email("persist")
        password = _unique_password()
        auth_client.register(
            email=email, password=password, full_name="Persistence Probe"
        ).assert_status(201)

        row = db.user_by_email(email)
        assert row is not None, f"No users row was written for {email}"
        stored_hash = str(row["password_hash"])
        assert stored_hash.startswith(
            BCRYPT_PREFIXES
        ), f"password_hash is not a bcrypt digest: {stored_hash[:12]!r}"
        # A bcrypt digest is 60 characters; a truncated column would silently
        # weaken every future verification.
        assert (
            len(stored_hash) == 60
        ), f"bcrypt digest is {len(stored_hash)} characters, expected 60"

        leaks = db.users_containing_text(password)
        assert leaks == [], f"The plaintext password appears in users rows {leaks}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_email_is_stored_lower_cased(
        self, auth_client: AuthClient, db: DatabaseQueries
    ) -> None:
        """Uniqueness is enforced by a plain UNIQUE index on ``email``.

        If the stored value kept its original casing, ``Alice@x.test`` and
        ``alice@x.test`` would be two accounts for one mailbox - and login,
        which looks up the normalised form, would find neither reliably.
        """
        mixed_case = unique_email("MiXeDcAsE")
        assert mixed_case != mixed_case.lower(), "The fixture must produce a mixed-case address"
        auth_client.register(
            email=mixed_case, password=_unique_password(), full_name="Casing Probe"
        ).assert_status(201)

        # The query looks the address up by its lower-cased form, so finding
        # the row at all is the proof that it was normalised on write.
        row = db.user_by_email(mixed_case)
        assert row is not None, f"No row stored under the normalised form of {mixed_case}"
        assert row["email"] == mixed_case.lower()


@allure.feature("Persistence")
@allure.story("Cart")
class TestCartPersistence:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_adding_to_cart_writes_a_cart_and_a_cart_item_row(
        self,
        customer: Customer,
        cart_client: CartClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """The cart has to outlive the request that created it.

        A cart held only in the session would vanish on the next deploy or
        whenever the customer switched device, and they would arrive at
        checkout with an empty basket. These rows are what make it durable.
        """
        product = product_with_stock(10)
        cart_client.add_item(product["id"], 4).assert_status(201)

        cart = db.cart_for_user(customer.id)
        assert cart is not None, "No carts row was created for the customer"

        items = db.cart_items_for_user(customer.id)
        assert len(items) == 1, f"Expected one cart_items row, got {items}"
        assert items[0]["product_id"] == product["id"]
        assert items[0]["quantity"] == 4

    @allure.severity(allure.severity_level.NORMAL)
    def test_re_adding_the_same_product_updates_the_existing_row(
        self,
        customer: Customer,
        cart_client: CartClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """``cart_items`` is unique on (cart_id, product_id).

        Writing a second row instead of merging would either violate that
        constraint - a 500 for the customer - or, without it, show the same
        product twice in the basket and charge for both lines.
        """
        product = product_with_stock(10)
        cart_client.add_item(product["id"], 2).assert_status(201)
        cart_client.add_item(product["id"], 3).assert_status(201)

        items = db.cart_items_for_user(customer.id)
        assert len(items) == 1, f"Expected the lines to merge into one row, got {items}"
        assert items[0]["quantity"] == 5


@allure.feature("Persistence")
@allure.story("Checkout")
class TestCheckoutPersistence:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_paid_checkout_writes_order_items_payment_and_decrements_stock(
        self,
        customer_with_address: tuple[Customer, int],
        cart_client: CartClient,
        order_client: OrderClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """Checkout is the one place where money, stock and history all change
        at once, and all four writes have to land together.

        If the order row is written but stock is not decremented, the shop
        oversells. If stock moves but no payment row is written, revenue
        reconciliation silently loses a transaction. Asserting stock before and
        after pins the decrement to the exact quantity ordered rather than
        merely "it went down".
        """
        _, address_id = customer_with_address
        product = product_with_stock(7, price=float(UNIT_PRICE))
        assert db.stock_for(product["id"]) == 7, "Fixture did not create the expected stock level"

        cart_client.add_item(product["id"], 3).assert_status(201)
        response = order_client.checkout(address_id=address_id, card_number=CARD_APPROVED)
        response.assert_status(201)
        order_id = int(response.body["id"])

        order = db.order_by_id(order_id)
        assert order is not None, "Checkout returned 201 but wrote no orders row"
        assert order["status"] == "confirmed"
        assert order["payment_status"] == "paid"
        # 3 x 49.99 = 149.97; over the 100.00 threshold, so shipping is free.
        assert order["subtotal"] == Decimal("149.97")
        assert order["discount_total"] == Decimal("0.00")
        assert order["tax"] == Decimal("12.00")
        assert order["shipping_fee"] == Decimal("0.00")
        assert order["total"] == Decimal("161.97")
        assert order["currency"] == "USD"

        items = db.order_items(order_id)
        assert len(items) == 1, f"Expected one order_items row, got {items}"
        assert items[0]["product_id"] == product["id"]
        assert items[0]["sku"] == product["sku"]
        assert items[0]["quantity"] == 3
        assert items[0]["unit_price"] == UNIT_PRICE
        assert items[0]["line_total"] == Decimal("149.97")
        # The invoice must add up to what the order says it does.
        assert db.sum_of_order_items(order_id) == order["subtotal"]

        payments = db.payments_for_order(order_id)
        assert len(payments) == 1, f"Expected exactly one payment attempt, got {payments}"
        assert payments[0]["status"] == "paid"
        assert payments[0]["amount"] == order["total"]
        assert payments[0]["card_last4"] == CARD_APPROVED[-4:]
        assert payments[0]["failure_code"] is None
        assert payments[0][
            "provider_reference"
        ], "No provider reference stored to reconcile against"

        assert db.stock_for(product["id"]) == 4, "Stock was not decremented by exactly 3"

    @allure.severity(allure.severity_level.BLOCKER)
    def test_declined_payment_fails_the_payment_row_and_restores_stock(
        self,
        customer_with_address: tuple[Customer, int],
        cart_client: CartClient,
        order_client: OrderClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """Stock is reserved *before* the card is charged, so a decline has to
        give it back.

        Without the unwind, every declined checkout would permanently remove
        units from the shop - a customer whose card fails three times could
        take the last item off sale for everybody. The failed payment row also
        has to survive: support cannot explain a decline they cannot see.
        """
        _, address_id = customer_with_address
        product = product_with_stock(5)
        cart_client.add_item(product["id"], 2).assert_status(201)

        response = order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_FUNDS)
        response.assert_error("PAYMENT_DECLINED", 402)
        order_id = int(response.details["order_id"])

        order = db.order_by_id(order_id)
        assert order is not None, "The declined order was not persisted at all"
        assert order["status"] == "cancelled"
        assert order["payment_status"] == "failed"
        assert order["cancelled_reason"], "No reason recorded for the cancellation"

        payments = db.payments_for_order(order_id)
        assert len(payments) == 1, f"Expected one failed payment attempt, got {payments}"
        assert payments[0]["status"] == "failed"
        assert payments[0]["failure_code"], "A failed payment must record why it failed"

        assert db.stock_for(product["id"]) == 5, "Reserved stock was not returned after the decline"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_cancelling_a_paid_order_restores_stock_and_records_a_refund(
        self,
        customer_with_address: tuple[Customer, int],
        cart_client: CartClient,
        order_client: OrderClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """A cancellation moves money and inventory in opposite directions.

        The units have to go back on sale, and the refund has to exist as its
        own payment row - overwriting the original ``paid`` row would destroy
        the audit trail showing the customer was charged before being refunded,
        which is exactly what a chargeback dispute turns on.
        """
        _, address_id = customer_with_address
        product = product_with_stock(6)
        cart_client.add_item(product["id"], 2).assert_status(201)

        order = order_client.place_successful_order(address_id=address_id)
        order_id = int(order["id"])
        assert db.stock_for(product["id"]) == 4

        order_client.cancel(order_id, reason="Changed my mind").assert_status(200)

        assert db.stock_for(product["id"]) == 6, "Cancelling did not return the stock"
        row = db.order_by_id(order_id)
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["payment_status"] == "refunded"

        statuses = [payment["status"] for payment in db.payments_for_order(order_id)]
        assert statuses == [
            "paid",
            "refunded",
        ], f"Expected the charge and its refund to both be kept, got {statuses}"

    @allure.severity(allure.severity_level.BLOCKER)
    def test_idempotent_replay_leaves_exactly_one_order_row(
        self,
        customer_with_address: tuple[Customer, int],
        cart_client: CartClient,
        order_client: OrderClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """A double-clicked Pay button must not become a double charge.

        The response body alone cannot prove this: an implementation that
        created a second order and returned the first would look identical over
        HTTP. Only the row count for the key, the payment count and the stock
        level show whether the replay was genuinely a no-op.
        """
        _, address_id = customer_with_address
        product = product_with_stock(8)
        cart_client.add_item(product["id"], 2).assert_status(201)

        key = uuid4().hex
        first = order_client.checkout(address_id=address_id, idempotency_key=key)
        first.assert_status(201)
        second = order_client.checkout(address_id=address_id, idempotency_key=key)
        second.assert_status(201)

        assert second.body["id"] == first.body["id"], "The replay returned a different order"
        assert second.body["order_number"] == first.body["order_number"]

        rows = db.orders_with_idempotency_key(key)
        assert len(rows) == 1, f"Idempotency-Key {key} produced {len(rows)} orders: {rows}"

        order_id = int(first.body["id"])
        assert (
            len(db.payments_for_order(order_id)) == 1
        ), "The replay charged the card a second time"
        assert db.stock_for(product["id"]) == 6, "The replay decremented stock a second time"


@allure.feature("Persistence")
@allure.story("Snapshots")
class TestOrderSnapshots:
    """An order is a historical record, so it must not change when the things
    it referenced change. Both tests here mutate the source data *through the
    API* afterwards and prove the order did not follow.
    """

    @allure.severity(allure.severity_level.CRITICAL)
    def test_order_keeps_the_shipping_address_it_was_placed_with(
        self,
        customer_with_address: tuple[Customer, int],
        cart_client: CartClient,
        order_client: OrderClient,
        address_client: AddressClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """This is why the shipping columns are copied onto the order rather
        than joined from ``addresses``.

        A customer who moves house and edits their saved address must not
        retroactively rewrite where last month's parcel was sent - the record
        of where goods were delivered is what disputes and returns depend on.
        """
        _, address_id = customer_with_address
        product = product_with_stock(5)
        cart_client.add_item(product["id"], 1).assert_status(201)
        order = order_client.place_successful_order(address_id=address_id)
        order_id = int(order["id"])

        address_client.update(
            address_id,
            {"line1": "999 Moved Lane", "city": "Relocated", "postal_code": "99999"},
        ).assert_status(200)

        after = db.order_by_id(order_id)
        assert after is not None
        # The values the address fixture ships with, unchanged by the edit.
        assert after["shipping_line1"] == "1 Integration Way"
        assert after["shipping_city"] == "Austin"
        assert after["shipping_postal_code"] == "73301"
        assert after["shipping_state"] == "TX"
        assert after["shipping_country"] == "US"

        # The address book itself did change - otherwise the test would pass
        # simply because the update silently failed.
        addresses = {row["id"]: row for row in db.addresses_for_user(order["user_id"])}
        assert addresses[address_id]["city"] == "Relocated"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_order_items_keep_the_price_charged_at_the_time(
        self,
        customer_with_address: tuple[Customer, int],
        cart_client: CartClient,
        order_client: OrderClient,
        admin_client: AdminClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """Order lines store their own ``unit_price`` for the same reason.

        If the invoice read today's catalogue price, every price change would
        rewrite history: past orders would stop matching what customers were
        charged, refunds would be calculated against the wrong amount, and
        revenue reports would move every time marketing ran a sale.
        """
        _, address_id = customer_with_address
        product = product_with_stock(5, price=float(UNIT_PRICE))
        cart_client.add_item(product["id"], 2).assert_status(201)
        order = order_client.place_successful_order(address_id=address_id)
        order_id = int(order["id"])

        admin_client.update_product(product["id"], {"price": 129.99}).assert_status(200)

        catalogue = db.product_by_id(product["id"])
        assert catalogue is not None
        assert catalogue["price"] == Decimal("129.99"), "The price change did not take effect"

        items = db.order_items(order_id)
        assert len(items) == 1
        assert items[0]["unit_price"] == UNIT_PRICE, "The order line followed the catalogue price"
        assert items[0]["line_total"] == Decimal("99.98")

        row = db.order_by_id(order_id)
        assert row is not None
        assert row["subtotal"] == Decimal(
            "99.98"
        ), "The order total was rewritten by a price change"


@allure.feature("Persistence")
@allure.story("Promotions")
class TestPromotionPersistence:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_using_a_promotion_increments_its_usage_counter(
        self,
        customer_with_address: tuple[Customer, int],
        cart_client: CartClient,
        order_client: OrderClient,
        product_with_stock: Callable[..., dict[str, Any]],
        db: DatabaseQueries,
    ) -> None:
        """``times_used`` is what enforces a promotion's usage limit.

        If checkout applied the discount without incrementing the counter, a
        capped campaign could be redeemed without limit - the discount would
        keep working long after the budget for it was exhausted, and nobody
        would notice until the finance reconciliation.

        The assertion is "at least one more" rather than "exactly one more"
        because the promotions table is shared seed data: another test running
        in parallel may legitimately redeem the same code.
        """
        _, address_id = customer_with_address
        before = db.promotion("WELCOME10")
        assert before is not None, "WELCOME10 is not seeded"

        product = product_with_stock(5, price=float(UNIT_PRICE))
        cart_client.add_item(product["id"], 3).assert_status(201)

        response = order_client.checkout(address_id=address_id, promo_code="WELCOME10")
        response.assert_status(201)

        order = db.order_by_id(int(response.body["id"]))
        assert order is not None
        assert order["promo_code"] == "WELCOME10"
        # 10% of a 149.97 subtotal, well under the 100.00 cap.
        assert order["discount_total"] == Decimal("15.00")
        assert order["total"] == Decimal("145.77")

        after = db.promotion("WELCOME10")
        assert after is not None
        assert (
            after["times_used"] >= before["times_used"] + 1
        ), f"times_used did not move: {before['times_used']} -> {after['times_used']}"
