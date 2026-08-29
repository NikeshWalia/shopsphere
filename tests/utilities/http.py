"""HTTP plumbing shared by every API client.

`ApiResponse` wraps httpx's response with the two things assertions repeatedly
need - the parsed body and the elapsed time - plus assertion helpers that
produce a readable failure message instead of `assert 409 == 201`.

Every request and response is attached to the Allure report, so a failure in CI
can be diagnosed from the report alone without re-running anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import allure
import httpx

from tests.configuration.settings import settings

# Header and body keys whose values must never reach a report artifact.
REDACT_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key"}
REDACT_FIELDS = {
    "password",
    "password_confirm",
    "current_password",
    "new_password",
    "access_token",
    "card_number",
    "cvv",
}


def _redact_headers(headers: Any) -> dict[str, str]:
    return {
        key: ("***redacted***" if key.lower() in REDACT_HEADERS else value)
        for key, value in dict(headers).items()
    }


def _redact_body(body: Any) -> Any:
    if isinstance(body, dict):
        return {
            key: ("***redacted***" if key.lower() in REDACT_FIELDS else _redact_body(value))
            for key, value in body.items()
        }
    if isinstance(body, list):
        return [_redact_body(item) for item in body]
    return body


@dataclass(frozen=True)
class ApiResponse:
    """A response plus the context needed to assert on it and report it."""

    status_code: int
    body: Any
    headers: dict[str, str]
    elapsed_ms: float
    method: str
    url: str
    raw_text: str

    # -- Convenience -------------------------------------------------------
    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def error_code(self) -> str | None:
        """The API's machine-readable error code, if this is an error body."""
        if isinstance(self.body, dict):
            code = self.body.get("error")
            return code if isinstance(code, str) else None
        return None

    @property
    def error_message(self) -> str:
        if isinstance(self.body, dict):
            message = self.body.get("message")
            if isinstance(message, str):
                return message
        return self.raw_text[:300]

    @property
    def details(self) -> dict[str, Any]:
        if isinstance(self.body, dict) and isinstance(self.body.get("details"), dict):
            return self.body["details"]
        return {}

    def json(self) -> Any:
        return self.body

    def _context(self) -> str:
        return f"{self.method} {self.url} -> {self.status_code} in {self.elapsed_ms:.0f}ms\n{self.raw_text[:600]}"

    # -- Assertions --------------------------------------------------------
    def assert_status(self, expected: int) -> ApiResponse:
        assert (
            self.status_code == expected
        ), f"Expected HTTP {expected}, got {self.status_code}.\n{self._context()}"
        return self

    def assert_status_in(self, *expected: int) -> ApiResponse:
        assert (
            self.status_code in expected
        ), f"Expected one of {expected}, got {self.status_code}.\n{self._context()}"
        return self

    def assert_ok(self) -> ApiResponse:
        assert self.ok, f"Expected a 2xx response, got {self.status_code}.\n{self._context()}"
        return self

    def assert_error(self, code: str, status: int | None = None) -> ApiResponse:
        """Assert the standard error envelope with a specific error code."""
        if status is not None:
            self.assert_status(status)
        assert isinstance(self.body, dict), f"Expected a JSON error body.\n{self._context()}"
        assert set(self.body) >= {
            "error",
            "message",
            "details",
        }, f"Error body is missing envelope keys; got {sorted(self.body)}.\n{self._context()}"
        assert (
            self.error_code == code
        ), f"Expected error code {code!r}, got {self.error_code!r}.\n{self._context()}"
        return self

    def assert_faster_than(self, budget_ms: int | None = None) -> ApiResponse:
        """Guard against pathological slowness. Not a performance benchmark."""
        limit = budget_ms if budget_ms is not None else settings.api_sla_ms
        assert (
            self.elapsed_ms <= limit
        ), f"Response took {self.elapsed_ms:.0f}ms, budget is {limit}ms.\n{self._context()}"
        return self

    def assert_has_keys(self, *keys: str) -> ApiResponse:
        assert isinstance(self.body, dict), f"Expected a JSON object.\n{self._context()}"
        missing = [key for key in keys if key not in self.body]
        assert not missing, f"Response is missing keys {missing}.\n{self._context()}"
        return self

    def assert_header(self, name: str, expected: str | None = None) -> ApiResponse:
        lowered = {key.lower(): value for key, value in self.headers.items()}
        assert (
            name.lower() in lowered
        ), f"Response is missing the {name!r} header. Present: {sorted(lowered)}"
        if expected is not None:
            actual = lowered[name.lower()]
            assert actual == expected, f"Header {name}: expected {expected!r}, got {actual!r}"
        return self


class HttpClient:
    """Thin wrapper over httpx that records timing and reports every call."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.api_url).rstrip("/")
        self.token = token
        self._client = httpx.Client(
            timeout=timeout if timeout is not None else settings.api_timeout_seconds,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def with_token(self, token: str | None) -> HttpClient:
        """Return a client authenticated as somebody else.

        Returns a new instance rather than mutating this one: a test that
        borrows a client to make one unauthenticated call must not leave the
        shared client logged out.
        """
        return HttpClient(self.base_url, token=token)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        token: str | None = None,
        authenticate: bool = True,
    ) -> ApiResponse:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"

        final_headers: dict[str, str] = {"Accept": "application/json"}
        effective_token = token if token is not None else (self.token if authenticate else None)
        if effective_token:
            final_headers["Authorization"] = f"Bearer {effective_token}"
        if headers:
            final_headers.update(headers)

        response = self._client.request(
            method.upper(), url, json=json_body, params=params, headers=final_headers
        )

        try:
            body: Any = response.json() if response.content else None
        except ValueError:
            body = None

        result = ApiResponse(
            status_code=response.status_code,
            body=body,
            headers=dict(response.headers),
            elapsed_ms=response.elapsed.total_seconds() * 1000,
            method=method.upper(),
            url=str(response.url),
            raw_text=response.text,
        )
        self._attach(result, request_headers=final_headers, request_body=json_body, params=params)
        return result

    @staticmethod
    def _attach(
        response: ApiResponse,
        *,
        request_headers: dict[str, str],
        request_body: Any,
        params: dict[str, Any] | None,
    ) -> None:
        """Attach the exchange to the Allure report, with secrets removed."""
        try:
            request_payload = {
                "method": response.method,
                "url": response.url,
                "params": params or {},
                "headers": _redact_headers(request_headers),
                "body": _redact_body(request_body),
            }
            allure.attach(
                json.dumps(request_payload, indent=2, default=str),
                name=f"{response.method} {response.url} - request",
                attachment_type=allure.attachment_type.JSON,
            )
            response_payload = {
                "status_code": response.status_code,
                "elapsed_ms": round(response.elapsed_ms, 1),
                "headers": _redact_headers(response.headers),
                "body": _redact_body(response.body),
            }
            allure.attach(
                json.dumps(response_payload, indent=2, default=str),
                name=f"{response.method} {response.url} - response ({response.status_code})",
                attachment_type=allure.attachment_type.JSON,
            )
        except Exception:  # noqa: S110
            # Reporting must never be the reason a test fails: a broken
            # attachment should cost a report entry, not a red build.
            pass

    # -- Verb helpers ------------------------------------------------------
    def get(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("DELETE", path, **kwargs)
