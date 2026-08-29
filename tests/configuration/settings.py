"""Test-platform configuration.

Every environment-specific value the suites need lives here, read from the
environment with sensible local defaults. Nothing in `tests/` hardcodes a URL,
a credential or a connection string - which is what lets the identical suite run
against a local stack, against Docker Compose and in CI by changing only
environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# Loaded without override so real environment variables always beat the file.
# CI sets them directly; a developer's .env fills the gaps.
load_dotenv(REPO_ROOT / ".env", override=False)


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TestSettings:
    # -- Targets -----------------------------------------------------------
    api_base_url: str = field(default_factory=lambda: _env("API_BASE_URL", "http://127.0.0.1:8000"))
    ui_base_url: str = field(default_factory=lambda: _env("UI_BASE_URL", "http://127.0.0.1:5173"))
    payment_mock_url: str = field(
        default_factory=lambda: _env("PAYMENT_MOCK_URL", "http://127.0.0.1:9100")
    )
    api_prefix: str = "/api/v1"

    # -- Database ----------------------------------------------------------
    # Credentials come from the environment, never from source. Tests connect
    # directly to assert on persisted state that the API does not expose.
    database_url: str = field(
        default_factory=lambda: _env(
            "TEST_DATABASE_URL",
            "postgresql://shopsphere:shopsphere@127.0.0.1:5432/shopsphere",
        )
    )

    # -- Seeded accounts ---------------------------------------------------
    admin_email: str = field(
        default_factory=lambda: _env("TEST_ADMIN_EMAIL", "admin@shopsphere.test")
    )
    admin_password: str = field(
        default_factory=lambda: _env("TEST_ADMIN_PASSWORD", "AdminPass123!")
    )
    customer_email: str = field(
        default_factory=lambda: _env("TEST_CUSTOMER_EMAIL", "alice@shopsphere.test")
    )
    customer_password: str = field(
        default_factory=lambda: _env("TEST_CUSTOMER_PASSWORD", "CustomerPass123!")
    )

    # Must match the backend's SECRET_KEY. Security tests mint expired and
    # tampered tokens with it, which is how token-expiry can be tested in
    # milliseconds instead of by sleeping until a real token expires.
    jwt_secret: str = field(
        default_factory=lambda: _env("TEST_JWT_SECRET", "dev-only-insecure-secret-change-me")
    )
    jwt_algorithm: str = field(default_factory=lambda: _env("JWT_ALGORITHM", "HS256"))

    # -- Commerce rules, mirrored so tests can assert exact amounts ---------
    tax_rate: str = field(default_factory=lambda: _env("TAX_RATE", "0.08"))
    shipping_flat_fee: str = field(default_factory=lambda: _env("SHIPPING_FLAT_FEE", "9.99"))
    free_shipping_threshold: str = field(
        default_factory=lambda: _env("FREE_SHIPPING_THRESHOLD", "100.00")
    )
    max_item_quantity: int = field(default_factory=lambda: _env_int("MAX_ITEM_QUANTITY", 99))

    # -- Timeouts and budgets ---------------------------------------------
    api_timeout_seconds: float = field(
        default_factory=lambda: _env_float("API_TIMEOUT_SECONDS", 30.0)
    )
    ui_timeout_ms: int = field(default_factory=lambda: _env_int("UI_TIMEOUT_MS", 15_000))
    # A regression guard against pathological queries, not a benchmark. Set
    # generously so it only fires on a genuine problem.
    api_sla_ms: int = field(default_factory=lambda: _env_int("API_SLA_MS", 2_000))

    # -- Browser -----------------------------------------------------------
    headless: bool = field(default_factory=lambda: _env_bool("HEADLESS", True))
    browser: str = field(default_factory=lambda: _env("BROWSER", "chromium"))
    slow_mo_ms: int = field(default_factory=lambda: _env_int("SLOW_MO_MS", 0))
    capture_video: str = field(default_factory=lambda: _env("CAPTURE_VIDEO", "retain-on-failure"))
    capture_trace: str = field(default_factory=lambda: _env("CAPTURE_TRACE", "retain-on-failure"))

    # -- Artifacts ---------------------------------------------------------
    artifacts_dir: Path = field(default_factory=lambda: REPO_ROOT / "artifacts")
    allure_results_dir: Path = field(default_factory=lambda: REPO_ROOT / "allure-results")

    @property
    def api_url(self) -> str:
        """Fully-qualified base for versioned API calls."""
        return f"{self.api_base_url.rstrip('/')}{self.api_prefix}"

    def url(self, path: str) -> str:
        return f"{self.api_url}/{path.lstrip('/')}"

    @property
    def psycopg_dsn(self) -> str:
        """psycopg understands plain postgresql:// but not SQLAlchemy's +driver form."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://")


@lru_cache(maxsize=1)
def get_settings() -> TestSettings:
    return TestSettings()


settings = get_settings()
