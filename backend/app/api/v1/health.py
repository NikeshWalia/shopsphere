"""Liveness and readiness probes."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import settings
from app.core.deps import DbSession
from app.core.logging import get_logger
from app.schemas.common import HealthResponse, ReadinessResponse

logger = get_logger(__name__)
router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 as soon as the process can serve HTTP. Checks no dependencies.",
)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Verifies the dependencies needed to serve real traffic. Returns 503 while any "
        "of them is unavailable. Docker Compose and the CI pipeline wait on this endpoint "
        "instead of sleeping for a fixed period."
    ),
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
def readiness(db: DbSession, response: Response) -> ReadinessResponse:
    checks: dict[str, str] = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("Readiness: database unavailable", extra={"error": type(exc).__name__})
        checks["database"] = "unavailable"

    try:
        with httpx.Client(timeout=2.0) as client:
            provider = client.get(f"{settings.payment_service_url.rstrip('/')}/health")
        checks["payment_provider"] = "ok" if provider.is_success else "degraded"
    except httpx.HTTPError:
        # The shop can still be browsed without the payment provider, so this is
        # reported as degraded rather than counted as a hard readiness failure.
        checks["payment_provider"] = "unavailable"

    ready = checks["database"] == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
