"""Shared schema building blocks.

The most consequential thing in this module is :data:`Money`.

Pydantic v2 serialises ``Decimal`` as a JSON *string* by default. A response of
``{"price": "129.99"}`` breaks every client that does arithmetic on the value,
and it is exactly the kind of silent type drift the contract test suite exists
to catch. Money is therefore given an explicit serializer that emits a JSON
number and declares itself as ``number`` in the OpenAPI document, so the schema
and the wire format agree.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Generic, TypeVar

import email_validator
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

CENTS = Decimal("0.01")


def _validate_email(value: Any) -> str:
    """Validate and normalise an email address.

    Deliberately differs from Pydantic's stock ``EmailStr`` in two ways:

    ``check_deliverability=False`` - resolving MX records would put a DNS lookup
    on the request path, making registration latency depend on a third party and
    turning a slow resolver into a flaky test.

    ``test_environment=True`` - permits the RFC 2606 reserved domains
    (``.test``, ``.example``, ``.invalid``, ``.localhost``). The seed accounts
    and every generated test user live at ``@shopsphere.test``, which is exactly
    what that TLD is reserved for; rejecting it would force test data to use
    domains that might belong to somebody.
    """
    if not isinstance(value, str):
        raise ValueError("Email must be a string")
    try:
        result = email_validator.validate_email(
            value.strip(), check_deliverability=False, test_environment=True
        )
    except email_validator.EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    # `normalized` lowercases the domain and applies Unicode normalisation; the
    # local part is lowercased separately so uniqueness is case-insensitive.
    local, _, domain = result.normalized.rpartition("@")
    return f"{local.lower()}@{domain.lower()}"


EmailAddress = Annotated[
    str,
    BeforeValidator(_validate_email),
    WithJsonSchema({"type": "string", "format": "email", "examples": ["ada@example.com"]}),
]


def quantize_money(value: Decimal | int | float | str) -> Decimal:
    """Round a monetary value to two decimal places using half-up rounding.

    Python's default is banker's rounding, which rounds 2.675 down to 2.67.
    Retail arithmetic expects 2.68, so the rounding mode is pinned explicitly
    and used by every code path that touches money.
    """
    return Decimal(str(value)).quantize(CENTS, rounding=ROUND_HALF_UP)


def _serialise_money(value: Decimal) -> float:
    return float(quantize_money(value))


Money = Annotated[
    Decimal,
    PlainSerializer(_serialise_money, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "format": "decimal", "examples": [129.99]}),
]


def _reject_bool(value: Any) -> Any:
    """Refuse booleans where an integer is expected.

    Pydantic's lax mode coerces ``True`` to ``1``, so ``{"quantity": true}``
    would otherwise be accepted as an order for one unit that the customer never
    asked for. ``bool`` is a subclass of ``int`` in Python, which is exactly why
    this has to be rejected explicitly rather than by the type annotation.
    """
    if isinstance(value, bool):
        raise ValueError("Expected a whole number, not a boolean")
    return value


#: An integer field that will not silently accept ``true``/``false``.
StrictQuantity = Annotated[int, BeforeValidator(_reject_bool)]


class ErrorResponse(BaseModel):
    """The single response envelope used for every non-2xx status."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": "INSUFFICIENT_INVENTORY",
                    "message": "Only 2 units of 'Aurora 14 Ultrabook' are available",
                    "details": {"product_id": 7, "requested": 5, "available": 2},
                }
            ]
        }
    )

    error: str = Field(description="Stable machine-readable error code")
    message: str = Field(description="Human-readable, safe to display to end users")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Structured context; never contains internals"
    )


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Envelope for every paginated collection.

    Returning metadata alongside the items (rather than only a bare array) means
    clients can render pagination without a second count request, and lets tests
    assert on boundaries directly.
    """

    items: list[T]
    total: int = Field(description="Total number of matching records")
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=0)
    has_next: bool
    has_previous: bool

    @classmethod
    def build(cls, items: list[T], *, total: int, page: int, page_size: int) -> Page[T]:
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1 and total_pages > 0,
        )


class MessageResponse(BaseModel):
    """Acknowledgement body for operations with nothing meaningful to return."""

    message: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Dependency-aware readiness.

    ``/health`` says the process is alive; ``/health/ready`` says it can
    actually serve traffic. Docker Compose and CI wait on the latter, which is
    what removes the need for arbitrary sleeps during startup.
    """

    status: str
    checks: dict[str, str]
