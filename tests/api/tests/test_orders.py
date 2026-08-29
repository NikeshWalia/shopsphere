"""API tests for quoting, checkout, order history and cancellation.

Checkout is where money, stock and authorisation all meet, so this is the
highest-value file in the API suite. The rules being defended:

* the client never dictates a price,
* a failed payment never produces a paid order,
* stock is reserved on success and returned on failure,
* one intent produces one order, however many times the button is pressed,
* one customer can never see another's order.
"""

from __future__ import annotations

import uuid
from typing import Any

import allure
import pytest

from tests.api.clients import (
    CARD_APPROVED,
    CARD_DECLINED_EXPIRED,
    CARD_DECLINED_FUNDS,
    CARD_INVALID_LUHN,
    CARD_PROVIDER_ERROR,
    AddressClient,
    AdminClient,
    CartClient,
    OrderClient,
    ProductClient,
    payment_payload,
)
from tests.test_data.factories import PAYMENT_OUTCOMES

pytestmark = [allure.epic("Commerce"), allure.feature("Checkout")]

MONEY_FIELDS = ("subtotal", "discount_total", "tax", "shipping_fee", "total")


def assert_totals_add_up(body: dict[str, Any]) -> None:
    """total == subtotal - discount + tax + shipping, to the cent.

    Asserted as a property rather than by recomputing the formula the API uses,
    so it still fails if that formula is wrong.
    """
    expected = round(
        body["subtotal"] - body["discount_total"] + body["tax"] + body["shipping_fee"], 2
    )
    assert abs(body["total"] - expected) < 0.005, (
        f"total {body['total']} does not equal "
        f"{body['subtotal']} - {body['discount_total']} + {body['tax']} + {body['shipping_fee']}"
    )


def assert_money_types(body: dict[str, Any]) -> None:
    for field in MONEY_FIELDS:
        value = body[field]
        assert isinstance(value, int | float) and not isinstance(
            value, bool
        ), f"{field} is {value!r} ({type(value).__name__}); money must be a JSON number"


@pytest.fixture
def stocked_cart(cart_client: CartClient, product_factory) -> dict[str, Any]:
    """A cart holding two units of a product created by this test.

    Created rather than borrowed from the catalogue: these tests move stock
    around, and doing that to a shared product would break other tests.
    """
    product = product_factory(stock_quantity=10, price=25.00)
    cart_client.add_item(product["id"], 2).assert_status(201)
    return product


# ---------------------------------------------------------------------------
@allure.story("Quote")
class TestQuote:
    def test_a_quote_prices_the_current_cart(self, order_client: OrderClient, stocked_cart) -> None:
        response = order_client.quote()
        response.assert_status(200)
        assert_money_types(response.body)
        assert_totals_add_up(response.body)
        assert response.body["subtotal"] == 50.00  # 2 x 25.00
        assert response.body["item_count"] == 2
        assert response.body["is_checkout_ready"] is True

    def test_tax_is_charged_on_the_discounted_subtotal_only(
        self, order_client: OrderClient, stocked_cart
    ) -> None:
        """Shipping is not taxed.

        50.00 subtotal at 8% is 4.00. Taxing the 59.99 that includes shipping
        would give 4.80 - a difference the customer would notice.
        """
        body = order_client.quote().assert_status(200).body
        assert body["subtotal"] == 50.00
        assert body["tax"] == 4.00
        assert body["shipping_fee"] == 9.99
        assert body["total"] == 63.99

    def test_an_order_over_the_threshold_ships_free(
        self, order_client: OrderClient, cart_client: CartClient, product_factory
    ) -> None:
        product = product_factory(stock_quantity=5, price=120.00)
        cart_client.add_item(product["id"], 1).assert_status(201)

        body = order_client.quote().assert_status(200).body
        assert body["subtotal"] == 120.00
        assert body["shipping_fee"] == 0.0

    def test_a_valid_promotion_reduces_the_total(
        self, order_client: OrderClient, stocked_cart
    ) -> None:
        plain = order_client.quote().body
        discounted = order_client.quote("WELCOME10").assert_status(200).body

        assert discounted["promo_code"] == "WELCOME10"
        assert discounted["discount_total"] == 5.00  # 10% of 50.00
        assert discounted["total"] < plain["total"]
        assert_totals_add_up(discounted)

    def test_a_promotion_below_its_minimum_is_refused_with_a_reason(
        self, order_client: OrderClient, stocked_cart
    ) -> None:
        """SAVE15 needs a $150 subtotal; this cart is $50."""
        body = order_client.quote("SAVE15").assert_status(200).body
        assert body["discount_total"] == 0.0
        assert any("minimum" in issue.lower() for issue in body["issues"]), body["issues"]

    @pytest.mark.parametrize(
        ("code", "expected_fragment"),
        [("EXPIRED20", "expired"), ("INACTIVE5", "no longer active"), ("NOSUCHCODE", "recognised")],
    )
    def test_an_unusable_promotion_is_reported_as_an_issue_not_a_failure(
        self, order_client: OrderClient, stocked_cart, code: str, expected_fragment: str
    ) -> None:
        """A bad code must not stop the basket rendering.

        Failing the whole quote would leave the customer looking at an error
        page instead of their cart with a note about the code.
        """
        response = order_client.quote(code)
        response.assert_status(200)
        assert response.body["discount_total"] == 0.0
        assert any(
            expected_fragment in issue.lower() for issue in response.body["issues"]
        ), f"Expected an issue mentioning {expected_fragment!r}; got {response.body['issues']}"

    def test_quoting_requires_a_session(self, http) -> None:
        http.post("/checkout/quote", json_body={}, authenticate=False).assert_status(401)


# ---------------------------------------------------------------------------
@allure.story("Successful checkout")
class TestCheckoutSuccess:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_an_order_is_created_confirmed_and_paid(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        response = order_client.checkout(address_id=address_id)

        response.assert_status(201)
        order = response.body
        assert order["status"] == "confirmed"
        assert order["payment_status"] == "paid"
        assert order["order_number"].startswith("SS-")
        assert_money_types(order)
        assert_totals_add_up(order)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_the_amount_charged_matches_the_amount_quoted(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        """Preview and charge come from the same calculation, so they cannot drift."""
        _, address_id = customer_with_address
        quoted = order_client.quote().assert_status(200).body
        order = order_client.checkout(address_id=address_id).assert_status(201).body

        for field in MONEY_FIELDS:
            assert (
                order[field] == quoted[field]
            ), f"{field}: quoted {quoted[field]}, charged {order[field]}"

    def test_order_lines_snapshot_the_product(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        order = order_client.checkout(address_id=address_id).assert_status(201).body

        assert len(order["items"]) == 1
        line = order["items"][0]
        assert line["product_id"] == stocked_cart["id"]
        assert line["sku"] == stocked_cart["sku"]
        assert line["product_name"] == stocked_cart["name"]
        assert line["unit_price"] == 25.00
        assert line["quantity"] == 2
        assert line["line_total"] == 50.00

    @allure.severity(allure.severity_level.CRITICAL)
    def test_the_full_card_number_never_appears_in_the_response(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        response = order_client.checkout(address_id=address_id, card_number=CARD_APPROVED)
        response.assert_status(201)

        assert CARD_APPROVED not in response.raw_text, "The PAN was echoed back"
        assert response.body["payments"][0].get("cvv") != "123", "The CVV was persisted"
        payment = response.body["payments"][0]
        assert payment["card_last4"] == "1111"
        assert payment["card_brand"] == "visa"
        assert payment["status"] == "paid"

    def test_stock_is_decremented_by_exactly_the_ordered_quantity(
        self,
        order_client: OrderClient,
        product_client: ProductClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        _, address_id = customer_with_address
        before = product_client.get(stocked_cart["id"]).body["stock_quantity"]

        order_client.checkout(address_id=address_id).assert_status(201)

        after = product_client.get(stocked_cart["id"]).body["stock_quantity"]
        assert before - after == 2, f"Stock went {before} -> {after}; expected a decrease of 2"

    def test_the_cart_is_emptied_after_a_successful_order(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        _, address_id = customer_with_address
        order_client.checkout(address_id=address_id).assert_status(201)

        cart = cart_client.get().assert_status(200).body
        assert cart["items"] == []
        assert cart["item_count"] == 0

    def test_the_shipping_address_is_copied_onto_the_order(
        self, order_client: OrderClient, address_client: AddressClient, customer, stocked_cart
    ) -> None:
        created = address_client.create(
            {
                "label": "Work",
                "full_name": "Ada Lovelace",
                "line1": "12 Analytical Way",
                "city": "Cambridge",
                "state": "MA",
                "postal_code": "02139",
                "country": "US",
            }
        )
        created.assert_status(201)

        order = order_client.checkout(address_id=created.body["id"]).assert_status(201).body
        shipping = order["shipping_address"]
        assert shipping["full_name"] == "Ada Lovelace"
        assert shipping["line1"] == "12 Analytical Way"
        assert shipping["city"] == "Cambridge"
        assert shipping["postal_code"] == "02139"

    def test_a_promotion_is_applied_and_recorded(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        order = order_client.checkout(address_id=address_id, promo_code="WELCOME10")
        order.assert_status(201)
        assert order.body["promo_code"] == "WELCOME10"
        assert order.body["discount_total"] == 5.00
        assert_totals_add_up(order.body)


# ---------------------------------------------------------------------------
@allure.story("Payment failures")
class TestPaymentFailures:
    @pytest.mark.parametrize(
        ("label", "card", "expected_status"),
        PAYMENT_OUTCOMES,
        ids=[case[0] for case in PAYMENT_OUTCOMES],
    )
    def test_every_provider_outcome_maps_to_the_right_status(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
        label: str,
        card: str,
        expected_status: int,
    ) -> None:
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5, price=20.00)
        cart_client.add_item(product["id"], 1).assert_status(201)

        response = order_client.checkout(address_id=address_id, card_number=card)
        response.assert_status(expected_status)

    @allure.severity(allure.severity_level.BLOCKER)
    def test_a_declined_card_never_produces_a_paid_order(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        """Business rule 5, asserted at the API boundary.

        The response must say, unambiguously, that the order was cancelled and
        the payment failed - not merely that something went wrong.
        """
        _, address_id = customer_with_address
        response = order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_FUNDS)

        response.assert_error("PAYMENT_DECLINED", 402)
        details = response.details
        assert details["order_status"] == "cancelled"
        assert details["payment_status"] == "failed"
        assert details["failure_code"] == "insufficient_funds"
        assert "order_number" in details

    @allure.severity(allure.severity_level.BLOCKER)
    def test_a_declined_card_returns_the_stock(
        self,
        order_client: OrderClient,
        product_client: ProductClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        """Business rule 8.

        Stock is reserved before the charge, so a decline that failed to return
        it would silently make units unsellable forever.
        """
        _, address_id = customer_with_address
        before = product_client.get(stocked_cart["id"]).body["stock_quantity"]

        order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_FUNDS).assert_status(
            402
        )

        after = product_client.get(stocked_cart["id"]).body["stock_quantity"]
        assert after == before, f"Stock went {before} -> {after}; a decline must restore it"

    def test_the_cart_survives_a_declined_payment(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        """The customer must be able to retry with another card without rebuilding their basket."""
        _, address_id = customer_with_address
        order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_FUNDS).assert_status(
            402
        )

        cart = cart_client.get().assert_status(200).body
        assert len(cart["items"]) == 1
        assert cart["items"][0]["quantity"] == 2

    def test_a_customer_can_retry_with_a_good_card(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_FUNDS).assert_status(
            402
        )

        retry = order_client.checkout(address_id=address_id, card_number=CARD_APPROVED)
        retry.assert_status(201)
        assert retry.body["payment_status"] == "paid"

    def test_a_provider_error_becomes_a_502_and_returns_the_stock(
        self,
        order_client: OrderClient,
        product_client: ProductClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        _, address_id = customer_with_address
        before = product_client.get(stocked_cart["id"]).body["stock_quantity"]

        response = order_client.checkout(address_id=address_id, card_number=CARD_PROVIDER_ERROR)
        response.assert_error("PAYMENT_PROVIDER_ERROR", 502)
        assert response.details["payment_status"] == "failed"

        assert product_client.get(stocked_cart["id"]).body["stock_quantity"] == before

    def test_an_expired_card_is_declined(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        response = order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_EXPIRED)
        response.assert_status_in(402, 502)
        assert response.error_code in ("PAYMENT_DECLINED", "PAYMENT_PROVIDER_ERROR")

    def test_a_card_number_failing_its_checksum_is_refused(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        response = order_client.checkout(address_id=address_id, card_number=CARD_INVALID_LUHN)
        response.assert_status_in(402, 502)

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            ("non-numeric-card", {"card_number": "abcd-efgh-ijkl-mnop"}),
            ("short-card", {"card_number": "411111"}),
            ("bad-month", {"expiry_month": 13}),
            ("zero-month", {"expiry_month": 0}),
            ("bad-year", {"expiry_year": 1999}),
            ("short-cvv", {"cvv": "1"}),
            ("non-numeric-cvv", {"cvv": "abc"}),
            ("empty-holder", {"card_holder": ""}),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_a_malformed_payment_body_is_rejected_before_any_charge(
        self,
        order_client: OrderClient,
        customer_with_address,
        stocked_cart,
        label: str,
        overrides: dict[str, Any],
    ) -> None:
        _, address_id = customer_with_address
        response = order_client.checkout(
            address_id=address_id, payment=payment_payload(**overrides)
        )
        response.assert_error("VALIDATION_ERROR", 422)


# ---------------------------------------------------------------------------
@allure.story("Idempotency")
class TestIdempotency:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_repeating_a_checkout_with_the_same_key_returns_the_original_order(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        """Business rule 9 - the double-clicked button.

        The second request must return the *same* order, not a second one and
        not an error.
        """
        _, address_id = customer_with_address
        key = uuid.uuid4().hex

        first = order_client.checkout(address_id=address_id, idempotency_key=key)
        first.assert_status(201)

        second = order_client.checkout(address_id=address_id, idempotency_key=key)
        second.assert_status_in(200, 201)
        assert second.body["id"] == first.body["id"]
        assert second.body["order_number"] == first.body["order_number"]

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_replayed_checkout_does_not_decrement_stock_twice(
        self,
        order_client: OrderClient,
        product_client: ProductClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        _, address_id = customer_with_address
        key = uuid.uuid4().hex
        before = product_client.get(stocked_cart["id"]).body["stock_quantity"]

        order_client.checkout(address_id=address_id, idempotency_key=key).assert_status(201)
        after_first = product_client.get(stocked_cart["id"]).body["stock_quantity"]

        order_client.checkout(address_id=address_id, idempotency_key=key)
        after_replay = product_client.get(stocked_cart["id"]).body["stock_quantity"]

        assert before - after_first == 2
        assert after_replay == after_first, "The replay decremented stock a second time"

    def test_different_keys_create_different_orders(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        customer_with_address,
        product_factory,
    ) -> None:
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=10, price=15.00)

        cart_client.add_item(product["id"], 1).assert_status(201)
        first = order_client.checkout(address_id=address_id, idempotency_key=uuid.uuid4().hex)
        first.assert_status(201)

        cart_client.add_item(product["id"], 1).assert_status(201)
        second = order_client.checkout(address_id=address_id, idempotency_key=uuid.uuid4().hex)
        second.assert_status(201)

        assert first.body["id"] != second.body["id"]

    def test_one_customers_key_is_not_visible_to_another(
        self,
        order_client: OrderClient,
        customer_with_address,
        stocked_cart,
        second_customer,
        http,
        product_factory,
    ) -> None:
        """Keys are client-generated, so they must be scoped per user.

        Otherwise guessing a key would hand an attacker somebody else's order.
        """
        _, address_id = customer_with_address
        key = "a-predictable-key-0001"
        mine = order_client.checkout(address_id=address_id, idempotency_key=key)
        mine.assert_status(201)

        other = http.post(
            "/orders",
            json_body={"address_id": address_id, "payment": payment_payload()},
            headers={"Idempotency-Key": key},
            token=second_customer.token,
        )
        # The other customer must not receive my order. They get their own
        # failure (no such address / empty cart), never a 201 carrying my data.
        assert other.status_code != 201 or other.body["id"] != mine.body["id"]
        assert mine.body["order_number"] not in other.raw_text


# ---------------------------------------------------------------------------
@allure.story("Checkout validation")
class TestCheckoutValidation:
    def test_an_empty_cart_cannot_be_checked_out(
        self, order_client: OrderClient, cart_client: CartClient, customer_with_address
    ) -> None:
        _, address_id = customer_with_address
        cart_client.clear().assert_status(200)

        response = order_client.checkout(address_id=address_id)
        response.assert_error("EMPTY_CART", 409)

    def test_an_unknown_address_is_rejected(
        self, order_client: OrderClient, customer, stocked_cart
    ) -> None:
        order_client.checkout(address_id=99_999_999).assert_error("ADDRESS_NOT_FOUND", 404)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_another_customers_address_cannot_be_used(
        self,
        order_client: OrderClient,
        address_client: AddressClient,
        second_customer,
        http,
        stocked_cart,
    ) -> None:
        """Shipping to an address you do not own is both a bug and a privacy leak.

        The 404 is deliberate: it makes another customer's address id
        indistinguishable from one that does not exist.
        """
        mine = address_client.create_and_get_id()

        response = http.post(
            "/orders",
            json_body={"address_id": mine, "payment": payment_payload()},
            headers={"Idempotency-Key": uuid.uuid4().hex},
            token=second_customer.token,
        )
        response.assert_error("ADDRESS_NOT_FOUND", 404)

    def test_checkout_requires_a_session(self, http, customer_with_address) -> None:
        _, address_id = customer_with_address
        http.post(
            "/orders",
            json_body={"address_id": address_id, "payment": payment_payload()},
            authenticate=False,
        ).assert_status(401)

    def test_checkout_is_blocked_when_stock_fell_short(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        admin_client: AdminClient,
        product_factory,
        customer_with_address,
    ) -> None:
        """The gap between adding to a cart and paying is where overselling lives."""
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5, price=10.00)
        cart_client.add_item(product["id"], 4).assert_status(201)

        admin_client.set_stock(product["id"], 1).assert_status(200)

        response = order_client.checkout(address_id=address_id)
        response.assert_status(409)
        assert response.error_code in ("INVALID_ORDER_STATE", "INSUFFICIENT_INVENTORY")

    def test_no_monetary_field_in_the_request_body_is_honoured(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        """Business rule 4, asserted directly.

        A crafted body carrying its own totals must be ignored entirely - the
        charge is whatever the catalogue says, not what the client claims.
        """
        _, address_id = customer_with_address
        response = order_client.checkout_raw(
            {
                "address_id": address_id,
                "payment": payment_payload(),
                "total": 0.01,
                "subtotal": 0.01,
                "tax": 0,
                "shipping_fee": 0,
                "discount_total": 999,
            },
            idempotency_key=uuid.uuid4().hex,
        )
        response.assert_status(201)
        assert response.body["subtotal"] == 50.00
        assert response.body["total"] == 63.99
        assert response.body["discount_total"] == 0.0


# ---------------------------------------------------------------------------
@allure.story("Order history")
class TestOrderHistory:
    def test_orders_appear_in_the_customers_history(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        placed = order_client.checkout(address_id=address_id).assert_status(201).body

        history = order_client.list().assert_status(200)
        assert history.body["total"] >= 1
        assert any(row["id"] == placed["id"] for row in history.body["items"])

    def test_history_is_scoped_to_the_signed_in_customer(
        self, order_client: OrderClient, customer_with_address, stocked_cart, second_customer, http
    ) -> None:
        _, address_id = customer_with_address
        mine = order_client.checkout(address_id=address_id).assert_status(201).body

        theirs = http.get("/orders", token=second_customer.token)
        theirs.assert_status(200)
        assert all(row["id"] != mine["id"] for row in theirs.body["items"])

    def test_an_order_can_be_opened_in_full(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        placed = order_client.checkout(address_id=address_id).assert_status(201).body

        detail = order_client.get(placed["id"]).assert_status(200).body
        assert detail["order_number"] == placed["order_number"]
        assert len(detail["items"]) == len(placed["items"])
        assert detail["payments"]
        assert detail["shipping_address"]["city"]

    @allure.severity(allure.severity_level.BLOCKER)
    def test_a_customer_cannot_open_another_customers_order(
        self, order_client: OrderClient, customer_with_address, stocked_cart, second_customer, http
    ) -> None:
        """Business rule 2 - the IDOR case.

        404 rather than 403 on purpose: a 403 would confirm the order exists,
        which is itself information the requester should not have.
        """
        _, address_id = customer_with_address
        mine = order_client.checkout(address_id=address_id).assert_status(201).body

        response = http.get(f"/orders/{mine['id']}", token=second_customer.token)
        response.assert_error("ORDER_NOT_FOUND", 404)
        assert mine["order_number"] not in response.raw_text

    def test_an_unknown_order_is_a_404(self, order_client: OrderClient, customer) -> None:
        order_client.get(99_999_999).assert_error("ORDER_NOT_FOUND", 404)

    def test_history_requires_a_session(self, http) -> None:
        http.get("/orders", authenticate=False).assert_status(401)

    def test_history_pagination_boundaries(self, order_client: OrderClient, customer) -> None:
        order_client.list(page=1, page_size=1).assert_status(200)
        order_client.list(page=9999).assert_status(200)
        order_client.list(page=0).assert_status(422)
        order_client.list(page_size=101).assert_status(422)


# ---------------------------------------------------------------------------
@allure.story("Cancellation")
class TestCancellation:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_cancelling_restores_the_stock_and_refunds(
        self,
        order_client: OrderClient,
        product_client: ProductClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        _, address_id = customer_with_address
        before = product_client.get(stocked_cart["id"]).body["stock_quantity"]
        order = order_client.checkout(address_id=address_id).assert_status(201).body
        during = product_client.get(stocked_cart["id"]).body["stock_quantity"]

        cancelled = order_client.cancel(order["id"], "Changed my mind").assert_status(200).body

        assert cancelled["status"] == "cancelled"
        assert cancelled["payment_status"] == "refunded"
        assert cancelled["cancelled_reason"] == "Changed my mind"
        assert during == before - 2
        assert product_client.get(stocked_cart["id"]).body["stock_quantity"] == before

    def test_an_order_cannot_be_cancelled_twice(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        """A second cancellation must not restore the stock a second time."""
        _, address_id = customer_with_address
        order = order_client.checkout(address_id=address_id).assert_status(201).body

        order_client.cancel(order["id"]).assert_status(200)
        order_client.cancel(order["id"]).assert_error("INVALID_ORDER_STATE", 409)

    def test_a_shipped_order_cannot_be_cancelled(
        self,
        order_client: OrderClient,
        admin_client: AdminClient,
        customer_with_address,
        stocked_cart,
    ) -> None:
        """Once it is in transit, cancellation is a returns problem."""
        _, address_id = customer_with_address
        order = order_client.checkout(address_id=address_id).assert_status(201).body

        for status in ("processing", "shipped"):
            admin_client.set_order_status(order["id"], status).assert_status(200)

        response = order_client.cancel(order["id"])
        response.assert_error("INVALID_ORDER_STATE", 409)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_customer_cannot_cancel_another_customers_order(
        self, order_client: OrderClient, customer_with_address, stocked_cart, second_customer, http
    ) -> None:
        _, address_id = customer_with_address
        mine = order_client.checkout(address_id=address_id).assert_status(201).body

        response = http.post(
            f"/orders/{mine['id']}/cancel",
            json_body={"reason": "not mine"},
            token=second_customer.token,
        )
        response.assert_error("ORDER_NOT_FOUND", 404)

        # And my order is untouched.
        assert order_client.get(mine["id"]).body["status"] == "confirmed"

    def test_a_declined_order_is_already_cancelled(
        self, order_client: OrderClient, customer_with_address, stocked_cart
    ) -> None:
        _, address_id = customer_with_address
        failure = order_client.checkout(address_id=address_id, card_number=CARD_DECLINED_FUNDS)
        failure.assert_status(402)

        order_id = failure.details["order_id"]
        detail = order_client.get(order_id).assert_status(200).body
        assert detail["status"] == "cancelled"
        assert detail["payment_status"] == "failed"
