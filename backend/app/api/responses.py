"""Reusable OpenAPI response declarations.

Documenting the failure cases is what makes the generated spec worth testing
against: the contract suite reads these declarations, so an endpoint that can
return 409 but does not say so is a documentation bug the pipeline catches.
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import ErrorResponse

_DESCRIPTIONS = {
    400: "Malformed request",
    401: "Missing, malformed or expired access token",
    402: "The payment was declined",
    403: "Authenticated, but not permitted to perform this action",
    404: "The resource does not exist, or is not visible to this caller",
    409: "The request conflicts with the current state (stock, duplicates, order state)",
    422: "The request body or query failed validation",
    502: "The payment provider returned an error",
    504: "The payment provider did not respond in time",
}


def errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build the ``responses=`` mapping for the given status codes."""
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS.get(code, "Error")}
        for code in status_codes
    }


# Shorthands for the combinations that recur across the API.
AUTH_ERRORS = errors(401, 403)
NOT_FOUND = errors(404)
AUTH_AND_NOT_FOUND = errors(401, 403, 404)
VALIDATION = errors(422)
