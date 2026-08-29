"""UI tests for browsing, searching, filtering and sorting.

Every assertion reads a `data-*` attribute rather than parsing rendered text,
so a copy change or a restyle cannot break the suite - only a genuine change in
behaviour can.
"""

from __future__ import annotations

import uuid

import allure
import pytest
from playwright.sync_api import expect

from tests.ui.pages.catalog import HomePage, ProductDetailPage, ProductsPage

pytestmark = [allure.epic("Storefront"), allure.feature("Catalogue")]


@allure.story("Home page")
class TestHomePage:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_the_home_page_shows_categories_and_products(self, home_page: HomePage) -> None:
        home_page.open()
        expect(home_page.header).to_be_visible()
        expect(home_page.category_tiles).to_have_count(7)
        expect(home_page.featured_products.first).to_be_visible()
        assert home_page.featured_products.count() > 0
        assert home_page.newest_products.count() > 0

    def test_a_category_tile_opens_a_filtered_listing(
        self, home_page: HomePage, products_page: ProductsPage
    ) -> None:
        home_page.open()
        home_page.open_category("laptops")

        products_page.wait_for_results()
        assert set(products_page.card_categories()) == {"laptops"}

    def test_the_hero_button_reaches_the_catalogue(
        self, home_page: HomePage, products_page: ProductsPage
    ) -> None:
        home_page.open()
        home_page.hero_shop_button.click()
        products_page.wait_for_results()
        assert products_page.total_results() >= 60


@allure.story("Search")
class TestSearch:
    def test_searching_from_the_header_finds_products(
        self, home_page: HomePage, products_page: ProductsPage
    ) -> None:
        home_page.open()
        home_page.search("laptop")

        products_page.wait_for_results()
        assert products_page.total_results() > 0
        expect(products_page.product_cards.first).to_be_visible()

    def test_a_search_with_no_matches_shows_the_empty_state(
        self, products_page: ProductsPage
    ) -> None:
        """An empty result must look deliberate, not broken."""
        products_page.open_with(q="zzzz-definitely-not-a-product")
        products_page.wait_for_results()

        expect(products_page.no_results).to_be_visible()
        expect(products_page.product_cards).to_have_count(0)
        assert products_page.total_results() == 0

    def test_the_search_box_reflects_the_term_in_the_url(self, products_page: ProductsPage) -> None:
        """A shared or bookmarked search link must restore its own term."""
        products_page.open_with(q="phone")
        products_page.wait_for_results()
        expect(products_page.search_input).to_have_value("phone")


@allure.story("Filtering")
class TestFiltering:
    def test_filtering_by_category(self, products_page: ProductsPage) -> None:
        products_page.open()
        products_page.select_category("phones")
        assert set(products_page.card_categories()) == {"phones"}

    def test_filtering_by_brand(self, products_page: ProductsPage) -> None:
        products_page.open()
        products_page.select_brand("Aurora")
        assert set(products_page.card_brands()) == {"Aurora"}

    def test_filtering_by_price_range(self, products_page: ProductsPage) -> None:
        products_page.open()
        products_page.set_price_range("100", "300")
        prices = products_page.card_prices()
        assert prices, "The price filter matched nothing"
        assert all(100 <= price <= 300 for price in prices), prices

    def test_filtering_by_minimum_rating(self, products_page: ProductsPage) -> None:
        products_page.open()
        products_page.select_min_rating("4.5")
        assert all(rating >= 4.5 for rating in products_page.card_ratings())

    def test_filtering_to_in_stock_only(self, products_page: ProductsPage) -> None:
        products_page.open()
        products_page.only_in_stock()
        assert set(products_page.card_stock_flags()) == {"true"}

    @allure.severity(allure.severity_level.CRITICAL)
    def test_combining_filters_narrows_the_results(self, products_page: ProductsPage) -> None:
        """Each control must further restrict, never widen."""
        products_page.open()
        products_page.select_category("laptops")
        broad = products_page.total_results()

        products_page.select_min_rating("4.5")
        narrow = products_page.total_results()

        assert narrow <= broad
        assert set(products_page.card_categories()) == {"laptops"}
        assert all(rating >= 4.5 for rating in products_page.card_ratings())

    def test_clearing_filters_restores_the_full_catalogue(
        self, products_page: ProductsPage
    ) -> None:
        products_page.open()
        products_page.select_category("books")
        filtered = products_page.total_results()

        products_page.clear_filters()
        assert products_page.total_results() > filtered

    def test_a_filter_combination_can_be_reached_directly_by_url(
        self, products_page: ProductsPage
    ) -> None:
        """The URL is the state, which is what makes a filtered view shareable."""
        products_page.open_with(category="laptops", min_price=1000, sort="price_asc")
        products_page.wait_for_results()

        assert set(products_page.card_categories()) == {"laptops"}
        assert all(price >= 1000 for price in products_page.card_prices())


@allure.story("Sorting")
class TestSorting:
    @pytest.mark.parametrize(
        ("option", "check"),
        [
            ("price_asc", lambda values: values == sorted(values)),
            ("price_desc", lambda values: values == sorted(values, reverse=True)),
        ],
        ids=["price-ascending", "price-descending"],
    )
    def test_price_sorting_reorders_the_grid(
        self, products_page: ProductsPage, option: str, check
    ) -> None:
        products_page.open()
        products_page.sort_by(option)
        prices = products_page.card_prices()
        assert len(prices) > 1
        assert check(prices), f"{option} produced {prices}"

    def test_rating_sorting_reorders_the_grid(self, products_page: ProductsPage) -> None:
        products_page.open()
        products_page.sort_by("rating_desc")
        ratings = products_page.card_ratings()
        assert ratings == sorted(ratings, reverse=True)

    def test_name_sorting_is_alphabetical(self, products_page: ProductsPage) -> None:
        products_page.open()
        products_page.sort_by("name_asc")
        names = products_page.card_names()
        assert names == sorted(names)


@allure.story("Pagination")
class TestPagination:
    def test_moving_between_pages_changes_the_products(self, products_page: ProductsPage) -> None:
        products_page.open()
        expect(products_page.pagination).to_be_visible()

        first_page = products_page.card_ids()
        assert products_page.current_page() == 1

        products_page.go_to_next_page()
        second_page = products_page.card_ids()

        assert products_page.current_page() == 2
        assert not (
            set(first_page) & set(second_page)
        ), "A product appeared on both pages - pagination is not stable"

    def test_the_previous_button_is_disabled_on_the_first_page(
        self, products_page: ProductsPage
    ) -> None:
        products_page.open()
        expect(products_page.previous_page_button).to_be_disabled()
        expect(products_page.next_page_button).to_be_enabled()

    def test_going_forward_and_back_returns_the_same_products(
        self, products_page: ProductsPage
    ) -> None:
        products_page.open_with(sort="price_asc")
        products_page.wait_for_results()
        original = products_page.card_ids()

        products_page.go_to_next_page()
        products_page.previous_page_button.click()
        products_page.wait_for_results()

        assert products_page.card_ids() == original


@allure.story("Product detail")
class TestProductDetail:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_the_detail_page_shows_every_attribute(
        self, products_page: ProductsPage, product_detail_page: ProductDetailPage, seeded_product
    ) -> None:
        product_detail_page.open_product(seeded_product["id"])

        expect(product_detail_page.name).to_have_text(seeded_product["name"])
        expect(product_detail_page.sku).to_have_text(seeded_product["sku"])
        expect(product_detail_page.brand).to_have_text(seeded_product["brand"])
        expect(product_detail_page.description).to_be_visible()
        expect(product_detail_page.stock_badge).to_be_visible()
        assert product_detail_page.money_value(product_detail_page.price) == seeded_product["price"]
        assert product_detail_page.stock_quantity() == seeded_product["stock_quantity"]

    def test_a_product_can_be_opened_from_the_listing(
        self, products_page: ProductsPage, product_detail_page: ProductDetailPage, product_factory
    ) -> None:
        """Filtered to a brand this test owns.

        Clicking "the first card" in the unfiltered catalogue reads the id from
        one render and asserts on the next, which another worker's product can
        change in between.
        """
        product = product_factory(brand=f"ClickBrand{uuid.uuid4().hex[:8]}")
        products_page.open_with(brand=product["brand"])
        products_page.wait_for_results()

        opened = products_page.open_product(0)
        assert opened == product["id"]
        expect(product_detail_page.expect_loaded("product-detail-page")).to_have_attribute(
            "data-product-id", str(product["id"])
        )

    def test_an_unknown_product_shows_a_not_found_state(
        self, product_detail_page: ProductDetailPage
    ) -> None:
        product_detail_page.open("/products/99999999")
        expect(product_detail_page.not_found).to_be_visible()

    def test_an_out_of_stock_product_cannot_be_added(
        self, product_detail_page: ProductDetailPage, out_of_stock_product
    ) -> None:
        """The button must be disabled, not merely fail on click."""
        product_detail_page.open_product(out_of_stock_product["id"])

        expect(product_detail_page.add_to_cart_button).to_be_disabled()
        expect(product_detail_page.stock_badge).to_have_attribute("data-stock", "0")

    def test_the_quantity_stepper_cannot_exceed_available_stock(
        self, product_detail_page: ProductDetailPage, product_with_stock
    ) -> None:
        """The ceiling comes from the server's stock figure, not a guess."""
        product = product_with_stock(3)
        product_detail_page.open_product(product["id"])

        assert product_detail_page.max_quantity() == 3

        product_detail_page.set_quantity(3)
        assert product_detail_page.quantity() == 3
        expect(product_detail_page.quantity_increase).to_be_disabled()

    def test_the_quantity_stepper_cannot_go_below_one(
        self, product_detail_page: ProductDetailPage, seeded_product
    ) -> None:
        product_detail_page.open_product(seeded_product["id"])
        assert product_detail_page.quantity() == 1
        expect(product_detail_page.quantity_decrease).to_be_disabled()
