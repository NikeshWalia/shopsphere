"""Page objects for authentication, cart, checkout, orders and admin."""

from __future__ import annotations

from playwright.sync_api import Locator, expect

from tests.configuration.settings import settings
from tests.ui.pages.base import BasePage

#: Order-item rows are addressed by product, which is how a test knows a line.
ITEM_SELECTOR = '[data-testid="order-item"][data-product-id="{product_id}"]'


class LoginPage(BasePage):
    path = "/login"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("login-page")

    @property
    def email_input(self) -> Locator:
        return self.page.get_by_test_id("login-email")

    @property
    def password_input(self) -> Locator:
        return self.page.get_by_test_id("login-password")

    @property
    def submit_button(self) -> Locator:
        return self.page.get_by_test_id("login-submit")

    @property
    def error(self) -> Locator:
        return self.page.get_by_test_id("login-error")

    def login(self, email: str, password: str) -> None:
        self.email_input.fill(email)
        self.password_input.fill(password)
        self.submit_button.click()

    def login_and_wait(self, email: str, password: str) -> None:
        """Log in and wait for the header to reflect a signed-in session.

        Waiting for the logout button - rather than for a URL - is what makes
        this deterministic: the redirect and the state update are separate, and
        only the second one means "logged in".
        """
        self.login(email, password)
        expect(self.logout_button).to_be_visible(timeout=settings.ui_timeout_ms)


class RegisterPage(BasePage):
    path = "/register"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("register-page")

    @property
    def name_input(self) -> Locator:
        return self.page.get_by_test_id("register-name")

    @property
    def email_input(self) -> Locator:
        return self.page.get_by_test_id("register-email")

    @property
    def password_input(self) -> Locator:
        return self.page.get_by_test_id("register-password")

    @property
    def password_confirm_input(self) -> Locator:
        return self.page.get_by_test_id("register-password-confirm")

    @property
    def submit_button(self) -> Locator:
        return self.page.get_by_test_id("register-submit")

    @property
    def error(self) -> Locator:
        return self.page.get_by_test_id("register-error")

    def register(
        self,
        *,
        email: str,
        password: str,
        full_name: str = "UI Test User",
        password_confirm: str | None = None,
    ) -> None:
        self.name_input.fill(full_name)
        self.email_input.fill(email)
        self.password_input.fill(password)
        self.password_confirm_input.fill(
            password_confirm if password_confirm is not None else password
        )
        self.submit_button.click()


class CartPage(BasePage):
    path = "/cart"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("cart-page")

    @property
    def lines(self) -> Locator:
        return self.page.get_by_test_id("cart-line")

    @property
    def empty_state(self) -> Locator:
        return self.page.get_by_test_id("empty-cart")

    @property
    def issues(self) -> Locator:
        return self.page.get_by_test_id("cart-issues")

    @property
    def checkout_button(self) -> Locator:
        return self.page.get_by_test_id("checkout-button")

    @property
    def clear_button(self) -> Locator:
        return self.page.get_by_test_id("clear-cart")

    @property
    def subtotal(self) -> Locator:
        return self.page.get_by_test_id("summary-subtotal")

    @property
    def tax(self) -> Locator:
        return self.page.get_by_test_id("summary-tax")

    @property
    def shipping(self) -> Locator:
        return self.page.get_by_test_id("summary-shipping")

    @property
    def total(self) -> Locator:
        return self.page.get_by_test_id("summary-total")

    def line_for(self, product_id: int) -> Locator:
        return self.page.locator(f'[data-testid="cart-line"][data-product-id="{product_id}"]')

    def line_quantity(self, product_id: int) -> int:
        return self.int_attr(self.line_for(product_id), "data-quantity")

    def line_total(self, product_id: int) -> float:
        return self.money_value(self.line_for(product_id).get_by_test_id("cart-line-total"))

    def line_unit_price(self, product_id: int) -> float:
        return self.money_value(self.line_for(product_id).get_by_test_id("cart-line-unit-price"))

    def line_stepper(self, product_id: int) -> Locator:
        return self.line_for(product_id).get_by_test_id("cart-line-quantity")

    def line_max_quantity(self, product_id: int) -> int:
        """The ceiling the stepper enforces, which the server derives from stock."""
        return self.int_attr(self.line_stepper(product_id), "data-max")

    def increase_button(self, product_id: int) -> Locator:
        return self.line_for(product_id).get_by_test_id("quantity-increase")

    def decrease_button(self, product_id: int) -> Locator:
        return self.line_for(product_id).get_by_test_id("quantity-decrease")

    def line_stock_warning(self, product_id: int) -> Locator:
        return self.line_for(product_id).get_by_test_id("cart-line-stock-warning")

    def increase_quantity(self, product_id: int) -> None:
        """Click + and wait for the server-recomputed cart to arrive.

        Waiting on the response - not on the number changing - is what keeps
        this deterministic: the button click and the re-render are separated by
        a round trip, and asserting too early would race it.
        """
        line = self.line_for(product_id)
        with self.page.expect_response(
            lambda r: "/api/v1/cart/items" in r.url and r.request.method == "PATCH"
        ):
            line.get_by_test_id("quantity-increase").click()

    def decrease_quantity(self, product_id: int) -> None:
        line = self.line_for(product_id)
        with self.page.expect_response(
            lambda r: "/api/v1/cart/items" in r.url and r.request.method == "PATCH"
        ):
            line.get_by_test_id("quantity-decrease").click()

    def remove_line(self, product_id: int) -> None:
        with self.page.expect_response(
            lambda r: "/api/v1/cart" in r.url and r.request.method == "DELETE"
        ):
            self.line_for(product_id).get_by_test_id("cart-line-remove").click()

    def empty_cart(self) -> None:
        """Empty the cart and wait for the server to confirm it.

        The empty state renders from the response, so waiting for the DELETE is
        what makes the following assertion deterministic.
        """
        with self.page.expect_response(
            lambda r: r.url.endswith("/api/v1/cart") and r.request.method == "DELETE"
        ):
            self.clear_button.click()

    def proceed_to_checkout(self) -> None:
        self.checkout_button.click()


class CheckoutPage(BasePage):
    path = "/checkout"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("checkout-page")

    def current_step(self) -> str:
        value = self.page.get_by_test_id("checkout-page").get_attribute("data-step")
        return value or ""

    # -- Address step ------------------------------------------------------
    @property
    def address_options(self) -> Locator:
        return self.page.get_by_test_id("address-option")

    @property
    def address_form(self) -> Locator:
        return self.page.get_by_test_id("address-form")

    @property
    def continue_to_payment(self) -> Locator:
        return self.page.get_by_test_id("continue-to-payment")

    def fill_new_address(self, **overrides: str) -> None:
        values = {
            "address-full-name": "UI Test Customer",
            "address-line1": "1 Playwright Way",
            "address-city": "Austin",
            "address-state": "TX",
            "address-postal-code": "73301",
            "address-country": "US",
        }
        values.update(overrides)
        for testid, value in values.items():
            self.page.get_by_test_id(testid).fill(value)
        self.page.get_by_test_id("address-save").click()

    def ensure_address_selected(self) -> None:
        """Select an existing address, or create one if the account has none."""
        if self.address_form.count() > 0 and self.address_form.is_visible():
            self.fill_new_address()
        expect(self.address_options.first).to_be_visible(timeout=settings.ui_timeout_ms)
        self.address_options.first.get_by_test_id("address-radio").check()

    # -- Payment step ------------------------------------------------------
    @property
    def card_number_input(self) -> Locator:
        return self.page.get_by_test_id("card-number")

    @property
    def card_holder_input(self) -> Locator:
        return self.page.get_by_test_id("card-holder")

    @property
    def continue_to_review(self) -> Locator:
        return self.page.get_by_test_id("continue-to-review")

    def fill_payment(self, card_number: str, holder: str = "UI Test Customer") -> None:
        self.card_number_input.fill(card_number)
        self.card_holder_input.fill(holder)

    # -- Review step -------------------------------------------------------
    @property
    def place_order_button(self) -> Locator:
        return self.page.get_by_test_id("place-order-button")

    @property
    def checkout_error(self) -> Locator:
        return self.page.get_by_test_id("checkout-error")

    # -- Summary -----------------------------------------------------------
    @property
    def review_items(self) -> Locator:
        return self.page.get_by_test_id("review-item")

    @property
    def empty_cart_notice(self) -> Locator:
        return self.page.get_by_test_id("checkout-empty-cart")

    @property
    def summary_total(self) -> Locator:
        return self.page.get_by_test_id("checkout-total")

    @property
    def summary_shipping(self) -> Locator:
        return self.page.get_by_test_id("checkout-shipping")

    @property
    def summary_subtotal(self) -> Locator:
        return self.page.get_by_test_id("checkout-subtotal")

    @property
    def summary_tax(self) -> Locator:
        return self.page.get_by_test_id("checkout-tax")

    @property
    def summary_discount(self) -> Locator:
        return self.page.get_by_test_id("checkout-discount")

    @property
    def promo_input(self) -> Locator:
        return self.page.get_by_test_id("promo-input")

    def apply_promo(self, code: str) -> None:
        self.promo_input.fill(code)
        with self.page.expect_response(lambda r: "/checkout/quote" in r.url):
            self.page.get_by_test_id("promo-apply").click()

    # -- Whole flow --------------------------------------------------------
    def go_to_review(self, card_number: str) -> None:
        """Walk address -> payment -> review, stopping before the charge.

        Split out from :meth:`complete` so a test can read the quoted total on
        the review step and compare it with what the confirmation reports.
        """
        self.ensure_address_selected()
        self.continue_to_payment.click()
        self.fill_payment(card_number)
        self.continue_to_review.click()
        expect(self.place_order_button).to_be_visible(timeout=settings.ui_timeout_ms)

    def place_order(self) -> None:
        """Submit the order and wait for the checkout call to come back.

        Both outcomes - a redirect to the confirmation or an inline error - are
        driven by this one response, so waiting for it covers the success and
        the failure path without a branch.
        """
        with self.page.expect_response(
            lambda r: r.url.endswith("/api/v1/orders") and r.request.method == "POST"
        ):
            self.place_order_button.click()

    def complete(self, card_number: str) -> None:
        """Walk address -> payment -> review -> place order."""
        self.go_to_review(card_number)
        self.place_order()


class OrderConfirmationPage(BasePage):
    @property
    def card(self) -> Locator:
        return self.page.get_by_test_id("confirmation-card")

    @property
    def order_number(self) -> Locator:
        return self.page.get_by_test_id("confirmation-order-number")

    @property
    def total(self) -> Locator:
        return self.page.get_by_test_id("confirmation-total")

    @property
    def order_status(self) -> Locator:
        return self.page.get_by_test_id("order-status")

    @property
    def payment_status(self) -> Locator:
        return self.page.get_by_test_id("payment-status")

    @property
    def view_order_detail_button(self) -> Locator:
        return self.page.get_by_test_id("view-order-detail")

    def order_id(self) -> int:
        """The order this confirmation is for, taken from the page root."""
        root = self.page.get_by_test_id("order-confirmation-page")
        expect(root).to_be_visible(timeout=settings.ui_timeout_ms)
        return self.int_attr(root, "data-order-id")

    def wait_until_visible(self) -> None:
        expect(self.card).to_be_visible(timeout=settings.ui_timeout_ms)


class OrdersPage(BasePage):
    path = "/orders"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("orders-page")

    @property
    def rows(self) -> Locator:
        return self.page.get_by_test_id("order-row")

    @property
    def empty_state(self) -> Locator:
        return self.page.get_by_test_id("empty-orders")

    def row_for(self, order_id: int) -> Locator:
        return self.page.locator(f'[data-testid="order-row"][data-order-id="{order_id}"]')

    def open_order(self, order_id: int) -> None:
        self.row_for(order_id).get_by_test_id("view-order").click()

    def order_numbers(self) -> list[str]:
        return [
            (text or "").strip()
            for text in self.rows.get_by_test_id("order-number").all_text_contents()
        ]


class OrderDetailPage(BasePage):
    def open_order(self, order_id: int) -> None:
        super().open(f"/orders/{order_id}")
        self.expect_loaded("order-detail-page")

    @property
    def order_number(self) -> Locator:
        return self.page.get_by_test_id("order-number")

    @property
    def status(self) -> Locator:
        """The order's own status badge, in the page header.

        Scoped with .first because the payments table renders a status badge per
        attempt; an unscoped locator matches several and Playwright's strict
        mode - correctly - refuses to guess which one the test meant.
        """
        return self.page.get_by_test_id("order-status").first

    @property
    def payment_status(self) -> Locator:
        """The order's overall payment status, not an individual attempt's."""
        return self.page.get_by_test_id("payment-status").first

    @property
    def items(self) -> Locator:
        return self.page.get_by_test_id("order-item")

    @property
    def total(self) -> Locator:
        return self.page.get_by_test_id("order-total")

    @property
    def subtotal(self) -> Locator:
        return self.page.get_by_test_id("order-subtotal")

    @property
    def tax(self) -> Locator:
        return self.page.get_by_test_id("order-tax")

    @property
    def shipping(self) -> Locator:
        return self.page.get_by_test_id("order-shipping")

    @property
    def payment_attempts(self) -> Locator:
        return self.page.get_by_test_id("payment-attempt")

    def item_for(self, product_id: int) -> Locator:
        return self.page.locator(ITEM_SELECTOR.format(product_id=product_id))

    def item_total(self, product_id: int) -> float:
        return self.money_value(self.item_for(product_id).get_by_test_id("order-item-total"))

    @property
    def cancel_button(self) -> Locator:
        return self.page.get_by_test_id("cancel-order-button")

    @property
    def not_found(self) -> Locator:
        return self.page.get_by_test_id("order-not-found")

    def cancel(self) -> None:
        with self.page.expect_response(lambda r: "/cancel" in r.url and r.request.method == "POST"):
            self.cancel_button.click()


class AdminPage(BasePage):
    path = "/admin"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("admin-page")

    def open_expecting_forbidden(self) -> None:
        """Navigate to /admin as a non-admin.

        A separate entry point because :meth:`open` waits for the console shell,
        which a forbidden visitor must never be shown - so reusing it would hang
        for the full timeout before failing for the wrong reason.
        """
        BasePage.open(self, self.path)
        expect(self.forbidden_message).to_be_visible(timeout=settings.ui_timeout_ms)

    @property
    def forbidden_message(self) -> Locator:
        return self.page.get_by_test_id("forbidden-message")

    def select_tab(self, name: str) -> None:
        self.page.get_by_test_id(f"admin-tab-{name}").click()
        expect(self.page.get_by_test_id("loading")).to_have_count(0, timeout=settings.ui_timeout_ms)

    # -- Overview ----------------------------------------------------------
    @property
    def stat_products(self) -> Locator:
        return self.page.get_by_test_id("stat-products")

    @property
    def stat_orders(self) -> Locator:
        return self.page.get_by_test_id("stat-orders")

    # -- Inventory ---------------------------------------------------------
    def search_inventory(self, term: str) -> None:
        """Filter the inventory table.

        Without this, locating a specific product means hoping it lands on the
        first page of a table sorted by stock level - which is exactly the kind
        of incidental dependency that makes a test fail for reasons unrelated to
        what it tests.
        """
        self.page.get_by_test_id("inventory-search").fill(term)
        with self.page.expect_response(lambda r: "/admin/inventory" in r.url):
            self.page.get_by_test_id("inventory-search-submit").click()

    def inventory_row(self, product_id: int) -> Locator:
        return self.page.locator(f'[data-testid="inventory-row"][data-product-id="{product_id}"]')

    def inventory_quantity(self, product_id: int) -> int:
        cell = self.inventory_row(product_id).get_by_test_id("inventory-quantity")
        return self.int_attr(cell, "data-quantity")

    def set_stock(self, product_id: int, quantity: int) -> None:
        row = self.inventory_row(product_id)
        row.get_by_test_id("inventory-input").fill(str(quantity))
        with self.page.expect_response(lambda r: "/stock" in r.url and r.request.method == "PUT"):
            row.get_by_test_id("inventory-save").click()

    # -- Orders ------------------------------------------------------------
    def admin_order_row(self, order_id: int) -> Locator:
        return self.page.locator(f'[data-testid="admin-order-row"][data-order-id="{order_id}"]')

    def advance_order(self, order_id: int) -> None:
        with self.page.expect_response(
            lambda r: "/status" in r.url and r.request.method == "PATCH"
        ):
            self.admin_order_row(order_id).get_by_test_id("advance-order").click()

    # -- Users -------------------------------------------------------------
    @property
    def user_rows(self) -> Locator:
        return self.page.get_by_test_id("user-row")

    def search_users(self, term: str) -> None:
        self.page.get_by_test_id("user-search").fill(term)
        with self.page.expect_response(lambda r: "/admin/users" in r.url):
            self.page.get_by_test_id("user-search-submit").click()


class ProfilePage(BasePage):
    path = "/profile"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("profile-page")

    @property
    def email(self) -> Locator:
        return self.page.get_by_test_id("profile-email")

    @property
    def role(self) -> Locator:
        return self.page.get_by_test_id("profile-role")

    def select_tab(self, name: str) -> None:
        self.page.get_by_test_id(f"tab-{name}").click()

    @property
    def address_cards(self) -> Locator:
        return self.page.get_by_test_id("address-card")
