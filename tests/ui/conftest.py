"""Playwright fixtures: browser lifecycle, sessions and failure artifacts.

Deliberately does *not* use ``pytest-playwright``'s own fixtures, because this
suite needs two things they do not give:

* **Per-test context, shared browser.** Launching a browser per test costs
  roughly a second each; a fresh *context* costs milliseconds and is what
  actually provides isolation - its own cookies, its own localStorage, its own
  cache. So the browser is session-scoped and the context is function-scoped.
* **Artifacts only on failure.** A trace per passing test would produce
  hundreds of megabytes of CI artifacts nobody opens. Screenshot, video and
  trace are captured for failures, where they are the difference between a
  diagnosable CI failure and a re-run.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import allure
import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    expect,
    sync_playwright,
)

from tests.api.clients import AuthClient
from tests.configuration.settings import settings
from tests.ui.pages.account import (
    AdminPage,
    CartPage,
    CheckoutPage,
    LoginPage,
    OrderConfirmationPage,
    OrderDetailPage,
    OrdersPage,
    ProfilePage,
    RegisterPage,
)
from tests.ui.pages.catalog import HomePage, ProductDetailPage, ProductsPage

ARTIFACTS = settings.artifacts_dir / "ui"
VIDEO_DIR = ARTIFACTS / "videos"
TRACE_DIR = ARTIFACTS / "traces"
SCREENSHOT_DIR = ARTIFACTS / "screenshots"

# Playwright's `expect()` keeps its own 5-second default, entirely separate
# from the context timeout set below. Without this line an assertion could
# time out at 5s while the action that preceded it was allowed 15s - which
# is exactly the kind of inconsistency that produces failures under load and
# passes when run alone.
expect.set_options(timeout=settings.ui_timeout_ms)


def _safe_name(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)[:120]


@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Playwright]:
    with sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    """One browser process for the whole session.

    ``--browser`` from pytest-playwright is not available here, so the engine is
    selected with the BROWSER environment variable, which also keeps the CI
    matrix a simple env change.
    """
    engine = {
        "chromium": playwright_instance.chromium,
        "firefox": playwright_instance.firefox,
        "webkit": playwright_instance.webkit,
    }.get(settings.browser.lower())
    if engine is None:
        raise pytest.UsageError(
            f"BROWSER must be chromium, firefox or webkit; got {settings.browser!r}"
        )

    instance = engine.launch(headless=settings.headless, slow_mo=settings.slow_mo_ms)
    yield instance
    instance.close()


@pytest.fixture
def context(browser: Browser, request: pytest.FixtureRequest) -> Iterator[BrowserContext]:
    """A fresh, isolated browser context per test."""
    for directory in (VIDEO_DIR, TRACE_DIR, SCREENSHOT_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    record_video = settings.capture_video in ("on", "retain-on-failure")
    ctx = browser.new_context(
        base_url=settings.ui_base_url,
        viewport={"width": 1440, "height": 900},
        ignore_https_errors=True,
        record_video_dir=str(VIDEO_DIR) if record_video else None,
        record_video_size={"width": 1280, "height": 800} if record_video else None,
    )
    ctx.set_default_timeout(settings.ui_timeout_ms)
    ctx.set_default_navigation_timeout(settings.ui_timeout_ms)

    if settings.capture_trace in ("on", "retain-on-failure"):
        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)

    yield ctx

    failed = getattr(request.node, "_ui_test_failed", False)
    name = _safe_name(request.node.nodeid)

    if settings.capture_trace != "off":
        trace_path = TRACE_DIR / f"{name}.zip"
        if failed or settings.capture_trace == "on":
            ctx.tracing.stop(path=str(trace_path))
            _attach_file(trace_path, "Playwright trace", extension="zip")
        else:
            ctx.tracing.stop()

    ctx.close()  # videos are only finalised once the context is closed

    if record_video:
        _handle_videos(ctx, failed=failed, name=name)


def _handle_videos(ctx: BrowserContext, *, failed: bool, name: str) -> None:
    """Keep recordings for failures; discard the rest.

    Called only after the context is closed, because Playwright finalises video
    files at that point and reading one earlier yields a truncated recording.
    """
    keep = failed or settings.capture_video == "on"

    for index, page in enumerate(ctx.pages):
        video = page.video
        if video is None:
            continue
        try:
            if keep:
                suffix = "" if index == 0 else f"-{index}"
                target = VIDEO_DIR / f"{name}{suffix}.webm"
                video.save_as(str(target))
                _attach_file(target, "Video", allure.attachment_type.WEBM)
            else:
                # Otherwise a full suite run fills the disk with recordings of
                # tests that passed and nobody will ever watch.
                video.delete()
        except Exception:  # noqa: S110
            # Artifact handling must never turn a passing test into a failure,
            # nor mask the real reason a failing one failed.
            pass


def _attach_file(
    path: Path, name: str, attachment_type: Any = None, *, extension: str | None = None
) -> None:
    """Attach a file to the Allure report, tolerating anything going wrong.

    Allure has no ZIP attachment type, so Playwright traces are attached by
    extension instead - which still makes them downloadable from the report.
    """
    if not path.exists():
        return
    with contextlib.suppress(Exception):
        allure.attach.file(
            str(path), name=name, attachment_type=attachment_type, extension=extension
        )


@pytest.fixture
def page(context: BrowserContext, request: pytest.FixtureRequest) -> Iterator[Page]:
    """A page that reports browser console errors and captures a screenshot on failure."""
    browser_page = context.new_page()

    console_errors: list[str] = []
    browser_page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    browser_page.on("pageerror", lambda error: console_errors.append(str(error)))

    yield browser_page

    if getattr(request.node, "_ui_test_failed", False):
        name = _safe_name(request.node.nodeid)
        screenshot = SCREENSHOT_DIR / f"{name}.png"
        try:
            browser_page.screenshot(path=str(screenshot), full_page=True)
            _attach_file(screenshot, "Screenshot on failure", allure.attachment_type.PNG)
            allure.attach(
                browser_page.url, name="URL at failure", attachment_type=allure.attachment_type.TEXT
            )
        except Exception:  # noqa: S110
            pass

    if console_errors:
        # Attached rather than asserted: a console error does not always mean the
        # behaviour under test is broken, but it is nearly always worth seeing.
        allure.attach(
            "\n".join(console_errors),
            name="Browser console errors",
            attachment_type=allure.attachment_type.TEXT,
        )


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Any:
    """Record failure so the context and page fixtures can act on it in teardown.

    pytest does not expose the outcome to fixtures directly, so the standard
    approach is to stash it on the item during the report hook.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when in ("setup", "call") and report.failed:
        item._ui_test_failed = True


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
@pytest.fixture
def logged_in_page(page: Page, customer) -> Page:
    """A page already authenticated as this test's own customer.

    The token is injected into localStorage rather than typed into the login
    form, for two reasons: a test about the cart should not fail because the
    login form changed, and skipping the form removes a bcrypt verification and
    a page load from every test that merely needs a session. Tests that are
    *about* logging in use the login page properly.
    """
    page.goto(f"{settings.ui_base_url}/", wait_until="domcontentloaded")
    page.evaluate("token => localStorage.setItem('shopsphere.token', token)", customer.token)
    page.reload(wait_until="domcontentloaded")
    return page


@pytest.fixture
def admin_page(page: Page, admin_token: str) -> Page:
    """The default page, signed in as the administrator.

    Note that this *replaces* the session on the shared page. A test that needs
    an admin and a customer at the same time must use ``second_admin_page``,
    which has a context of its own.
    """
    page.goto(f"{settings.ui_base_url}/", wait_until="domcontentloaded")
    page.evaluate("token => localStorage.setItem('shopsphere.token', token)", admin_token)
    page.reload(wait_until="domcontentloaded")
    return page


@pytest.fixture
def second_admin_page(browser: Browser, admin_token: str) -> Iterator[Page]:
    """An administrator in a completely separate browser context.

    Necessary for any test with two roles on screen at once. ``page`` and
    ``admin_page`` are the *same* page - injecting an admin token there signs
    the customer out, so a test that asked for both would silently be driving
    one browser wearing two hats.

    A second context is the faithful model: its own storage, its own session,
    genuinely independent of the customer's.
    """
    context = browser.new_context(
        base_url=settings.ui_base_url, viewport={"width": 1440, "height": 900}
    )
    context.set_default_timeout(settings.ui_timeout_ms)
    admin = context.new_page()
    admin.goto(f"{settings.ui_base_url}/", wait_until="domcontentloaded")
    admin.evaluate("token => localStorage.setItem('shopsphere.token', token)", admin_token)
    admin.reload(wait_until="domcontentloaded")

    yield admin

    context.close()


@pytest.fixture
def ui_customer(auth_client: AuthClient, customer):
    """Alias making it explicit in UI tests which account is being driven."""
    return customer


# ---------------------------------------------------------------------------
# Page objects
# ---------------------------------------------------------------------------
@pytest.fixture
def home_page(page: Page) -> HomePage:
    return HomePage(page)


@pytest.fixture
def products_page(page: Page) -> ProductsPage:
    return ProductsPage(page)


@pytest.fixture
def product_detail_page(page: Page) -> ProductDetailPage:
    return ProductDetailPage(page)


@pytest.fixture
def login_page(page: Page) -> LoginPage:
    return LoginPage(page)


@pytest.fixture
def register_page(page: Page) -> RegisterPage:
    return RegisterPage(page)


@pytest.fixture
def cart_page(logged_in_page: Page) -> CartPage:
    return CartPage(logged_in_page)


@pytest.fixture
def checkout_page(logged_in_page: Page) -> CheckoutPage:
    return CheckoutPage(logged_in_page)


@pytest.fixture
def confirmation_page(logged_in_page: Page) -> OrderConfirmationPage:
    return OrderConfirmationPage(logged_in_page)


@pytest.fixture
def orders_page(logged_in_page: Page) -> OrdersPage:
    return OrdersPage(logged_in_page)


@pytest.fixture
def order_detail_page(logged_in_page: Page) -> OrderDetailPage:
    return OrderDetailPage(logged_in_page)


@pytest.fixture
def profile_page(logged_in_page: Page) -> ProfilePage:
    return ProfilePage(logged_in_page)


@pytest.fixture
def admin_console(admin_page: Page) -> AdminPage:
    return AdminPage(admin_page)
