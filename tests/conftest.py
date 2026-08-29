"""Root configuration for the quality-engineering platform.

Responsibilities:

* Register the fixture modules under ``tests/fixtures/`` as plugins, so fixtures
  live in focused files instead of one sprawling conftest.
* Attach environment metadata and failure categories to the Allure report.

The "is the stack actually running?" gate lives in the repository-root
conftest.py, so it applies however pytest is invoked.
"""

from __future__ import annotations

import os

import allure
import pytest

from tests.configuration.settings import settings

pytest_plugins = [
    "tests.fixtures.clients",
    "tests.fixtures.users",
    "tests.fixtures.data",
]


def pytest_configure(config: pytest.Config) -> None:
    """Write environment metadata into the Allure results directory.

    An Allure report is far less useful when you cannot tell which environment,
    branch or commit produced it - especially when comparing a CI failure with
    a local pass.
    """
    allure_dir = config.getoption("--alluredir", default=None)
    if not allure_dir:
        return

    from pathlib import Path

    target = Path(str(allure_dir))
    target.mkdir(parents=True, exist_ok=True)

    properties = {
        "API.Base.URL": settings.api_base_url,
        "UI.Base.URL": settings.ui_base_url,
        "Payment.Provider.URL": settings.payment_mock_url,
        "Browser": settings.browser,
        "Headless": str(settings.headless),
        "Python": os.sys.version.split()[0],
        "CI": os.getenv("CI", "false"),
        "Git.Branch": os.getenv("GITHUB_REF_NAME", os.getenv("GIT_BRANCH", "local")),
        "Git.Commit": os.getenv("GITHUB_SHA", os.getenv("GIT_COMMIT", "unknown"))[:12],
    }
    (target / "environment.properties").write_text(
        "\n".join(f"{key}={value}" for key, value in properties.items()),
        encoding="utf-8",
    )

    # Groups reruns and known-failure categories sensibly in the report.
    categories = """[
      {"name": "Product defects", "matchedStatuses": ["failed"]},
      {"name": "Test infrastructure", "matchedStatuses": ["broken"],
       "traceRegex": ".*(ConnectionError|ConnectTimeout|OperationalError).*"},
      {"name": "Skipped by configuration", "matchedStatuses": ["skipped"]}
    ]"""
    (target / "categories.json").write_text(categories, encoding="utf-8")


@pytest.fixture(autouse=True)
def _tag_environment() -> None:
    """Label every test with the environment it ran against."""
    allure.dynamic.label("environment", settings.api_base_url)
