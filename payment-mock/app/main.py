"""Mock payment provider.

A deliberately separate HTTP service rather than an in-process stub, so the
failure paths the test suite exercises are real ones: a timeout is a real socket
timeout, a 500 is a real 500, and the backend's own error handling is what gets
tested rather than a mock's approximation of it.

Two independent ways to choose an outcome:

**Card number (default).** Like a real provider's test cards, the number decides
the result. Per-request and stateless, so tests running in parallel never
interfere with each other - this is what the automated suites use.

    4111 1111 1111 1111   approved
    4000 0000 0000 0002   declined (insufficient funds)
    4000 0000 0000 0069   declined (expired card)
    4000 0000 0000 0127   declined (incorrect CVC)
    4000 0000 0000 0119   HTTP 500 - provider error
    4000 0000 0000 0259   stalls, so the caller times out
    anything failing Luhn  HTTP 400 - invalid request

**Global mode.** ``PAYMENT_MODE`` (or the ``X-Payment-Mode`` request header)
forces one outcome for every charge regardless of card. Intended for chaos runs
and for the demonstration in docs/failure-simulation.md, not for the test suite:
it is shared process state and would make parallel tests interfere.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, Response, status
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)-7s payment-mock %(message)s",
)
logger = logging.getLogger("payment_mock")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    payment_mode: Literal["card", "success", "declined", "timeout", "server_error"] = "card"
    # How long the timeout card stalls. Must exceed the backend's client-side
    # timeout (PAYMENT_TIMEOUT_SECONDS) for the caller to actually time out.
    payment_mock_timeout_delay_seconds: float = 30.0
    # Baseline artificial latency applied to every charge, for load testing.
    payment_mock_latency_ms: int = 0


settings = Settings()

app = FastAPI(
    title="ShopSphere Mock Payment Provider",
    version="1.0.0",
    description=__doc__,
    docs_url="/docs",
)


class Outcome(StrEnum):
    APPROVED = "approved"
    DECLINED = "declined"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    INVALID = "invalid"


# Card number -> (outcome, decline_code, human message)
TEST_CARDS: dict[str, tuple[Outcome, str | None, str]] = {
    "4111111111111111": (Outcome.APPROVED, None, "Approved"),
    "5555555555554444": (Outcome.APPROVED, None, "Approved"),
    "378282246310005": (Outcome.APPROVED, None, "Approved"),
    "4000000000000002": (Outcome.DECLINED, "insufficient_funds", "Insufficient funds."),
    "4000000000000069": (Outcome.DECLINED, "expired_card", "The card has expired."),
    "4000000000000127": (Outcome.DECLINED, "incorrect_cvc", "The security code is incorrect."),
    "4000000000009995": (Outcome.DECLINED, "do_not_honor", "The issuer declined the charge."),
    "4000000000000119": (Outcome.SERVER_ERROR, None, "Processing error at the issuer."),
    "4000000000000259": (Outcome.TIMEOUT, None, "Issuer did not respond."),
}


class ChargeRequest(BaseModel):
    # Sent as a string so no floating-point rounding can occur in transit; the
    # validator converts it once, here.
    amount: Decimal = Field(description="Amount as a decimal string, e.g. '129.99'")
    currency: str = Field(min_length=3, max_length=3)
    card_number: str = Field(min_length=12, max_length=19)
    card_holder: str = Field(min_length=1, max_length=120)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2100)
    cvv: str = Field(min_length=3, max_length=4)
    reference: str = Field(min_length=1, max_length=64)

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: Any) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("amount must be a decimal value") from exc
        if amount <= 0:
            raise ValueError("amount must be greater than zero")
        return amount

    @field_validator("card_number")
    @classmethod
    def _strip_separators(cls, value: str) -> str:
        return value.replace(" ", "").replace("-", "")


class RefundRequest(BaseModel):
    amount: Decimal = Field(gt=0)


def luhn_is_valid(card_number: str) -> bool:
    digits = [int(c) for c in card_number if c.isdigit()]
    if len(digits) < 12 or len(digits) != len(card_number):
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _expiry_is_past(month: int, year: int) -> bool:
    now = datetime.now(UTC)
    return (year, month) < (now.year, now.month)


def resolve_outcome(card_number: str, mode: str) -> tuple[Outcome, str | None, str]:
    """Decide the outcome for a charge.

    The global mode wins when set to anything other than ``card``; otherwise the
    card number decides. An unrecognised but Luhn-valid number is approved, so
    generated test data works without having to be registered here.
    """
    if mode == "success":
        return Outcome.APPROVED, None, "Approved (forced by PAYMENT_MODE)"
    if mode == "declined":
        return Outcome.DECLINED, "generic_decline", "Declined (forced by PAYMENT_MODE)"
    if mode == "server_error":
        return Outcome.SERVER_ERROR, None, "Provider error (forced by PAYMENT_MODE)"
    if mode == "timeout":
        return Outcome.TIMEOUT, None, "No response (forced by PAYMENT_MODE)"

    if not luhn_is_valid(card_number):
        return Outcome.INVALID, "invalid_number", "The card number failed its checksum."
    return TEST_CARDS.get(card_number, (Outcome.APPROVED, None, "Approved"))


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Same envelope shape as the main API, so clients parse failures uniformly."""
    return {"error": code, "message": message, "details": details or {}}


@app.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "payment-mock", "mode": settings.payment_mode}


@app.get("/test-cards", summary="The card numbers this provider recognises")
async def test_cards() -> dict[str, Any]:
    """Self-documenting failure simulation.

    Exposed so the checkout UI can show the available scenarios and so anyone
    exploring the API can discover them without reading the source.
    """
    return {
        "active_mode": settings.payment_mode,
        "note": (
            "With mode 'card' the number decides the outcome. Any other mode forces that "
            "outcome for every charge."
        ),
        "cards": [
            {
                "card_number": number,
                "outcome": outcome.value,
                "decline_code": decline_code,
                "message": message,
            }
            for number, (outcome, decline_code, message) in TEST_CARDS.items()
        ],
    }


@app.post(
    "/payments/charge",
    summary="Attempt a charge",
    responses={
        200: {"description": "Approved"},
        400: {"description": "The request was malformed or the card failed validation"},
        402: {"description": "The card was declined"},
        500: {"description": "Simulated provider failure"},
    },
)
async def charge(
    payload: ChargeRequest,
    response: Response,
    x_payment_mode: Annotated[str | None, Header(alias="X-Payment-Mode")] = None,
) -> dict[str, Any]:
    mode = x_payment_mode or settings.payment_mode
    outcome, decline_code, message = resolve_outcome(payload.card_number, mode)
    last4 = payload.card_number[-4:]

    if settings.payment_mock_latency_ms:
        await asyncio.sleep(settings.payment_mock_latency_ms / 1000)

    if outcome is Outcome.TIMEOUT:
        # Stall rather than return. The caller's client-side timeout is what
        # ends the request, which is exactly what a real hung provider does.
        logger.warning(
            "Stalling charge to simulate a provider timeout (reference=%s, delay=%ss)",
            payload.reference,
            settings.payment_mock_timeout_delay_seconds,
        )
        await asyncio.sleep(settings.payment_mock_timeout_delay_seconds)
        response.status_code = status.HTTP_504_GATEWAY_TIMEOUT
        return _error("PROVIDER_TIMEOUT", "The issuer did not respond in time.")

    if outcome is Outcome.INVALID:
        logger.info("Rejecting invalid card (reference=%s, last4=%s)", payload.reference, last4)
        response.status_code = status.HTTP_400_BAD_REQUEST
        return _error("INVALID_CARD_NUMBER", message, {"decline_code": decline_code})

    if _expiry_is_past(payload.expiry_month, payload.expiry_year):
        response.status_code = status.HTTP_400_BAD_REQUEST
        return _error(
            "CARD_EXPIRED",
            "The card expiry date is in the past.",
            {"expiry_month": payload.expiry_month, "expiry_year": payload.expiry_year},
        )

    if outcome is Outcome.SERVER_ERROR:
        logger.error("Simulating provider server error (reference=%s)", payload.reference)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return _error("PROVIDER_ERROR", message)

    transaction_id = f"txn_{uuid.uuid4().hex[:20]}"

    if outcome is Outcome.DECLINED:
        logger.info(
            "Declining charge (reference=%s, last4=%s, code=%s)",
            payload.reference,
            last4,
            decline_code,
        )
        response.status_code = status.HTTP_402_PAYMENT_REQUIRED
        return {
            "transaction_id": transaction_id,
            "status": "declined",
            "decline_code": decline_code,
            "message": message,
            "amount": str(payload.amount),
            "currency": payload.currency.upper(),
            "card_last4": last4,
            "reference": payload.reference,
            "processed_at": datetime.now(UTC).isoformat(),
        }

    logger.info(
        "Approved charge (reference=%s, last4=%s, amount=%s %s)",
        payload.reference,
        last4,
        payload.amount,
        payload.currency.upper(),
    )
    return {
        "transaction_id": transaction_id,
        "status": "approved",
        "message": message,
        "amount": str(payload.amount),
        "currency": payload.currency.upper(),
        "card_last4": last4,
        "reference": payload.reference,
        "processed_at": datetime.now(UTC).isoformat(),
    }


@app.post("/payments/{transaction_id}/refund", summary="Refund a charge")
async def refund(transaction_id: str, payload: RefundRequest) -> dict[str, Any]:
    """Acknowledge a refund.

    Stateless by design: the mock keeps no ledger, so it acknowledges any
    well-formed request. The backend is the authority on whether a refund is
    legitimate, and the database is where that is asserted.
    """
    logger.info("Refunding %s for transaction %s", payload.amount, transaction_id)
    return {
        "refund_id": f"rfnd_{uuid.uuid4().hex[:20]}",
        "transaction_id": transaction_id,
        "status": "refunded",
        "amount": str(payload.amount),
        "processed_at": datetime.now(UTC).isoformat(),
    }
