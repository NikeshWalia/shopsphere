"""Client for the mock payment provider.

Talks to the provider directly, bypassing the backend. That is how the contract
between the two services gets tested independently: if the provider's response
shape changes, these tests fail on their own rather than surfacing as a
confusing checkout failure somewhere else.
"""

from __future__ import annotations

from typing import Any

from tests.api.clients.base import BaseClient
from tests.utilities.http import ApiResponse


class PaymentMockClient(BaseClient):
    def health(self) -> ApiResponse:
        return self.http.get("/health", authenticate=False)

    def test_cards(self) -> ApiResponse:
        return self.http.get("/test-cards", authenticate=False)

    def charge(
        self,
        *,
        amount: str = "100.00",
        currency: str = "USD",
        card_number: str = "4111111111111111",
        card_holder: str = "Test Customer",
        expiry_month: int = 12,
        expiry_year: int = 2032,
        cvv: str = "123",
        reference: str = "TEST-REF-0001",
        mode: str | None = None,
    ) -> ApiResponse:
        headers = {"X-Payment-Mode": mode} if mode else None
        return self.http.post(
            "/payments/charge",
            json_body={
                "amount": amount,
                "currency": currency,
                "card_number": card_number,
                "card_holder": card_holder,
                "expiry_month": expiry_month,
                "expiry_year": expiry_year,
                "cvv": cvv,
                "reference": reference,
            },
            headers=headers,
            authenticate=False,
        )

    def charge_raw(self, payload: dict[str, Any]) -> ApiResponse:
        return self.http.post("/payments/charge", json_body=payload, authenticate=False)

    def refund(self, transaction_id: str, amount: str = "100.00") -> ApiResponse:
        return self.http.post(
            f"/payments/{transaction_id}/refund", json_body={"amount": amount}, authenticate=False
        )
