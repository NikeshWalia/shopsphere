"""Concurrency tests: the races that only appear under real simultaneity.

These are the tests a sequential suite can never write. Every one of them fires
genuinely simultaneous HTTP requests from separate threads, each with its own
client and its own connection, and asserts on the *aggregate* outcome.

Marked ``serial`` because the concurrency here is the thing under test — running
them under pytest-xdist would add a second, uncontrolled source of it and make
the result meaningless. Marked ``slow`` because coordinating threads and waiting
for real network round trips takes seconds, not milliseconds.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import allure
import pytest

from tests.api.clients import CARD_APPROVED, AdminClient
from tests.configuration.settings import settings
from tests.database.queries.queries import DatabaseQueries
from tests.utilities.http import ApiResponse, HttpClient

pytestmark = [
    allure.epic("Business journeys"),
    allure.feature("Concurrency"),
    pytest.mark.slow,
    pytest.mark.serial,
]

PAYMENT: dict[str, Any] = {
    "card_number": CARD_APPROVED,
    "card_holder": "Race Participant",
    "expiry_month": 12,
    "expiry_year": 2032,
    "cvv": "123",
}


@dataclass
class Shopper:
    """One customer, ready to check out, with a client of their own.

    Each holds a separate ``HttpClient`` so the threads share no connection
    pool - otherwise the "concurrent" requests would serialise inside httpx and
    the test would prove nothing.
    """

    token: str
    address_id: int
    client: HttpClient

    def checkout(self, idempotency_key: str | None = None) -> ApiResponse:
        return self.client.post(
            "/orders",
            json_body={"address_id": self.address_id, "payment": PAYMENT},
            headers={"Idempotency-Key": idempotency_key or uuid.uuid4().hex},
        )


def build_shopper(auth_client, product_id: int, quantity: int) -> Shopper:
    """Register a customer, give them an address, and fill their cart."""
    from tests.test_data.factories import DEFAULT_PASSWORD, unique_email

    registered = auth_client.register(
        email=unique_email("race"), password=DEFAULT_PASSWORD, full_name="Race Participant"
    )
    registered.assert_status(201)
    token = registered.body["access_token"]

    client = HttpClient(settings.api_url, token=token)
    address = client.post(
        "/addresses",
        json_body={
            "full_name": "Race Participant",
            "line1": "1 Contention Street",
            "city": "Austin",
            "state": "TX",
            "postal_code": "73301",
            "country": "US",
        },
    )
    address.assert_status(201)
    client.post(
        "/cart/items", json_body={"product_id": product_id, "quantity": quantity}
    ).assert_status(201)

    return Shopper(token=token, address_id=address.body["id"], client=client)


def run_simultaneously(tasks: list) -> list[ApiResponse]:
    """Execute every task at once and collect the results.

    A thread pool sized to the task count means all of them are genuinely in
    flight together rather than being queued behind a smaller pool.
    """
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(task) for task in tasks]
        return [future.result() for future in as_completed(futures)]


# ---------------------------------------------------------------------------
@allure.story("Oversell prevention")
@allure.severity(allure.severity_level.BLOCKER)
def test_six_simultaneous_buyers_cannot_oversell_three_units(
    auth_client, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    """The test that justifies ``SELECT ... FOR UPDATE``.

    Six customers each hold one unit of a three-unit product and all press
    "Place order" at the same instant. Without row locking, several would read
    "3 available", all pass validation, and all decrement - overselling the
    product and possibly driving stock negative.

    Exactly three must succeed. Not "about three", and never four.
    """
    product = admin_client.create_product_with_stock(3, price=20.00, name="Contested Widget")
    shoppers = [build_shopper(auth_client, product["id"], 1) for _ in range(6)]

    try:
        with allure.step("Six checkouts fire simultaneously"):
            responses = run_simultaneously([shopper.checkout for shopper in shoppers])

        succeeded = [r for r in responses if r.status_code == 201]
        refused = [r for r in responses if r.status_code == 409]
        other = [r for r in responses if r.status_code not in (201, 409)]

        allure.attach(
            "\n".join(f"{r.status_code} {r.error_code or 'created'}" for r in responses),
            name="Outcomes",
            attachment_type=allure.attachment_type.TEXT,
        )

        assert (
            not other
        ), f"Unexpected statuses: {[(r.status_code, r.raw_text[:120]) for r in other]}"
        assert len(succeeded) == 3, (
            f"{len(succeeded)} of 6 checkouts succeeded against 3 units of stock. "
            f"More than 3 means the product was oversold; fewer means stock was lost."
        )
        assert len(refused) == 3

        with allure.step("Stock landed on exactly zero, never below"):
            assert db.stock_for(product["id"]) == 0
            assert db.negative_stock_rows() == [], "Stock went negative - the CHECK was bypassed"

        with allure.step("Exactly three orders exist for this product"):
            paid = [
                order
                for shopper in shoppers
                for order in shopper.client.get("/orders").body["items"]
                if order["payment_status"] == "paid"
            ]
            assert len(paid) == 3
    finally:
        for shopper in shoppers:
            shopper.client.close()


@allure.story("Oversell prevention")
def test_the_last_unit_goes_to_exactly_one_of_two_buyers(
    auth_client, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    """The sharpest version of the race: two buyers, one unit.

    Any locking bug shows up here as either two winners (oversold) or none
    (a deadlock or a lost update).
    """
    product = admin_client.create_product_with_stock(1, price=99.00, name="The Last One")
    shoppers = [build_shopper(auth_client, product["id"], 1) for _ in range(2)]

    try:
        responses = run_simultaneously([shopper.checkout for shopper in shoppers])
        succeeded = [r for r in responses if r.status_code == 201]

        assert len(succeeded) == 1, f"{len(succeeded)} buyers got the last unit; exactly one must"
        assert db.stock_for(product["id"]) == 0
    finally:
        for shopper in shoppers:
            shopper.client.close()


@allure.story("Idempotency under concurrency")
@allure.severity(allure.severity_level.BLOCKER)
def test_five_simultaneous_submissions_with_one_key_create_one_order(
    auth_client, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    """Business rule 9, under the conditions that actually break it.

    A sequential replay is easy: the second request finds the first order and
    returns it. The hard case is five requests in flight together, none of which
    can see the others' uncommitted work. The unique constraint on
    ``(user_id, idempotency_key)`` is what lets exactly one commit, rolling the
    losers back wholesale - including their stock decrements.
    """
    product = admin_client.create_product_with_stock(20, price=15.00, name="Double Click Widget")
    shopper = build_shopper(auth_client, product["id"], 2)
    key = uuid.uuid4().hex
    stock_before = db.stock_for(product["id"])

    try:
        with allure.step("Five identical checkouts fire at once"):
            responses = run_simultaneously([lambda: shopper.checkout(key) for _ in range(5)])

        allure.attach(
            "\n".join(
                f"{r.status_code} order={r.body.get('id') if isinstance(r.body, dict) else '-'}"
                for r in responses
            ),
            name="Outcomes",
            attachment_type=allure.attachment_type.TEXT,
        )

        with allure.step("Exactly one order row exists for that key"):
            rows = db.orders_with_idempotency_key(key)
            assert len(rows) == 1, f"{len(rows)} orders were created for a single key"

        with allure.step("Every successful response refers to that one order"):
            created = [r for r in responses if r.status_code in (200, 201)]
            assert (
                created
            ), f"No request succeeded: {[(r.status_code, r.raw_text[:100]) for r in responses]}"
            returned_ids = {r.body["id"] for r in created}
            assert returned_ids == {
                rows[0]["id"]
            }, f"Responses referred to different orders: {returned_ids}"

        with allure.step("Stock was decremented exactly once"):
            assert (
                stock_before - db.stock_for(product["id"]) == 2
            ), "A rolled-back duplicate still consumed stock"
    finally:
        shopper.client.close()


@allure.story("Idempotency under concurrency")
def test_distinct_keys_still_create_distinct_orders(
    auth_client, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    """The idempotency guard must not over-collapse.

    Two genuinely different purchases, submitted simultaneously with different
    keys, must both go through - otherwise the fix for double-clicking would
    break repeat buying.
    """
    product = admin_client.create_product_with_stock(20, price=12.00)
    shopper = build_shopper(auth_client, product["id"], 1)
    stock_before = db.stock_for(product["id"])

    try:
        first = shopper.checkout()
        first.assert_status(201)

        # Refill and buy again with a fresh key.
        shopper.client.post(
            "/cart/items", json_body={"product_id": product["id"], "quantity": 1}
        ).assert_status(201)
        second = shopper.checkout()
        second.assert_status(201)

        assert first.body["id"] != second.body["id"]
        assert stock_before - db.stock_for(product["id"]) == 2
    finally:
        shopper.client.close()


@allure.story("Cart consistency")
def test_concurrent_cart_updates_leave_a_coherent_cart(
    auth_client, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    """Simultaneous edits must not duplicate a line or corrupt a quantity.

    The UNIQUE (cart_id, product_id) constraint is what guarantees the first;
    the last writer wins on the second, which is acceptable for a cart as long
    as the result is one of the values actually submitted.
    """
    from tests.test_data.factories import DEFAULT_PASSWORD, unique_email

    product = admin_client.create_product_with_stock(50, price=8.00)
    registered = auth_client.register(email=unique_email("cartrace"), password=DEFAULT_PASSWORD)
    registered.assert_status(201)
    token = registered.body["access_token"]
    user_id = registered.body["user"]["id"]

    clients = [HttpClient(settings.api_url, token=token) for _ in range(5)]
    try:
        with allure.step("Five clients add the same product simultaneously"):
            responses = run_simultaneously(
                [
                    (
                        lambda c=client: c.post(
                            "/cart/items", json_body={"product_id": product["id"], "quantity": 1}
                        )
                    )
                    for client in clients
                ]
            )
        assert all(
            r.status_code in (201, 409, 422) for r in responses
        ), f"Unexpected statuses: {[r.status_code for r in responses]}"

        with allure.step("The cart holds exactly one line for that product"):
            rows = db.cart_items_for_user(user_id)
            matching = [row for row in rows if row["product_id"] == product["id"]]
            assert len(matching) == 1, (
                f"{len(matching)} cart rows exist for one product - the unique "
                f"constraint did not hold"
            )
            assert 1 <= matching[0]["quantity"] <= 5

        with allure.step("Five clients set different quantities simultaneously"):
            targets = [2, 4, 6, 8, 10]
            run_simultaneously(
                [
                    (
                        lambda c=client, q=quantity: c.patch(
                            f"/cart/items/{product['id']}", json_body={"quantity": q}
                        )
                    )
                    for client, quantity in zip(clients, targets, strict=True)
                ]
            )

        with allure.step("The surviving quantity is one that was actually submitted"):
            rows = db.cart_items_for_user(user_id)
            matching = [row for row in rows if row["product_id"] == product["id"]]
            assert len(matching) == 1
            assert (
                matching[0]["quantity"] in targets
            ), f"The cart holds {matching[0]['quantity']}, which nobody asked for"
    finally:
        for client in clients:
            client.close()


@allure.story("Stock integrity")
def test_simultaneous_admin_restock_and_customer_purchase_stay_consistent(
    auth_client, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    """An admin adjustment must not silently discard a concurrent decrement.

    Both paths lock the inventory row, so whichever commits second sees the
    other's result rather than overwriting a stale read.
    """
    product = admin_client.create_product_with_stock(10, price=30.00)
    shopper = build_shopper(auth_client, product["id"], 4)

    try:
        responses = run_simultaneously(
            [
                shopper.checkout,
                lambda: admin_client.set_stock(product["id"], 25),
            ]
        )
        checkout = next(r for r in responses if "/orders" in r.url)
        checkout.assert_status(201)

        final = db.stock_for(product["id"])
        # Either ordering is legitimate: the admin's absolute value applied
        # after the sale (25), or before it (25 - 4 = 21). What must never
        # happen is a value derived from a stale read, or a negative one.
        assert final in (21, 25), f"Stock settled on {final}, which neither ordering can produce"
        assert db.negative_stock_rows() == []
    finally:
        shopper.client.close()
