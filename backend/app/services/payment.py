"""Client for the mock payment provider.

The provider is a genuinely separate HTTP service, not an in-process stub. That
matters: a timeout here is a real socket timeout and a 500 is a real 500, so the
failure paths the test suite exercises are the ones that would actually happen
in production rather than a mocked approximation of them.

Declines are *not* exceptions - a declined card is an ordinary business outcome.
Only infrastructure failures (timeout, unreachable, malformed response) are
signalled as such, via the :class:`ChargeOutcome` returned to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ChargeOutcome(StrEnum):
    APPROVED = "approved"
    DECLINED = "declined"
    TIMEOUT = "timeout"
    PROVIDER_ERROR = "provider_error"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True, slots=True)
class ChargeResult:
    outcome: ChargeOutcome
    provider_reference: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    card_last4: str | None = None
    card_brand: str | None = None
    latency_ms: int | None = None

    @property
    def approved(self) -> bool:
        return self.outcome is ChargeOutcome.APPROVED


def detect_card_brand(card_number: str) -> str:
    """Identify the card scheme from its leading digits (IIN ranges)."""
    if not card_number[:4].isdigit():
        return "unknown"
    if card_number.startswith("4"):
        return "visa"
    if card_number[:2] in {"34", "37"}:
        return "amex"
    if card_number[:2] in {"51", "52", "53", "54", "55"}:
        return "mastercard"
    if 2221 <= int(card_number[:4]) <= 2720:  # Mastercard's newer 2-series range
        return "mastercard"
    if card_number.startswith("6"):
        return "discover"
    return "unknown"


def luhn_is_valid(card_number: str) -> bool:
    """Luhn checksum.

    Validating locally means an obviously mistyped number is rejected before a
    network call, and gives the security suite a deterministic way to produce a
    400 from the provider.
    """
    digits = [int(c) for c in card_number if c.isdigit()]
    if len(digits) < 12:
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


class PaymentGateway:
    """Thin HTTP client around the mock provider."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.payment_service_url).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.payment_timeout_seconds

    def charge(
        self,
        *,
        amount: Decimal,
        currency: str,
        card_number: str,
        card_holder: str,
        expiry_month: int,
        expiry_year: int,
        cvv: str,
        reference: str,
    ) -> ChargeResult:
        """Attempt a charge and classify the result.

        Every failure mode is mapped to a :class:`ChargeOutcome` rather than
        being allowed to propagate: the caller must be able to roll inventory
        back on *any* non-approval, and an unhandled exception escaping here
        would leave stock decremented for an order that was never paid.
        """
        card_last4 = card_number[-4:]
        card_brand = detect_card_brand(card_number)
        payload: dict[str, Any] = {
            "amount": str(amount),
            "currency": currency,
            "card_number": card_number,
            "card_holder": card_holder,
            "expiry_month": expiry_month,
            "expiry_year": expiry_year,
            "cvv": cvv,
            "reference": reference,
        }
        # The global chaos switch is forwarded as a header so the provider can
        # override whatever the card number would otherwise decide.
        headers = {"X-Payment-Mode": settings.payment_mode}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/payments/charge", json=payload, headers=headers
                )
        except httpx.TimeoutException:
            # Logged without the card number or CVV - `reference` is enough to
            # correlate with the order.
            logger.warning(
                "Payment provider timed out",
                extra={"reference": reference, "timeout_seconds": self.timeout},
            )
            return ChargeResult(
                outcome=ChargeOutcome.TIMEOUT,
                failure_code="PROVIDER_TIMEOUT",
                failure_message=f"Payment provider did not respond within {self.timeout}s.",
                card_last4=card_last4,
                card_brand=card_brand,
            )
        except httpx.HTTPError as exc:
            logger.error(
                "Payment provider unreachable",
                extra={"reference": reference, "error": type(exc).__name__},
            )
            return ChargeResult(
                outcome=ChargeOutcome.PROVIDER_ERROR,
                failure_code="PROVIDER_UNREACHABLE",
                failure_message="Payment provider could not be reached.",
                card_last4=card_last4,
                card_brand=card_brand,
            )

        return self._interpret(
            response, card_last4=card_last4, card_brand=card_brand, reference=reference
        )

    @staticmethod
    def _interpret(
        response: httpx.Response, *, card_last4: str, card_brand: str, reference: str
    ) -> ChargeResult:
        # `elapsed` is only populated once the response has been read, and
        # raises RuntimeError otherwise. It is diagnostic information, so it
        # must never be the reason a charge fails to be classified.
        try:
            latency_ms: int | None = int(response.elapsed.total_seconds() * 1000)
        except RuntimeError:
            latency_ms = None

        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            # A JSON array or bare string is not something this code can read;
            # treating it as an empty mapping routes it to the "unexpected
            # response" branch below rather than raising an AttributeError.
            body = {}

        if response.status_code >= 500:
            logger.error(
                "Payment provider returned a server error",
                extra={"reference": reference, "status": response.status_code},
            )
            return ChargeResult(
                outcome=ChargeOutcome.PROVIDER_ERROR,
                failure_code=body.get("error") or "PROVIDER_ERROR",
                failure_message=body.get("message") or "Payment provider returned an error.",
                card_last4=card_last4,
                card_brand=card_brand,
                latency_ms=latency_ms,
            )

        if response.status_code == 400:
            return ChargeResult(
                outcome=ChargeOutcome.INVALID_REQUEST,
                failure_code=body.get("error") or "INVALID_PAYMENT_REQUEST",
                failure_message=body.get("message") or "The payment request was rejected.",
                card_last4=card_last4,
                card_brand=card_brand,
                latency_ms=latency_ms,
            )

        if response.status_code == 402 or body.get("status") == "declined":
            return ChargeResult(
                outcome=ChargeOutcome.DECLINED,
                provider_reference=body.get("transaction_id"),
                failure_code=body.get("decline_code") or body.get("error") or "CARD_DECLINED",
                failure_message=body.get("message") or "The card was declined.",
                card_last4=card_last4,
                card_brand=card_brand,
                latency_ms=latency_ms,
            )

        if response.is_success and body.get("status") == "approved":
            return ChargeResult(
                outcome=ChargeOutcome.APPROVED,
                provider_reference=body.get("transaction_id"),
                card_last4=card_last4,
                card_brand=card_brand,
                latency_ms=latency_ms,
            )

        # Anything else is a contract violation by the provider. Treated as a
        # failure so an unexpected shape can never be mistaken for approval.
        logger.error(
            "Unexpected payment provider response",
            extra={"reference": reference, "status": response.status_code},
        )
        return ChargeResult(
            outcome=ChargeOutcome.PROVIDER_ERROR,
            failure_code="UNEXPECTED_PROVIDER_RESPONSE",
            failure_message="Payment provider returned an unrecognised response.",
            card_last4=card_last4,
            card_brand=card_brand,
            latency_ms=latency_ms,
        )

    def refund(self, *, transaction_id: str, amount: Decimal) -> bool:
        """Best-effort refund. Returns whether the provider acknowledged it."""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/payments/{transaction_id}/refund",
                    json={"amount": str(amount)},
                )
            return response.is_success
        except httpx.HTTPError as exc:
            logger.error(
                "Refund request failed",
                extra={"transaction_id": transaction_id, "error": type(exc).__name__},
            )
            return False


def get_payment_gateway() -> PaymentGateway:
    """FastAPI dependency; overridden in tests that need a stubbed provider."""
    return PaymentGateway()
