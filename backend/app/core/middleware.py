"""Request middleware: correlation ids, access logging and security headers."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.config import get_settings, settings
from app.core.logging import get_logger, request_id_ctx, user_id_ctx

logger = get_logger("shopsphere.access")

RequestHandler = Callable[[Request], Awaitable[Response]]

# Static security headers. Modest but real: each one closes a specific hole and
# each is asserted by the security suite.
SECURITY_HEADERS = {
    # Stops a browser from MIME-sniffing a JSON response into something executable.
    "X-Content-Type-Options": "nosniff",
    # The API is never meant to be framed.
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # The API returns JSON only, so it needs no script, style or frame sources.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # API responses are per-user; caching them anywhere shared would be a leak.
    "Cache-Control": "no-store",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id and reset the per-request logging context.

    An inbound ``X-Request-ID`` is honoured so a trace can be followed across
    the frontend, the API and the payment provider; otherwise one is generated.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        user_token = user_id_ctx.set(None)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
            user_id_ctx.reset(user_token)
        response.headers["X-Request-ID"] = request_id
        return response


class RejectNullBytesMiddleware(BaseHTTPMiddleware):
    """Reject requests containing a NUL byte, with 422 rather than 500.

    PostgreSQL text columns cannot hold ``\\x00``, so any NUL that reaches the
    driver raises ``ValueError: A string literal cannot contain NUL (0x00)
    characters``. That surfaced as an unhandled 500 on *every* endpoint that
    stores or searches text - one character, from any anonymous caller, on the
    search box, registration, addresses and the admin search alike.

    Checking centrally rather than field by field is deliberate: a per-field
    validator would have to be remembered on every new string field, and the
    one that gets forgotten is the one that gets found. A NUL byte is never
    legitimate input to this API, so there is nothing to lose by refusing it at
    the door.

    The body is read here and cached by Starlette, so the endpoint's own parsing
    re-uses it rather than paying for a second read.
    """

    MAX_INSPECTED_BYTES = 1_000_000

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        query = request.url.query
        if "\x00" in query or "%00" in query.lower():
            return self._reject("query string")

        if request.method in ("POST", "PUT", "PATCH"):
            body = (await request.body())[: self.MAX_INSPECTED_BYTES]
            # Both encodings have to be checked. A raw NUL byte can appear in
            # a non-JSON body, but JSON *escapes* it: a JSON request carrying a
            # NUL arrives as a six-character unicode escape and contains no
            # 0x00 byte at all. Checking only for the raw byte would therefore
            # miss every JSON request - which is all of them.
            if body and (b"\x00" in body or b"\\u0000" in body or b"\\U00000000" in body):
                return self._reject("request body")

        return await call_next(request)

    @staticmethod
    def _reject(location: str) -> JSONResponse:
        logger.info("Rejected a request containing a NUL byte", extra={"location": location})
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": "The request contains a NUL byte, which is not valid text.",
                "details": {"location": location},
            },
        )


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Throttle repeated calls to the credential-checking endpoints.

    Scoped deliberately narrowly: it guards only ``POST /auth/login`` and
    ``POST /auth/register``, the two routes where an attacker can guess. Every
    other request - browsing the catalogue, viewing an order - passes straight
    through, so normal use is never throttled.

    The window is per client address. bcrypt makes each login attempt cost real
    CPU by design; without a limit that strength inverts into a cheap way to peg
    the process, so the limit is what keeps a burst of guesses from either
    succeeding or exhausting the server.

    Settings read fresh per request via ``get_settings()`` so a test can enable
    the limiter on an isolated app instance. Off whenever the process is running
    a test suite (``is_testing``): those suites hammer auth far harder than any
    real client and would otherwise throttle themselves.
    """

    GUARDED_PATHS = frozenset({"/api/v1/auth/login", "/api/v1/auth/register"})
    # A ceiling on how many distinct client keys we track, so a flood of spoofed
    # source addresses cannot grow the map without bound. Evicted oldest-first.
    MAX_TRACKED_CLIENTS = 10_000

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        config = get_settings()
        if (
            not config.rate_limit_enabled
            or config.is_testing
            or request.method != "POST"
            or request.url.path not in self.GUARDED_PATHS
        ):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        window = float(config.rate_limit_auth_window_seconds)
        limit = config.rate_limit_auth_max_attempts
        now = time.monotonic()

        hits = self._hits[client]
        while hits and now - hits[0] > window:
            hits.popleft()

        if len(hits) >= limit:
            retry_after = max(1, int(window - (now - hits[0])))
            logger.warning(
                "Rate-limited an auth request",
                extra={"client": client, "path": request.url.path, "retry_after": retry_after},
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RATE_LIMITED",
                    "message": ("Too many attempts. Wait a moment before trying again."),
                    "details": {"retry_after_seconds": retry_after},
                },
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        if len(self._hits) > self.MAX_TRACKED_CLIENTS:
            self._evict_stale(now, window)
        return await call_next(request)

    def _evict_stale(self, now: float, window: float) -> None:
        """Drop client entries whose window has fully elapsed.

        Called only when the map exceeds its ceiling, so the common path stays
        allocation-free.
        """
        stale = [key for key, hits in self._hits.items() if not hits or now - hits[-1] > window]
        for key in stale:
            del self._hits[key]


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emit one structured line per request.

    Deliberately logs the *path template* rather than the raw path where FastAPI
    resolved a route, so that ``/orders/1`` and ``/orders/2`` aggregate into one
    ``/orders/{order_id}`` bucket instead of producing unbounded cardinality.
    Query strings are not logged: they can contain search terms and filters that
    are none of the log's business.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "Request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "status_code": 500,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        route = request.scope.get("route")
        fields = {
            "method": request.method,
            "path": getattr(route, "path", request.url.path),
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "client_ip": request.client.host if request.client else None,
        }
        if (user_id := getattr(request.state, "user_id", None)) is not None:
            fields["user_id"] = user_id

        if response.status_code >= 500:
            logger.error("Request completed", extra=fields)
        elif response.status_code >= 400 or duration_ms > settings.slow_request_ms:
            logger.warning("Request completed", extra=fields)
        else:
            logger.info("Request completed", extra=fields)

        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the static security headers to every response.

    The interactive docs are exempt from the CSP because Swagger UI legitimately
    loads its own scripts and styles; the exemption is scoped to those two paths
    so it cannot weaken the API surface.
    """

    DOC_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        response = await call_next(request)
        is_docs = request.url.path in self.DOC_PATHS
        for header, value in SECURITY_HEADERS.items():
            if is_docs and header in ("Content-Security-Policy", "Cache-Control"):
                continue
            response.headers.setdefault(header, value)
        return response
