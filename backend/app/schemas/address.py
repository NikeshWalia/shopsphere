"""Shipping address request/response bodies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AddressCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "label": "Home",
                    "full_name": "Ada Lovelace",
                    "line1": "12 Analytical Way",
                    "line2": "Apt 4",
                    "city": "Cambridge",
                    "state": "MA",
                    "postal_code": "02139",
                    "country": "US",
                    "phone": "+1-555-0100",
                    "is_default": True,
                }
            ]
        }
    )

    label: str = Field(default="Home", min_length=1, max_length=50)
    full_name: str = Field(min_length=2, max_length=120)
    line1: str = Field(min_length=3, max_length=160)
    line2: str | None = Field(default=None, max_length=160)
    city: str = Field(min_length=1, max_length=80)
    state: str = Field(min_length=1, max_length=80)
    postal_code: str = Field(min_length=3, max_length=20)
    country: str = Field(default="US", min_length=2, max_length=2)
    phone: str | None = Field(default=None, max_length=32)
    is_default: bool = False

    @field_validator("country")
    @classmethod
    def _uppercase_country(cls, value: str) -> str:
        # ISO 3166-1 alpha-2 is uppercase; accepting "us" and storing "US"
        # avoids duplicate-looking values in the database.
        if not value.isalpha():
            raise ValueError("Country must be a 2-letter ISO code, e.g. US")
        return value.upper()


class AddressUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=50)
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    line1: str | None = Field(default=None, min_length=3, max_length=160)
    line2: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, min_length=1, max_length=80)
    state: str | None = Field(default=None, min_length=1, max_length=80)
    postal_code: str | None = Field(default=None, min_length=3, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    phone: str | None = Field(default=None, max_length=32)
    is_default: bool | None = None

    @field_validator("country")
    @classmethod
    def _uppercase_country(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.isalpha():
            raise ValueError("Country must be a 2-letter ISO code, e.g. US")
        return value.upper()

    @model_validator(mode="after")
    def _at_least_one_field(self) -> AddressUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    full_name: str
    line1: str
    line2: str | None
    city: str
    state: str
    postal_code: str
    country: str
    phone: str | None
    is_default: bool
