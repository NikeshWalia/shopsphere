"""A minimal check that the UI framework itself is wired correctly.

Deliberately small: it proves the browser launches, the page objects resolve
their locators, and a logged-in session can be established. If this fails, every
other UI failure is noise.
"""

from __future__ import annotations

import allure
import pytest
from playwright.sync_api import expect

from tests.ui.pages.catalog import HomePage, ProductsPage

pytestmark = [pytest.mark.ui, allure.epic("Storefront"), allure.feature("Framework")]


@allure.story("Browser and page objects")
@allure.severity(allure.severity_level.BLOCKER)
def test_home_page_renders(home_page: HomePage) -> None:
    home_page.open()
    expect(home_page.header).to_be_visible()
    assert home_page.category_tiles.count() == 7
    assert home_page.featured_products.count() > 0


@allure.story("Browser and page objects")
def test_products_page_lists_the_catalogue(products_page: ProductsPage) -> None:
    products_page.open()
    products_page.wait_for_results()
    assert products_page.total_results() >= 60
    assert products_page.product_cards.count() > 0


@allure.story("Sessions")
@allure.severity(allure.severity_level.BLOCKER)
def test_injected_session_is_recognised(logged_in_page, customer) -> None:
    """The session fixture must produce a genuinely authenticated browser."""
    page = HomePage(logged_in_page)
    page.open()
    expect(page.logout_button).to_be_visible()
    expect(page.login_link).to_have_count(0)
