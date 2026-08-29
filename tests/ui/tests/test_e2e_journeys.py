"""End-to-end browser journeys.

The top of the pyramid: slow, few, and the only tests that answer "can a person
actually do this?". Everything below them has already proved the rules are
correct in isolation - these prove the whole thing hangs together in a browser.

Where a journey concerns money or stock, it is verified against the database as
well as the screen. A confirmation page can say "paid" while the order says
otherwise, and only checking both catches that.
"""

from __future__ import annotations

import uuid

import allure
import pytest
from playwright.sync_api import expect

from tests.api.clients import (
    CARD_APPROVED,
    CARD_DECLINED_FUNDS,
    AdminClient,
)
from tests.database.queries.queries import DatabaseQueries
from tests.test_data.factories import DEFAULT_PASSWORD, unique_email
from tests.ui.pages.account import (
    AdminPage,
    CartPage,
    CheckoutPage,
    OrderConfirmationPage,
    OrderDetailPage,
    OrdersPage,
    RegisterPage,
)
from tests.ui.pages.catalog import ProductDetailPage, ProductsPage

pytestmark = [pytest.mark.e2e, allure.epic("End-to-end journeys")]


@allure.feature("E2E 1: register, shop, pay")
@allure.severity(allure.severity_level.BLOCKER)
def test_a_new_customer_can_register_and_complete_a_purchase(
    page, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    """The flagship journey: no account at the start, a paid order at the end.

    Deliberately starts from a signed-out browser and a brand-new address, so
    nothing about it has been prepared in advance.
    """
    # A unique name, because previous runs leave products behind and searching
    # for a shared name would open whichever one happens to sort first.
    name = f"E2E Journey Product {uuid.uuid4().hex[:8]}"
    product = admin_client.create_product_with_stock(10, price=64.00, name=name)
    stock_before = db.stock_for(product["id"])

    with allure.step("Register through the form"):
        register = RegisterPage(page)
        register.open()
        register.register(
            email=unique_email("e2e"), password=DEFAULT_PASSWORD, full_name="E2E Shopper"
        )
        expect(register.logout_button).to_be_visible()

    with allure.step("Find the product by searching"):
        register.search(name)
        products = ProductsPage(page)
        products.wait_for_results()
        assert products.total_results() == 1, "The search did not uniquely identify the product"

    with allure.step("Open it and add two to the cart"):
        detail = ProductDetailPage(page)
        opened = products.open_product(0)
        assert opened == product["id"]
        expect(detail.expect_loaded("product-detail-page")).to_be_visible()
        detail.add_to_cart(2)
        detail.expect_cart_badge(2)

    with allure.step("Review the cart"):
        cart = CartPage(page)
        cart.open()
        assert cart.line_quantity(product["id"]) == 2
        assert cart.money_value(cart.subtotal) == 128.00
        cart.proceed_to_checkout()

    with allure.step("Address, payment, review"):
        checkout = CheckoutPage(page)
        expect(checkout.expect_loaded("checkout-page")).to_be_visible()
        assert checkout.money_value(checkout.summary_total) > 128.00  # tax applied
        checkout.complete(CARD_APPROVED)

    with allure.step("The confirmation shows a paid, confirmed order"):
        confirmation = OrderConfirmationPage(page)
        confirmation.wait_until_visible()
        expect(confirmation.order_number).to_contain_text("SS-")
        expect(confirmation.order_status).to_have_attribute("data-status", "confirmed")
        expect(confirmation.payment_status).to_have_attribute("data-status", "paid")

        order_number = (confirmation.order_number.text_content() or "").strip()

    with allure.step("The database agrees"):
        row = db.order_by_number(order_number)
        assert row is not None, f"No order row for {order_number}"
        assert row["status"] == "confirmed"
        assert row["payment_status"] == "paid"
        assert db.stock_for(product["id"]) == stock_before - 2

    with allure.step("The cart is empty afterwards"):
        cart.open()
        expect(cart.empty_state).to_be_visible()


@allure.feature("E2E 2: an edited basket")
def test_the_total_shown_at_checkout_is_the_total_charged(
    logged_in_page, admin_client: AdminClient
) -> None:
    """The customer must never be charged more than the screen promised.

    A basket edited several times is where a cached total would surface.
    """
    first = admin_client.create_product_with_stock(10, price=30.00, name="E2E Item A")
    second = admin_client.create_product_with_stock(10, price=45.00, name="E2E Item B")

    detail = ProductDetailPage(logged_in_page)
    with allure.step("Add two different products"):
        detail.open_product(first["id"])
        detail.add_to_cart(1)
        detail.open_product(second["id"])
        detail.add_to_cart(1)

    cart = CartPage(logged_in_page)
    with allure.step("Change the quantities"):
        cart.open()
        cart.increase_quantity(first["id"])
        cart.increase_quantity(first["id"])
        assert cart.line_quantity(first["id"]) == 3
        assert cart.money_value(cart.subtotal) == 135.00  # 3x30 + 1x45

    checkout = CheckoutPage(logged_in_page)
    with allure.step("Proceed to checkout and note the quoted total"):
        cart.proceed_to_checkout()
        expect(checkout.expect_loaded("checkout-page")).to_be_visible()
        quoted_total = checkout.money_value(checkout.summary_total)
        assert checkout.money_value(checkout.summary_subtotal) == 135.00

    with allure.step("Place the order"):
        checkout.complete(CARD_APPROVED)

    with allure.step("The confirmation shows exactly the quoted total"):
        confirmation = OrderConfirmationPage(logged_in_page)
        confirmation.wait_until_visible()
        assert confirmation.money_value(confirmation.total) == quoted_total


@allure.feature("E2E 3: order history")
def test_a_completed_order_appears_in_history_and_opens(
    logged_in_page, admin_client: AdminClient
) -> None:
    product = admin_client.create_product_with_stock(10, price=52.00, name="E2E History Product")

    detail = ProductDetailPage(logged_in_page)
    detail.open_product(product["id"])
    detail.add_to_cart(1)

    checkout = CheckoutPage(logged_in_page)
    checkout.open()
    checkout.complete(CARD_APPROVED)

    confirmation = OrderConfirmationPage(logged_in_page)
    confirmation.wait_until_visible()
    order_number = (confirmation.order_number.text_content() or "").strip()

    with allure.step("The order is listed in history"):
        orders = OrdersPage(logged_in_page)
        orders.open()
        assert order_number in orders.order_numbers()

    with allure.step("Opening it shows the matching detail"):
        orders.rows.first.get_by_test_id("view-order").click()
        detail_page = OrderDetailPage(logged_in_page)
        expect(detail_page.expect_loaded("order-detail-page")).to_be_visible()
        expect(detail_page.order_number).to_contain_text(order_number)
        expect(detail_page.items).to_have_count(1)
        expect(detail_page.payment_status).to_have_attribute("data-status", "paid")


@allure.feature("E2E 4: declined payment")
@allure.severity(allure.severity_level.BLOCKER)
def test_a_declined_card_shows_an_error_and_creates_no_paid_order(
    logged_in_page, admin_client: AdminClient, db: DatabaseQueries, customer
) -> None:
    """Business rule 5, from the customer's seat.

    The screen must say what went wrong, the customer must stay where they can
    retry, and nothing anywhere may be marked paid.
    """
    product = admin_client.create_product_with_stock(8, price=41.00, name="E2E Declined Product")
    stock_before = db.stock_for(product["id"])

    detail = ProductDetailPage(logged_in_page)
    detail.open_product(product["id"])
    detail.add_to_cart(2)

    checkout = CheckoutPage(logged_in_page)
    with allure.step("Pay with a card that will be declined"):
        checkout.open()
        checkout.complete(CARD_DECLINED_FUNDS)

    with allure.step("The failure is shown and the customer stays on checkout"):
        expect(checkout.checkout_error).to_be_visible()
        expect(checkout.expect_loaded("checkout-page")).to_be_visible()

    with allure.step("Nothing is marked paid, and the stock came back"):
        orders = db.orders_for_user(customer.id)
        assert all(
            order["payment_status"] != "paid" for order in orders
        ), "A declined payment produced a paid order"
        assert db.stock_for(product["id"]) == stock_before

    with allure.step("The customer can retry with a good card"):
        # Back to the payment step rather than restarting checkout: after a
        # decline the customer is still on the review step with their address
        # already chosen, and replaying the address step would not reflect what
        # they actually do.
        checkout.page.get_by_test_id("back-to-payment").click()
        checkout.fill_payment(CARD_APPROVED)
        checkout.continue_to_review.click()
        checkout.place_order()

        confirmation = OrderConfirmationPage(logged_in_page)
        confirmation.wait_until_visible()
        expect(confirmation.payment_status).to_have_attribute("data-status", "paid")


@allure.feature("E2E 5: cancellation")
def test_a_customer_can_cancel_an_order_from_its_detail_page(
    logged_in_page, admin_client: AdminClient, db: DatabaseQueries
) -> None:
    product = admin_client.create_product_with_stock(10, price=27.00, name="E2E Cancel Product")
    stock_before = db.stock_for(product["id"])

    detail = ProductDetailPage(logged_in_page)
    detail.open_product(product["id"])
    detail.add_to_cart(3)

    checkout = CheckoutPage(logged_in_page)
    checkout.open()
    checkout.complete(CARD_APPROVED)

    confirmation = OrderConfirmationPage(logged_in_page)
    confirmation.wait_until_visible()
    assert db.stock_for(product["id"]) == stock_before - 3

    with allure.step("Open the order and cancel it"):
        confirmation.page.get_by_test_id("view-order-detail").click()
        order_detail = OrderDetailPage(logged_in_page)
        expect(order_detail.expect_loaded("order-detail-page")).to_be_visible()
        order_detail.cancel()

    with allure.step("The status changes and the stock is returned"):
        expect(order_detail.status).to_have_attribute("data-status", "cancelled")
        expect(order_detail.payment_status).to_have_attribute("data-status", "refunded")
        assert db.stock_for(product["id"]) == stock_before


@allure.feature("E2E 6: admin restock reaches the storefront")
def test_an_admin_restock_is_visible_to_a_customer(
    page, second_admin_page, admin_client: AdminClient
) -> None:
    """Two roles, two browser contexts, one product.

    ``second_admin_page`` has a context of its own. Using the ordinary
    ``admin_page`` fixture here would sign the *same* browser in as the admin,
    so the "customer" and the "admin" would be one session and the test would
    prove nothing about visibility across users.
    """
    product = admin_client.create_product_with_stock(0, price=88.00, name="E2E Restock Product")

    with allure.step("A customer sees it as out of stock"):
        customer_detail = ProductDetailPage(page)
        customer_detail.open_product(product["id"])
        expect(customer_detail.add_to_cart_button).to_be_disabled()
        expect(customer_detail.stock_badge).to_have_attribute("data-stock", "0")

    with allure.step("An admin restocks it in the console"):
        console = AdminPage(second_admin_page)
        console.open()
        console.select_tab("inventory")
        # Searched rather than scrolled: the table is sorted by stock level and
        # paginated, so relying on a new product landing on page one would make
        # this fail as soon as the dataset grew.
        console.search_inventory(product["sku"])
        console.set_stock(product["id"], 12)
        assert console.inventory_quantity(product["id"]) == 12

    with allure.step("The customer sees the new availability on reload"):
        customer_detail.reload()
        expect(customer_detail.expect_loaded("product-detail-page")).to_be_visible()
        assert customer_detail.stock_quantity() == 12
        expect(customer_detail.add_to_cart_button).to_be_enabled()


@allure.feature("E2E 7: admin area is off limits")
@allure.severity(allure.severity_level.CRITICAL)
def test_a_customer_visiting_the_admin_area_is_refused(logged_in_page) -> None:
    """The UI guard is convenience; the API refuses regardless.

    Asserted here because a customer who reaches an admin screen - even a broken
    one - is a bug worth catching at the layer they actually experience.
    """
    console = AdminPage(logged_in_page)
    console.open_expecting_forbidden()

    expect(console.forbidden_message).to_be_visible()
    expect(console.page.get_by_test_id("admin-overview")).to_have_count(0)


@allure.feature("E2E 8: guest checkout is not possible")
def test_a_signed_out_visitor_is_sent_to_login_before_the_cart(page) -> None:
    from tests.ui.pages.account import LoginPage

    login = LoginPage(page)
    login.open("/cart")

    expect(login.expect_loaded("login-page")).to_be_visible()
    expect(login.email_input).to_be_visible()
