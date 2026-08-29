"""Authentication and account API client."""

from __future__ import annotations

from typing import Any

from tests.api.clients.base import BaseClient
from tests.utilities.http import ApiResponse


class AuthClient(BaseClient):
    def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str = "Test User",
        password_confirm: str | None = None,
        phone: str | None = None,
        **overrides: Any,
    ) -> ApiResponse:
        payload: dict[str, Any] = {
            "email": email,
            "password": password,
            # Defaults to matching, so a test that cares about the mismatch rule
            # must opt into it explicitly and reads unambiguously.
            "password_confirm": password_confirm if password_confirm is not None else password,
            "full_name": full_name,
        }
        if phone is not None:
            payload["phone"] = phone
        payload.update(overrides)
        return self._post("/auth/register", json_body=payload, authenticate=False)

    def register_raw(self, payload: dict[str, Any]) -> ApiResponse:
        """Send an arbitrary body, for validation and malformed-input tests."""
        return self._post("/auth/register", json_body=payload, authenticate=False)

    def login(self, email: str, password: str) -> ApiResponse:
        return self._post(
            "/auth/login", json_body={"email": email, "password": password}, authenticate=False
        )

    def login_raw(self, payload: dict[str, Any]) -> ApiResponse:
        return self._post("/auth/login", json_body=payload, authenticate=False)

    def me(self, *, token: str | None = None) -> ApiResponse:
        return self._get("/auth/me", token=token)

    def me_with_header(self, authorization: str) -> ApiResponse:
        """Send a raw Authorization header value.

        Needed for the malformed-header cases ("Basic ...", "Bearer", no scheme)
        that the normal token path cannot express.
        """
        return self.http.request(
            "GET", "/auth/me", headers={"Authorization": authorization}, authenticate=False
        )

    def logout(self, *, token: str | None = None) -> ApiResponse:
        return self._post("/auth/logout", token=token)

    def update_profile(self, payload: dict[str, Any], *, token: str | None = None) -> ApiResponse:
        return self._patch("/auth/me", json_body=payload, token=token)

    def change_password(
        self, current_password: str, new_password: str, *, token: str | None = None
    ) -> ApiResponse:
        return self._post(
            "/auth/me/password",
            json_body={"current_password": current_password, "new_password": new_password},
            token=token,
        )

    # -- Convenience -------------------------------------------------------
    def token_for(self, email: str, password: str) -> str:
        """Log in and return the token, failing loudly if that is not possible.

        Used by fixtures that need a session and for which a failed login is a
        setup error rather than the thing under test.
        """
        response = self.login(email, password)
        assert (
            response.status_code == 200
        ), f"Could not authenticate {email}: HTTP {response.status_code} {response.raw_text[:200]}"
        return str(response.body["access_token"])
