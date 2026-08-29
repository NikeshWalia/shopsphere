"""Catalogue request/response bodies."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import ProductSort
from app.schemas.common import Money, StrictQuantity


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None = None


class CategoryWithCountResponse(CategoryResponse):
    product_count: int = Field(description="Number of active products in this category")


class ProductSummaryResponse(BaseModel):
    """Fields needed to render a product card in a listing."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    price: Money
    brand: str
    rating: Decimal
    image_url: str | None = None
    is_active: bool
    in_stock: bool
    stock_quantity: int
    category: CategoryResponse


class ProductDetailResponse(ProductSummaryResponse):
    description: str
    created_at: datetime
    updated_at: datetime


class ProductFilters(BaseModel):
    """Validated catalogue query.

    Constructed from query parameters in the endpoint so that all filter
    validation - including cross-field rules such as min <= max - lives in one
    testable place rather than being scattered through the route handler.
    """

    q: str | None = Field(default=None, max_length=120, description="Free-text search term")
    category_id: int | None = Field(default=None, ge=1)
    category: str | None = Field(default=None, max_length=80, description="Category slug or name")
    brand: str | None = Field(default=None, max_length=80)
    min_price: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000"))
    max_price: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000"))
    min_rating: Decimal | None = Field(default=None, ge=0, le=5)
    in_stock: bool | None = None
    # Only admins may pass include_inactive; the endpoint enforces that.
    include_inactive: bool = False
    sort: ProductSort = ProductSort.RELEVANCE
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("q", "brand", "category")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        # "?q=" and "?q=%20" must behave like "no search term", not like a
        # search for the empty string.
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _price_range_is_coherent(self) -> ProductFilters:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price must be less than or equal to max_price")
        return self


class ProductCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sku": "ELEC-9001",
                    "name": "Nimbus Noise-Cancelling Headphones",
                    "description": "Over-ear headphones with 40h battery life.",
                    "price": 249.99,
                    "category_id": 1,
                    "brand": "Nimbus",
                    "rating": 4.5,
                    "stock_quantity": 40,
                    "image_url": "https://picsum.photos/seed/elec-9001/600/600",
                }
            ]
        }
    )

    sku: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default="", max_length=4000)
    price: Decimal = Field(ge=0, le=Decimal("1000000"))
    category_id: int = Field(ge=1)
    brand: str = Field(min_length=1, max_length=80)
    rating: Decimal = Field(default=Decimal("0"), ge=0, le=5)
    image_url: str | None = Field(default=None, max_length=512)
    stock_quantity: StrictQuantity = Field(default=0, ge=0, le=1_000_000)
    is_active: bool = True

    @field_validator("sku")
    @classmethod
    def _normalise_sku(cls, value: str) -> str:
        return value.strip().upper()


class ProductUpdateRequest(BaseModel):
    """Partial update. Every field optional; omitted fields are left untouched."""

    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    price: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000"))
    category_id: int | None = Field(default=None, ge=1)
    brand: str | None = Field(default=None, min_length=1, max_length=80)
    rating: Decimal | None = Field(default=None, ge=0, le=5)
    image_url: str | None = Field(default=None, max_length=512)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> ProductUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class StockUpdateRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"quantity": 25}]})

    quantity: StrictQuantity = Field(ge=0, le=1_000_000, description="Absolute stock level to set")


class InventoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    sku: str
    name: str
    quantity: int
    updated_at: datetime


class BrandResponse(BaseModel):
    """Facet entry used to populate the brand filter in the UI."""

    brand: str
    product_count: int
