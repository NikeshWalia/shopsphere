"""Checkout, order and payment request/response bodies."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import OrderStatus, PaymentStatus
from app.schemas.common import Money


class PaymentDetails(BaseModel):
    """Card details submitted at checkout.

    Nothing here is persisted beyond the last four digits and the brand. The
    card number is passed straight to the mock provider and dropped.

    The number also selects the outcome (see docs/failure-simulation.md), which
    is how a test picks "declined" or "timeout" without mutating global state -
    critical for running the suite in parallel.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "card_number": "4111111111111111",
                    "card_holder": "Ada Lovelace",
                    "expiry_month": 12,
                    "expiry_year": 2030,
                    "cvv": "123",
                }
            ]
        }
    )

    card_number: str = Field(min_length=12, max_length=19)
    card_holder: str = Field(min_length=2, max_length=120)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2000, le=2100)
    cvv: str = Field(min_length=3, max_length=4)

    @field_validator("card_number")
    @classmethod
    def _digits_only(cls, value: str) -> str:
        cleaned = value.replace(" ", "").replace("-", "")
        if not cleaned.isdigit():
            raise ValueError("Card number must contain digits only")
        return cleaned

    @field_validator("cvv")
    @classmethod
    def _cvv_digits(cls, value: str) -> str:
        if not value.isdigit():
            raise ValueError("CVV must contain digits only")
        return value


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "address_id": 3,
                    "promo_code": "WELCOME10",
                    "payment": {
                        "card_number": "4111111111111111",
                        "card_holder": "Ada Lovelace",
                        "expiry_month": 12,
                        "expiry_year": 2030,
                        "cvv": "123",
                    },
                }
            ]
        }
    )

    address_id: int = Field(ge=1)
    payment: PaymentDetails
    promo_code: str | None = Field(default=None, max_length=32)

    @field_validator("promo_code")
    @classmethod
    def _normalise_promo(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class QuoteRequest(BaseModel):
    """Preview totals before committing to a purchase.

    Lets the checkout UI display tax, shipping and discount without the client
    ever calculating them - the same service computes the quote and the order.
    """

    promo_code: str | None = Field(default=None, max_length=32)

    @field_validator("promo_code")
    @classmethod
    def _normalise_promo(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper() or None


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    product_name: str
    sku: str
    unit_price: Money
    quantity: int
    line_total: Money


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_reference: str | None
    amount: Money
    currency: str
    status: PaymentStatus
    method: str
    card_last4: str | None
    card_brand: str | None
    failure_code: str | None
    failure_message: str | None
    attempt: int
    created_at: datetime


class ShippingAddressSnapshot(BaseModel):
    full_name: str
    line1: str
    line2: str | None
    city: str
    state: str
    postal_code: str
    country: str
    phone: str | None


class OrderSummaryResponse(BaseModel):
    """Order-history row: enough to render a list without the full item detail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    status: OrderStatus
    payment_status: PaymentStatus
    total: Money
    currency: str
    item_count: int
    created_at: datetime


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    user_id: int
    status: OrderStatus
    payment_status: PaymentStatus
    subtotal: Money
    discount_total: Money
    tax: Money
    shipping_fee: Money
    total: Money
    currency: str
    promo_code: str | None
    items: list[OrderItemResponse]
    payments: list[PaymentResponse]
    shipping_address: ShippingAddressSnapshot
    cancelled_reason: str | None
    created_at: datetime
    updated_at: datetime


class QuoteResponse(BaseModel):
    subtotal: Money
    discount_total: Money
    tax: Money
    shipping_fee: Money
    total: Money
    currency: str
    promo_code: str | None
    promo_description: str | None = None
    item_count: int
    issues: list[str] = Field(default_factory=list)
    is_checkout_ready: bool


class CancelOrderRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class OrderStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "shipped"}]})

    status: OrderStatus
