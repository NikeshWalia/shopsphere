"""User and session fixtures.

The central isolation guarantee lives here: **`customer` creates a brand-new
account for every test that asks for one.** Registration is a public endpoint,
so this costs one request and buys complete independence - a test can empty its
cart, change its password or place ten orders without any possibility of
affecting another test, in this run or in a parallel worker.

The seeded admin *is* shared, because it is treated as read-only infrastructure:
tests use it to create products and read dashboards, never to mutate the admin
account itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from tests.api.clients import AuthClient
from tests.configuration.settings import settings
from tests.test_data.factories import DEFAULT_PASSWORD, UserSpec, user_spec
from tests.utilities.http import HttpClient


@dataclass(frozen=True)
class TestUser:
    """An account created for one test, with everything needed to act as it."""

    # Its name begins with "Test", so pytest would otherwise try to collect it
    # as a test class and warn that it cannot (it has a constructor).
    __test__ = False

    id: int
    email: str
    password: str
    full_name: str
    token: str
    role: str = "customer"

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _register(auth: AuthClient, spec: UserSpec) -> TestUser:
    response = auth.register(
        email=spec.email,
        password=spec.password,
        full_name=spec.full_name,
        phone=spec.phone,
    )
    assert response.status_code == 201, (
        f"Fixture could not register {spec.email}: "
        f"HTTP {response.status_code} {response.raw_text[:300]}"
    )
    body = response.body
    return TestUser(
        id=int(body["user"]["id"]),
        email=spec.email,
        password=spec.password,
        full_name=spec.full_name,
        token=str(body["access_token"]),
        role=str(body["user"]["role"]),
    )


@pytest.fixture
def customer(auth_client: AuthClient) -> TestUser:
    """A freshly registered customer, unique to this test."""
    return _register(auth_client, user_spec())


@pytest.fixture
def second_customer(auth_client: AuthClient) -> TestUser:
    """A second, unrelated customer.

    The other half of every authorisation test: proving that customer A cannot
    reach customer B's orders needs a real, separate B.
    """
    return _register(auth_client, user_spec())


@pytest.fixture
def make_customer(auth_client: AuthClient) -> Callable[..., TestUser]:
    """Factory for tests needing several users, or specific attributes."""

    def _make(**overrides: object) -> TestUser:
        return _register(auth_client, user_spec(**overrides))

    return _make


@pytest.fixture(scope="session")
def admin_token(auth_client: AuthClient) -> str:
    """Session token for the seeded administrator.

    Shared deliberately: obtaining it costs a bcrypt verification, and tests
    treat the admin account as read-only infrastructure. Anything that would
    mutate the admin account itself must create its own admin instead.
    """
    return auth_client.token_for(settings.admin_email, settings.admin_password)


@pytest.fixture(scope="session")
def admin_user(admin_token: str, auth_client: AuthClient) -> TestUser:
    response = auth_client.me(token=admin_token)
    assert (
        response.status_code == 200
    ), f"Could not read the admin profile: {response.raw_text[:200]}"
    body = response.body
    return TestUser(
        id=int(body["id"]),
        email=body["email"],
        password=settings.admin_password,
        full_name=body["full_name"],
        token=admin_token,
        role=body["role"],
    )


@pytest.fixture
def customer_with_address(customer: TestUser, address_client) -> tuple[TestUser, int]:
    """A customer who already has a shipping address.

    Checkout needs one, and creating it inline in every checkout test would be
    noise that obscures what each test is actually about.
    """
    address_id = address_client.create_and_get_id()
    return customer, address_id


@pytest.fixture
def anonymous_customer_http() -> Iterator[HttpClient]:
    """An explicitly unauthenticated client, for 401 assertions."""
    client = HttpClient(settings.api_url, token=None)
    yield client
    client.close()


@pytest.fixture
def default_password() -> str:
    return DEFAULT_PASSWORD
