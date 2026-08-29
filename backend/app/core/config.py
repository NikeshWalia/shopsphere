"""Application configuration.

Every tunable value is read from the environment so that the same image can run
locally, in CI and in Docker without code changes. Defaults are chosen to be
safe for local development only; :func:`Settings.validate_production_safety`
refuses to start with development secrets outside the ``local`` environment.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

# Recognisable placeholder. Any deployment that still carries this value is by
# definition not configured, so we fail fast rather than sign tokens with it.
DEV_SECRET_KEY = "dev-only-insecure-secret-change-me"  # noqa: S105


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application -------------------------------------------------------
    app_name: str = "ShopSphere API"
    app_version: str = "1.0.0"
    environment: Literal["local", "test", "ci", "production"] = "local"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # -- Database ----------------------------------------------------------
    database_url: str = "postgresql+psycopg://shopsphere:shopsphere@localhost:5432/shopsphere"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_pre_ping: bool = True
    db_echo: bool = False
    # Postgres aborts a statement that exceeds this. Prevents a pathological
    # query from pinning a connection for the lifetime of the process.
    db_statement_timeout_ms: int = 15_000

    # -- Authentication ----------------------------------------------------
    secret_key: str = DEV_SECRET_KEY
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = 60
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)
    password_min_length: int = 8

    # -- CORS --------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"

    # -- Commerce rules ----------------------------------------------------
    currency: str = "USD"
    tax_rate: Decimal = Decimal("0.08")
    shipping_flat_fee: Decimal = Decimal("9.99")
    free_shipping_threshold: Decimal = Decimal("100.00")
    max_item_quantity: int = 99
    max_cart_items: int = 50

    # -- Payment provider --------------------------------------------------
    payment_service_url: str = "http://127.0.0.1:9100"
    payment_timeout_seconds: float = 8.0
    # Global chaos switch. ``card`` (the default) means the outcome is decided
    # by the card number, which lets individual tests choose an outcome without
    # mutating shared process state. Any other value forces every charge.
    payment_mode: Literal["card", "success", "declined", "timeout", "server_error"] = "card"

    # -- Observability -----------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    slow_request_ms: int = 1_000

    # -- Seed data ---------------------------------------------------------
    seed_admin_email: str = "admin@shopsphere.test"
    # Demo credentials for the seeded accounts, published in the README so a
    # newcomer can sign in. Overridden by SEED_* environment variables, and the
    # production guard above refuses to start with the placeholder SECRET_KEY.
    seed_admin_password: str = "AdminPass123!"  # noqa: S105
    seed_customer_password: str = "CustomerPass123!"  # noqa: S105

    @field_validator("tax_rate")
    @classmethod
    def _tax_rate_is_a_fraction(cls, value: Decimal) -> Decimal:
        if not Decimal("0") <= value < Decimal("1"):
            raise ValueError("tax_rate must be a fraction between 0 and 1, e.g. 0.08 for 8%")
        return value

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, value: str) -> str:
        # SQLAlchemy silently falls back to psycopg2 for a bare ``postgresql://``
        # URL, which is not installed. Normalise instead of failing at connect time.
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if self.environment == "production":
            if self.secret_key == DEV_SECRET_KEY:
                raise ValueError(
                    "SECRET_KEY must be set to a unique value outside local development"
                )
            if self.debug:
                raise ValueError("DEBUG must be disabled in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_testing(self) -> bool:
        return self.environment in ("test", "ci")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that configuration is parsed and validated exactly once. Tests
    that need different configuration call ``get_settings.cache_clear()``.
    """
    return Settings()


settings = get_settings()
