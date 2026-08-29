"""Catalogue API client."""

from __future__ import annotations

from typing import Any

from tests.api.clients.base import BaseClient
from tests.utilities.http import ApiResponse


class ProductClient(BaseClient):
    def list(self, **params: Any) -> ApiResponse:
        """List products. Any keyword becomes a query parameter.

        Deliberately permissive so negative tests can send values the API is
        expected to reject (min_price greater than max_price, page zero, an
        unknown sort key) without the client itself getting in the way.
        """
        clean = {key: value for key, value in params.items() if value is not None}
        return self._get("/products", params=clean, authenticate=False)

    def list_as(self, token: str | None, **params: Any) -> ApiResponse:
        clean = {key: value for key, value in params.items() if value is not None}
        return self._get("/products", params=clean, token=token)

    def search(self, term: str, **params: Any) -> ApiResponse:
        return self.list(q=term, **params)

    def get(self, product_id: int | str, *, token: str | None = None) -> ApiResponse:
        return self._get(f"/products/{product_id}", token=token, authenticate=token is not None)

    def brands(self) -> ApiResponse:
        return self._get("/products/brands", authenticate=False)

    def categories(self) -> ApiResponse:
        return self._get("/categories", authenticate=False)

    def category(self, slug: str) -> ApiResponse:
        return self._get(f"/categories/{slug}", authenticate=False)

    # -- Convenience -------------------------------------------------------
    def first_in_stock(self, *, min_stock: int = 1) -> dict[str, Any]:
        """Return any product with at least `min_stock` units.

        Tests that merely need "a purchasable product" use this instead of
        hardcoding an id, so they keep working when the catalogue changes.
        """
        response = self.list(in_stock=True, page_size=100, sort="price_asc")
        response.assert_status(200)
        for item in response.body["items"]:
            if item["stock_quantity"] >= min_stock:
                return dict(item)
        raise AssertionError(f"No seeded product has at least {min_stock} units in stock")

    def any_out_of_stock(self) -> dict[str, Any] | None:
        response = self.list(in_stock=False, page_size=20)
        response.assert_status(200)
        items = response.body["items"]
        return dict(items[0]) if items else None
