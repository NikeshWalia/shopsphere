"""Repository-root pytest configuration.

Three jobs:

1. Make ``app`` (the backend package) and ``tests`` importable regardless of the
   directory pytest was invoked from.
2. Apply the suite marker automatically from a test's location, so `pytest -m
   api` is always accurate and a new file cannot silently miss its marker.
3. Refuse to run suites that need the running stack when the stack is down -
   once, with an actionable message, instead of sixty identical connection
   errors.

An alternative to (1) would be installing the backend as a package, but that
would imply the repository is a distributable library, which it is not - see the
note in pyproject.toml.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parent
BACKEND = REPO_ROOT / "backend"

for path in (REPO_ROOT, BACKEND):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

# Directory -> marker. The single source of truth for which suite a test is in.
SUITE_MARKERS: dict[str, str] = {
    "backend/tests/unit": "unit",
    "tests/api": "api",
    "tests/ui": "ui",
    "tests/database": "database",
    "tests/integration": "integration",
    "tests/contract": "contract",
    "tests/security": "security",
}

# Suites that require a running API, database and payment provider.
NEEDS_LIVE_STACK = {"api", "ui", "database", "integration", "contract", "security", "e2e", "smoke"}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--skip-health-check",
        action="store_true",
        default=False,
        help="Do not verify that the API is reachable before running tests.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply suite markers by path, then gate on the stack being up.

    Marking by path rather than by hand means `pytest -m security` can never
    miss a file somebody forgot to decorate, and the marker in the report always
    matches where the test actually lives.
    """
    for item in items:
        relative = item.path.relative_to(REPO_ROOT).as_posix() if item.path else ""
        for prefix, marker in SUITE_MARKERS.items():
            if relative.startswith(prefix):
                item.add_marker(getattr(pytest.mark, marker))
                break

    if config.getoption("--skip-health-check"):
        return

    needs_stack = any(
        NEEDS_LIVE_STACK & {mark.name for mark in item.iter_markers()} for item in items
    )
    if needs_stack:
        _require_live_stack()


def _reachable(url: str, timeout: float = 3.0) -> bool:
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _require_live_stack() -> None:
    # Imported lazily so the unit suite does not need the test platform's
    # dependencies merely to collect.
    from tests.configuration.settings import settings

    if not _reachable(settings.api_base_url):
        raise pytest.UsageError(
            f"\n\nThe ShopSphere API is not reachable at {settings.api_base_url}.\n\n"
            f"Start the stack first:\n"
            f"    make up      # everything in Docker\n"
            f"    make dev     # or run the services directly\n\n"
            f"To run only the tests that need nothing running:\n"
            f"    pytest backend/tests\n\n"
            f"To bypass this check entirely, pass --skip-health-check.\n"
        )

    import httpx

    try:
        response = httpx.get(f"{settings.api_base_url}/health/ready", timeout=10.0)
        checks = response.json().get("checks", {})
    except (httpx.HTTPError, ValueError) as exc:
        raise pytest.UsageError(
            f"\n\nThe API at {settings.api_base_url} did not answer its readiness probe: {exc}\n"
        ) from exc

    if checks.get("database") != "ok":
        raise pytest.UsageError(
            f"\n\nThe API is running but its database is unavailable: {checks}\n"
            f"Check PostgreSQL is up and migrations have been applied:\n"
            f"    make migrate && make seed\n"
        )
