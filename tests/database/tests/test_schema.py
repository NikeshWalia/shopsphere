"""The database schema is a contract, so it gets tested like one.

Every assertion here defends something the application silently relies on:

* A **missing table** means a migration was written but never run against this
  environment - the API would fail at the first query.
* A **missing index** does not break correctness, so no functional test will
  ever catch it. It breaks the shop three months later, when the catalogue is
  large enough that a sequential scan on ``products`` times out under load.
* A **missing CHECK or UNIQUE constraint** turns "the service layer prevents
  this" into "the service layer prevents this *unless* there is a bug, a race,
  or a manual UPDATE". Constraints are the last line of defence that no
  application code path can bypass.
* A **wrong delete rule** silently destroys data: ``ON DELETE CASCADE`` from
  ``order_items`` to ``products`` would erase order history the moment somebody
  deleted a discontinued product.

The data-integrity section asserts properties that must hold at every instant,
not just after a particular workflow. They run against whatever state the
database happens to be in, so a violation left behind by *any* code path -
tested or not - is caught here.
"""

from __future__ import annotations

import allure
import pytest

from tests.configuration.settings import settings
from tests.database.queries.queries import DatabaseQueries

pytestmark = [pytest.mark.database, allure.epic("Database")]

# The domain model, as tables. Listed explicitly rather than derived from the
# ORM metadata: deriving it would make the test agree with the code by
# construction and prove nothing about the database that is actually deployed.
EXPECTED_TABLES: tuple[str, ...] = (
    "roles",
    "users",
    "addresses",
    "categories",
    "products",
    "inventory",
    "carts",
    "cart_items",
    "orders",
    "order_items",
    "payments",
    "promotions",
)


def _normalise(definition: str) -> str:
    """Reduce PostgreSQL's rendering of a constraint to its meaning.

    ``pg_get_constraintdef`` prints ``CHECK ((price >= (0)::numeric))``. The
    casts and defensive parentheses are noise; matching on them would make the
    assertion depend on a formatting detail rather than on the rule enforced.
    """
    return (
        definition.lower()
        .replace("::numeric", "")
        .replace("(", "")
        .replace(")", "")
        .replace(" ", "")
    )


def _index_definitions(db: DatabaseQueries, table: str) -> list[str]:
    return [row["definition"] for row in db.index_definitions(table)]


# (table, fragment of the index definition that must appear)
INDEX_CASES: tuple[tuple[str, str], ...] = (
    ("products", "btree (category_id)"),
    ("products", "btree (brand)"),
    ("products", "btree (price)"),
    ("products", "btree (rating)"),
    ("products", "btree (created_at)"),
    ("products", "btree (lower("),
    ("orders", "btree (user_id, created_at)"),
    ("orders", "btree (status)"),
    ("orders", "btree (payment_status)"),
    ("order_items", "btree (order_id)"),
    ("payments", "btree (order_id)"),
)

# (table, normalised predicate that must be enforced)
CHECK_CASES: tuple[tuple[str, str], ...] = (
    ("inventory", "quantity>=0"),
    ("cart_items", "quantity>0"),
    ("order_items", "quantity>0"),
    ("products", "price>=0"),
    ("products", "rating>=0andrating<=5"),
    ("orders", "subtotal>=0"),
    ("orders", "discount_total>=0"),
    ("orders", "total>=0"),
)

# (table, exact definition PostgreSQL renders for the constraint)
UNIQUE_CASES: tuple[tuple[str, str], ...] = (
    ("users", "UNIQUE (email)"),
    ("products", "UNIQUE (sku)"),
    ("categories", "UNIQUE (slug)"),
    ("carts", "UNIQUE (user_id)"),
    ("cart_items", "UNIQUE (cart_id, product_id)"),
    ("orders", "UNIQUE (order_number)"),
    ("orders", "UNIQUE (user_id, idempotency_key)"),
)

# (table, column, referenced table, delete rule)
FOREIGN_KEY_CASES: tuple[tuple[str, str, str, str], ...] = (
    # History must survive catalogue changes: deleting a product has to be
    # refused while any order still references it.
    ("order_items", "product_id", "products", "RESTRICT"),
    ("orders", "user_id", "users", "RESTRICT"),
    ("products", "category_id", "categories", "RESTRICT"),
    ("users", "role_id", "roles", "RESTRICT"),
    # Transient, owned data goes with its owner.
    ("cart_items", "cart_id", "carts", "CASCADE"),
    ("cart_items", "product_id", "products", "CASCADE"),
    ("carts", "user_id", "users", "CASCADE"),
    ("addresses", "user_id", "users", "CASCADE"),
    ("inventory", "product_id", "products", "CASCADE"),
    ("order_items", "order_id", "orders", "CASCADE"),
    ("payments", "order_id", "orders", "CASCADE"),
    # The order carries its own address snapshot, so losing the address-book
    # entry must not take the order with it.
    ("orders", "shipping_address_id", "addresses", "SET NULL"),
)


@allure.feature("Schema")
@allure.story("Tables and migrations")
class TestTablesAndMigrations:
    @allure.severity(allure.severity_level.BLOCKER)
    @pytest.mark.parametrize("table", EXPECTED_TABLES, ids=EXPECTED_TABLES)
    def test_expected_table_exists(self, db: DatabaseQueries, table: str) -> None:
        """A missing table means this environment runs an older schema.

        Every request touching it would return a 500, so this is the first
        thing worth knowing when a freshly deployed environment misbehaves.
        """
        present = db.table_names()
        assert table in present, f"Table {table!r} is missing. Present: {present}"

    @allure.severity(allure.severity_level.BLOCKER)
    def test_alembic_version_table_exists_and_is_populated(self, db: DatabaseQueries) -> None:
        """An empty ``alembic_version`` means the tables were not created by a
        migration - so the next ``alembic upgrade`` would try to create them
        again and fail, and nobody could tell which schema is deployed.
        """
        assert "alembic_version" in db.table_names(), "alembic_version table is missing"
        version = db.alembic_version()
        assert version, "alembic_version is empty: migrations have never been stamped here"

    @allure.severity(allure.severity_level.NORMAL)
    def test_schema_holds_no_unexpected_extra_tables(self, db: DatabaseQueries) -> None:
        """A stray table is usually a hand-made backup or a half-reverted
        migration. Either way the deployed schema no longer matches source
        control, and the next migration is likely to fail on it.
        """
        allowed = {*EXPECTED_TABLES, "alembic_version"}
        extra = sorted(set(db.table_names()) - allowed)
        assert not extra, f"Unexpected tables present: {extra}"


@allure.feature("Schema")
@allure.story("Indexes")
class TestIndexes:
    """Index coverage is asserted on the *definition*, never the index name.

    A test checking only for a name would pass against an index renamed to
    match while covering entirely different columns.
    """

    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.parametrize(
        ("table", "fragment"),
        INDEX_CASES,
        ids=[f"{table}-{fragment}" for table, fragment in INDEX_CASES],
    )
    def test_query_path_is_indexed(self, db: DatabaseQueries, table: str, fragment: str) -> None:
        """These are the columns the API filters, sorts and joins on.

        Without the index the query still returns the right answer, which is
        exactly why no functional test catches its loss - the failure arrives
        much later, as a timeout under production data volumes.
        """
        definitions = _index_definitions(db, table)
        assert any(
            fragment in definition for definition in definitions
        ), f"No index on {table} covers {fragment!r}. Present:\n" + "\n".join(definitions)

    @allure.severity(allure.severity_level.NORMAL)
    def test_case_insensitive_name_search_is_indexed(self, db: DatabaseQueries) -> None:
        """Product search lower-cases the name, so a plain index on ``name``
        could never be used for it. The expression index is the only thing
        keeping catalogue search off a full table scan.
        """
        definitions = _index_definitions(db, "products")
        assert any("lower((name)" in definition for definition in definitions), (
            "products has no lower(name) expression index; search will scan the table.\n"
            + "\n".join(definitions)
        )


@allure.feature("Schema")
@allure.story("Constraints")
class TestCheckConstraints:
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        ("table", "predicate"),
        CHECK_CASES,
        ids=[f"{table}-{predicate}" for table, predicate in CHECK_CASES],
    )
    def test_check_constraint_is_enforced(
        self, db: DatabaseQueries, table: str, predicate: str
    ) -> None:
        """The service layer already refuses these values - but only on the
        paths it controls. A concurrency bug, a future endpoint or a manual
        ``UPDATE`` can each produce negative stock; the CHECK cannot be
        bypassed by any of them.
        """
        definitions = [_normalise(row["definition"]) for row in db.check_constraints(table)]
        assert any(
            predicate in definition for definition in definitions
        ), f"{table} does not enforce {predicate!r}. Present: {definitions}"


@allure.feature("Schema")
@allure.story("Constraints")
class TestUniqueConstraints:
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        ("table", "definition"),
        UNIQUE_CASES,
        ids=[f"{table}-{definition}" for table, definition in UNIQUE_CASES],
    )
    def test_unique_constraint_exists(
        self, db: DatabaseQueries, table: str, definition: str
    ) -> None:
        """Each of these prevents a duplication the application cannot prevent
        alone under concurrency: two signups for one email, two carts for one
        user, two lines for one product in a cart, or - most expensively - two
        orders charged for one Idempotency-Key when a customer double-clicks
        Pay.
        """
        present = [row["definition"] for row in db.unique_constraints(table)]
        assert definition in present, f"{table} is missing {definition}. Present: {present}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_idempotency_key_is_unique_per_user_not_globally(self, db: DatabaseQueries) -> None:
        """Scoping matters as much as uniqueness.

        A globally unique key would let one customer's key collide with
        another's, and the replay path would then hand them somebody else's
        order. Scoping to ``user_id`` makes that impossible.
        """
        present = [row["definition"] for row in db.unique_constraints("orders")]
        assert "UNIQUE (user_id, idempotency_key)" in present
        assert (
            "UNIQUE (idempotency_key)" not in present
        ), "idempotency_key is globally unique; keys must be scoped per user"


@allure.feature("Schema")
@allure.story("Referential integrity")
class TestForeignKeys:
    @allure.severity(allure.severity_level.CRITICAL)
    @pytest.mark.parametrize(
        ("table", "column", "references", "delete_rule"),
        FOREIGN_KEY_CASES,
        ids=[
            f"{table}.{column}-to-{references}-{rule}"
            for table, column, references, rule in FOREIGN_KEY_CASES
        ],
    )
    def test_foreign_key_and_delete_rule(
        self,
        db: DatabaseQueries,
        table: str,
        column: str,
        references: str,
        delete_rule: str,
    ) -> None:
        """Delete rules decide what happens to data nobody is looking at.

        Getting one wrong stays invisible until the day a product is deleted
        and years of order history vanish with it - by which point the backup
        window has usually closed.
        """
        matches = [
            row
            for row in db.foreign_keys()
            if row["table_name"] == table
            and row["column_name"] == column
            and row["references_table"] == references
        ]
        assert matches, f"No foreign key {table}.{column} -> {references}"
        assert matches[0]["delete_rule"] == delete_rule, (
            f"{table}.{column} -> {references} has delete rule "
            f"{matches[0]['delete_rule']!r}, expected {delete_rule!r}"
        )


@allure.feature("Schema")
@allure.story("Data integrity invariants")
class TestDataIntegrityInvariants:
    """Properties that must hold at every instant, whatever ran before.

    Deliberately global rather than scoped to rows this test created: the point
    is to catch a violation produced by *any* code path, including ones nobody
    wrote a test for.
    """

    @allure.severity(allure.severity_level.BLOCKER)
    def test_no_inventory_row_is_negative(self, db: DatabaseQueries) -> None:
        """Negative stock means the shop has sold units it does not have, and
        every downstream availability check becomes a lie.
        """
        rows = db.negative_stock_rows()
        assert rows == [], f"Inventory rows below zero: {rows}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_every_product_has_an_inventory_row(self, db: DatabaseQueries) -> None:
        """Stock is read through a join. A product with no inventory row reads
        as permanently unavailable - listed in the catalogue, impossible to
        buy. That is lost revenue nobody gets an alert about.
        """
        rows = db.products_without_inventory()
        assert rows == [], f"Products with no inventory row: {rows}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_no_order_items_are_orphaned(self, db: DatabaseQueries) -> None:
        """An order line pointing at a deleted order is money that appears in
        revenue aggregates but belongs to no customer.
        """
        rows = db.orphaned_order_items()
        assert rows == [], f"Orphaned order_items: {rows}"

    @allure.severity(allure.severity_level.BLOCKER)
    def test_every_paid_order_has_a_successful_payment(self, db: DatabaseQueries) -> None:
        """An order marked paid with no successful payment row means goods ship
        against money that was never captured - the most expensive
        inconsistency this schema can hold.
        """
        rows = db.paid_orders_without_payment()
        assert rows == [], f"Paid orders with no successful payment row: {rows}"

    @allure.severity(allure.severity_level.BLOCKER)
    def test_no_payment_row_stores_more_than_four_card_digits(self, db: DatabaseQueries) -> None:
        """Storing a full PAN is a PCI-DSS violation and turns any future
        database leak into a card-fraud incident. Four digits is the maximum
        that may ever be persisted.
        """
        rows = db.payment_card_numbers_stored()
        assert rows == [], f"Payment rows holding more than four card digits: {rows}"


@allure.feature("Schema")
@allure.story("Seed data")
class TestSeededData:
    """The demo dataset the rest of the suite assumes exists.

    When these fail, dozens of unrelated tests fail too. Having one test that
    says "the database was never seeded" turns a confusing mass failure into a
    one-line diagnosis.
    """

    @allure.severity(allure.severity_level.CRITICAL)
    def test_catalogue_is_seeded_with_enough_volume(self, db: DatabaseQueries) -> None:
        """Search, filtering, sorting and pagination tests all need a catalogue
        with real volume; against a handful of rows they would pass vacuously.
        """
        total = db.product_count()
        assert total >= 60, f"Only {total} products seeded; the catalogue suites need at least 60"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_catalogue_contains_an_inactive_product(self, db: DatabaseQueries) -> None:
        """Visibility rules are only testable when something is hidden. If every
        seeded product were active, the "customers cannot see deactivated
        products" tests would pass without exercising anything.
        """
        total = db.product_count()
        active = db.product_count(active_only=True)
        assert active < total, "Every seeded product is active; nothing exercises the hidden path"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_all_seven_categories_are_seeded(self, db: DatabaseQueries) -> None:
        """Category filters are asserted against a known taxonomy; a missing
        category silently shrinks the space those tests cover.
        """
        count = db.category_count()
        assert count == 7, f"Expected 7 categories, found {count}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_both_roles_exist(self, db: DatabaseQueries) -> None:
        """Registration assigns the ``customer`` role by lookup. A missing role
        row makes every signup fail with a foreign-key error.
        """
        names = db.role_names()
        assert "admin" in names and "customer" in names, f"Roles present: {names}"

    @allure.severity(allure.severity_level.CRITICAL)
    def test_seeded_administrator_exists_and_is_active(self, db: DatabaseQueries) -> None:
        """Every admin-side test authenticates as this account. If it is missing
        or deactivated, the whole admin suite reports authentication failures
        instead of the real cause.
        """
        admin = db.user_by_email(settings.admin_email)
        assert admin is not None, f"No user {settings.admin_email!r} in the database"
        assert admin["role"] == "admin", f"Seeded admin has role {admin['role']!r}"
        assert admin["is_active"] is True, "The seeded administrator is deactivated"

    @allure.severity(allure.severity_level.NORMAL)
    @pytest.mark.parametrize(
        "code",
        ("WELCOME10", "SAVE15", "BIGSPENDER", "EXPIRED20", "INACTIVE5"),
        ids=("percentage-capped", "fixed-with-minimum", "big-spender", "expired", "inactive"),
    )
    def test_seeded_promotion_exists(self, db: DatabaseQueries, code: str) -> None:
        """Promotion tests need a valid, an expired and an inactive code to
        exist. Without all three, the negative cases quietly stop testing
        anything and every promo code looks acceptable.
        """
        assert db.promotion(code) is not None, f"Promotion {code} is not seeded"
