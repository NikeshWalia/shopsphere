"""Test data factories.

The isolation strategy in one sentence: **every test that needs mutable state
creates its own, with a globally unique identity.**

Uniqueness comes from a UUID rather than from a counter or a cleanup step,
because the suite runs under pytest-xdist across several processes and repeatedly
against the same database. A counter would collide across workers; relying on
cleanup would break the moment a test failed before its teardown ran.

Faker supplies realistic names and addresses. It is *not* used for anything a
test asserts on - a test that depended on a random value would fail
unpredictably. Randomness is for realism; assertions are on values the test
chose deliberately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from faker import Faker

fake = Faker("en_US")

# RFC 2606 reserves .test for exactly this. Using a domain that might belong to
# somebody would risk generated addresses reaching a real inbox.
TEST_EMAIL_DOMAIN = "shopsphere.test"

# Satisfies the password policy (length, upper, lower, digit) and is shared by
# generated users so a test can log back in without tracking the value.
DEFAULT_PASSWORD = "TestPass123!"


def unique_suffix() -> str:
    return uuid.uuid4().hex[:12]


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}_{unique_suffix()}@{TEST_EMAIL_DOMAIN}"


def unique_sku(prefix: str = "TST") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def unique_idempotency_key() -> str:
    return uuid.uuid4().hex


@dataclass
class UserSpec:
    """A user to be created through the public registration endpoint.

    Created via the API rather than by inserting rows: a user manufactured
    behind the application's back would not have gone through password hashing
    or role assignment, so tests using it would prove nothing about the real
    signup path.
    """

    email: str = field(default_factory=unique_email)
    password: str = DEFAULT_PASSWORD
    full_name: str = field(default_factory=fake.name)
    phone: str | None = None

    def registration_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "email": self.email,
            "password": self.password,
            "password_confirm": self.password,
            "full_name": self.full_name,
        }
        if self.phone:
            payload["phone"] = self.phone
        return payload


def user_spec(**overrides: Any) -> UserSpec:
    return UserSpec(**overrides)


def address_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": "Home",
        "full_name": fake.name(),
        "line1": fake.street_address(),
        "line2": None,
        "city": fake.city(),
        # A 2-letter state code; Faker's state_abbr matches the column width.
        "state": fake.state_abbr(),
        "postal_code": fake.postcode()[:10],
        "country": "US",
        "phone": "+1-555-0100",
        "is_default": False,
    }
    payload.update(overrides)
    return payload


def product_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sku": unique_sku(),
        "name": f"Test Product {unique_suffix()[:6]}",
        "description": "Created by the automated test suite.",
        "price": 49.99,
        "category_id": 1,
        "brand": "TestBrand",
        "rating": 4.0,
        "stock_quantity": 25,
        "is_active": True,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Parametrised datasets
# ---------------------------------------------------------------------------
# Kept here rather than inline in tests so the same data can drive several
# suites, and so adding a case is a one-line change in one place.

SEARCH_TERMS: tuple[tuple[str, bool], ...] = (
    # (term, expect_results)
    ("laptop", True),
    ("Laptop", True),
    ("LAPTOP", True),
    ("phone", True),
    ("wireless", True),
    ("headphones", True),
    ("aurora", True),
    ("ultrabook", True),
    ("LAP-1001", True),
    ("zzzzz-no-such-product", False),
    ("!!!!", False),
)

PRICE_RANGES: tuple[tuple[float | None, float | None], ...] = (
    (None, 50.0),
    (50.0, 200.0),
    (200.0, 1000.0),
    (1000.0, None),
    (0.0, 0.0),  # a valid but empty range
    (0.0, 1_000_000.0),  # the whole catalogue
)

SORT_OPTIONS: tuple[str, ...] = (
    "relevance",
    "price_asc",
    "price_desc",
    "rating_desc",
    "newest",
    "name_asc",
)

INVALID_QUANTITIES: tuple[tuple[str, Any], ...] = (
    ("zero", 0),
    ("negative", -1),
    ("large-negative", -9999),
    ("far-above-limit", 1_000_000),
    ("string", "two"),
    ("float", 1.5),
    ("null", None),
    ("boolean", True),
)

INVALID_EMAILS: tuple[str, ...] = (
    "",
    "plainstring",
    "no-at-sign.test",
    "@no-local-part.test",
    "spaces in@address.test",
    "double@@at.test",
    "trailing-dot@address.",
    "a" * 300 + "@shopsphere.test",
)

WEAK_PASSWORDS: tuple[tuple[str, str], ...] = (
    ("too-short", "Ab1!"),
    ("no-uppercase", "lowercase123"),
    ("no-lowercase", "UPPERCASE123"),
    ("no-digit", "NoDigitsHere"),
    ("empty", ""),
)

# (label, card number, expected HTTP status from POST /orders)
PAYMENT_OUTCOMES: tuple[tuple[str, str, int], ...] = (
    ("approved-visa", "4111111111111111", 201),
    ("approved-mastercard", "5555555555554444", 201),
    ("declined-insufficient-funds", "4000000000000002", 402),
    ("declined-expired-card", "4000000000000069", 402),
    ("declined-incorrect-cvc", "4000000000000127", 402),
    ("declined-do-not-honor", "4000000000009995", 402),
    ("provider-server-error", "4000000000000119", 502),
)

# Payloads that must be handled safely. The expectation is never "the app
# breaks" - it is that these are treated as ordinary text, stored or matched
# literally, and never executed or reflected as markup.
INJECTION_PAYLOADS: tuple[tuple[str, str], ...] = (
    ("sql-or-true", "' OR '1'='1"),
    ("sql-drop-table", "'; DROP TABLE products; --"),
    ("sql-union", "' UNION SELECT NULL, version() --"),
    ("sql-comment", "admin'--"),
    ("sql-time-based", "'; SELECT pg_sleep(5); --"),
    ("xss-script-tag", "<script>alert('xss')</script>"),
    ("xss-img-onerror", "<img src=x onerror=alert(1)>"),
    ("xss-svg", "<svg/onload=alert(1)>"),
    ("xss-javascript-uri", "javascript:alert(1)"),
    ("path-traversal", "../../../../etc/passwd"),
    ("null-byte", "test\x00.txt"),
    ("template-injection", "{{7*7}}"),
    ("like-wildcards", "%_%"),
    ("unicode-rtl-override", "‮test"),
)

PAGINATION_BOUNDARIES: tuple[tuple[str, dict[str, Any], int], ...] = (
    # (label, params, expected status)
    ("first-page", {"page": 1, "page_size": 1}, 200),
    ("max-page-size", {"page": 1, "page_size": 100}, 200),
    ("page-beyond-end", {"page": 9999, "page_size": 20}, 200),
    ("page-zero", {"page": 0}, 422),
    ("negative-page", {"page": -1}, 422),
    ("page-size-zero", {"page_size": 0}, 422),
    ("page-size-too-large", {"page_size": 101}, 422),
    ("page-not-a-number", {"page": "abc"}, 422),
)
