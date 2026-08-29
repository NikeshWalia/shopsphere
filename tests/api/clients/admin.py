"""Administration API client."""

from __future__ import annotations

import uuid
from typing import Any

from tests.api.clients.base import BaseClient
from tests.utilities.http import ApiResponse


def unique_sku(prefix: str = "TST") -> str:
    """A SKU no other test run will collide with.

    Test data is created concurrently under pytest-xdist and across repeated
    runs against the same database, so uniqueness has to come from the value
    itself rather than from a cleanup step that might not have happened.
    """
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def product_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sku": unique_sku(),
        "name": "Test Fixture Product",
        "description": "Created by the automated test suite.",
        "price": 49.99,
        "category_id": 1,
        "brand": "TestBrand",
        "rating": 4.0,
        "stock_quantity": 25,
        "is_active": True,
    }
    payload.update(overrides)
    return payload


class AdminClient(BaseClient):
    # -- Products ----------------------------------------------------------
    def create_product(
        self, payload: dict[str, Any] | None = None, *, token: str | None = None
    ) -> ApiResponse:
        return self._post("/admin/products", json_body=payload or product_payload(), token=token)

    def update_product(
        self, product_id: int, payload: dict[str, Any], *, token: str | None = None
    ) -> ApiResponse:
        return self._patch(f"/admin/products/{product_id}", json_body=payload, token=token)

    def deactivate_product(self, product_id: int, *, token: str | None = None) -> ApiResponse:
        return self._delete(f"/admin/products/{product_id}", token=token)

    def set_stock(self, product_id: int, quantity: int, *, token: str | None = None) -> ApiResponse:
        return self._put(
            f"/admin/products/{product_id}/stock", json_body={"quantity": quantity}, token=token
        )

    def inventory(self, *, token: str | None = None, **params: Any) -> ApiResponse:
        clean = {key: value for key, value in params.items() if value is not None}
        return self._get("/admin/inventory", params=clean, token=token)

    # -- Orders ------------------------------------------------------------
    def orders(self, *, token: str | None = None, **params: Any) -> ApiResponse:
        clean = {key: value for key, value in params.items() if value is not None}
        return self._get("/admin/orders", params=clean, token=token)

    def order(self, order_id: int, *, token: str | None = None) -> ApiResponse:
        return self._get(f"/admin/orders/{order_id}", token=token)

    def set_order_status(
        self, order_id: int, status: str, *, token: str | None = None
    ) -> ApiResponse:
        return self._patch(
            f"/admin/orders/{order_id}/status", json_body={"status": status}, token=token
        )

    # -- Users -------------------------------------------------------------
    def users(self, *, token: str | None = None, **params: Any) -> ApiResponse:
        clean = {key: value for key, value in params.items() if value is not None}
        return self._get("/admin/users", params=clean, token=token)

    def set_user_active(
        self, user_id: int, is_active: bool, *, token: str | None = None
    ) -> ApiResponse:
        return self._patch(
            f"/admin/users/{user_id}/active", params={"is_active": is_active}, token=token
        )

    # -- Dashboard ---------------------------------------------------------
    def stats(self, *, token: str | None = None) -> ApiResponse:
        return self._get("/admin/stats", token=token)

    # -- Convenience -------------------------------------------------------
    def create_product_with_stock(self, stock: int, **overrides: Any) -> dict[str, Any]:
        """Create a product with an exact stock level.

        The backbone of inventory testing: a test that needs "a product with
        exactly 3 units" creates one rather than hunting for a seeded product
        that happens to have 3 - which another test could change underneath it.
        """
        response = self.create_product(product_payload(stock_quantity=stock, **overrides))
        assert (
            response.status_code == 201
        ), f"Could not create test product: {response.status_code} {response.raw_text[:300]}"
        return dict(response.body)
