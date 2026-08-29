"""API tests for registration, login, tokens and profile management.

The rules under test: an account can be created exactly once per address, a
token is only issued for correct credentials, and the endpoint cannot be used to
discover which email addresses are registered.
"""

from __future__ import annotations

import uuid
from typing import Any

import allure
import pytest

from tests.api.clients import AuthClient
from tests.test_data.factories import (
    DEFAULT_PASSWORD,
    INVALID_EMAILS,
    WEAK_PASSWORDS,
    unique_email,
)
from tests.utilities.http import ApiResponse
from tests.utilities.tokens import MALFORMED_TOKENS, expired_token, wrong_signature_token

pytestmark = [allure.epic("Identity"), allure.feature("Authentication")]

# Every credential-shaped key that must never appear in a response body.
SECRET_KEYS = ("password", "password_confirm", "password_hash", "hashed_password", "secret")


def assert_envelope(response: ApiResponse) -> None:
    """Every error, from every layer, uses one shape."""
    assert isinstance(response.body, dict), f"Expected a JSON object, got {response.raw_text[:200]}"
    assert set(response.body) == {
        "error",
        "message",
        "details",
    }, f"Error envelope has keys {sorted(response.body)}; expected error/message/details"
    assert isinstance(response.body["error"], str)
    assert isinstance(response.body["message"], str)
    assert isinstance(response.body["details"], dict)


# ---------------------------------------------------------------------------
@allure.story("Registration")
class TestRegistration:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_a_new_account_is_created_and_signed_in(self, auth_client: AuthClient) -> None:
        """Registration returns a usable token.

        Returning a token means the client does not have to make a second login
        call, so a network blip between the two cannot leave a customer with an
        account they appear not to be signed into.
        """
        email = unique_email()
        response = auth_client.register(
            email=email, password=DEFAULT_PASSWORD, full_name="Ada Lovelace"
        )

        response.assert_status(201).assert_has_keys(
            "access_token", "token_type", "expires_in", "user"
        )
        assert response.body["token_type"] == "bearer"
        assert response.body["expires_in"] == settings_expiry_seconds()
        user = response.body["user"]
        assert user["email"] == email
        assert user["full_name"] == "Ada Lovelace"
        assert user["is_active"] is True
        assert user["id"] > 0

        # The token must actually work, not merely be present.
        auth_client.me(token=response.body["access_token"]).assert_status(200)

    def test_new_accounts_are_always_customers(self, auth_client: AuthClient) -> None:
        """Registration must not be a route to privilege.

        Sending role=admin is the obvious attempt; the field is not part of the
        schema, so it is discarded rather than honoured.
        """
        response = auth_client.register_raw(
            {
                "email": unique_email(),
                "password": DEFAULT_PASSWORD,
                "password_confirm": DEFAULT_PASSWORD,
                "full_name": "Would-be Admin",
                "role": "admin",
                "is_active": True,
            }
        )
        response.assert_status(201)
        assert response.body["user"]["role"] == "customer"

    def test_no_credential_is_echoed_back(self, auth_client: AuthClient) -> None:
        response = auth_client.register(email=unique_email(), password=DEFAULT_PASSWORD)
        response.assert_status(201)
        raw = response.raw_text.lower()
        assert DEFAULT_PASSWORD.lower() not in raw, "The password was echoed in the response"
        for key in SECRET_KEYS:
            assert key not in response.body.get("user", {}), f"Response leaks {key!r}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_the_same_address_cannot_register_twice(self, auth_client: AuthClient) -> None:
        email = unique_email()
        auth_client.register(email=email, password=DEFAULT_PASSWORD).assert_status(201)

        second = auth_client.register(email=email, password=DEFAULT_PASSWORD)
        second.assert_error("EMAIL_ALREADY_REGISTERED", 409)
        assert_envelope(second)

    def test_addresses_are_case_insensitive(self, auth_client: AuthClient) -> None:
        """`Ada@Example.com` and `ada@example.com` are one account, not two.

        Treating them as distinct would let a customer accidentally create a
        second account and lose their order history.
        """
        local = f"MiXeD_{uuid.uuid4().hex[:10]}"
        upper = f"{local}@ShopSphere.TEST"

        created = auth_client.register(email=upper, password=DEFAULT_PASSWORD)
        created.assert_status(201)
        assert created.body["user"]["email"] == upper.lower()

        auth_client.register(email=upper.lower(), password=DEFAULT_PASSWORD).assert_error(
            "EMAIL_ALREADY_REGISTERED", 409
        )
        # ...and the original credentials work when typed in either case.
        auth_client.login(upper.lower(), DEFAULT_PASSWORD).assert_status(200)

    @pytest.mark.parametrize("email", INVALID_EMAILS, ids=lambda value: (value or "empty")[:24])
    def test_malformed_addresses_are_rejected(self, auth_client: AuthClient, email: str) -> None:
        response = auth_client.register(email=email, password=DEFAULT_PASSWORD)
        response.assert_error("VALIDATION_ERROR", 422)
        assert_envelope(response)

    @pytest.mark.parametrize(
        ("label", "password"), WEAK_PASSWORDS, ids=[w[0] for w in WEAK_PASSWORDS]
    )
    def test_weak_passwords_are_rejected_with_the_reason(
        self, auth_client: AuthClient, label: str, password: str
    ) -> None:
        """The policy is stated in the error, so a UI can act on it.

        "Invalid password" alone turns signup into a guessing game.
        """
        response = auth_client.register(email=unique_email(), password=password)
        response.assert_error("VALIDATION_ERROR", 422)
        assert "password" in response.raw_text.lower()

    def test_a_mismatched_confirmation_is_rejected(self, auth_client: AuthClient) -> None:
        response = auth_client.register(
            email=unique_email(), password=DEFAULT_PASSWORD, password_confirm="Different123!"
        )
        response.assert_error("VALIDATION_ERROR", 422)
        assert "match" in response.error_message.lower()

    @pytest.mark.parametrize("missing", ["email", "password", "password_confirm", "full_name"])
    def test_every_required_field_is_required(self, auth_client: AuthClient, missing: str) -> None:
        payload: dict[str, Any] = {
            "email": unique_email(),
            "password": DEFAULT_PASSWORD,
            "password_confirm": DEFAULT_PASSWORD,
            "full_name": "Ada Lovelace",
        }
        payload.pop(missing)

        response = auth_client.register_raw(payload)
        response.assert_error("VALIDATION_ERROR", 422)
        fields = {entry["field"] for entry in response.details.get("fields", [])}
        assert missing in fields, f"The error does not name the missing field. Got {fields}"

    def test_validation_errors_name_every_offending_field(self, auth_client: AuthClient) -> None:
        """A form should be fixable in one pass, not one field at a time."""
        response = auth_client.register_raw(
            {"email": "not-an-email", "password": "x", "password_confirm": "y", "full_name": "A"}
        )
        response.assert_error("VALIDATION_ERROR", 422)
        fields = {entry["field"] for entry in response.details["fields"]}
        assert {"email", "password", "full_name"} <= fields, f"Only reported {fields}"

    def test_surrounding_whitespace_in_a_name_is_trimmed(self, auth_client: AuthClient) -> None:
        response = auth_client.register(
            email=unique_email(), password=DEFAULT_PASSWORD, full_name="  Ada Lovelace  "
        )
        response.assert_status(201)
        assert response.body["user"]["full_name"] == "Ada Lovelace"


# ---------------------------------------------------------------------------
@allure.story("Login")
class TestLogin:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_correct_credentials_return_a_working_token(
        self, auth_client: AuthClient, customer
    ) -> None:
        response = auth_client.login(customer.email, customer.password)
        response.assert_status(200).assert_has_keys(
            "access_token", "token_type", "expires_in", "user"
        )
        assert response.body["user"]["id"] == customer.id
        auth_client.me(token=response.body["access_token"]).assert_status(200)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_wrong_password_is_rejected(self, auth_client: AuthClient, customer) -> None:
        response = auth_client.login(customer.email, "DefinitelyWrong123!")
        response.assert_error("INVALID_CREDENTIALS", 401)
        assert_envelope(response)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_an_unknown_account_returns_the_identical_error(
        self, auth_client: AuthClient, customer
    ) -> None:
        """No user enumeration.

        If "no such account" and "wrong password" were distinguishable, this
        endpoint would become a free tool for discovering which of a leaked
        address list are registered here.
        """
        unknown = auth_client.login(unique_email("ghost"), DEFAULT_PASSWORD)
        wrong_password = auth_client.login(customer.email, "DefinitelyWrong123!")

        unknown.assert_error("INVALID_CREDENTIALS", 401)
        wrong_password.assert_error("INVALID_CREDENTIALS", 401)
        assert (
            unknown.body["message"] == wrong_password.body["message"]
        ), "The two failures are distinguishable by message, which enables enumeration"
        assert unknown.body["details"] == wrong_password.body["details"]

    def test_login_is_case_insensitive_on_the_address(
        self, auth_client: AuthClient, customer
    ) -> None:
        auth_client.login(customer.email.upper(), customer.password).assert_status(200)

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("no-password", {"email": "someone@shopsphere.test"}),
            ("no-email", {"password": DEFAULT_PASSWORD}),
            ("empty-body", {}),
            ("null-email", {"email": None, "password": DEFAULT_PASSWORD}),
            ("wrong-types", {"email": 12345, "password": ["a"]}),
            ("empty-password", {"email": "someone@shopsphere.test", "password": ""}),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_malformed_requests_are_rejected(
        self, auth_client: AuthClient, label: str, payload: dict[str, Any]
    ) -> None:
        response = auth_client.login_raw(payload)
        response.assert_status(422)
        assert_envelope(response)

    def test_login_responds_within_the_budget(self, auth_client: AuthClient, customer) -> None:
        """A regression guard, not a benchmark.

        bcrypt verification is intentionally slow; this only catches a change
        that makes it pathologically so.
        """
        auth_client.login(customer.email, customer.password).assert_status(200).assert_faster_than()


# ---------------------------------------------------------------------------
@allure.story("Token handling")
class TestTokens:
    def test_the_current_user_is_returned(self, auth_client: AuthClient, customer) -> None:
        response = auth_client.me(token=customer.token)
        response.assert_status(200)
        assert response.body["id"] == customer.id
        assert response.body["email"] == customer.email
        assert response.body["role"] == "customer"
        for key in SECRET_KEYS:
            assert key not in response.body, f"/auth/me leaks {key!r}"

    def test_a_missing_token_is_rejected(self, http) -> None:
        response = http.get("/auth/me", authenticate=False)
        response.assert_status(401)
        assert_envelope(response)

    @pytest.mark.parametrize("label,token", MALFORMED_TOKENS, ids=[m[0] for m in MALFORMED_TOKENS])
    def test_malformed_tokens_are_rejected(
        self, auth_client: AuthClient, http, label: str, token: str
    ) -> None:
        if token:
            response = auth_client.me(token=token)
        else:
            # An empty token means the client has nothing stored, so it sends no
            # header at all. `Authorization: Bearer ` is not merely rejected by
            # the server - httpx refuses to transmit a header with a trailing
            # space, because it is not a legal header value.
            response = http.get("/auth/me", authenticate=False)

        response.assert_status(401)
        assert response.error_code in (
            "INVALID_TOKEN",
            "AUTHENTICATION_FAILED",
        ), response.error_code

    @allure.severity(allure.severity_level.CRITICAL)
    def test_an_expired_token_is_rejected(self, auth_client: AuthClient, customer) -> None:
        """Minted already-expired rather than waiting an hour for a real one.

        Sleeping until expiry would make this the slowest test in the suite and
        the first to fail on a loaded CI runner.
        """
        response = auth_client.me(token=expired_token(user_id=customer.id))
        response.assert_error("TOKEN_EXPIRED", 401)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_token_signed_with_another_key_is_rejected(
        self, auth_client: AuthClient, customer
    ) -> None:
        auth_client.me(token=wrong_signature_token(user_id=customer.id)).assert_status(401)

    def test_a_tampered_payload_invalidates_the_signature(
        self, auth_client: AuthClient, customer
    ) -> None:
        """Editing a single character of the claims must break verification."""
        header, payload, signature = customer.token.split(".")
        flipped = "A" if payload[10] != "A" else "B"
        tampered = f"{header}.{payload[:10]}{flipped}{payload[11:]}.{signature}"

        auth_client.me(token=tampered).assert_status(401)

    @pytest.mark.parametrize(
        ("label", "header"),
        [
            ("basic-scheme", "Basic dXNlcjpwYXNz"),
            ("token-scheme", "Token abc.def.ghi"),
            ("no-scheme", "abc.def.ghi"),
            ("bearer-only", "Bearer"),
            ("empty", ""),
            ("double-bearer", "Bearer Bearer abc.def.ghi"),
        ],
        ids=lambda value: value if isinstance(value, str) and " " not in value else "",
    )
    def test_malformed_authorization_headers_are_rejected(
        self, auth_client: AuthClient, label: str, header: str
    ) -> None:
        auth_client.me_with_header(header).assert_status(401)


# ---------------------------------------------------------------------------
@allure.story("Profile and password")
class TestProfile:
    def test_the_profile_can_be_updated(self, auth_client: AuthClient, customer) -> None:
        response = auth_client.update_profile(
            {"full_name": "Ada King", "phone": "+1-555-0199"}, token=customer.token
        )
        response.assert_status(200)
        assert response.body["full_name"] == "Ada King"
        assert response.body["phone"] == "+1-555-0199"

        # Persisted, not merely echoed.
        assert auth_client.me(token=customer.token).body["full_name"] == "Ada King"

    def test_an_empty_update_is_rejected(self, auth_client: AuthClient, customer) -> None:
        auth_client.update_profile({}, token=customer.token).assert_status(422)

    def test_the_profile_cannot_be_updated_anonymously(self, http) -> None:
        http.patch("/auth/me", json_body={"full_name": "Nobody"}, authenticate=False).assert_status(
            401
        )

    @allure.severity(allure.severity_level.CRITICAL)
    def test_changing_the_password_invalidates_the_old_one(
        self, auth_client: AuthClient, customer
    ) -> None:
        new_password = "BrandNewPass456!"
        auth_client.change_password(
            customer.password, new_password, token=customer.token
        ).assert_status(200)

        auth_client.login(customer.email, customer.password).assert_error(
            "INVALID_CREDENTIALS", 401
        )
        auth_client.login(customer.email, new_password).assert_status(200)

    def test_the_current_password_must_be_correct(self, auth_client: AuthClient, customer) -> None:
        response = auth_client.change_password(
            "NotMyPassword1!", "BrandNewPass456!", token=customer.token
        )
        response.assert_status(401)
        # The original password must still work - a failed change changes nothing.
        auth_client.login(customer.email, customer.password).assert_status(200)

    def test_the_new_password_must_satisfy_the_policy(
        self, auth_client: AuthClient, customer
    ) -> None:
        auth_client.change_password(customer.password, "weak", token=customer.token).assert_status(
            422
        )

    def test_the_new_password_must_actually_be_different(
        self, auth_client: AuthClient, customer
    ) -> None:
        response = auth_client.change_password(
            customer.password, customer.password, token=customer.token
        )
        response.assert_status(422)


# ---------------------------------------------------------------------------
@allure.story("Logout")
class TestLogout:
    def test_logout_acknowledges_and_documents_the_trade_off(
        self, auth_client: AuthClient, customer
    ) -> None:
        """Logout is client-side token disposal, and the token stays valid.

        This is the documented consequence of stateless JWTs: there is no
        server-side session to destroy, so a token remains usable until it
        expires. Asserting it here makes the trade-off explicit rather than an
        unstated surprise - see "Known limitations" in the README.
        """
        auth_client.logout(token=customer.token).assert_status(200)
        auth_client.me(token=customer.token).assert_status(200)

    def test_logout_requires_a_session(self, http) -> None:
        http.post("/auth/logout", authenticate=False).assert_status(401)


def settings_expiry_seconds() -> int:
    """The token lifetime the API advertises, derived from configuration.

    Read from settings rather than hardcoded so the assertion stays true when
    CI shortens the lifetime.
    """
    import os

    return int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")) * 60
