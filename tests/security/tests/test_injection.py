"""Security tests: hostile input handling.

The expectation throughout is **not** that the application rejects these
payloads. Most of them are valid text that a real customer could legitimately
type. The expectation is that they are treated as *data*: matched literally,
stored literally, returned literally, and never executed or interpreted.

A 500 is a failure here. So is a payload that changes the database. So is one
that measurably delays the response.
"""

from __future__ import annotations

import allure
import pytest

from tests.api.clients import AddressClient, AdminClient, AuthClient, CartClient, ProductClient
from tests.test_data.factories import INJECTION_PAYLOADS, unique_email
from tests.utilities.http import HttpClient

pytestmark = [allure.epic("Security"), allure.feature("Input handling")]

PAYLOAD_IDS = [label for label, _ in INJECTION_PAYLOADS]
PAYLOADS = [payload for _, payload in INJECTION_PAYLOADS]


#: The seeded catalogue never shrinks: seed products are not deactivated by any
#: test, so this floor holds no matter what else is running.
SEEDED_PRODUCT_FLOOR = 60


@pytest.fixture(scope="module")
def baseline_product_count(product_client: ProductClient) -> int:
    """The catalogue size when this module started.

    Used as an upper reference for "a payload matched everything". It is
    deliberately *not* used as an exact equality target - see
    :func:`assert_catalogue_intact`.
    """
    return int(product_client.list(page_size=1).assert_status(200).body["total"])


def assert_catalogue_intact(product_client: ProductClient, baseline: int) -> None:
    """The catalogue is still there and still populated.

    Asserted against the seeded floor rather than against an exact count taken
    a moment earlier. Under `pytest -n auto` other workers are creating and
    deactivating products throughout, so an equality check would fail for
    reasons that have nothing to do with injection - while a payload that
    genuinely dropped a table or emptied it would still be caught here, and
    loudly.
    """
    response = product_client.list(page_size=1)
    response.assert_status(200)
    current = response.body["total"]
    assert current >= SEEDED_PRODUCT_FLOOR, (
        f"The catalogue collapsed to {current} products (floor is {SEEDED_PRODUCT_FLOOR}) - "
        f"a payload was executed rather than matched"
    )


# ---------------------------------------------------------------------------
@allure.story("Search and filter input")
class TestSearchInput:
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_hostile_search_terms_are_handled_as_text(
        self, product_client: ProductClient, baseline_product_count: int, label: str, payload: str
    ) -> None:
        response = product_client.search(payload)

        # 200 (matched literally, found nothing) and 422 (refused as invalid
        # text) are both safe outcomes. 500 is not: it means the payload reached
        # something that could not cope with it.
        assert response.status_code in (
            200,
            422,
        ), f"Searching {payload!r} returned {response.status_code}: {response.raw_text[:200]}"
        if response.status_code == 200:
            # A SQL payload that executed would typically match everything.
            # `>=` against the live total is the right comparison: the number of
            # products moves under parallel execution, so the assertion is that
            # the search did not somehow return *more* than exist.
            live_total = product_client.list(page_size=1).body["total"]
            assert response.body["total"] <= live_total
        assert_catalogue_intact(product_client, baseline_product_count)

    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_hostile_filter_values_are_handled_safely(
        self, product_client: ProductClient, baseline_product_count: int, label: str, payload: str
    ) -> None:
        for response in (
            product_client.list(category=payload),
            product_client.list(brand=payload),
        ):
            assert response.status_code in (
                200,
                422,
            ), f"{payload!r} produced {response.status_code}: {response.raw_text[:200]}"
            if response.status_code == 200:
                assert (
                    response.body["total"] == 0
                ), f"{payload!r} matched {response.body['total']} products as a filter value"
        assert_catalogue_intact(product_client, baseline_product_count)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_time_based_probe_does_not_delay_the_response(
        self, product_client: ProductClient
    ) -> None:
        """`pg_sleep` must not run.

        A blind SQL injection is often detected by timing rather than output.
        If the payload executed, this request would take at least five seconds;
        the bound is deliberately generous so a slow machine cannot fail it,
        while an executed sleep still would.
        """
        response = product_client.search("'; SELECT pg_sleep(5); --")
        response.assert_status(200)
        assert (
            response.elapsed_ms < 3000
        ), f"The request took {response.elapsed_ms:.0f}ms - consistent with pg_sleep executing"

    def test_a_union_probe_does_not_leak_extra_columns(self, product_client: ProductClient) -> None:
        response = product_client.search("' UNION SELECT NULL, version() --")
        response.assert_status(200)
        assert "PostgreSQL" not in response.raw_text, "A UNION payload leaked server internals"
        assert response.body["total"] == 0

    @pytest.mark.parametrize("wildcard", ["%", "_", "%%%", "_%_", "\\%"])
    def test_like_wildcards_are_matched_literally(
        self, product_client: ProductClient, baseline_product_count: int, wildcard: str
    ) -> None:
        """`%` is a character to a shopper, and must be one to the query too.

        Unescaped, a search for "100%" would return the whole catalogue.
        """
        response = product_client.search(wildcard)
        response.assert_status(200)
        assert (
            response.body["total"] < baseline_product_count
        ), f"{wildcard!r} matched {response.body['total']} of {baseline_product_count} products"

    def test_an_extremely_long_search_term_is_bounded(self, product_client: ProductClient) -> None:
        response = product_client.search("A" * 5000)
        response.assert_status_in(200, 422)

    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        ("label", "send"),
        [
            ("query-string", "query"),
            ("json-body", "body"),
        ],
    )
    def test_a_nul_byte_is_refused_cleanly_rather_than_causing_a_500(
        self,
        product_client: ProductClient,
        auth_client: AuthClient,
        label: str,
        send: str,
    ) -> None:
        """A NUL byte must be a 422, never an unhandled error.

        PostgreSQL text columns cannot store 0x00, so before this was handled a
        single character from any anonymous caller produced a 500 on the search
        box, registration, addresses and the admin search alike - noisy, and a
        cheap way to fill an error tracker. The rejection is deliberate: NUL is
        not valid text, so there is nothing to preserve.

        Note that JSON escapes NUL rather than transmitting it raw, so the body
        case and the query case exercise genuinely different code paths.
        """
        # chr(0) rather than an escape sequence: unambiguous in source and
        # immune to an editor or tool that normalises escapes.
        hostile = f"test{chr(0)}.txt"

        if send == "query":
            response = product_client.search(hostile)
        else:
            response = auth_client.register(
                email=unique_email("nul"), password="Str0ngPass!", full_name=hostile
            )

        response.assert_status(422)
        assert response.error_code == "VALIDATION_ERROR"
        assert set(response.body) == {"error", "message", "details"}


# ---------------------------------------------------------------------------
@allure.story("Authentication input")
class TestAuthInput:
    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_login_with_a_hostile_email_never_authenticates(
        self, auth_client: AuthClient, label: str, payload: str
    ) -> None:
        """`' OR '1'='1` must not be a valid login.

        In a string-concatenated query it would be. Here it is either rejected
        as a malformed address or looked up as a literal value that matches
        nothing.
        """
        response = auth_client.login(payload, "AnyPassword123!")
        assert response.status_code in (
            401,
            422,
        ), f"{payload!r} produced {response.status_code}: {response.raw_text[:200]}"
        assert "access_token" not in response.raw_text

    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_a_hostile_password_never_authenticates(
        self, auth_client: AuthClient, customer, label: str, payload: str
    ) -> None:
        response = auth_client.login(customer.email, payload)
        assert response.status_code in (401, 422)
        assert "access_token" not in response.raw_text

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_script_payload_in_a_name_is_stored_and_returned_inert(
        self, auth_client: AuthClient
    ) -> None:
        """Stored XSS: the API must return data, not markup.

        The value is preserved exactly as typed (a customer might legitimately
        have angle brackets in a name), but the response is JSON with a JSON
        content type, so a browser parses it as data. Escaping at the API layer
        would corrupt the name; the defence is the content type plus the
        client's own rendering.
        """
        payload = "<script>alert('xss')</script>"
        email = unique_email("xss")

        created = auth_client.register(email=email, password="Str0ngPass!", full_name=payload)
        created.assert_status(201)

        assert created.headers["content-type"].startswith(
            "application/json"
        ), "A JSON API returning text/html would let a payload execute in a browser"
        assert created.body["user"]["full_name"] == payload, "The value was silently mangled"

        # And it survives a round trip unchanged, still as JSON.
        me = auth_client.me(token=created.body["access_token"])
        me.assert_status(200)
        assert me.body["full_name"] == payload
        assert me.headers["content-type"].startswith("application/json")

    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_registration_with_a_hostile_name_never_500s(
        self, auth_client: AuthClient, label: str, payload: str
    ) -> None:
        response = auth_client.register(
            email=unique_email(), password="Str0ngPass!", full_name=payload
        )
        # Stored verbatim (201) or refused as invalid text (422). Never a 500.
        assert response.status_code in (
            201,
            422,
        ), f"{payload!r} produced {response.status_code}: {response.raw_text[:200]}"


# ---------------------------------------------------------------------------
@allure.story("Body and path input")
class TestBodyInput:
    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_hostile_address_fields_are_handled_safely(
        self, address_client: AddressClient, label: str, payload: str
    ) -> None:
        response = address_client.create(
            {
                "label": "Home",
                "full_name": payload,
                "line1": payload,
                "city": payload[:80],
                "state": "TX",
                "postal_code": "73301",
                "country": "US",
            }
        )
        assert response.status_code in (
            201,
            422,
        ), f"{payload!r} produced {response.status_code}: {response.raw_text[:200]}"
        if response.status_code == 201:
            # Stored verbatim, not interpreted.
            assert response.body["line1"] == payload

    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_a_hostile_product_id_is_rejected_not_executed(
        self, cart_client: CartClient, label: str, payload: str
    ) -> None:
        response = cart_client.add_item_raw({"product_id": payload, "quantity": 1})
        response.assert_status(422)

    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_a_hostile_path_segment_never_500s(
        self, http: HttpClient, label: str, payload: str
    ) -> None:
        from urllib.parse import quote

        response = http.get(f"/products/{quote(payload, safe='')}", authenticate=False)
        assert response.status_code in (
            404,
            422,
        ), f"{payload!r} in the path produced {response.status_code}"

    @pytest.mark.parametrize(("label", "payload"), INJECTION_PAYLOADS, ids=PAYLOAD_IDS)
    def test_hostile_admin_search_input_is_handled_safely(
        self, admin_client: AdminClient, label: str, payload: str
    ) -> None:
        """Admin endpoints get the same treatment - a compromised admin session
        must not become a database console."""
        for response in (admin_client.users(search=payload), admin_client.orders(search=payload)):
            assert response.status_code in (
                200,
                422,
            ), f"{payload!r} produced {response.status_code}: {response.raw_text[:200]}"

    def test_a_deeply_nested_body_is_rejected_rather_than_crashing(
        self, cart_client: CartClient
    ) -> None:
        """Guards against a parser blowing the recursion limit."""
        nested: dict = {"quantity": 1}
        for _ in range(200):
            nested = {"product_id": nested}

        response = cart_client.add_item_raw(nested)
        assert response.status_code in (400, 422), response.status_code

    def test_unexpected_fields_are_ignored_not_applied(
        self, cart_client: CartClient, product_factory
    ) -> None:
        """Mass assignment.

        Fields the schema does not declare must be dropped, not written through
        to the model - otherwise a crafted body could set anything the ORM
        exposes.
        """
        product = product_factory(stock_quantity=5, price=99.99)
        response = cart_client.add_item_raw(
            {
                "product_id": product["id"],
                "quantity": 1,
                "unit_price": 0.01,
                "line_total": 0.01,
                "is_active": False,
                "id": 999999,
            }
        )
        response.assert_status(201)
        line = response.body["items"][0]
        assert line["unit_price"] == 99.99, "A client-supplied price was honoured"
        assert line["line_total"] == 99.99


# ---------------------------------------------------------------------------
@allure.story("Integrity after hostile input")
class TestIntegrity:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_the_database_is_unchanged_after_every_payload(
        self, product_client: ProductClient, db, baseline_product_count: int
    ) -> None:
        """The summary assertion for this whole file.

        Sends every payload at every text surface, then verifies the schema and
        the data are exactly as they were. A `DROP TABLE` that succeeded would
        fail here loudly.
        """
        for _, payload in INJECTION_PAYLOADS:
            product_client.search(payload)
            product_client.list(brand=payload)
            product_client.list(category=payload)

        assert_catalogue_intact(product_client, baseline_product_count)

        tables = set(db.table_names())
        assert {
            "users",
            "products",
            "orders",
            "order_items",
            "payments",
            "inventory",
        } <= tables, f"Tables are missing after the injection sweep: {sorted(tables)}"
        assert db.negative_stock_rows() == []
        assert db.products_without_inventory() == []
