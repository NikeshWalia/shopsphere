"""ShopSphere API application factory.

Notably, *every* error path in the application is normalised here. FastAPI's
default 422 body, Starlette's default 404 body and any unhandled exception all
end up in the same envelope::

    {"error": "...", "message": "...", "details": {...}}

A client - or a test - therefore never has to branch on which layer produced a
failure, and an unhandled exception can never leak a stack trace to a caller.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.router import api_router
from app.api.v1 import health
from app.core.config import settings
from app.core.errors import AppError, InternalError, NotFoundError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    AccessLogMiddleware,
    RejectNullBytesMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)

logger = get_logger(__name__)

API_DESCRIPTION = """
REST API for **ShopSphere**, a deliberately realistic e-commerce application built as the
system under test for a quality-engineering platform.

### Conventions

* **Errors** always use one envelope: `{"error": "CODE", "message": "...", "details": {...}}`.
  `error` is a stable machine-readable code; assert on it rather than on the message text.
* **Money** is always a JSON number with two decimal places, never a string.
* **Pagination** wraps collections in `{items, total, page, page_size, total_pages, has_next,
  has_previous}`.
* **Authentication** is a bearer JWT: `Authorization: Bearer <token>` from `/auth/login`.

### Rules the API enforces server-side

1. A customer can never buy more units than are in stock.
2. A customer can never read another customer's orders or addresses.
3. Customers cannot reach `/admin/*`.
4. The client never supplies a price - all totals are computed from the catalogue.
5. A failed payment never produces a paid order.
6. Repeating a checkout with the same `Idempotency-Key` returns the original order.
"""

TAGS_METADATA = [
    {"name": "Health", "description": "Liveness and readiness probes."},
    {"name": "Authentication", "description": "Registration, login and profile management."},
    {"name": "Catalogue", "description": "Public product search, filtering, sorting and browsing."},
    {"name": "Cart", "description": "Cart contents. Always priced server-side."},
    {"name": "Addresses", "description": "Shipping addresses, scoped to the authenticated user."},
    {"name": "Orders", "description": "Checkout, order history and cancellation."},
    {
        "name": "Admin",
        "description": "Administrator-only catalogue, inventory, order and user management.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info(
        "ShopSphere API starting",
        extra={
            "environment": settings.environment,
            "version": settings.app_version,
            "payment_mode": settings.payment_mode,
        },
    )
    yield
    logger.info("ShopSphere API stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "ShopSphere Quality Engineering"},
        license_info={"name": "MIT"},
    )

    # Middleware runs in reverse registration order for responses, so the last
    # one added is the outermost: request context must wrap everything, since
    # the access log and error handlers rely on the correlation id it sets.
    app.add_middleware(SecurityHeadersMiddleware)
    # Runs inside the access log so a rejected request is still logged, but
    # before routing so no endpoint ever sees a NUL byte.
    app.add_middleware(RejectNullBytesMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
    )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


def _error_response(
    status_code: int,
    error: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "message": message, "details": details or {}},
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        # Expected, deliberate failures. Logged at info/warning, never with a
        # traceback: these are business outcomes, not defects.
        log = logger.warning if exc.status_code >= 500 else logger.info
        log(
            "Request rejected",
            extra={
                "error_code": exc.error_code,
                "status_code": exc.status_code,
                "path": request.url.path,
            },
        )
        return _error_response(
            exc.status_code, exc.error_code, exc.message, exc.details, exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Normalise Pydantic's validation output into the standard envelope.

        The per-field breakdown is preserved under ``details.fields`` because it
        is genuinely useful to a client rendering a form - but it is reshaped so
        the top level looks like every other error.
        """
        fields = [
            {
                # loc[0] is the source ("body", "query", "path"); dropping it
                # gives the client the field path it actually recognises.
                "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        summary = fields[0]["message"] if fields else "The request failed validation."
        logger.info(
            "Request failed validation",
            extra={"path": request.url.path, "field_count": len(fields)},
        )
        return _error_response(
            422,
            "VALIDATION_ERROR",
            summary,
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Cover routing-level failures such as 404 and 405."""
        if exc.status_code == 404:
            return _error_response(
                404,
                NotFoundError.error_code,
                "The requested endpoint does not exist.",
                {"path": request.url.path},
            )
        if exc.status_code == 405:
            return _error_response(
                405,
                "METHOD_NOT_ALLOWED",
                f"{request.method} is not allowed on this endpoint.",
                {"path": request.url.path, "method": request.method},
                headers=getattr(exc, "headers", None),
            )
        return _error_response(
            exc.status_code,
            "HTTP_ERROR",
            str(exc.detail),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort.

        The traceback goes to the log, where it belongs. The caller gets a
        generic message and the request id, which is enough to correlate their
        report with the log entry without exposing anything internal.
        """
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "Unhandled exception",
            extra={"path": request.url.path, "method": request.method},
        )
        return _error_response(
            500,
            InternalError.error_code,
            InternalError.default_message,
            {"request_id": request_id} if request_id else {},
        )


app = create_app()
