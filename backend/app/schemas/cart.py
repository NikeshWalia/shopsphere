"""Cart request/response bodies.

Note what the request models do *not* contain: no price, no line total, no
order total. The client may only say which product and how many. Everything
with a currency symbol is computed server-side.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Money, StrictQuantity


class AddCartItemRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"product_id": 12, "quantity": 2}]})

    product_id: int = Field(ge=1)
    # Upper bound is validated again in the service against configuration and
    # against live stock; this is the cheap first gate.
    quantity: StrictQuantity = Field(ge=1, le=1000)


class UpdateCartItemRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"quantity": 3}]})

    quantity: StrictQuantity = Field(ge=1, le=1000)


class CartItemResponse(BaseModel):
    product_id: int
    sku: str
    name: str
    image_url: str | None
    unit_price: Money = Field(description="Current catalogue price, resolved server-side")
    quantity: int
    line_total: Money
    available_stock: int
    # True when the cart holds more units than are currently in stock - shown as
    # a warning in the UI and blocks checkout.
    exceeds_stock: bool
    is_active: bool


class CartTotals(BaseModel):
    subtotal: Money
    discount_total: Money
    tax: Money
    shipping_fee: Money
    total: Money
    currency: str


class CartResponse(BaseModel):
    id: int | None = Field(description="Null until the cart has been persisted")
    items: list[CartItemResponse]
    item_count: int = Field(description="Total units across all lines")
    distinct_item_count: int
    totals: CartTotals
    promo_code: str | None = None
    # Human-readable blockers, e.g. "Only 2 units of X are available".
    issues: list[str] = Field(default_factory=list)
    is_checkout_ready: bool
