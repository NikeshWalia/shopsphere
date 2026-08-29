"""API tests for the catalogue: search, filtering, sorting and pagination.

The catalogue is the most-hit surface in the application and the easiest place
for a subtle defect to hide - a filter that silently does nothing, a sort that
is only mostly ordered, a page 2 that repeats a row from page 1.
"""

from __future__ import annotations

import uuid
from typing import Any

import allure
import pytest

from tests.api.clients import AdminClient, ProductClient
from tests.test_data.factories import (
    PAGINATION_BOUNDARIES,
    PRICE_RANGES,
    SEARCH_TERMS,
    SORT_OPTIONS,
)
from tests.utilities.http import ApiResponse

pytestmark = [allure.epic("Catalogue"), allure.feature("Product discovery")]

PAGE_KEYS = {"items", "total", "page", "page_size", "total_pages", "has_next", "has_previous"}


def items(response: ApiResponse) -> list[dict[str, Any]]:
    response.assert_status(200)
    return list(response.body["items"])


def all_matching(client: ProductClient, **query: Any) -> list[dict[str, Any]]:
    """Fetch every match, so a filter assertion covers the whole result set.

    Checking only the first page would let a filter that leaks on page 3 pass.
    """
    collected: list[dict[str, Any]] = []
    page = 1
    while True:
        response = client.list(page=page, page_size=100, **query)
        response.assert_status(200)
        collected.extend(response.body["items"])
        if not response.body["has_next"]:
            return collected
        page += 1


# ---------------------------------------------------------------------------
@allure.story("Listing shape")
class TestListing:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_the_catalogue_is_returned_in_a_pagination_envelope(
        self, product_client: ProductClient
    ) -> None:
        response = product_client.list(page_size=5)
        response.assert_status(200)
        assert set(response.body) == PAGE_KEYS, f"Envelope keys are {sorted(response.body)}"
        assert response.body["total"] >= 60
        assert len(response.body["items"]) == 5
        assert response.body["page"] == 1
        assert response.body["has_previous"] is False

    @allure.severity(allure.severity_level.CRITICAL)
    def test_prices_are_json_numbers_not_strings(self, product_client: ProductClient) -> None:
        """The single most consequential type in the API.

        `{"price": "129.99"}` breaks every client that does arithmetic - a
        JavaScript front end would concatenate rather than add. Pydantic
        serialises Decimal as a string by default, so this is drift that would
        happen silently without a guard.
        """
        for product in items(product_client.list(page_size=20)):
            price = product["price"]
            assert isinstance(price, int | float) and not isinstance(
                price, bool
            ), f"{product['sku']} has price {price!r} of type {type(price).__name__}"
            assert round(float(price), 2) == float(
                price
            ), f"{product['sku']} has sub-cent precision"

    def test_a_product_card_carries_everything_needed_to_render_it(
        self, product_client: ProductClient
    ) -> None:
        product = items(product_client.list(page_size=1))[0]
        assert {
            "id",
            "sku",
            "name",
            "price",
            "brand",
            "rating",
            "image_url",
            "is_active",
            "in_stock",
            "stock_quantity",
            "category",
        } <= set(product)
        assert set(product["category"]) >= {"id", "name", "slug"}
        # in_stock must agree with the number it summarises.
        assert product["in_stock"] == (product["stock_quantity"] > 0)

    def test_only_active_products_are_public(self, product_client: ProductClient) -> None:
        assert all(p["is_active"] for p in all_matching(product_client))

    def test_the_listing_responds_within_the_budget(self, product_client: ProductClient) -> None:
        product_client.list(page_size=100).assert_status(200).assert_faster_than()


# ---------------------------------------------------------------------------
@allure.story("Search")
class TestSearch:
    @pytest.mark.parametrize(
        ("term", "expect_results"), SEARCH_TERMS, ids=[t[0][:20] for t in SEARCH_TERMS]
    )
    def test_search_terms_behave_as_expected(
        self, product_client: ProductClient, term: str, expect_results: bool
    ) -> None:
        response = product_client.search(term)
        response.assert_status(200)
        found = response.body["total"] > 0
        assert found is expect_results, (
            f"Searching {term!r} returned {response.body['total']} results; expected "
            f"{'some' if expect_results else 'none'}"
        )

    def test_search_is_case_insensitive(self, product_client: ProductClient) -> None:
        totals = {
            case: product_client.search(case).body["total"]
            for case in ("laptop", "Laptop", "LAPTOP", "LaPtOp")
        }
        assert len(set(totals.values())) == 1, f"Case changed the result count: {totals}"
        assert next(iter(totals.values())) > 0

    def test_search_matches_partial_words(self, product_client: ProductClient) -> None:
        """ "ultra" must find "Ultrabook" - shoppers do not type whole words."""
        assert product_client.search("ultra").body["total"] > 0
        assert product_client.search("book").body["total"] > 0

    def test_search_covers_name_brand_and_sku(self, product_client: ProductClient) -> None:
        by_sku = items(product_client.search("LAP-1001"))
        assert any(p["sku"] == "LAP-1001" for p in by_sku)

        by_brand = all_matching(product_client, q="Aurora")
        assert any(p["brand"] == "Aurora" for p in by_brand)

    def test_no_results_returns_an_empty_page_not_an_error(
        self, product_client: ProductClient
    ) -> None:
        response = product_client.search("zzzz-definitely-not-a-product")
        response.assert_status(200)
        assert response.body["total"] == 0
        assert response.body["items"] == []
        assert response.body["total_pages"] == 0
        assert response.body["has_next"] is False

    @pytest.mark.parametrize("term", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
    def test_a_blank_search_is_not_a_search_for_nothing(
        self, product_client: ProductClient, term: str
    ) -> None:
        """`?q=` must show the catalogue, not zero results."""
        response = product_client.search(term)
        response.assert_status(200)
        assert response.body["total"] >= 60

    def test_like_wildcards_are_treated_literally(self, product_client: ProductClient) -> None:
        """`%` must be a character, not "match everything".

        Without escaping, a search for "100%" would return the entire catalogue.
        """
        total = product_client.list().body["total"]
        for wildcard in ("%", "_", "%%", "_%_"):
            response = product_client.search(wildcard)
            response.assert_status(200)
            assert response.body["total"] < total, (
                f"Searching {wildcard!r} returned {response.body['total']} of {total} - "
                f"the wildcard was interpreted rather than matched literally"
            )


# ---------------------------------------------------------------------------
@allure.story("Filtering")
class TestFiltering:
    @pytest.mark.parametrize(
        ("minimum", "maximum"), PRICE_RANGES, ids=[f"{a}-{b}" for a, b in PRICE_RANGES]
    )
    def test_price_ranges_are_respected(
        self, product_client: ProductClient, minimum: float | None, maximum: float | None
    ) -> None:
        for product in all_matching(product_client, min_price=minimum, max_price=maximum):
            if minimum is not None:
                assert product["price"] >= minimum, f"{product['sku']} is below min_price"
            if maximum is not None:
                assert product["price"] <= maximum, f"{product['sku']} is above max_price"

    def test_filtering_by_category_slug(self, product_client: ProductClient) -> None:
        found = all_matching(product_client, category="laptops")
        assert found, "No laptops found"
        assert all(p["category"]["slug"] == "laptops" for p in found)

    def test_a_category_can_be_named_or_slugged(
        self, product_client: ProductClient, product_factory
    ) -> None:
        """`?category=Laptops` and `?category=laptops` must agree.

        A URL shared from a UI that uses display names should not return a
        different catalogue from one built from slugs.

        Scoped to a brand this test creates. Comparing two unfiltered
        whole-catalogue counts would compare two different moments in time, and
        another worker creating a product between them made this fail under
        `-n auto` for reasons unrelated to slug handling.
        """
        brand = f"SlugBrand{uuid.uuid4().hex[:10]}"
        for _ in range(3):
            product_factory(brand=brand)

        by_slug = product_client.list(category="laptops", brand=brand).body["total"]
        by_name = product_client.list(category="Laptops", brand=brand).body["total"]
        assert by_slug == by_name == 3

    def test_filtering_by_brand(self, product_client: ProductClient) -> None:
        found = all_matching(product_client, brand="Aurora")
        assert found
        assert all(p["brand"] == "Aurora" for p in found)

    def test_brand_matching_is_case_insensitive(self, product_client: ProductClient) -> None:
        assert product_client.list(brand="aurora").body["total"] == (
            product_client.list(brand="Aurora").body["total"]
        )

    @pytest.mark.parametrize("minimum", [3.0, 4.0, 4.5], ids=["3.0", "4.0", "4.5"])
    def test_filtering_by_minimum_rating(
        self, product_client: ProductClient, minimum: float
    ) -> None:
        for product in all_matching(product_client, min_rating=minimum):
            assert float(product["rating"]) >= minimum

    def test_in_stock_filter_both_ways(self, product_client: ProductClient) -> None:
        for product in all_matching(product_client, in_stock=True):
            assert product["stock_quantity"] > 0
        for product in all_matching(product_client, in_stock=False):
            assert product["stock_quantity"] == 0

    @allure.severity(allure.severity_level.CRITICAL)
    def test_filters_combine_with_and(self, product_client: ProductClient) -> None:
        """Every filter must narrow, not widen.

        A filter accidentally OR-ed with the others would return products the
        customer explicitly excluded.
        """
        found = all_matching(
            product_client,
            category="laptops",
            min_price=800,
            max_price=2000,
            min_rating=4.0,
            in_stock=True,
        )
        assert found, "The combined filter matched nothing; widen the fixture data"
        for product in found:
            assert product["category"]["slug"] == "laptops"
            assert 800 <= product["price"] <= 2000
            assert float(product["rating"]) >= 4.0
            assert product["stock_quantity"] > 0

    def test_combining_filters_never_widens_the_result_set(
        self, product_client: ProductClient
    ) -> None:
        broad = product_client.list(category="laptops").body["total"]
        narrow = product_client.list(category="laptops", min_rating=4.5).body["total"]
        assert narrow <= broad

    def test_an_inverted_price_range_is_rejected(self, product_client: ProductClient) -> None:
        """A cross-field rule must be a 422, not a 500 or an empty page."""
        response = product_client.list(min_price=500, max_price=100)
        response.assert_error("VALIDATION_ERROR", 422)
        assert set(response.body) == {"error", "message", "details"}

    def test_an_unknown_brand_returns_nothing_rather_than_everything(
        self, product_client: ProductClient
    ) -> None:
        assert product_client.list(brand="NoSuchBrandExists").body["total"] == 0


# ---------------------------------------------------------------------------
@allure.story("Sorting")
class TestSorting:
    @pytest.mark.parametrize("sort", SORT_OPTIONS, ids=list(SORT_OPTIONS))
    def test_every_sort_option_is_accepted(self, product_client: ProductClient, sort: str) -> None:
        product_client.list(sort=sort, page_size=100).assert_status(200)

    def test_price_ascending_is_actually_ascending(self, product_client: ProductClient) -> None:
        prices = [p["price"] for p in items(product_client.list(sort="price_asc", page_size=100))]
        assert prices == sorted(prices), "price_asc is not ordered"

    def test_price_descending_is_actually_descending(self, product_client: ProductClient) -> None:
        prices = [p["price"] for p in items(product_client.list(sort="price_desc", page_size=100))]
        assert prices == sorted(prices, reverse=True), "price_desc is not ordered"

    def test_rating_descending_is_actually_descending(self, product_client: ProductClient) -> None:
        ratings = [
            float(p["rating"])
            for p in items(product_client.list(sort="rating_desc", page_size=100))
        ]
        assert ratings == sorted(ratings, reverse=True)

    def test_name_ascending_is_alphabetical(self, product_client: ProductClient) -> None:
        names = [p["name"] for p in items(product_client.list(sort="name_asc", page_size=100))]
        assert names == sorted(names)

    def test_newest_first_puts_the_most_recent_product_first(
        self, product_client: ProductClient, product_factory
    ) -> None:
        """Scoped to this test's own brand.

        Asserting against the whole catalogue would mean asserting that no other
        worker created a product in the microseconds after this one - which is
        a statement about the test runner, not about sorting.
        """
        brand = f"NewestBrand{uuid.uuid4().hex[:10]}"
        older = product_factory(brand=brand, name="Older Product")
        newest = product_factory(brand=brand, name="Newer Product")

        ordered = items(product_client.list(brand=brand, sort="newest", page_size=10))
        assert [product["id"] for product in ordered] == [newest["id"], older["id"]]

    def test_an_unknown_sort_key_is_rejected(self, product_client: ProductClient) -> None:
        """Sort keys are an allow-list, so a request cannot inject an ORDER BY."""
        product_client.list(sort="price_asc; DROP TABLE products").assert_status(422)
        product_client.list(sort="id_desc").assert_status(422)


# ---------------------------------------------------------------------------
@allure.story("Pagination")
class TestPagination:
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize("sort", ["price_asc", "rating_desc", "name_asc"])
    def test_pages_never_repeat_or_skip_a_product(
        self, product_client: ProductClient, product_factory, sort: str
    ) -> None:
        """The reason every sort carries a unique tiebreaker.

        Ordering by a non-unique key alone lets PostgreSQL return equal-valued
        rows in any order it likes, so page 2 can repeat a row already shown on
        page 1 - and silently omit another.

        The products are created by this test, all with **identical** price,
        rating and name prefix, and are isolated behind a unique brand. That
        does two things: it forces the tie case the tiebreaker exists for, and
        it makes the walk immune to other workers creating products mid-run,
        which is what made an earlier version of this test fail under `-n auto`.
        """
        brand = f"PageBrand{uuid.uuid4().hex[:10]}"
        created = {
            product_factory(brand=brand, price=42.00, rating=4.0, name=f"Tied Product {index:02d}")[
                "id"
            ]
            for index in range(9)
        }

        seen: list[int] = []
        page = 1
        while True:
            response = product_client.list(brand=brand, sort=sort, page=page, page_size=4)
            response.assert_status(200)
            seen.extend(product["id"] for product in response.body["items"])
            if not response.body["has_next"]:
                total = response.body["total"]
                break
            page += 1

        duplicates = {product_id for product_id in seen if seen.count(product_id) > 1}
        assert not duplicates, f"Products appeared on more than one page: {sorted(duplicates)}"
        assert total == len(created), f"total says {total}, this test created {len(created)}"
        assert set(seen) == created, (
            f"Walking the pages did not return exactly the created set. "
            f"Missing: {sorted(created - set(seen))}, unexpected: {sorted(set(seen) - created)}"
        )

    @pytest.mark.parametrize(
        ("label", "params", "expected"),
        PAGINATION_BOUNDARIES,
        ids=[case[0] for case in PAGINATION_BOUNDARIES],
    )
    def test_pagination_boundaries(
        self, product_client: ProductClient, label: str, params: dict[str, Any], expected: int
    ) -> None:
        product_client.list(**params).assert_status(expected)

    def test_a_page_past_the_end_is_empty_not_an_error(self, product_client: ProductClient) -> None:
        response = product_client.list(page=9999, page_size=20)
        response.assert_status(200)
        assert response.body["items"] == []
        assert response.body["has_next"] is False
        assert response.body["has_previous"] is True

    def test_the_metadata_agrees_with_the_data(self, product_client: ProductClient) -> None:
        response = product_client.list(page_size=10)
        body = response.assert_status(200).body
        expected_pages = -(-body["total"] // body["page_size"])  # ceiling division
        assert body["total_pages"] == expected_pages
        assert body["has_next"] is (body["page"] < body["total_pages"])


# ---------------------------------------------------------------------------
@allure.story("Product detail")
class TestProductDetail:
    def test_a_product_can_be_fetched_by_id(
        self, product_client: ProductClient, seeded_product
    ) -> None:
        response = product_client.get(seeded_product["id"])
        response.assert_status(200)
        assert response.body["id"] == seeded_product["id"]
        assert response.body["sku"] == seeded_product["sku"]
        assert {"description", "created_at", "updated_at"} <= set(response.body)

    def test_an_unknown_id_is_a_404_in_the_standard_envelope(
        self, product_client: ProductClient
    ) -> None:
        response = product_client.get(99_999_999)
        response.assert_error("PRODUCT_NOT_FOUND", 404)
        assert set(response.body) == {"error", "message", "details"}

    @pytest.mark.parametrize(
        "bad_id", ["abc", "1.5", "-1", "0"], ids=["text", "float", "negative", "zero"]
    )
    def test_a_malformed_id_is_rejected(self, product_client: ProductClient, bad_id: str) -> None:
        product_client.get(bad_id).assert_status_in(404, 422)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_a_deactivated_product_is_hidden_from_customers_but_visible_to_admins(
        self,
        product_client: ProductClient,
        admin_product_client: ProductClient,
        admin_client: AdminClient,
        product_factory,
    ) -> None:
        """Withdrawing a product must remove it from the shop immediately.

        Admins keep seeing it because they need to be able to find and
        reactivate it - a withdrawn product that becomes invisible to its own
        administrator is unrecoverable.
        """
        product = product_factory(stock_quantity=5)
        product_client.get(product["id"]).assert_status(200)

        admin_client.deactivate_product(product["id"]).assert_status(200)

        product_client.get(product["id"]).assert_error("PRODUCT_NOT_FOUND", 404)
        admin_view = admin_product_client.get(product["id"], token=admin_client.token)
        admin_view.assert_status(200)
        assert admin_view.body["is_active"] is False

    def test_include_inactive_is_ignored_for_ordinary_customers(
        self, product_client: ProductClient, admin_client: AdminClient, product_factory
    ) -> None:
        """The flag exists for admins; a customer passing it sees no more."""
        product = product_factory(stock_quantity=1)
        admin_client.deactivate_product(product["id"]).assert_status(200)

        found = product_client.list(include_inactive=True, page_size=100, q=product["sku"])
        found.assert_status(200)
        assert found.body["total"] == 0


# ---------------------------------------------------------------------------
@allure.story("Categories and facets")
class TestCategoriesAndFacets:
    def test_every_seeded_category_is_listed_with_a_count(
        self, product_client: ProductClient
    ) -> None:
        response = product_client.categories()
        response.assert_status(200)
        assert len(response.body) == 7
        for category in response.body:
            assert {"id", "name", "slug", "product_count"} <= set(category)
            assert category["product_count"] >= 0

    @pytest.mark.serial
    def test_category_counts_match_the_listing(self, product_client: ProductClient) -> None:
        """A count that disagrees with the results is worse than no count.

        Marked serial: this compares two whole-catalogue reads, so another
        worker creating a product between them would break the equality for
        reasons that have nothing to do with the facet being wrong. The property
        is real; it is simply only observable when the catalogue is still.
        """
        for category in product_client.categories().body:
            listed = product_client.list(category=category["slug"], page_size=1).body["total"]
            assert (
                listed == category["product_count"]
            ), f"{category['slug']}: facet says {category['product_count']}, listing says {listed}"

    def test_a_category_can_be_fetched_by_slug(self, product_client: ProductClient) -> None:
        response = product_client.category("laptops")
        response.assert_status(200)
        assert response.body["slug"] == "laptops"

    def test_an_unknown_slug_is_a_404(self, product_client: ProductClient) -> None:
        product_client.category("no-such-category").assert_error("NOT_FOUND", 404)

    def test_the_brand_facet_lists_brands_with_counts(self, product_client: ProductClient) -> None:
        response = product_client.brands()
        response.assert_status(200)
        assert len(response.body) >= 5
        for entry in response.body:
            assert set(entry) == {"brand", "product_count"}
            assert entry["product_count"] > 0

    @pytest.mark.serial
    def test_brand_counts_match_the_listing(self, product_client: ProductClient) -> None:
        """Same whole-catalogue caveat as the category counts above."""
        for entry in product_client.brands().body[:5]:
            listed = product_client.list(brand=entry["brand"], page_size=1).body["total"]
            assert (
                listed == entry["product_count"]
            ), f"{entry['brand']}: facet says {entry['product_count']}, listing says {listed}"
