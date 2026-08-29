"""Cart, address, checkout and order API clients."""

from __future__ import annotations

import uuid
from typing import Any

from tests.api.clients.base import BaseClient
from tests.utilities.http import ApiResponse

# The mock provider's test cards. Named here so tests read as intent
# ("pay with DECLINED_CARD") rather than as a magic 16-digit number.
CARD_APPROVED = "4111111111111111"
CARD_APPROVED_MASTERCARD = "5555555555554444"
CARD_DECLINED_FUNDS = "4000000000000002"
CARD_DECLINED_EXPIRED = "4000000000000069"
CARD_DECLINED_CVC = "4000000000000127"
CARD_DECLINED_GENERIC = "4000000000009995"
CARD_PROVIDER_ERROR = "4000000000000119"
CARD_TIMEOUT = "4000000000000259"
CARD_INVALID_LUHN = "4111111111111112"


def payment_payload(card_number: str = CARD_APPROVED, **overrides: Any) -> dict[str, Any]:
    """Build a valid payment block, overriding individual fields as needed."""
    payload: dict[str, Any] = {
        "card_number": card_number,
        "card_holder": "Test Customer",
        "expiry_month": 12,
        "expiry_year": 2032,
        "cvv": "123",
    }
    payload.update(overrides)
    return payload


class CartClient(BaseClient):
    def get(self, *, token: str | None = None) -> ApiResponse:
        return self._get("/cart", token=token)

    def add_item(
        self, product_id: int, quantity: int = 1, *, token: str | None = None
    ) -> ApiResponse:
        return self._post(
            "/cart/items", json_body={"product_id": product_id, "quantity": quantity}, token=token
        )

    def add_item_raw(self, payload: dict[str, Any], *, token: str | None = None) -> ApiResponse:
        return self._post("/cart/items", json_body=payload, token=token)

    def update_item(
        self, product_id: int, quantity: int, *, token: str | None = None
    ) -> ApiResponse:
        return self._patch(
            f"/cart/items/{product_id}", json_body={"quantity": quantity}, token=token
        )

    def remove_item(self, product_id: int, *, token: str | None = None) -> ApiResponse:
        return self._delete(f"/cart/items/{product_id}", token=token)

    def clear(self, *, token: str | None = None) -> ApiResponse:
        return self._delete("/cart", token=token)

    def anonymous_get(self) -> ApiResponse:
        return self.http.get("/cart", authenticate=False)


class AddressClient(BaseClient):
    def list(self, *, token: str | None = None) -> ApiResponse:
        return self._get("/addresses", token=token)

    def create(
        self, payload: dict[str, Any] | None = None, *, token: str | None = None
    ) -> ApiResponse:
        return self._post("/addresses", json_body=payload or default_address(), token=token)

    def get(self, address_id: int, *, token: str | None = None) -> ApiResponse:
        return self._get(f"/addresses/{address_id}", token=token)

    def update(
        self, address_id: int, payload: dict[str, Any], *, token: str | None = None
    ) -> ApiResponse:
        return self._patch(f"/addresses/{address_id}", json_body=payload, token=token)

    def delete(self, address_id: int, *, token: str | None = None) -> ApiResponse:
        return self._delete(f"/addresses/{address_id}", token=token)

    def create_and_get_id(self, *, token: str | None = None) -> int:
        response = self.create(token=token)
        assert response.status_code == 201, f"Could not create address: {response.raw_text[:200]}"
        return int(response.body["id"])


def default_address(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": "Home",
        "full_name": "Test Customer",
        "line1": "1 Integration Way",
        "city": "Austin",
        "state": "TX",
        "postal_code": "73301",
        "country": "US",
        "phone": "+1-555-0123",
    }
    payload.update(overrides)
    return payload


class OrderClient(BaseClient):
    def quote(self, promo_code: str | None = None, *, token: str | None = None) -> ApiResponse:
        return self._post("/checkout/quote", json_body={"promo_code": promo_code}, token=token)

    def checkout(
        self,
        *,
        address_id: int,
        card_number: str = CARD_APPROVED,
        promo_code: str | None = None,
        idempotency_key: str | None = None,
        payment: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> ApiResponse:
        body: dict[str, Any] = {
            "address_id": address_id,
            "payment": payment if payment is not None else payment_payload(card_number),
            "promo_code": promo_code,
        }
        headers: dict[str, str] = {}
        # A fresh key per call by default, so two checkouts in the same test are
        # independent unless a test deliberately reuses one.
        headers["Idempotency-Key"] = idempotency_key or uuid.uuid4().hex
        return self._post("/orders", json_body=body, headers=headers, token=token)

    def checkout_raw(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        token: str | None = None,
    ) -> ApiResponse:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self._post("/orders", json_body=payload, headers=headers, token=token)

    def checkout_without_idempotency_key(
        self, *, address_id: int, card_number: str = CARD_APPROVED, token: str | None = None
    ) -> ApiResponse:
        """Deliberately omit the header, to document what it protects against."""
        return self._post(
            "/orders",
            json_body={"address_id": address_id, "payment": payment_payload(card_number)},
            token=token,
        )

    def list(self, *, token: str | None = None, **params: Any) -> ApiResponse:
        clean = {key: value for key, value in params.items() if value is not None}
        return self._get("/orders", params=clean, token=token)

    def get(self, order_id: int | str, *, token: str | None = None) -> ApiResponse:
        return self._get(f"/orders/{order_id}", token=token)

    def cancel(
        self, order_id: int, reason: str | None = None, *, token: str | None = None
    ) -> ApiResponse:
        return self._post(f"/orders/{order_id}/cancel", json_body={"reason": reason}, token=token)

    # -- Convenience -------------------------------------------------------
    def place_successful_order(
        self, *, address_id: int, token: str | None = None
    ) -> dict[str, Any]:
        """Place an order that must succeed; fail the test setup if it does not."""
        response = self.checkout(address_id=address_id, token=token)
        assert (
            response.status_code == 201
        ), f"Expected checkout to succeed, got {response.status_code}: {response.raw_text[:300]}"
        return dict(response.body)
