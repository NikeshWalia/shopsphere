"""Unit tests for the auth rate-limit middleware.

These drive the middleware's ``dispatch`` directly, with a hand-built request
scope, so there is no database, no bcrypt, no live stack - and, crucially, full
control over the client address, which the test client cannot vary. What is
under test is the throttling decision itself: after N attempts from one address
the next is refused with 429, a different address is unaffected, non-auth paths
are never touched, and the whole thing is a no-op under the test environment.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.middleware import AuthRateLimitMiddleware

LOGIN = "/api/v1/auth/login"
MAX = 3


@pytest.fixture
def configure() -> Iterator[None]:
    """Reset the cached settings around each test.

    The middleware reads ``get_settings()`` fresh per request, and that is
    ``lru_cache``d, so the cache is cleared afterwards to avoid leaking a
    doctored settings object into the rest of the suite.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _ok(_: Request) -> Response:
    return Response("ok", status_code=200)


def _send(
    mw: AuthRateLimitMiddleware, host: str, path: str = LOGIN, method: str = "POST"
) -> Response:
    """Run one request through the middleware from a chosen client address."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "headers": [],
        "query_string": b"",
        "client": (host, 5000),
        "server": ("testserver", 80),
        "scheme": "http",
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)
    return asyncio.run(mw.dispatch(request, _ok))


async def _noop_app(scope: object, receive: object, send: object) -> None:  # pragma: no cover
    return None


def _middleware() -> AuthRateLimitMiddleware:
    return AuthRateLimitMiddleware(_noop_app)


def _enable(
    monkeypatch: pytest.MonkeyPatch, environment: str = "local", enabled: str = "true"
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX_ATTEMPTS", str(MAX))
    monkeypatch.setenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "60")
    get_settings.cache_clear()


def test_login_is_throttled_after_the_limit(
    monkeypatch: pytest.MonkeyPatch, configure: None
) -> None:
    _enable(monkeypatch)
    mw = _middleware()

    allowed = [_send(mw, "10.0.0.1").status_code for _ in range(MAX)]
    assert allowed == [200] * MAX

    blocked = _send(mw, "10.0.0.1")
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]
    assert int(blocked.headers["Retry-After"]) >= 1
    assert json.loads(bytes(blocked.body))["error"] == "RATE_LIMITED"


def test_a_different_client_is_independent(
    monkeypatch: pytest.MonkeyPatch, configure: None
) -> None:
    _enable(monkeypatch)
    mw = _middleware()

    for _ in range(MAX + 1):
        _send(mw, "10.0.0.1")

    assert _send(mw, "10.0.0.1").status_code == 429  # attacker still blocked
    assert _send(mw, "10.0.0.2").status_code == 200  # bystander unaffected


def test_non_auth_paths_are_never_throttled(
    monkeypatch: pytest.MonkeyPatch, configure: None
) -> None:
    _enable(monkeypatch)
    mw = _middleware()

    codes = {
        _send(mw, "10.0.0.1", path="/api/v1/products", method="GET").status_code
        for _ in range(MAX * 3)
    }
    assert codes == {200}


def test_disabled_under_the_test_environment(
    monkeypatch: pytest.MonkeyPatch, configure: None
) -> None:
    # Same limit, but is_testing is true - the middleware must step aside so the
    # auth-heavy suites are never throttled by their own volume.
    _enable(monkeypatch, environment="ci")
    mw = _middleware()

    codes = {_send(mw, "10.0.0.1").status_code for _ in range(MAX * 3)}
    assert codes == {200}


def test_disabled_when_the_flag_is_off(monkeypatch: pytest.MonkeyPatch, configure: None) -> None:
    _enable(monkeypatch, enabled="false")
    mw = _middleware()

    codes = {_send(mw, "10.0.0.1").status_code for _ in range(MAX * 3)}
    assert codes == {200}
