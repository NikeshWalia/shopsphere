"""Page Object base class.

Design rules, applied consistently across every page object:

**Locators, not actions, are the interface.** A page object exposes
``self.add_to_cart_button`` rather than ``click_add_to_cart()`` wherever a raw
locator is enough. Playwright's locators are lazy and auto-waiting, so returning
them lets tests compose assertions (``expect(page.total).to_have_text(...)``)
without the page object having to anticipate every assertion.

**No sleeps. Ever.** Playwright's actionability checks already wait for an
element to be visible, stable, enabled and unobscured before acting. Where a
test needs to wait for something Playwright cannot see - a network response, a
count changing - it waits on that specific condition, never on the clock.

**Test ids, not text.** Selectors use ``data-testid`` so a copy change or a
restyle does not break the suite; only a genuine change in what an element *is*
does.
"""

from __future__ import annotations

import re

from playwright.sync_api import Locator, Page, Response, expect

from tests.configuration.settings import settings


class BasePage:
    """Shared navigation, header and waiting behaviour."""

    #: Overridden by subclasses; used by :meth:`open`.
    path: str = "/"

    def __init__(self, page: Page) -> None:
        self.page = page
        self.base_url = settings.ui_base_url.rstrip("/")

    # -- Navigation --------------------------------------------------------
    def open(self, path: str | None = None) -> None:
        """Navigate to this page.

        Waits for ``domcontentloaded`` rather than ``networkidle``: this is a
        SPA that polls and lazy-loads images, so "no network activity for 500ms"
        may never be true, and waiting for it is a classic source of flakiness.
        The subsequent locator assertions provide the real synchronisation.
        """
        self.page.goto(f"{self.base_url}{path or self.path}", wait_until="domcontentloaded")

    def reload(self) -> None:
        self.page.reload(wait_until="domcontentloaded")

    @property
    def current_path(self) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(self.page.url)
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")

    def expect_path(self, path: str) -> None:
        """Assert the browser landed on a path, waiting for navigation.

        Anchored on a compiled prefix pattern rather than an exact string so a
        query string the test does not care about ("?q=laptop") still matches,
        while a different route never does.
        """
        expect(self.page).to_have_url(re.compile(f"^{re.escape(self.base_url + path)}"))

    # -- Header ------------------------------------------------------------
    @property
    def header(self) -> Locator:
        return self.page.get_by_test_id("site-header")

    @property
    def cart_link(self) -> Locator:
        return self.page.get_by_test_id("nav-cart")

    @property
    def cart_count(self) -> Locator:
        return self.page.get_by_test_id("cart-count")

    @property
    def login_link(self) -> Locator:
        return self.page.get_by_test_id("nav-login")

    @property
    def register_link(self) -> Locator:
        return self.page.get_by_test_id("nav-register")

    @property
    def logout_button(self) -> Locator:
        return self.page.get_by_test_id("logout-button")

    @property
    def admin_link(self) -> Locator:
        return self.page.get_by_test_id("nav-admin")

    @property
    def orders_link(self) -> Locator:
        return self.page.get_by_test_id("nav-orders")

    @property
    def search_input(self) -> Locator:
        return self.page.get_by_test_id("header-search-input")

    def search(self, term: str) -> None:
        self.search_input.fill(term)
        self.page.get_by_test_id("header-search-submit").click()

    def go_to_cart(self) -> None:
        self.cart_link.click()

    def sign_out(self) -> None:
        self.logout_button.click()

    def expect_cart_badge(self, count: int) -> None:
        """Wait for the badge to settle on a value.

        The badge is repainted by an asynchronous cart reload, so reading it
        with ``cart_badge_count`` immediately after an action races that reload;
        this waits for the value instead.
        """
        expect(self.cart_count).to_have_text(str(count), timeout=settings.ui_timeout_ms)

    def expect_no_cart_badge(self) -> None:
        expect(self.cart_count).to_have_count(0, timeout=settings.ui_timeout_ms)

    def cart_badge_count(self) -> int:
        """The number on the cart badge, or 0 when it is not rendered.

        The badge is deliberately absent rather than showing "0", so its absence
        is itself a meaningful assertion.
        """
        if self.cart_count.count() == 0:
            return 0
        text = self.cart_count.text_content() or "0"
        return int(text.strip() or 0)

    # -- Feedback ----------------------------------------------------------
    @property
    def toast(self) -> Locator:
        return self.page.get_by_test_id("toast")

    @property
    def error_message(self) -> Locator:
        return self.page.get_by_test_id("error-message")

    def expect_toast(self, variant: str | None = None) -> Locator:
        toast = self.toast.first
        expect(toast).to_be_visible()
        if variant:
            expect(toast).to_have_attribute("data-variant", variant)
        return toast

    # -- Waiting on things Playwright cannot infer -------------------------
    def wait_for_api(self, url_fragment: str, method: str = "GET") -> Response:
        """Wait for a specific backend call to complete.

        Used where a UI change is driven by a request whose completion has no
        visible signal - the honest alternative to guessing with a sleep.
        """
        with self.page.expect_response(
            lambda response: url_fragment in response.url and response.request.method == method
        ) as info:
            pass
        return info.value

    def expect_loaded(self, testid: str) -> Locator:
        """Wait for a page's root element, which is its 'ready' signal."""
        root = self.page.get_by_test_id(testid)
        expect(root).to_be_visible(timeout=settings.ui_timeout_ms)
        return root

    # -- Small helpers -----------------------------------------------------
    @staticmethod
    def money_value(locator: Locator) -> float:
        """Read a money amount from ``data-amount`` rather than parsing "$1,249.00".

        Parsing formatted currency couples the assertion to locale and to
        presentation; the raw attribute is what the API actually returned.
        """
        raw = locator.get_attribute("data-amount")
        assert raw is not None, "Element does not expose a data-amount attribute"
        return float(raw)

    @staticmethod
    def int_attr(locator: Locator, name: str) -> int:
        raw = locator.get_attribute(name)
        assert raw is not None, f"Element does not expose a {name} attribute"
        return int(raw)
