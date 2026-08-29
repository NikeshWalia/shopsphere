"""Security tests: what the API gives away.

Covers the failures that leak rather than break - a header that is missing, a
field that should never have been serialised, a stack trace in an error body, a
timing difference that answers a question the caller should not be able to ask.
"""

from __future__ import annotations

import re
from typing import Any

import allure
import pytest

from tests.api.clients import AdminClient, AuthClient, CartClient, OrderClient, ProductClient
from tests.configuration.settings import settings
from tests.test_data.factories import unique_email
from tests.utilities.http import HttpClient
from tests.utilities.tokens import decode_without_verification

pytestmark = [allure.epic("Security"), allure.feature("Data exposure")]

# Headers asserted on every API response, with why each one is there.
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",  # stop a browser sniffing JSON into something executable
    "x-frame-options": "DENY",  # the API is never meant to be framed
    "referrer-policy": "no-referrer",  # do not leak URLs to third parties
    "cache-control": "no-store",  # responses are per-user; nothing shared may cache them
}

# Keys that must never appear anywhere in a response body.
FORBIDDEN_KEYS = (
    "password",
    "password_hash",
    "hashed_password",
    "secret",
    "secret_key",
    "salt",
    "card_number",
    "cvv",
    "cvc",
    "pan",
)

# Text that would indicate an internal detail escaped into an error.
LEAK_MARKERS = (
    "Traceback",
    'File "/app',
    "sqlalchemy.exc",
    "psycopg",
    "SELECT ",
    "INSERT INTO",
    "site-packages",
    "postgresql://",
    settings.jwt_secret,
)


def find_forbidden_keys(payload: Any, path: str = "") -> list[str]:
    """Walk a response looking for anything credential-shaped."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{path}.{key}" if path else key
            if key.lower() in FORBIDDEN_KEYS:
                found.append(here)
            found.extend(find_forbidden_keys(value, here))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(find_forbidden_keys(item, f"{path}[{index}]"))
    return found


# ---------------------------------------------------------------------------
@allure.story("Security headers")
class TestSecurityHeaders:
    @pytest.mark.parametrize(("header", "expected"), sorted(SECURITY_HEADERS.items()))
    def test_every_api_response_carries_the_header(
        self, product_client: ProductClient, header: str, expected: str
    ) -> None:
        response = product_client.list(page_size=1)
        response.assert_status(200)
        actual = {key.lower(): value for key, value in response.headers.items()}
        assert header in actual, f"{header} is missing. Present: {sorted(actual)}"
        assert actual[header] == expected, f"{header} is {actual[header]!r}, expected {expected!r}"

    def test_a_content_security_policy_is_set(self, product_client: ProductClient) -> None:
        headers = {k.lower(): v for k, v in product_client.list(page_size=1).headers.items()}
        csp = headers.get("content-security-policy", "")
        assert csp, "No Content-Security-Policy header"
        # A JSON API needs no script, style or frame sources at all.
        assert "default-src 'none'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_headers_are_present_on_error_responses_too(self, http: HttpClient) -> None:
        """A 401 is exactly when a browser should not be relaxing its rules."""
        response = http.get("/cart", authenticate=False)
        response.assert_status(401)
        headers = {key.lower() for key in response.headers}
        assert {"x-content-type-options", "x-frame-options"} <= headers

    def test_a_request_id_is_returned_and_echoed(self, http: HttpClient) -> None:
        """Correlates a client-side failure with a server log line."""
        generated = http.get("/products", authenticate=False)
        assert "x-request-id" in {key.lower() for key in generated.headers}

        supplied = http.get(
            "/products", headers={"X-Request-ID": "trace-me-0001"}, authenticate=False
        )
        echoed = {key.lower(): value for key, value in supplied.headers.items()}
        assert echoed["x-request-id"] == "trace-me-0001"

    def test_the_server_does_not_advertise_its_version(self, http: HttpClient) -> None:
        headers = {
            key.lower(): value
            for key, value in http.get("/products", authenticate=False).headers.items()
        }
        server = headers.get("server", "")
        assert not re.search(
            r"\d+\.\d+", server
        ), f"The Server header exposes a version: {server!r}"

    def test_cors_does_not_reflect_an_arbitrary_origin(self, http: HttpClient) -> None:
        """Reflecting any Origin would defeat the same-origin policy entirely.

        With credentials enabled it would let any site read authenticated
        responses on a visitor's behalf.
        """
        response = http.get(
            "/products", headers={"Origin": "https://evil.example"}, authenticate=False
        )
        allowed = {k.lower(): v for k, v in response.headers.items()}.get(
            "access-control-allow-origin"
        )
        assert allowed != "https://evil.example", "The API reflected an arbitrary Origin"
        assert allowed != "*" or "access-control-allow-credentials" not in {
            k.lower() for k in response.headers
        }, "Wildcard CORS combined with credentials"


# ---------------------------------------------------------------------------
@allure.story("Excessive data exposure")
class TestDataExposure:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_no_endpoint_returns_a_credential(
        self,
        product_client: ProductClient,
        cart_client: CartClient,
        order_client: OrderClient,
        auth_client: AuthClient,
        admin_client: AdminClient,
        customer,
        customer_with_address,
        product_factory,
    ) -> None:
        """Sweeps the main read surface for anything credential-shaped.

        Serialising a whole ORM object by accident is the usual way a password
        hash reaches a client, and it is invisible until somebody looks.
        """
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)
        order = order_client.checkout(address_id=address_id).assert_status(201).body

        responses = {
            "products": product_client.list(page_size=5).body,
            "product": product_client.get(product["id"]).body,
            "categories": product_client.categories().body,
            "me": auth_client.me(token=customer.token).body,
            "cart": cart_client.get().body,
            "orders": order_client.list().body,
            "order": order_client.get(order["id"]).body,
            "admin-users": admin_client.users(page_size=5).body,
            "admin-orders": admin_client.orders(page_size=5).body,
            "admin-stats": admin_client.stats().body,
        }

        for name, body in responses.items():
            leaked = find_forbidden_keys(body)
            assert not leaked, f"{name} exposes {leaked}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_only_the_last_four_card_digits_are_ever_returned(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
    ) -> None:
        from tests.api.clients import CARD_APPROVED

        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)

        response = order_client.checkout(address_id=address_id, card_number=CARD_APPROVED)
        response.assert_status(201)

        assert CARD_APPROVED not in response.raw_text, "The full PAN was returned"
        assert CARD_APPROVED[:12] not in response.raw_text, "Part of the PAN was returned"
        for payment in response.body["payments"]:
            assert payment["card_last4"] == CARD_APPROVED[-4:]
            assert len(payment["card_last4"]) == 4

    def test_the_database_stores_no_more_than_four_card_digits(self, db) -> None:
        """Asserted at the storage layer, not only at the API.

        A response that happens to omit the number tells you nothing about what
        was written to disk.
        """
        assert (
            db.payment_card_numbers_stored() == []
        ), "A payments row holds more than four card digits"

    def test_a_password_never_reaches_the_database_in_plaintext(
        self, auth_client: AuthClient, db
    ) -> None:
        email = unique_email("hashcheck")
        password = "VerySpecific123!"
        auth_client.register(email=email, password=password).assert_status(201)

        row = db.user_by_email(email)
        assert row is not None
        assert row["password_hash"] != password
        assert password not in row["password_hash"]
        assert row["password_hash"].startswith("$2"), "The stored value is not a bcrypt hash"

    def test_the_token_payload_carries_no_secret(self, customer) -> None:
        claims = decode_without_verification(customer.token)
        assert set(claims) <= {
            "sub",
            "email",
            "role",
            "type",
            "iat",
            "exp",
            "jti",
        }, f"The token carries unexpected claims: {sorted(claims)}"
        assert settings.jwt_secret not in str(claims)
        assert not find_forbidden_keys(claims)

    def test_the_admin_user_list_does_not_expose_hashes(self, admin_client: AdminClient) -> None:
        """Even an administrator has no business seeing password hashes."""
        response = admin_client.users(page_size=20)
        response.assert_status(200)
        assert not find_forbidden_keys(response.body)
        assert "$2b$" not in response.raw_text


# ---------------------------------------------------------------------------
@allure.story("Error responses")
class TestErrorDisclosure:
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        "case", ["not-found", "validation", "unauthorised", "forbidden", "conflict", "bad-route"]
    )
    def test_errors_never_leak_internals(
        self,
        http: HttpClient,
        product_client: ProductClient,
        customer_http: HttpClient,
        cart_client: CartClient,
        case: str,
    ) -> None:
        """A stack trace in a response body is a map of the application.

        It names files, line numbers, library versions and sometimes SQL - all
        of which make the next attack cheaper.
        """
        triggers = {
            "not-found": lambda: product_client.get(99_999_999),
            "validation": lambda: product_client.list(min_price=900, max_price=1),
            "unauthorised": lambda: http.get("/cart", authenticate=False),
            "forbidden": lambda: customer_http.get("/admin/users"),
            "conflict": lambda: cart_client.add_item(99_999_999, 1),
            "bad-route": lambda: http.get("/api/v1/nope", authenticate=False),
        }
        response = triggers[case]()

        assert response.status_code >= 400
        for marker in LEAK_MARKERS:
            assert (
                marker not in response.raw_text
            ), f"{case} leaked {marker!r}:\n{response.raw_text[:400]}"
        assert set(response.body) == {"error", "message", "details"}

    def test_a_malformed_json_body_is_rejected_cleanly(self, http: HttpClient, customer) -> None:
        response = http.request(
            "POST",
            "/cart/items",
            headers={"Content-Type": "application/json"},
            token=customer.token,
            json_body=None,
        )
        assert response.status_code in (400, 422)
        assert "Traceback" not in response.raw_text

    def test_a_wrong_method_returns_405_in_the_standard_envelope(self, http: HttpClient) -> None:
        response = http.delete("/products", authenticate=False)
        response.assert_status(405)
        assert set(response.body) == {"error", "message", "details"}
        assert response.body["error"] == "METHOD_NOT_ALLOWED"


# ---------------------------------------------------------------------------
@allure.story("Account enumeration")
class TestEnumeration:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_login_does_not_reveal_whether_an_address_is_registered(
        self, auth_client: AuthClient, customer
    ) -> None:
        unknown = auth_client.login(unique_email("ghost"), "AnyPassword123!")
        wrong_password = auth_client.login(customer.email, "AnyPassword123!")

        assert unknown.status_code == wrong_password.status_code == 401
        assert (
            unknown.body == wrong_password.body
        ), "The two failures differ, which makes address enumeration possible"

    def test_the_timing_of_the_two_failures_is_comparable(
        self, auth_client: AuthClient, customer
    ) -> None:
        """A timing oracle is enumeration by another route.

        If a missing account short-circuited before password verification, it
        would return measurably faster. The application verifies against a dummy
        hash instead. The tolerance is wide because wall-clock timing on a
        shared runner is noisy - this catches an order-of-magnitude gap, not a
        microsecond one.
        """
        unknown = [
            auth_client.login(unique_email("ghost"), "AnyPassword123!").elapsed_ms for _ in range(5)
        ]
        wrong = [auth_client.login(customer.email, "AnyPassword123!").elapsed_ms for _ in range(5)]

        median_unknown = sorted(unknown)[len(unknown) // 2]
        median_wrong = sorted(wrong)[len(wrong) // 2]
        ratio = max(median_unknown, median_wrong) / max(1.0, min(median_unknown, median_wrong))

        assert ratio < 10, (
            f"Unknown-account logins took {median_unknown:.0f}ms and wrong-password "
            f"{median_wrong:.0f}ms - a {ratio:.1f}x gap is an enumeration oracle"
        )

    def test_registration_reveals_only_that_the_address_is_taken(
        self, auth_client: AuthClient, customer
    ) -> None:
        """This one *must* disclose existence - a signup form cannot work
        otherwise - but it must disclose nothing further.
        """
        response = auth_client.register(email=customer.email, password="AnotherPass123!")
        response.assert_error("EMAIL_ALREADY_REGISTERED", 409)
        assert customer.full_name not in response.raw_text
        assert not find_forbidden_keys(response.body)


# ---------------------------------------------------------------------------
@allure.story("Exposed surface")
class TestExposedSurface:
    def test_the_interactive_docs_are_reachable_by_design(self, http: HttpClient) -> None:
        """A deliberate decision, recorded rather than left implicit.

        `/docs` and `/openapi.json` are public here because this is a portfolio
        application whose API is meant to be explored, and because the contract
        suite validates against the live spec. A real deployment would put both
        behind authentication or strip them in production - noted in the
        README's "Known limitations".
        """
        for path in ("/docs", "/openapi.json", "/redoc"):
            response = http.get(f"{settings.api_base_url}{path}", authenticate=False)
            assert response.status_code == 200, f"{path} returned {response.status_code}"

    def test_the_health_probes_reveal_no_internals(self, http: HttpClient) -> None:
        """A readiness probe must say ready or not ready, not why in detail.

        Connection strings, hostnames and versions in a public probe are free
        reconnaissance.
        """
        response = http.get(f"{settings.api_base_url}/health/ready", authenticate=False)
        response.assert_status(200)
        assert set(response.body) == {"status", "checks"}
        for marker in ("postgresql://", "password", "5432", settings.jwt_secret):
            assert marker not in response.raw_text, f"The readiness probe leaked {marker!r}"

    def test_the_liveness_probe_needs_no_authentication(self, http: HttpClient) -> None:
        http.get(f"{settings.api_base_url}/health", authenticate=False).assert_status(200)
