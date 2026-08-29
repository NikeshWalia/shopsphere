"""ShopSphere load model.

Three user classes model the traffic an online shop actually receives, in
roughly the proportions a real shop sees:

    AnonymousBrowser  60%  window shoppers who never log in
    ShopperUser       35%  signed-in customers, a minority of whom buy
    AdminUser          5%  staff refreshing dashboards

The point of the weighting is that the *shape* of the load matters more than
its volume. A run made entirely of checkouts would exercise the payment path
and the row locks on `products.stock_quantity` while proving nothing about the
catalogue queries that serve the overwhelming majority of real requests - and
those queries are what fall over first when an index goes missing.

Two conventions make the statistics table usable:

* Every request passes an explicit ``name=``. Without it, ``/products/17`` and
  ``/products/42`` become separate rows and the table degenerates into one line
  per product id, hiding the percentile that actually matters.
* Tasks that can legitimately be rejected by a business rule use
  ``catch_response`` and decide for themselves what counts as a failure. A 409
  because the seeded catalogue ran out of stock is the shop working correctly;
  counting it as an error would make the failure-rate metric meaningless.

Everything is configured from the environment so the same file runs against a
local stack, Docker Compose or a deployed environment without edits. See the
README in this directory for the variables and for how to run it.
"""

from __future__ import annotations

import logging
import os
import random
import uuid
from typing import Any

from locust import HttpUser, between, constant_pacing, task
from locust.exception import StopUser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration - environment first, working local defaults second
# ---------------------------------------------------------------------------
# PERF_* wins, then the variables the rest of the test platform already sets
# (so a developer with a working .env needs no extra setup), then a default.
API_HOST: str = os.getenv("PERF_HOST") or os.getenv("API_BASE_URL") or "http://127.0.0.1:8000"
API_PREFIX: str = os.getenv("PERF_API_PREFIX", "/api/v1")

ADMIN_EMAIL: str = (
    os.getenv("PERF_ADMIN_EMAIL") or os.getenv("TEST_ADMIN_EMAIL") or "admin@shopsphere.test"
)
ADMIN_PASSWORD: str = (
    os.getenv("PERF_ADMIN_PASSWORD") or os.getenv("TEST_ADMIN_PASSWORD") or "AdminPass123!"
)

# Load users register themselves rather than sharing a seeded account: a shared
# account would serialise on the same cart row and measure lock contention that
# no real shop experiences.
USER_PASSWORD: str = os.getenv("PERF_USER_PASSWORD", "LoadTest123!")
EMAIL_DOMAIN: str = os.getenv("PERF_EMAIL_DOMAIN", "shopsphere.test")

# The mock provider's always-approved card. A load run must not spend its time
# measuring the decline path - that is what the functional suite is for.
CARD_NUMBER: str = os.getenv("PERF_CARD_NUMBER", "4111111111111111")
PROMO_CODE: str = os.getenv("PERF_PROMO_CODE", "WELCOME10")

# How many catalogue rows a user keeps in mind when picking something to view.
CATALOGUE_SAMPLE_SIZE: int = int(os.getenv("PERF_CATALOGUE_SAMPLE", "50"))

SEARCH_TERMS: tuple[str, ...] = (
    "laptop",
    "wireless",
    "headphones",
    "phone",
    "desk",
    "jacket",
    "coffee",
    "monitor",
    "backpack",
    "keyboard",
)
SORT_OPTIONS: tuple[str, ...] = ("relevance", "price_asc", "price_desc", "rating_desc", "newest")

# ---------------------------------------------------------------------------
# Request names - the grouping keys in the statistics table
# ---------------------------------------------------------------------------
NAME_PRODUCT_LIST = "GET /products [list]"
NAME_PRODUCT_SEARCH = "GET /products [search]"
NAME_PRODUCT_FILTER = "GET /products [filtered]"
NAME_PRODUCT_DETAIL = "GET /products/[id]"
NAME_CATEGORIES = "GET /categories"
NAME_CATEGORY_DETAIL = "GET /categories/[slug]"
NAME_REGISTER = "POST /auth/register"
NAME_LOGIN = "POST /auth/login"
NAME_ME = "GET /auth/me"
NAME_ADDRESS_CREATE = "POST /addresses"
NAME_CART_GET = "GET /cart"
NAME_CART_ADD = "POST /cart/items"
NAME_QUOTE = "POST /checkout/quote"
NAME_CHECKOUT = "POST /orders [checkout]"
NAME_ORDER_LIST = "GET /orders"
NAME_ADMIN_STATS = "GET /admin/stats"
NAME_ADMIN_ORDERS = "GET /admin/orders"
NAME_ADMIN_ORDER_DETAIL = "GET /admin/orders/[id]"
NAME_ADMIN_INVENTORY = "GET /admin/inventory"
NAME_ADMIN_USERS = "GET /admin/users"

# Business rules that legitimately reject a checkout under sustained load.
# The seeded catalogue holds a finite number of units; once a hot product sells
# out, the correct response *is* 409 INSUFFICIENT_INVENTORY. Treating that as a
# failure would mean the failure-rate metric measures how long the run has been
# going rather than whether the API is healthy.
EXPECTED_CHECKOUT_REJECTIONS: frozenset[str] = frozenset(
    {"INSUFFICIENT_INVENTORY", "EMPTY_CART", "PRODUCT_UNAVAILABLE"}
)
EXPECTED_CART_REJECTIONS: frozenset[str] = frozenset(
    {"INSUFFICIENT_INVENTORY", "PRODUCT_UNAVAILABLE", "PRODUCT_NOT_FOUND"}
)


def _unique_email() -> str:
    return f"perf_{uuid.uuid4().hex[:14]}@{EMAIL_DOMAIN}"


def _error_code(response: Any) -> str:
    """Pull the API's machine-readable error code out of a response.

    Never raises: a load test that crashed while inspecting an error body would
    lose the very measurement it was taking.
    """
    try:
        body = response.json()
    except Exception:  # A malformed body is the API's problem; never ours to crash on.
        return ""
    return str(body.get("error", "")) if isinstance(body, dict) else ""


class CatalogueMixin:
    """Read-only catalogue traffic, shared by anonymous and signed-in users.

    Both classes browse identically - the only difference is whether an
    Authorization header rides along - so the tasks live in one place.
    """

    client: Any
    product_ids: list[int]
    category_slugs: list[str]

    def _headers(self) -> dict[str, str]:
        """Auth headers, if this user has a session. Anonymous users send none."""
        token = getattr(self, "token", None)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def refresh_catalogue(self) -> None:
        """Cache a page of in-stock ids so detail views hit products that exist.

        Picking ids at random from a range would generate a stream of 404s and
        measure the error path instead of the read path.
        """
        response = self.client.get(
            f"{API_PREFIX}/products",
            params={"in_stock": "true", "page_size": CATALOGUE_SAMPLE_SIZE, "sort": "newest"},
            headers=self._headers(),
            name=NAME_PRODUCT_LIST,
        )
        if response.status_code != 200:
            return
        items = response.json().get("items", [])
        self.product_ids = [int(item["id"]) for item in items]

    def refresh_categories(self) -> None:
        response = self.client.get(
            f"{API_PREFIX}/categories", headers=self._headers(), name=NAME_CATEGORIES
        )
        if response.status_code != 200:
            return
        self.category_slugs = [str(row["slug"]) for row in response.json()]

    def _random_product_id(self) -> int | None:
        if not self.product_ids:
            self.refresh_catalogue()
        return random.choice(self.product_ids) if self.product_ids else None

    # -- Tasks -------------------------------------------------------------
    def do_browse_catalogue(self) -> None:
        """Page through the catalogue - the single heaviest read in any shop."""
        self.client.get(
            f"{API_PREFIX}/products",
            params={
                "page": random.randint(1, 4),
                "page_size": random.choice([12, 20, 24]),
                "sort": random.choice(SORT_OPTIONS),
            },
            headers=self._headers(),
            name=NAME_PRODUCT_LIST,
        )

    def do_search(self) -> None:
        """Full-text search: the query most likely to lose its index."""
        self.client.get(
            f"{API_PREFIX}/products",
            params={"q": random.choice(SEARCH_TERMS), "page_size": 20},
            headers=self._headers(),
            name=NAME_PRODUCT_SEARCH,
        )

    def do_filtered_browse(self) -> None:
        """Faceted browse - price band plus category, the classic slow query."""
        low = random.choice([0, 25, 50, 100, 250])
        self.client.get(
            f"{API_PREFIX}/products",
            params={
                "min_price": low,
                "max_price": low + random.choice([50, 200, 750]),
                "min_rating": random.choice([0, 3, 4]),
                "in_stock": "true",
                "sort": random.choice(SORT_OPTIONS),
            },
            headers=self._headers(),
            name=NAME_PRODUCT_FILTER,
        )

    def do_view_product(self) -> None:
        """Product detail. Grouped under one name so ids do not explode the table."""
        product_id = self._random_product_id()
        if product_id is None:
            return
        self.client.get(
            f"{API_PREFIX}/products/{product_id}",
            headers=self._headers(),
            name=NAME_PRODUCT_DETAIL,
        )

    def do_view_categories(self) -> None:
        """Category nav. The list carries per-category counts, so it aggregates."""
        if not self.category_slugs:
            self.refresh_categories()
            return
        if random.random() < 0.5:
            self.refresh_categories()
            return
        self.client.get(
            f"{API_PREFIX}/categories/{random.choice(self.category_slugs)}",
            headers=self._headers(),
            name=NAME_CATEGORY_DETAIL,
        )


class AnonymousBrowser(CatalogueMixin, HttpUser):
    """The majority of real traffic: arrives, looks, leaves, never logs in.

    Deliberately the heaviest class. Anonymous reads are cacheable and cheap
    per request, but they arrive in the greatest volume, so they set the floor
    on database connection-pool pressure that every other request competes with.
    """

    weight = 12
    host = API_HOST
    # Human reading pace between page views.
    wait_time = between(1, 5)

    def on_start(self) -> None:
        self.product_ids = []
        self.category_slugs = []
        self.refresh_catalogue()
        self.refresh_categories()

    @task(30)
    def browse_catalogue(self) -> None:
        self.do_browse_catalogue()

    @task(18)
    def view_product(self) -> None:
        self.do_view_product()

    @task(10)
    def search(self) -> None:
        self.do_search()

    @task(6)
    def filtered_browse(self) -> None:
        self.do_filtered_browse()

    @task(5)
    def view_categories(self) -> None:
        self.do_view_categories()

    @task(1)
    def health_probe(self) -> None:
        """Stands in for the load balancer's health check, which never stops."""
        self.client.get("/health", name="GET /health")


class ShopperUser(CatalogueMixin, HttpUser):
    """A signed-in customer.

    Registers its own account on ``on_start`` so runs are repeatable and
    concurrent users never collide on the same cart, and creates one shipping
    address up front - exactly what a returning customer would already have.

    The task weights encode the funnel: for every checkout there are roughly
    thirty catalogue page views. That ratio is the whole point. If it were
    flatter, the run would report a p95 dominated by the payment call and hide
    a catalogue regression completely.
    """

    weight = 7
    host = API_HOST
    wait_time = between(1, 4)

    def on_start(self) -> None:
        self.token: str | None = None
        self.email: str = ""
        self.address_id: int | None = None
        self.cart_size: int = 0
        self.product_ids = []
        self.category_slugs = []

        self._register()
        if self.token:
            self._create_address()
        self.refresh_catalogue()
        self.refresh_categories()

    # -- Session -----------------------------------------------------------
    def _register(self) -> None:
        """Create this virtual user's own account.

        A failure here is logged and tolerated rather than fatal: under a
        deliberate overload the registration endpoint is expected to shed load,
        and killing every shopper at that moment would silently turn the run
        into a browse-only test just when the interesting data starts.
        """
        email = _unique_email()
        response = self.client.post(
            f"{API_PREFIX}/auth/register",
            json={
                "email": email,
                "password": USER_PASSWORD,
                "password_confirm": USER_PASSWORD,
                "full_name": "Load Test Shopper",
            },
            name=NAME_REGISTER,
        )
        if response.status_code == 201:
            self.email = email
            self.token = str(response.json()["access_token"])
        else:
            logger.warning(
                "Shopper registration failed with HTTP %s; continuing as a browser",
                response.status_code,
            )

    def _create_address(self) -> None:
        response = self.client.post(
            f"{API_PREFIX}/addresses",
            json={
                "label": "Home",
                "full_name": "Load Test Shopper",
                "line1": f"{random.randint(1, 999)} Throughput Avenue",
                "city": "Austin",
                "state": "TX",
                "postal_code": "73301",
                "country": "US",
                "phone": "+1-555-0123",
                "is_default": True,
            },
            headers=self._headers(),
            name=NAME_ADDRESS_CREATE,
        )
        if response.status_code == 201:
            self.address_id = int(response.json()["id"])

    # -- Browsing (the bulk of a signed-in session too) ---------------------
    @task(28)
    def browse_catalogue(self) -> None:
        self.do_browse_catalogue()

    @task(16)
    def view_product(self) -> None:
        self.do_view_product()

    @task(10)
    def search(self) -> None:
        self.do_search()

    @task(6)
    def filtered_browse(self) -> None:
        self.do_filtered_browse()

    @task(5)
    def view_categories(self) -> None:
        self.do_view_categories()

    # -- Account -----------------------------------------------------------
    @task(3)
    def log_in(self) -> None:
        """Re-authenticate: a returning visitor, or a client refreshing a token.

        Login is bcrypt-bound rather than database-bound, so it is the one
        endpoint whose latency barely moves with load and whose *throughput*
        cost is entirely CPU. Keeping it in the mix stops the run from
        flattering a machine that has spare I/O but no spare cores.
        """
        if not self.token:
            self._register()
            return
        response = self.client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": self.email, "password": USER_PASSWORD},
            name=NAME_LOGIN,
        )
        if response.status_code == 200:
            self.token = str(response.json()["access_token"])

    @task(2)
    def view_profile(self) -> None:
        if not self.token:
            return
        self.client.get(f"{API_PREFIX}/auth/me", headers=self._headers(), name=NAME_ME)

    # -- Cart --------------------------------------------------------------
    @task(8)
    def view_cart(self) -> None:
        if not self.token:
            return
        self.client.get(f"{API_PREFIX}/cart", headers=self._headers(), name=NAME_CART_GET)

    @task(6)
    def add_to_cart(self) -> None:
        """Add a line. Out-of-stock rejections are business outcomes, not errors.

        Same reasoning as checkout below: the seeded catalogue is finite, so a
        409 here says the shop refused to oversell, which is the behaviour the
        functional suite asserts on. Only unexpected statuses are failures.
        """
        if not self.token:
            return
        product_id = self._random_product_id()
        if product_id is None:
            return
        with self.client.post(
            f"{API_PREFIX}/cart/items",
            json={"product_id": product_id, "quantity": random.randint(1, 2)},
            headers=self._headers(),
            name=NAME_CART_ADD,
            catch_response=True,
        ) as response:
            if response.status_code in (200, 201):
                self.cart_size += 1
                response.success()
            elif _error_code(response) in EXPECTED_CART_REJECTIONS:
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")

    @task(3)
    def quote_cart(self) -> None:
        """The totals preview the checkout page renders before payment."""
        if not self.token or self.cart_size == 0:
            return
        with self.client.post(
            f"{API_PREFIX}/checkout/quote",
            json={"promo_code": PROMO_CODE if random.random() < 0.3 else None},
            headers=self._headers(),
            name=NAME_QUOTE,
            catch_response=True,
        ) as response:
            # The cart can empty underneath us if a concurrent checkout won the
            # last unit; an empty-cart quote is correct, not a server fault.
            if response.status_code == 200 or _error_code(response) == "EMPTY_CART":
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")

    # -- Buying ------------------------------------------------------------
    @task(2)
    def view_orders(self) -> None:
        if not self.token:
            return
        self.client.get(
            f"{API_PREFIX}/orders",
            params={"page": 1, "page_size": 10},
            headers=self._headers(),
            name=NAME_ORDER_LIST,
        )

    @task(1)
    def checkout(self) -> None:
        """The lightest task, and the most expensive request.

        Checkout writes an order, decrements stock under a row lock and makes an
        outbound call to the payment provider, so it is where saturation shows
        up first - but only a small fraction of sessions ever reach it, and the
        load profile has to reflect that or the numbers are fiction.

        ``catch_response`` is essential here. Under sustained load the seeded
        catalogue genuinely runs out of units, and the API answers 409
        INSUFFICIENT_INVENTORY. That is the inventory rule working. Letting
        Locust score it as a failure would make the failure-rate column climb
        steadily during a healthy run and hide a real 5xx when one appears.

        A 402 is *not* forgiven: this user always pays with the mock provider's
        approved card, so a decline means something is genuinely wrong with the
        payment integration and should show up red.
        """
        if not self.token or self.address_id is None:
            return
        if self.cart_size == 0:
            self.add_to_cart()
            if self.cart_size == 0:
                return

        with self.client.post(
            f"{API_PREFIX}/orders",
            json={
                "address_id": self.address_id,
                "payment": {
                    "card_number": CARD_NUMBER,
                    "card_holder": "Load Test Shopper",
                    "expiry_month": 12,
                    "expiry_year": 2032,
                    "cvv": "123",
                },
                "promo_code": PROMO_CODE if random.random() < 0.25 else None,
            },
            headers={**self._headers(), "Idempotency-Key": uuid.uuid4().hex},
            name=NAME_CHECKOUT,
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                self.cart_size = 0
                response.success()
            elif _error_code(response) in EXPECTED_CHECKOUT_REJECTIONS:
                # Sold out or raced to an empty cart - the shop behaving correctly.
                self.cart_size = 0
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")


class AdminUser(HttpUser):
    """Back-office staff with a dashboard open.

    Few in number but disproportionately expensive: the stats endpoint
    aggregates across orders and payments, and the inventory view scans the
    whole product table. A handful of these running alongside customer traffic
    is exactly the contention that turns a healthy p95 into a bad p99, which is
    why they belong in the model even at 5% of users.
    """

    weight = 1
    host = API_HOST
    # A dashboard on a polling refresh, not a human clicking.
    wait_time = constant_pacing(10)

    def on_start(self) -> None:
        self.token: str | None = None
        self.order_ids: list[int] = []
        response = self.client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            name=NAME_LOGIN,
        )
        if response.status_code != 200:
            # Without a token this user would only generate 401s and pollute the
            # error rate, so remove it from the run and say why exactly once.
            logger.error(
                "Admin login failed with HTTP %s for %s; AdminUser will not run",
                response.status_code,
                ADMIN_EMAIL,
            )
            raise StopUser
        self.token = str(response.json()["access_token"])

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @task(5)
    def dashboard_stats(self) -> None:
        """The aggregate query behind the landing dashboard."""
        self.client.get(f"{API_PREFIX}/admin/stats", headers=self._headers(), name=NAME_ADMIN_STATS)

    @task(3)
    def recent_orders(self) -> None:
        response = self.client.get(
            f"{API_PREFIX}/admin/orders",
            params={"page": 1, "page_size": 20},
            headers=self._headers(),
            name=NAME_ADMIN_ORDERS,
        )
        if response.status_code == 200:
            self.order_ids = [int(row["id"]) for row in response.json().get("items", [])]

    @task(2)
    def order_detail(self) -> None:
        if not self.order_ids:
            return
        self.client.get(
            f"{API_PREFIX}/admin/orders/{random.choice(self.order_ids)}",
            headers=self._headers(),
            name=NAME_ADMIN_ORDER_DETAIL,
        )

    @task(2)
    def low_stock_report(self) -> None:
        """Full-table inventory scan - the query most sensitive to catalogue size."""
        self.client.get(
            f"{API_PREFIX}/admin/inventory",
            params={"low_stock_threshold": 5, "page": 1, "page_size": 25},
            headers=self._headers(),
            name=NAME_ADMIN_INVENTORY,
        )

    @task(1)
    def user_directory(self) -> None:
        self.client.get(
            f"{API_PREFIX}/admin/users",
            params={"page": 1, "page_size": 20},
            headers=self._headers(),
            name=NAME_ADMIN_USERS,
        )
