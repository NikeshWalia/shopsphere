"""UI tests for registration, sign-in and the cart."""

from __future__ import annotations

import allure
from playwright.sync_api import expect

from tests.api.clients import AdminClient
from tests.configuration.settings import settings
from tests.test_data.factories import DEFAULT_PASSWORD, unique_email
from tests.ui.pages.account import CartPage, LoginPage, RegisterPage
from tests.ui.pages.catalog import ProductDetailPage

pytestmark = [allure.epic("Storefront")]


@allure.feature("Authentication")
@allure.story("Registration")
class TestRegistrationUI:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_registering_signs_the_customer_in(self, register_page: RegisterPage) -> None:
        register_page.open()
        register_page.register(
            email=unique_email("ui"), password=DEFAULT_PASSWORD, full_name="UI New Customer"
        )

        # The header changing is the real signal that a session exists; a URL
        # change alone would only mean the redirect fired.
        expect(register_page.logout_button).to_be_visible()
        expect(register_page.login_link).to_have_count(0)

    def test_a_duplicate_address_is_reported(self, register_page: RegisterPage, customer) -> None:
        register_page.open()
        register_page.register(email=customer.email, password=DEFAULT_PASSWORD)

        expect(register_page.error).to_be_visible()
        expect(register_page.logout_button).to_have_count(0)

    def test_mismatched_passwords_are_reported_before_submitting(
        self, register_page: RegisterPage
    ) -> None:
        register_page.open()
        register_page.register(
            email=unique_email("ui"), password=DEFAULT_PASSWORD, password_confirm="Different123!"
        )

        expect(register_page.error).to_be_visible()
        expect(register_page.error).to_contain_text("match")

    def test_a_weak_password_shows_the_policy(self, register_page: RegisterPage) -> None:
        """The message must say what to fix, not just that it failed."""
        register_page.open()
        register_page.register(email=unique_email("ui"), password="weak")

        expect(register_page.error).to_be_visible()
        expect(register_page.error).to_contain_text("Password")


@allure.feature("Authentication")
@allure.story("Sign in and out")
class TestLoginUI:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_valid_credentials_sign_the_customer_in(self, login_page: LoginPage, customer) -> None:
        login_page.open()
        login_page.login_and_wait(customer.email, customer.password)
        expect(login_page.logout_button).to_be_visible()

    def test_an_invalid_password_keeps_the_customer_on_the_form(
        self, login_page: LoginPage, customer
    ) -> None:
        login_page.open()
        login_page.login(customer.email, "WrongPassword123!")

        expect(login_page.error).to_be_visible()
        expect(login_page.submit_button).to_be_visible()
        expect(login_page.logout_button).to_have_count(0)

    def test_signing_out_clears_the_session(self, login_page: LoginPage, customer) -> None:
        login_page.open()
        login_page.login_and_wait(customer.email, customer.password)

        login_page.sign_out()

        expect(login_page.login_link).to_be_visible()
        expect(login_page.logout_button).to_have_count(0)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_protected_page_redirects_to_login_and_then_returns(
        self, login_page: LoginPage, customer
    ) -> None:
        """The attempted destination must survive the detour.

        Dropping the customer on the home page after login is the small kind of
        rudeness that loses a sale.
        """
        login_page.open("/cart")
        expect(login_page.expect_loaded("login-page")).to_be_visible()

        login_page.login_and_wait(customer.email, customer.password)

        expect(login_page.page).to_have_url(f"{settings.ui_base_url.rstrip('/')}/cart")


@allure.feature("Cart")
@allure.story("Cart contents")
class TestCartUI:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_adding_a_product_updates_the_header_badge(
        self, logged_in_page, product_with_stock
    ) -> None:
        product = product_with_stock(10)
        detail = ProductDetailPage(logged_in_page)

        detail.open_product(product["id"])
        detail.expect_no_cart_badge()

        detail.add_to_cart(2)

        detail.expect_toast("success")
        detail.expect_cart_badge(2)

    def test_the_badge_is_absent_rather_than_zero_for_an_empty_cart(
        self, cart_page: CartPage
    ) -> None:
        """Absence is a meaningful assertion; "0" would be noise in the UI."""
        cart_page.open()
        expect(cart_page.empty_state).to_be_visible()
        cart_page.expect_no_cart_badge()

    def test_a_cart_line_shows_the_right_quantity_and_totals(
        self, logged_in_page, cart_page: CartPage, product_with_stock
    ) -> None:
        product = product_with_stock(10, price=25.00)
        ProductDetailPage(logged_in_page).open_product(product["id"])
        ProductDetailPage(logged_in_page).add_to_cart(3)

        cart_page.open()
        assert cart_page.line_quantity(product["id"]) == 3
        assert cart_page.line_unit_price(product["id"]) == 25.00
        assert cart_page.line_total(product["id"]) == 75.00
        assert cart_page.money_value(cart_page.subtotal) == 75.00

    def test_changing_the_quantity_updates_the_totals(
        self, logged_in_page, cart_page: CartPage, product_with_stock
    ) -> None:
        product = product_with_stock(10, price=20.00)
        detail = ProductDetailPage(logged_in_page)
        detail.open_product(product["id"])
        detail.add_to_cart(2)

        cart_page.open()
        assert cart_page.money_value(cart_page.subtotal) == 40.00

        cart_page.increase_quantity(product["id"])
        assert cart_page.line_quantity(product["id"]) == 3
        assert cart_page.money_value(cart_page.subtotal) == 60.00

        cart_page.decrease_quantity(product["id"])
        assert cart_page.line_quantity(product["id"]) == 2
        assert cart_page.money_value(cart_page.subtotal) == 40.00

    def test_removing_a_line_empties_the_cart(
        self, logged_in_page, cart_page: CartPage, product_with_stock
    ) -> None:
        product = product_with_stock(5)
        detail = ProductDetailPage(logged_in_page)
        detail.open_product(product["id"])
        detail.add_to_cart(1)

        cart_page.open()
        cart_page.remove_line(product["id"])

        expect(cart_page.empty_state).to_be_visible()

    def test_emptying_the_cart_shows_the_empty_state(
        self, logged_in_page, cart_page: CartPage, product_with_stock
    ) -> None:
        product = product_with_stock(5)
        detail = ProductDetailPage(logged_in_page)
        detail.open_product(product["id"])
        detail.add_to_cart(2)

        cart_page.open()
        cart_page.empty_cart()

        expect(cart_page.empty_state).to_be_visible()
        cart_page.expect_no_cart_badge()

    def test_the_cart_stepper_stops_at_the_available_stock(
        self, logged_in_page, cart_page: CartPage, product_with_stock
    ) -> None:
        product = product_with_stock(2)
        detail = ProductDetailPage(logged_in_page)
        detail.open_product(product["id"])
        detail.add_to_cart(2)

        cart_page.open()
        assert cart_page.line_max_quantity(product["id"]) == 2
        expect(cart_page.increase_button(product["id"])).to_be_disabled()

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_cart_whose_stock_fell_short_blocks_checkout(
        self,
        logged_in_page,
        cart_page: CartPage,
        product_with_stock,
        admin_client: AdminClient,
    ) -> None:
        """The customer must be told, and must not be allowed to proceed.

        A checkout button that is enabled but always fails is worse than one
        that is honestly disabled.
        """
        product = product_with_stock(3)
        detail = ProductDetailPage(logged_in_page)
        detail.open_product(product["id"])
        detail.add_to_cart(3)

        with allure.step("Stock is reduced while the cart is open"):
            admin_client.set_stock(product["id"], 1).assert_status(200)

        cart_page.open()

        expect(cart_page.issues).to_be_visible()
        expect(cart_page.line_stock_warning(product["id"])).to_be_visible()
        expect(cart_page.checkout_button).to_be_disabled()

    def test_reducing_the_quantity_re_enables_checkout(
        self,
        logged_in_page,
        cart_page: CartPage,
        product_with_stock,
        admin_client: AdminClient,
    ) -> None:
        product = product_with_stock(3)
        detail = ProductDetailPage(logged_in_page)
        detail.open_product(product["id"])
        detail.add_to_cart(3)

        admin_client.set_stock(product["id"], 1).assert_status(200)
        cart_page.open()
        expect(cart_page.checkout_button).to_be_disabled()

        cart_page.decrease_quantity(product["id"])
        cart_page.decrease_quantity(product["id"])

        expect(cart_page.checkout_button).to_be_enabled()
        expect(cart_page.issues).to_have_count(0)

    def test_the_cart_survives_a_page_reload(
        self, logged_in_page, cart_page: CartPage, product_with_stock
    ) -> None:
        """The cart lives on the server, so a refresh must not lose it."""
        product = product_with_stock(10)
        detail = ProductDetailPage(logged_in_page)
        detail.open_product(product["id"])
        detail.add_to_cart(2)

        cart_page.open()
        cart_page.reload()

        expect(cart_page.expect_loaded("cart-page")).to_be_visible()
        assert cart_page.line_quantity(product["id"]) == 2
