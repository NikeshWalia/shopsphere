"""Page objects for the storefront: home, product listing and product detail."""

from __future__ import annotations

from playwright.sync_api import Locator, expect

from tests.configuration.settings import settings
from tests.ui.pages.base import BasePage


class HomePage(BasePage):
    path = "/"

    def open(self, path: str | None = None) -> None:
        """Open the home page and wait for its data to arrive.

        Waiting only for the page shell would be a trap: `Locator.count()` does
        not auto-wait, so a test calling it straight after navigation reads zero
        while the categories request is still in flight. Establishing the ready
        state here means every test built on this page object starts from a
        settled page.
        """
        super().open(path)
        self.expect_loaded("home-page")
        expect(self.page.get_by_test_id("loading")).to_have_count(0, timeout=settings.ui_timeout_ms)
        expect(self.category_tiles.first).to_be_visible(timeout=settings.ui_timeout_ms)

    @property
    def hero_shop_button(self) -> Locator:
        return self.page.get_by_test_id("hero-shop-button")

    @property
    def category_tiles(self) -> Locator:
        return self.page.get_by_test_id("category-tile")

    @property
    def featured_products(self) -> Locator:
        return self.page.get_by_test_id("featured-products").get_by_test_id("product-card")

    @property
    def newest_products(self) -> Locator:
        return self.page.get_by_test_id("newest-products").get_by_test_id("product-card")

    def open_category(self, slug: str) -> None:
        self.category_tiles.filter(has_not=None).nth(0)  # ensure the strip is rendered
        self.page.locator(f'[data-testid="category-tile"][data-category="{slug}"]').click()


class ProductsPage(BasePage):
    path = "/products"

    def open(self, path: str | None = None) -> None:
        super().open(path)
        self.expect_loaded("products-page")

    def open_with(self, **query: object) -> None:
        """Navigate straight to a filter combination.

        Faster and far more robust than clicking through the filter controls to
        reach a state, and it means a test that is *about* results does not
        depend on the filter widgets working.
        """
        from urllib.parse import urlencode

        params = {key: str(value) for key, value in query.items() if value is not None}
        suffix = f"?{urlencode(params)}" if params else ""
        super().open(f"{self.path}{suffix}")
        self.expect_loaded("products-page")

    # -- Results -----------------------------------------------------------
    @property
    def product_cards(self) -> Locator:
        return self.page.get_by_test_id("product-card")

    @property
    def result_count(self) -> Locator:
        return self.page.get_by_test_id("result-count")

    @property
    def no_results(self) -> Locator:
        return self.page.get_by_test_id("no-results")

    def total_results(self) -> int:
        return self.int_attr(self.result_count, "data-total")

    def wait_for_results(self) -> None:
        """Wait until the listing has settled into results or an empty state.

        Both outcomes are legitimate, so waiting only for cards would hang
        forever on a search that correctly returned nothing.
        """
        expect(self.page.get_by_test_id("loading")).to_have_count(0, timeout=settings.ui_timeout_ms)
        expect(self.page.get_by_test_id("product-grid").or_(self.no_results)).to_be_visible(
            timeout=settings.ui_timeout_ms
        )

    def card_prices(self) -> list[float]:
        self.wait_for_results()
        return [
            float(value)
            for value in self.product_cards.evaluate_all(
                "cards => cards.map(card => card.dataset.price)"
            )
        ]

    def card_attribute_values(self, attribute: str) -> list[str]:
        self.wait_for_results()
        return list(
            self.product_cards.evaluate_all(f"cards => cards.map(card => card.dataset.{attribute})")
        )

    def card_ids(self) -> list[int]:
        """Product ids in grid order - the cheapest way to compare two orderings."""
        return [int(value) for value in self.card_attribute_values("productId")]

    def card_ratings(self) -> list[float]:
        return [float(value) for value in self.card_attribute_values("rating")]

    def card_brands(self) -> list[str]:
        return self.card_attribute_values("brand")

    def card_categories(self) -> list[str]:
        return self.card_attribute_values("category")

    def card_stock_flags(self) -> list[str]:
        return self.card_attribute_values("inStock")

    def card_names(self) -> list[str]:
        """Displayed product names.

        The only ordering signal the card does not expose as a data attribute,
        so the name sort has to read the link text.
        """
        self.wait_for_results()
        return [
            (text or "").strip()
            for text in self.product_cards.get_by_test_id("product-name").all_text_contents()
        ]

    def open_product(self, index: int = 0) -> int:
        """Open the nth result and return its product id."""
        card = self.product_cards.nth(index)
        expect(card).to_be_visible()
        product_id = self.int_attr(card, "data-product-id")
        card.get_by_test_id("product-name").click()
        return product_id

    # -- Filter controls ---------------------------------------------------
    @property
    def category_filter(self) -> Locator:
        return self.page.get_by_test_id("filter-category")

    @property
    def brand_filter(self) -> Locator:
        return self.page.get_by_test_id("filter-brand")

    @property
    def min_price_filter(self) -> Locator:
        return self.page.get_by_test_id("filter-min-price")

    @property
    def max_price_filter(self) -> Locator:
        return self.page.get_by_test_id("filter-max-price")

    @property
    def min_rating_filter(self) -> Locator:
        return self.page.get_by_test_id("filter-min-rating")

    @property
    def in_stock_filter(self) -> Locator:
        return self.page.get_by_test_id("filter-in-stock")

    @property
    def clear_filters_button(self) -> Locator:
        return self.page.get_by_test_id("clear-filters")

    @property
    def sort_select(self) -> Locator:
        return self.page.get_by_test_id("sort-select")

    def select_category(self, slug: str) -> None:
        self.category_filter.select_option(slug)
        self.wait_for_results()

    def select_brand(self, brand: str) -> None:
        self.brand_filter.select_option(brand)
        self.wait_for_results()

    def sort_by(self, value: str) -> None:
        self.sort_select.select_option(value)
        self.wait_for_results()

    def set_price_range(self, minimum: str | None = None, maximum: str | None = None) -> None:
        # The inputs commit on blur, so each is explicitly blurred rather than
        # relying on the next click to do it incidentally.
        if minimum is not None:
            self.min_price_filter.fill(minimum)
            self.min_price_filter.blur()
        if maximum is not None:
            self.max_price_filter.fill(maximum)
            self.max_price_filter.blur()
        self.wait_for_results()

    def only_in_stock(self) -> None:
        self.in_stock_filter.check()
        self.wait_for_results()

    def select_min_rating(self, value: str) -> None:
        self.min_rating_filter.select_option(value)
        self.wait_for_results()

    def clear_filters(self) -> None:
        self.clear_filters_button.click()
        self.wait_for_results()

    def active_filter_count(self) -> int:
        """How many filters the panel believes are applied.

        Rendered as "Clear (3)" on the button, which is the UI's own answer -
        useful for proving a combination really did apply rather than silently
        replacing the previous filter.
        """
        if self.clear_filters_button.count() == 0:
            return 0
        text = self.clear_filters_button.text_content() or ""
        digits = "".join(character for character in text if character.isdigit())
        return int(digits or 0)

    # -- Pagination --------------------------------------------------------
    @property
    def pagination(self) -> Locator:
        return self.page.get_by_test_id("pagination")

    @property
    def next_page_button(self) -> Locator:
        return self.page.get_by_test_id("pagination-next")

    @property
    def previous_page_button(self) -> Locator:
        return self.page.get_by_test_id("pagination-previous")

    def current_page(self) -> int:
        return self.int_attr(self.pagination, "data-page")

    @property
    def page_label(self) -> Locator:
        return self.page.get_by_test_id("pagination-label")

    def total_pages(self) -> int:
        return self.int_attr(self.pagination, "data-total-pages")

    def go_to_next_page(self) -> None:
        self.next_page_button.click()
        self.wait_for_results()

    def go_to_previous_page(self) -> None:
        self.previous_page_button.click()
        self.wait_for_results()


class ProductDetailPage(BasePage):
    path = "/products"

    def open_product(self, product_id: int) -> None:
        super().open(f"/products/{product_id}")
        self.expect_loaded("product-detail-page")

    @property
    def name(self) -> Locator:
        return self.page.get_by_test_id("product-name")

    @property
    def price(self) -> Locator:
        return self.page.get_by_test_id("product-price")

    @property
    def sku(self) -> Locator:
        return self.page.get_by_test_id("product-sku")

    @property
    def brand(self) -> Locator:
        return self.page.get_by_test_id("product-brand")

    @property
    def category(self) -> Locator:
        return self.page.get_by_test_id("product-category")

    @property
    def description(self) -> Locator:
        return self.page.get_by_test_id("product-description")

    @property
    def rating(self) -> Locator:
        return self.page.get_by_test_id("product-rating")

    @property
    def stock(self) -> Locator:
        return self.page.get_by_test_id("product-stock")

    @property
    def stock_badge(self) -> Locator:
        return self.page.get_by_test_id("stock-badge")

    @property
    def add_to_cart_button(self) -> Locator:
        return self.page.get_by_test_id("add-to-cart-button")

    @property
    def add_to_cart_error(self) -> Locator:
        return self.page.get_by_test_id("add-to-cart-error")

    @property
    def not_found(self) -> Locator:
        return self.page.get_by_test_id("product-not-found")

    @property
    def quantity_stepper(self) -> Locator:
        return self.page.get_by_test_id("quantity-stepper")

    @property
    def quantity_increase(self) -> Locator:
        return self.page.get_by_test_id("quantity-increase")

    @property
    def quantity_decrease(self) -> Locator:
        return self.page.get_by_test_id("quantity-decrease")

    def quantity(self) -> int:
        return self.int_attr(self.quantity_stepper, "data-quantity")

    def max_quantity(self) -> int:
        return self.int_attr(self.quantity_stepper, "data-max")

    def stock_quantity(self) -> int:
        return self.int_attr(self.stock, "data-stock")

    def set_quantity(self, target: int) -> None:
        """Step the quantity to a target value.

        Bounded by the stepper's own maximum so a test asking for more than the
        control allows fails on its assertion rather than looping forever.
        """
        for _ in range(abs(target - self.quantity()) + 1):
            current = self.quantity()
            if current == target:
                return
            button = self.quantity_increase if current < target else self.quantity_decrease
            if button.is_disabled():
                return
            button.click()

    def expect_open(self, product_id: int) -> None:
        """Assert this is the detail page of a specific product.

        Used after clicking a card, where the assertion that matters is *which*
        product opened, not merely that some detail page rendered.
        """
        expect(self.page.get_by_test_id("product-detail-page")).to_have_attribute(
            "data-product-id", str(product_id), timeout=settings.ui_timeout_ms
        )

    def click_add_to_cart(self, quantity: int = 1) -> None:
        """Click the button without waiting for the server.

        Only for tests that need to observe the in-flight state. Anything that
        asserts on the *result* of adding should use :meth:`add_to_cart`.
        """
        if quantity != 1:
            self.set_quantity(quantity)
        self.add_to_cart_button.click()

    def add_to_cart(self, quantity: int = 1) -> None:
        """Add to the cart and wait for the server to acknowledge it.

        Waiting is the default rather than an opt-in. A test that clicks and
        moves on is racing the request: the next step - reading the badge,
        changing the stock level, opening the cart - can easily land before the
        POST arrives, and the failure then looks like a product bug rather than
        a missing await.
        """
        if quantity != 1:
            self.set_quantity(quantity)
        with self.page.expect_response(
            lambda response: "/api/v1/cart/items" in response.url
            and response.request.method == "POST"
        ):
            self.add_to_cart_button.click()
