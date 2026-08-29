"""Authentication and account request/response bodies."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.security import PasswordPolicyError, validate_password_strength
from app.schemas.common import EmailAddress


class RegisterRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "ada@example.com",
                    "password": "Str0ngPass!",
                    "password_confirm": "Str0ngPass!",
                    "full_name": "Ada Lovelace",
                    "phone": "+1-555-0100",
                }
            ]
        }
    )

    email: EmailAddress
    password: str = Field(min_length=1, max_length=128)
    password_confirm: str = Field(min_length=1, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("Full name must be at least 2 characters")
        return cleaned

    @field_validator("password")
    @classmethod
    def _enforce_policy(cls, value: str) -> str:
        # Re-raised as ValueError so it surfaces through the normal 422
        # validation envelope with the policy text intact.
        try:
            validate_password_strength(value)
        except PasswordPolicyError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def _passwords_match(self) -> RegisterRequest:
        if self.password != self.password_confirm:
            raise ValueError("Password and confirmation do not match")
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"email": "ada@example.com", "password": "Str0ngPass!"}]}
    )

    email: EmailAddress
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailAddress
    full_name: str
    phone: str | None = None
    role: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIs...",
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "user": {
                        "id": 1,
                        "email": "ada@example.com",
                        "full_name": "Ada Lovelace",
                        "role": "customer",
                        "is_active": True,
                    },
                }
            ]
        }
    )

    access_token: str
    # The OAuth 2.0 token type, not a password.
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Token lifetime in seconds")
    user: UserResponse


class UpdateProfileRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateProfileRequest:
        if self.full_name is None and self.phone is None:
            raise ValueError("Provide at least one field to update")
        return self


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _enforce_policy(cls, value: str) -> str:
        try:
            validate_password_strength(value)
        except PasswordPolicyError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def _must_actually_change(self) -> ChangePasswordRequest:
        if self.current_password == self.new_password:
            raise ValueError("New password must be different from the current password")
        return self
