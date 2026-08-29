"""Security tests: authentication, authorisation and object-level access.

Non-destructive throughout. Nothing here attempts to damage data or exhaust
resources; every test asserts that an attempt is *refused*.

The distinction these tests defend most carefully is 401 versus 403 versus 404:

* **401** you are not authenticated.
* **403** you are, but you may not do this.
* **404** you may not know whether this exists.

Returning 403 where the answer should be 404 leaks the existence of another
customer's order, which is itself information an attacker can use.
"""

from __future__ import annotations

from typing import Any

import allure
import pytest

from tests.api.clients import AddressClient, AdminClient, CartClient, OrderClient
from tests.utilities.http import HttpClient
from tests.utilities.tokens import (
    alg_none_token,
    expired_token,
    forged_admin_token,
    mint_token,
    self_signed_admin_token,
    wrong_signature_token,
)

pytestmark = [allure.epic("Security"), allure.feature("Authorisation")]

# Every admin route, with a body where the verb needs one.
ADMIN_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/admin/users", None),
    ("GET", "/admin/orders", None),
    ("GET", "/admin/orders/1", None),
    ("GET", "/admin/inventory", None),
    ("GET", "/admin/stats", None),
    (
        "POST",
        "/admin/products",
        {"sku": "HACK-1", "name": "Hacked", "price": 1, "category_id": 1, "brand": "X"},
    ),
    ("PATCH", "/admin/products/1", {"name": "Renamed by an attacker"}),
    ("DELETE", "/admin/products/1", None),
    ("PUT", "/admin/products/1/stock", {"quantity": 99999}),
    ("PATCH", "/admin/orders/1/status", {"status": "delivered"}),
    ("PATCH", "/admin/users/1/active", None),
]

PROTECTED_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/auth/me", None),
    ("PATCH", "/auth/me", {"full_name": "Nobody"}),
    ("POST", "/auth/logout", None),
    ("GET", "/cart", None),
    ("POST", "/cart/items", {"product_id": 1, "quantity": 1}),
    ("DELETE", "/cart", None),
    ("GET", "/addresses", None),
    (
        "POST",
        "/addresses",
        {
            "full_name": "N",
            "line1": "1 St",
            "city": "C",
            "state": "S",
            "postal_code": "12345",
            "country": "US",
        },
    ),
    ("GET", "/orders", None),
    ("GET", "/orders/1", None),
    ("POST", "/checkout/quote", {}),
]


def call(client: HttpClient, method: str, path: str, body: dict[str, Any] | None, **kwargs: Any):
    return client.request(method, path, json_body=body, **kwargs)


# ---------------------------------------------------------------------------
@allure.story("Anonymous access")
class TestAnonymousAccess:
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        PROTECTED_ENDPOINTS,
        ids=[f"{m}-{p.strip('/').replace('/', '-')}" for m, p, _ in PROTECTED_ENDPOINTS],
    )
    def test_protected_endpoints_reject_anonymous_callers(
        self, http: HttpClient, method: str, path: str, body: dict[str, Any] | None
    ) -> None:
        response = call(http, method, path, body, authenticate=False)
        response.assert_status(401)
        assert set(response.body) == {"error", "message", "details"}

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        ADMIN_ENDPOINTS,
        ids=[f"{m}-{p.strip('/').replace('/', '-')}" for m, p, _ in ADMIN_ENDPOINTS],
    )
    def test_admin_endpoints_reject_anonymous_callers(
        self, http: HttpClient, method: str, path: str, body: dict[str, Any] | None
    ) -> None:
        call(http, method, path, body, authenticate=False).assert_status(401)


# ---------------------------------------------------------------------------
@allure.story("Role enforcement")
class TestRoleEnforcement:
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        ADMIN_ENDPOINTS,
        ids=[f"{m}-{p.strip('/').replace('/', '-')}" for m, p, _ in ADMIN_ENDPOINTS],
    )
    def test_customers_are_refused_on_every_admin_endpoint(
        self, customer_http: HttpClient, method: str, path: str, body: dict[str, Any] | None
    ) -> None:
        """Business rule 3.

        The whole admin surface, not a sample: a route added without the
        dependency would be caught here rather than in production.
        """
        response = call(customer_http, method, path, body)
        response.assert_error("PERMISSION_DENIED", 403)

    def test_an_authenticated_customer_gets_403_not_401(self, customer_http: HttpClient) -> None:
        """The two must stay distinguishable.

        Returning 401 to a signed-in customer would make clients try to
        re-authenticate in a loop that can never succeed.
        """
        anonymous = customer_http.get("/admin/users", authenticate=False)
        authenticated = customer_http.get("/admin/users")

        anonymous.assert_status(401)
        authenticated.assert_status(403)

    def test_the_admin_role_is_reported_but_not_granted_by_the_client(
        self, customer_http: HttpClient, customer
    ) -> None:
        """A client cannot promote itself by asserting a role.

        The extra `role` field is not part of the update schema, so it is
        discarded rather than honoured - the request succeeds and changes only
        what it was allowed to change.
        """
        response = customer_http.patch(
            "/auth/me", json_body={"full_name": "Renamed User", "role": "admin"}
        )
        response.assert_status(200)
        assert response.body["role"] == "customer"
        assert response.body["full_name"] == "Renamed User"

        # And the change did not confer authority on the next request either.
        assert customer_http.get("/auth/me").body["role"] == "customer"
        customer_http.get("/admin/users").assert_status(403)

    def test_an_admin_can_reach_the_admin_area(self, admin_client: AdminClient) -> None:
        """The negative tests above are only meaningful if the positive one works."""
        admin_client.users().assert_status(200)
        admin_client.stats().assert_status(200)


# ---------------------------------------------------------------------------
@allure.story("Token forgery")
class TestTokenForgery:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_a_correctly_signed_token_claiming_admin_is_still_refused(
        self, http: HttpClient, customer
    ) -> None:
        """The most important test in this file.

        This token is signed with the real key and verifies perfectly - it
        simply claims `role: admin` for an account that is a customer. It is
        refused because authority is read from the user record on every request,
        never from the token body. A server that trusted the claim would hand
        admin to anyone who could obtain a token.
        """
        token = self_signed_admin_token(user_id=customer.id)

        # The token is accepted as authentication...
        http.get("/auth/me", token=token).assert_status(200)
        # ...but confers no authority.
        http.get("/admin/users", token=token).assert_error("PERMISSION_DENIED", 403)
        http.get("/admin/stats", token=token).assert_status(403)

    def test_a_token_signed_with_another_key_is_not_authentication(
        self, http: HttpClient, customer
    ) -> None:
        http.get("/admin/users", token=forged_admin_token(user_id=customer.id)).assert_status(401)
        http.get("/auth/me", token=wrong_signature_token(user_id=customer.id)).assert_status(401)

    @allure.severity(allure.severity_level.CRITICAL)
    def test_an_unsigned_alg_none_token_is_rejected(self, http: HttpClient, customer) -> None:
        """The classic JWT downgrade attack.

        Blocked because the decoder pins an explicit algorithm allow-list rather
        than trusting the algorithm named in the token's own header.
        """
        http.get("/auth/me", token=alg_none_token(user_id=customer.id)).assert_status(401)
        http.get("/admin/users", token=alg_none_token(user_id=customer.id)).assert_status(401)

    def test_an_expired_token_is_rejected_everywhere(self, http: HttpClient, customer) -> None:
        token = expired_token(user_id=customer.id)
        for path in ("/auth/me", "/cart", "/orders", "/addresses"):
            response = http.get(path, token=token)
            response.assert_error("TOKEN_EXPIRED", 401)

    def test_a_token_for_a_nonexistent_account_is_rejected(self, http: HttpClient) -> None:
        """A correctly signed token whose subject was deleted must not work."""
        http.get("/auth/me", token=mint_token(user_id=999_999_999)).assert_status(401)

    @pytest.mark.parametrize(
        ("label", "kwargs"),
        [
            ("no-exp", {"omit_claims": ("exp",)}),
            ("no-sub", {"omit_claims": ("sub",)}),
            ("no-type", {"omit_claims": ("type",)}),
            ("wrong-type", {"token_type": "refresh"}),
            ("non-numeric-sub", {"extra_claims": {"sub": "not-a-number"}}),
        ],
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_tokens_with_missing_or_wrong_claims_are_rejected(
        self, http: HttpClient, customer, label: str, kwargs: dict[str, Any]
    ) -> None:
        """Absent must never mean valid-by-default."""
        http.get("/auth/me", token=mint_token(user_id=customer.id, **kwargs)).assert_status(401)

    def test_a_tampered_payload_breaks_the_signature(self, http: HttpClient, customer) -> None:
        header, payload, signature = customer.token.split(".")
        replacement = "A" if payload[8] != "A" else "B"
        tampered = f"{header}.{payload[:8]}{replacement}{payload[9:]}.{signature}"
        http.get("/auth/me", token=tampered).assert_status(401)


# ---------------------------------------------------------------------------
@allure.story("Object-level access (IDOR)")
class TestObjectLevelAccess:
    @allure.severity(allure.severity_level.BLOCKER)
    def test_a_customer_cannot_read_another_customers_order(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
        second_customer,
        http: HttpClient,
    ) -> None:
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)
        mine = order_client.checkout(address_id=address_id).assert_status(201).body

        response = http.get(f"/orders/{mine['id']}", token=second_customer.token)

        response.assert_error("ORDER_NOT_FOUND", 404)
        assert mine["order_number"] not in response.raw_text, "The response leaked the order number"

    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
    def test_a_customer_cannot_touch_another_customers_address(
        self, address_client: AddressClient, second_customer, http: HttpClient, method: str
    ) -> None:
        created = address_client.create(
            {
                "label": "Private",
                "full_name": "Ada Lovelace",
                "line1": "12 Secret Way",
                "city": "Cambridge",
                "state": "MA",
                "postal_code": "02139",
                "country": "US",
            }
        )
        created.assert_status(201)
        address_id = created.body["id"]

        body = {"city": "Hijacked"} if method == "PATCH" else None
        response = http.request(
            method, f"/addresses/{address_id}", json_body=body, token=second_customer.token
        )

        response.assert_error("ADDRESS_NOT_FOUND", 404)
        assert "12 Secret Way" not in response.raw_text
        # And the address is genuinely unchanged.
        assert address_client.get(address_id).body["city"] == "Cambridge"

    def test_a_customer_cannot_cancel_another_customers_order(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
        second_customer,
        http: HttpClient,
    ) -> None:
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)
        mine = order_client.checkout(address_id=address_id).assert_status(201).body

        http.post(
            f"/orders/{mine['id']}/cancel",
            json_body={"reason": "not yours"},
            token=second_customer.token,
        ).assert_error("ORDER_NOT_FOUND", 404)

        assert order_client.get(mine["id"]).body["status"] == "confirmed"

    def test_an_unknown_id_and_a_forbidden_id_are_indistinguishable(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
        second_customer,
        http: HttpClient,
    ) -> None:
        """Enumeration defence.

        If "someone else's order" and "no such order" gave different responses,
        an attacker could walk the id space and learn exactly how many orders
        the shop has taken and which ids are real.
        """
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)
        mine = order_client.checkout(address_id=address_id).assert_status(201).body

        forbidden = http.get(f"/orders/{mine['id']}", token=second_customer.token)
        nonexistent = http.get("/orders/99999999", token=second_customer.token)

        assert forbidden.status_code == nonexistent.status_code == 404
        assert forbidden.body["error"] == nonexistent.body["error"]
        assert forbidden.body["message"] == nonexistent.body["message"]

    def test_one_customers_cart_is_invisible_to_another(
        self, cart_client: CartClient, product_factory, second_customer, http: HttpClient
    ) -> None:
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 3).assert_status(201)

        theirs = http.get("/cart", token=second_customer.token)
        theirs.assert_status(200)
        assert theirs.body["items"] == [], "One customer can see another's cart"

    def test_order_history_never_includes_another_customers_orders(
        self,
        order_client: OrderClient,
        cart_client: CartClient,
        product_factory,
        customer_with_address,
        second_customer,
        http: HttpClient,
    ) -> None:
        _, address_id = customer_with_address
        product = product_factory(stock_quantity=5)
        cart_client.add_item(product["id"], 1).assert_status(201)
        mine = order_client.checkout(address_id=address_id).assert_status(201).body

        history = http.get("/orders", params={"page_size": 100}, token=second_customer.token)
        history.assert_status(200)
        assert all(row["id"] != mine["id"] for row in history.body["items"])


# ---------------------------------------------------------------------------
@allure.story("Account state")
class TestAccountState:
    @allure.severity(allure.severity_level.CRITICAL)
    def test_deactivating_an_account_takes_effect_immediately(
        self, admin_client: AdminClient, customer, http: HttpClient
    ) -> None:
        """Revocation without a token denylist.

        The user record is re-read on every authenticated request, so a
        deactivated account stops working on the very next call rather than
        whenever its token happens to expire. That costs one indexed lookup and
        is what makes stateless tokens acceptable here.
        """
        http.get("/auth/me", token=customer.token).assert_status(200)

        admin_client.set_user_active(customer.id, False).assert_status(200)
        try:
            blocked = http.get("/auth/me", token=customer.token)
            blocked.assert_error("ACCOUNT_INACTIVE", 403)
            http.get("/cart", token=customer.token).assert_status(403)
        finally:
            # Restored so the account cannot affect anything else, even if the
            # assertions above failed.
            admin_client.set_user_active(customer.id, True)

        http.get("/auth/me", token=customer.token).assert_status(200)

    def test_a_deactivated_account_cannot_log_in(
        self, admin_client: AdminClient, auth_client, customer
    ) -> None:
        admin_client.set_user_active(customer.id, False).assert_status(200)
        try:
            auth_client.login(customer.email, customer.password).assert_error(
                "ACCOUNT_INACTIVE", 403
            )
        finally:
            admin_client.set_user_active(customer.id, True)

    def test_an_admin_cannot_deactivate_themselves(
        self, admin_client: AdminClient, admin_user
    ) -> None:
        """Guards against locking the last administrator out of the system."""
        admin_client.set_user_active(admin_user.id, False).assert_error("PERMISSION_DENIED", 403)
        # Still an admin, still working.
        admin_client.users().assert_status(200)
