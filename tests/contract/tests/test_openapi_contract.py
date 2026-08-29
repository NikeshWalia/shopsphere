"""Contract tests: live responses validated against the live OpenAPI document.

The drift these exist to catch is the quiet kind. A field that changes from a
number to a string, a key that disappears, an enum that grows a value — none of
these break the server, and none show up as a failing status code. They break
whoever is consuming the API, usually in production, usually much later.

The spec is fetched from the running service rather than from a committed copy,
so this cannot pass against a stale snapshot: it always compares what the API
*says* it returns with what it *actually* returns.
"""

from __future__ import annotations

from typing import Any

import allure
import httpx
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012

from tests.api.clients import AddressClient, CartClient, OrderClient, ProductClient
from tests.configuration.settings import settings

pytestmark = [allure.epic("Contract"), allure.feature("OpenAPI conformance")]

#: Identifier the live spec is registered under so $refs resolve absolutely.
SPEC_URI = "urn:shopsphere:openapi"

# Every monetary field, by response shape. Money is the type most likely to
# drift and the most damaging when it does.
MONEY_FIELDS = {
    "product": ["price"],
    "cart_item": ["unit_price", "line_total"],
    "totals": ["subtotal", "discount_total", "tax", "shipping_fee", "total"],
    "order_item": ["unit_price", "line_total"],
}


@pytest.fixture(scope="session")
def openapi_spec() -> dict[str, Any]:
    """The live OpenAPI document."""
    response = httpx.get(f"{settings.api_base_url}/openapi.json", timeout=30.0)
    assert response.status_code == 200, f"Could not fetch the spec: {response.status_code}"
    return dict(response.json())


@pytest.fixture(scope="session")
def validator_for(openapi_spec: dict[str, Any]):
    """Build a validator for a component schema, with $ref resolution.

    OpenAPI schemas are heavily $ref'd into ``components/schemas``. A bare
    ``{"$ref": "#/components/schemas/X"}`` would resolve that fragment against
    *itself* and fail with PointerToNowhere, so the whole document is registered
    under an explicit URI and referenced absolutely. Without this, nested
    references such as OrderResponse -> OrderItemResponse would error instead of
    validating - and a validator that errors on everything checks nothing.
    """
    registry = Registry().with_resource(
        uri=SPEC_URI, resource=DRAFT202012.create_resource(openapi_spec)
    )

    def _build(component_name: str) -> Draft202012Validator:
        assert component_name in openapi_spec["components"]["schemas"], (
            f"{component_name} is not declared in the spec. Available: "
            f"{sorted(openapi_spec['components']['schemas'])[:20]}"
        )
        schema = {"$ref": f"{SPEC_URI}#/components/schemas/{component_name}"}
        return Draft202012Validator(schema, registry=registry)

    return _build


def assert_valid(validator: Draft202012Validator, instance: Any, label: str) -> None:
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if errors:
        detail = "\n".join(
            f"  at {'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:8]
        )
        raise AssertionError(f"{label} does not match its declared schema:\n{detail}")


# ---------------------------------------------------------------------------
@allure.story("The specification itself")
class TestSpecification:
    def test_the_spec_is_a_well_formed_openapi_document(self, openapi_spec: dict[str, Any]) -> None:
        assert openapi_spec["openapi"].startswith("3."), openapi_spec["openapi"]
        assert {"openapi", "info", "paths", "components"} <= set(openapi_spec)
        assert openapi_spec["info"]["title"]
        assert openapi_spec["info"]["version"]

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/api/v1/auth/register", "post"),
            ("/api/v1/auth/login", "post"),
            ("/api/v1/auth/me", "get"),
            ("/api/v1/products", "get"),
            ("/api/v1/products/{product_id}", "get"),
            ("/api/v1/categories", "get"),
            ("/api/v1/cart", "get"),
            ("/api/v1/cart/items", "post"),
            ("/api/v1/checkout/quote", "post"),
            ("/api/v1/orders", "post"),
            ("/api/v1/orders", "get"),
            ("/api/v1/orders/{order_id}", "get"),
            ("/api/v1/orders/{order_id}/cancel", "post"),
            ("/api/v1/admin/products", "post"),
            ("/health", "get"),
            ("/health/ready", "get"),
        ],
    )
    def test_every_documented_endpoint_is_present(
        self, openapi_spec: dict[str, Any], path: str, method: str
    ) -> None:
        assert path in openapi_spec["paths"], f"{path} is missing from the spec"
        assert method in openapi_spec["paths"][path], f"{method.upper()} {path} is missing"

    def test_operations_carry_a_summary(self, openapi_spec: dict[str, Any]) -> None:
        """An undocumented operation is one nobody can use without reading the source."""
        undocumented = [
            f"{method.upper()} {path}"
            for path, operations in openapi_spec["paths"].items()
            for method, operation in operations.items()
            if method in ("get", "post", "put", "patch", "delete") and not operation.get("summary")
        ]
        assert not undocumented, f"Operations without a summary: {undocumented}"

    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        ("path", "method", "expected_codes"),
        [
            ("/api/v1/orders", "post", {"201", "401", "402", "409", "422", "502", "504"}),
            ("/api/v1/cart/items", "post", {"201", "401", "404", "409", "422"}),
            ("/api/v1/auth/register", "post", {"201", "409", "422"}),
            ("/api/v1/auth/login", "post", {"200", "401", "422"}),
            ("/api/v1/orders/{order_id}", "get", {"200", "401", "403", "404"}),
        ],
    )
    def test_failure_responses_are_documented(
        self, openapi_spec: dict[str, Any], path: str, method: str, expected_codes: set[str]
    ) -> None:
        """A 409 the spec does not mention is a 409 no client is ready for.

        Documenting only the happy path is the most common way an OpenAPI
        document becomes misleading.
        """
        declared = set(openapi_spec["paths"][path][method]["responses"])
        missing = expected_codes - declared
        assert not missing, f"{method.upper()} {path} does not document {sorted(missing)}"

    def test_money_is_declared_as_a_number_in_the_schema(
        self, openapi_spec: dict[str, Any]
    ) -> None:
        """The schema must promise what the serialiser delivers.

        Pydantic serialises Decimal as a string by default, and would also
        declare it as one. A custom serialiser fixes the wire format; this
        checks the document was not left describing the old behaviour.
        """
        schemas = openapi_spec["components"]["schemas"]
        for component, fields in (
            ("ProductSummaryResponse", ["price"]),
            ("CartTotals", ["subtotal", "tax", "total"]),
            ("OrderResponse", ["subtotal", "tax", "total"]),
        ):
            properties = schemas[component]["properties"]
            for field in fields:
                declared = properties[field]
                kind = declared.get("type") or [
                    option.get("type") for option in declared.get("anyOf", [])
                ]
                assert "number" in (
                    kind if isinstance(kind, list) else [kind]
                ), f"{component}.{field} is declared as {kind!r}, not number"

    def test_order_status_enums_match_the_documented_values(
        self, openapi_spec: dict[str, Any]
    ) -> None:
        schemas = openapi_spec["components"]["schemas"]
        assert set(schemas["OrderStatus"]["enum"]) == {
            "pending",
            "confirmed",
            "processing",
            "shipped",
            "delivered",
            "cancelled",
        }
        assert set(schemas["PaymentStatus"]["enum"]) == {
            "pending",
            "paid",
            "failed",
            "refunded",
        }

    def test_the_validator_actually_rejects_a_mismatch(self, validator_for) -> None:
        """Proves these tests can fail.

        A validator misconfigured so that $ref never resolves would silently
        accept anything, and every test in this file would pass while checking
        nothing. This asserts the mechanism has teeth before relying on it.
        """
        validator = validator_for("ProductSummaryResponse")
        broken = {
            "id": "not-an-integer",
            "sku": "X",
            "name": "X",
            "price": "129.99",
            "brand": "X",
            "rating": "4.0",
            "is_active": True,
            "in_stock": True,
            "stock_quantity": 1,
            "category": {"id": 1, "name": "X", "slug": "x"},
        }
        assert list(validator.iter_errors(broken)), "The validator accepted an invalid object"


# ---------------------------------------------------------------------------
@allure.story("Response conformance")
class TestResponseConformance:
    def test_product_listing_items_match_their_schema(
        self, product_client: ProductClient, validator_for
    ) -> None:
        validator = validator_for("ProductSummaryResponse")
        response = product_client.list(page_size=20)
        response.assert_status(200)
        for product in response.body["items"]:
            assert_valid(validator, product, f"Product {product['sku']}")

    def test_product_detail_matches_its_schema(
        self, product_client: ProductClient, seeded_product, validator_for
    ) -> None:
        response = product_client.get(seeded_product["id"])
        response.assert_status(200)
        assert_valid(validator_for("ProductDetailResponse"), response.body, "Product detail")

    def test_categories_match_their_schema(
        self, product_client: ProductClient, validator_for
    ) -> None:
        validator = validator_for("CategoryWithCountResponse")
        for category in product_client.categories().assert_status(200).body:
            assert_valid(validator, category, f"Category {category['slug']}")

    def test_the_login_response_matches_its_schema(
        self, auth_client, customer, validator_for
    ) -> None:
        response = auth_client.login(customer.email, customer.password)
        response.assert_status(200)
        assert_valid(validator_for("TokenResponse"), response.body, "Login response")

    def test_the_current_user_matches_its_schema(
        self, auth_client, customer, validator_for
    ) -> None:
        response = auth_client.me(token=customer.token)
        response.assert_status(200)
        assert_valid(validator_for("UserResponse"), response.body, "User")

    def test_the_cart_matches_its_schema(
        self, cart_client: CartClient, product_factory, validator_for
    ) -> None:
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 2).assert_status(201)

        response = cart_client.get().assert_status(200)
        assert_valid(validator_for("CartResponse"), response.body, "Cart")

    def test_a_quote_matches_its_schema(
        self, order_client: OrderClient, cart_client: CartClient, product_factory, validator_for
    ) -> None:
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)

        response = order_client.quote().assert_status(200)
        assert_valid(validator_for("QuoteResponse"), response.body, "Quote")

    def test_an_order_matches_its_schema(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
        validator_for,
    ) -> None:
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)

        created = order_client.checkout(address_id=address_id).assert_status(201)
        assert_valid(validator_for("OrderResponse"), created.body, "Order")

        fetched = order_client.get(created.body["id"]).assert_status(200)
        assert_valid(validator_for("OrderResponse"), fetched.body, "Order detail")

    def test_addresses_match_their_schema(
        self, address_client: AddressClient, validator_for
    ) -> None:
        created = address_client.create().assert_status(201)
        assert_valid(validator_for("AddressResponse"), created.body, "Address")


# ---------------------------------------------------------------------------
@allure.story("Money type contract")
class TestMoneyContract:
    """The exact drift described in the brief: `100` becoming `"100"`.

    Every one of these would still return HTTP 200 if it regressed, which is
    precisely why a status-code assertion is not enough.
    """

    @staticmethod
    def assert_number(value: Any, label: str) -> None:
        assert isinstance(value, int | float) and not isinstance(
            value, bool
        ), f"{label} is {value!r} of type {type(value).__name__}; money must be a JSON number"
        assert round(float(value), 2) == float(value), f"{label} has more than 2 decimal places"

    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize("field", MONEY_FIELDS["product"])
    def test_product_money_is_a_number(self, product_client: ProductClient, field: str) -> None:
        for product in product_client.list(page_size=30).assert_status(200).body["items"]:
            self.assert_number(product[field], f"product[{product['sku']}].{field}")

    @pytest.mark.parametrize("field", MONEY_FIELDS["totals"])
    def test_cart_totals_are_numbers(
        self, cart_client: CartClient, product_factory, field: str
    ) -> None:
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 2).assert_status(201)

        body = cart_client.get().assert_status(200).body
        self.assert_number(body["totals"][field], f"cart.totals.{field}")

    @pytest.mark.parametrize("field", MONEY_FIELDS["cart_item"])
    def test_cart_line_money_is_a_number(
        self, cart_client: CartClient, product_factory, field: str
    ) -> None:
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 2).assert_status(201)

        for line in cart_client.get().assert_status(200).body["items"]:
            self.assert_number(line[field], f"cart.items[].{field}")

    @pytest.mark.parametrize("field", MONEY_FIELDS["totals"])
    def test_quote_money_is_a_number(
        self, order_client: OrderClient, cart_client: CartClient, product_factory, field: str
    ) -> None:
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)

        self.assert_number(order_client.quote().assert_status(200).body[field], f"quote.{field}")

    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize("field", MONEY_FIELDS["totals"])
    def test_order_money_is_a_number(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
        field: str,
    ) -> None:
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)

        order = order_client.checkout(address_id=address_id).assert_status(201).body
        self.assert_number(order[field], f"order.{field}")
        for line in order["items"]:
            for line_field in MONEY_FIELDS["order_item"]:
                self.assert_number(line[line_field], f"order.items[].{line_field}")
        for payment in order["payments"]:
            self.assert_number(payment["amount"], "order.payments[].amount")


# ---------------------------------------------------------------------------
@allure.story("Error envelope contract")
class TestErrorEnvelope:
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        ("label", "expected_status"),
        [
            ("not-found", 404),
            ("validation", 422),
            ("unauthorised", 401),
            ("conflict", 409),
            ("route-not-found", 404),
            ("method-not-allowed", 405),
        ],
    )
    def test_every_error_uses_the_same_envelope(
        self,
        http,
        product_client: ProductClient,
        cart_client: CartClient,
        customer,
        label: str,
        expected_status: int,
    ) -> None:
        """One shape for every failure, from every layer.

        FastAPI's own 422, Starlette's 404 and the application's business errors
        would otherwise each have a different body, and a client would need
        three parsers.
        """
        responses = {
            "not-found": lambda: product_client.get(99_999_999),
            "validation": lambda: product_client.list(min_price=500, max_price=1),
            "unauthorised": lambda: http.get("/cart", authenticate=False),
            "conflict": lambda: cart_client.add_item(99_999_999, 1),
            "route-not-found": lambda: http.get("/no-such-endpoint", authenticate=False),
            "method-not-allowed": lambda: http.delete("/products", authenticate=False),
        }
        response = responses[label]()

        if label == "conflict":
            # An unknown product is a 404; the point is the envelope, not the code.
            assert response.status_code in (404, 409)
        else:
            assert (
                response.status_code == expected_status
            ), f"{label}: expected {expected_status}, got {response.status_code}"

        assert isinstance(response.body, dict), f"{label} returned {response.raw_text[:200]}"
        assert set(response.body) == {
            "error",
            "message",
            "details",
        }, f"{label} envelope has keys {sorted(response.body)}"
        assert isinstance(response.body["error"], str) and response.body["error"]
        assert isinstance(response.body["message"], str) and response.body["message"]
        assert isinstance(response.body["details"], dict)

    def test_the_error_schema_is_declared_in_the_spec(self, openapi_spec: dict[str, Any]) -> None:
        schema = openapi_spec["components"]["schemas"]["ErrorResponse"]
        assert set(schema["properties"]) == {"error", "message", "details"}
        assert set(schema["required"]) >= {"error", "message"}

    def test_validation_errors_name_the_offending_fields(self, auth_client) -> None:
        response = auth_client.register_raw({"email": "nope", "password": "x"})
        response.assert_status(422)
        fields = response.details.get("fields")
        assert isinstance(fields, list) and fields
        for entry in fields:
            assert {"field", "message", "type"} <= set(entry)


# ---------------------------------------------------------------------------
@allure.story("Pagination contract")
class TestPaginationContract:
    @pytest.mark.parametrize("endpoint", ["/products", "/orders"])
    def test_every_collection_uses_the_same_envelope(self, http, customer, endpoint: str) -> None:
        response = http.get(endpoint, params={"page": 1, "page_size": 5}, token=customer.token)
        response.assert_status(200)
        assert set(response.body) == {
            "items",
            "total",
            "page",
            "page_size",
            "total_pages",
            "has_next",
            "has_previous",
        }, f"{endpoint} envelope has keys {sorted(response.body)}"
        assert isinstance(response.body["items"], list)
        assert isinstance(response.body["total"], int)
        assert isinstance(response.body["has_next"], bool)
