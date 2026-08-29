"""Direct database access for verification.

Two rules this layer exists to enforce:

1. **No raw SQL in test files.** Tests call ``db.order_by_number(...)``; the SQL
   lives here. When a column is renamed, one file changes rather than twenty.
2. **No credentials in source.** The DSN comes from ``TEST_DATABASE_URL``.

Why assert against the database at all when there is an API? Because some
invariants are invisible over HTTP. That inventory was decremented by exactly
the ordered quantity, that a payment row was written with the right failure
code, that cancelling an order restored stock - these are properties of the
persisted state, and testing only what the API chooses to return would miss a
whole class of defect.

The connection is opened in autocommit mode and used read-only. Tests observe
state; they never manufacture it behind the application's back, because data
created by a back door would not have gone through the rules being tested.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from tests.configuration.settings import settings


class DatabaseQueries:
    """Read-only query helpers used by the database and integration suites."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or settings.psycopg_dsn
        self._connection: psycopg.Connection | None = None

    # -- Connection lifecycle ---------------------------------------------
    def connect(self) -> psycopg.Connection:
        if self._connection is None or self._connection.closed:
            self._connection = psycopg.connect(self._dsn, autocommit=True, row_factory=dict_row)
        return self._connection

    def close(self) -> None:
        if self._connection is not None and not self._connection.closed:
            self._connection.close()
        self._connection = None

    def __enter__(self) -> DatabaseQueries:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def cursor(self):
        with self.connect().cursor() as cur:
            yield cur

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.fetch_one(sql, params)
        return next(iter(row.values())) if row else None

    # -- Users -------------------------------------------------------------
    def user_by_email(self, email: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """
            SELECT u.id, u.email, u.full_name, u.phone, u.is_active,
                   u.password_hash, u.created_at, r.name AS role
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.email = %s
            """,
            (email.lower(),),
        )

    def user_count(self) -> int:
        return int(self.scalar("SELECT COUNT(*) AS n FROM users") or 0)

    def role_names(self) -> list[str]:
        return [row["name"] for row in self.fetch_all("SELECT name FROM roles ORDER BY name")]

    # -- Catalogue ---------------------------------------------------------
    def product_by_id(self, product_id: int) -> dict[str, Any] | None:
        return self.fetch_one(
            """
            SELECT p.id, p.sku, p.name, p.price, p.brand, p.rating, p.is_active,
                   p.category_id, c.slug AS category_slug, i.quantity AS stock
            FROM products p
            JOIN categories c ON c.id = p.category_id
            LEFT JOIN inventory i ON i.product_id = p.id
            WHERE p.id = %s
            """,
            (product_id,),
        )

    def product_by_sku(self, sku: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT id, sku, name, price, is_active FROM products WHERE upper(sku) = upper(%s)",
            (sku,),
        )

    def stock_for(self, product_id: int) -> int:
        value = self.scalar("SELECT quantity FROM inventory WHERE product_id = %s", (product_id,))
        return int(value) if value is not None else 0

    def product_count(self, *, active_only: bool = False) -> int:
        sql = "SELECT COUNT(*) AS n FROM products"
        if active_only:
            sql += " WHERE is_active = true"
        return int(self.scalar(sql) or 0)

    def products_without_inventory(self) -> list[dict[str, Any]]:
        """Referential-integrity check: every product must have a stock row."""
        return self.fetch_all(
            """
            SELECT p.id, p.sku FROM products p
            LEFT JOIN inventory i ON i.product_id = p.id
            WHERE i.id IS NULL
            """
        )

    def negative_stock_rows(self) -> list[dict[str, Any]]:
        """Must always be empty - the CHECK constraint makes it impossible."""
        return self.fetch_all("SELECT product_id, quantity FROM inventory WHERE quantity < 0")

    # -- Cart --------------------------------------------------------------
    def cart_for_user(self, user_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT id, user_id FROM carts WHERE user_id = %s", (user_id,))

    def cart_items_for_user(self, user_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT ci.product_id, ci.quantity, p.sku, p.price
            FROM cart_items ci
            JOIN carts c ON c.id = ci.cart_id
            JOIN products p ON p.id = ci.product_id
            WHERE c.user_id = %s
            ORDER BY ci.id
            """,
            (user_id,),
        )

    # -- Orders ------------------------------------------------------------
    def order_by_id(self, order_id: int) -> dict[str, Any] | None:
        return self.fetch_one(
            """
            SELECT id, order_number, user_id, status, payment_status,
                   subtotal, discount_total, tax, shipping_fee, total, currency,
                   promo_code, idempotency_key, cancelled_reason,
                   shipping_full_name, shipping_line1, shipping_city,
                   shipping_state, shipping_postal_code, shipping_country,
                   created_at, updated_at
            FROM orders WHERE id = %s
            """,
            (order_id,),
        )

    def order_by_number(self, order_number: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT id, order_number, user_id, status, payment_status, total FROM orders WHERE order_number = %s",
            (order_number,),
        )

    def order_items(self, order_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT id, product_id, product_name, sku, unit_price, quantity, line_total
            FROM order_items WHERE order_id = %s ORDER BY id
            """,
            (order_id,),
        )

    def orders_for_user(self, user_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT id, order_number, status, payment_status, total, created_at
            FROM orders WHERE user_id = %s ORDER BY id
            """,
            (user_id,),
        )

    def order_count_for_user(self, user_id: int) -> int:
        return int(
            self.scalar("SELECT COUNT(*) AS n FROM orders WHERE user_id = %s", (user_id,)) or 0
        )

    def orders_with_idempotency_key(self, key: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT id, order_number, user_id FROM orders WHERE idempotency_key = %s", (key,)
        )

    # -- Payments ----------------------------------------------------------
    def payments_for_order(self, order_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT id, order_id, provider_reference, amount, currency, status,
                   method, card_last4, card_brand, failure_code, failure_message,
                   attempt, created_at
            FROM payments WHERE order_id = %s ORDER BY attempt, id
            """,
            (order_id,),
        )

    def latest_payment(self, order_id: int) -> dict[str, Any] | None:
        payments = self.payments_for_order(order_id)
        return payments[-1] if payments else None

    def payment_card_numbers_stored(self) -> list[dict[str, Any]]:
        """Sanity check that only the last four digits are ever persisted.

        A 16-digit value in card_last4 would mean a full PAN reached the
        database, which is the single worst thing this application could do.
        """
        return self.fetch_all("SELECT id, card_last4 FROM payments WHERE length(card_last4) > 4")

    # -- Promotions --------------------------------------------------------
    def promotion(self, code: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """
            SELECT id, code, discount_type, value, min_subtotal, max_discount,
                   is_active, valid_from, valid_to, usage_limit, times_used
            FROM promotions WHERE upper(code) = upper(%s)
            """,
            (code,),
        )

    # -- Addresses ---------------------------------------------------------
    def addresses_for_user(self, user_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT id, label, full_name, city, is_default FROM addresses WHERE user_id = %s ORDER BY id",
            (user_id,),
        )

    # -- Schema introspection ---------------------------------------------
    def table_names(self) -> list[str]:
        return [
            row["table_name"]
            for row in self.fetch_all(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
        ]

    def indexes_on(self, table: str) -> list[str]:
        return [
            row["indexname"]
            for row in self.fetch_all(
                "SELECT indexname FROM pg_indexes WHERE tablename = %s ORDER BY indexname",
                (table,),
            )
        ]

    def constraints_on(self, table: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT conname AS name, contype AS type
            FROM pg_constraint
            WHERE conrelid = %s::regclass
            ORDER BY conname
            """,
            (table,),
        )

    def foreign_keys(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT tc.table_name, kcu.column_name, ccu.table_name AS references_table,
                   rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
            JOIN information_schema.referential_constraints rc
              ON rc.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
            ORDER BY tc.table_name, kcu.column_name
            """
        )

    def alembic_version(self) -> str | None:
        value = self.scalar("SELECT version_num FROM alembic_version")
        return str(value) if value is not None else None

    # -- Aggregates used by assertions ------------------------------------
    def sum_of_order_items(self, order_id: int) -> Decimal:
        value = self.scalar(
            "SELECT COALESCE(SUM(line_total), 0) AS total FROM order_items WHERE order_id = %s",
            (order_id,),
        )
        return Decimal(str(value or "0"))

    def orphaned_order_items(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT oi.id FROM order_items oi
            LEFT JOIN orders o ON o.id = oi.order_id
            WHERE o.id IS NULL
            """
        )

    def paid_orders_without_payment(self) -> list[dict[str, Any]]:
        """A paid order with no successful payment row would be a serious bug."""
        return self.fetch_all(
            """
            SELECT o.id, o.order_number FROM orders o
            WHERE o.payment_status = 'paid'
              AND NOT EXISTS (
                SELECT 1 FROM payments p WHERE p.order_id = o.id AND p.status = 'paid'
              )
            """
        )

    # -- Schema introspection: definitions, not just names ------------------
    #
    # Asserting on a *name* only proves somebody created an object with that
    # name. These return PostgreSQL's own rendering of the object, so a test can
    # assert on the columns an index actually covers and the predicate a CHECK
    # actually enforces - which is what the application depends on.
    def index_definitions(self, table: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT indexname AS name, indexdef AS definition
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = %s
            ORDER BY indexname
            """,
            (table,),
        )

    def check_constraints(self, table: str) -> list[dict[str, Any]]:
        return self._constraint_definitions(table, "c")

    def unique_constraints(self, table: str) -> list[dict[str, Any]]:
        return self._constraint_definitions(table, "u")

    def _constraint_definitions(self, table: str, contype: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT conname AS name, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = %s::regclass AND contype = %s
            ORDER BY conname
            """,
            (table, contype),
        )

    def column_names(self, table: str) -> list[str]:
        return [
            row["column_name"]
            for row in self.fetch_all(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
        ]

    def category_count(self) -> int:
        return int(self.scalar("SELECT COUNT(*) AS n FROM categories") or 0)

    # -- Secret-leak scanning ---------------------------------------------
    def users_containing_text(self, needle: str) -> list[dict[str, Any]]:
        """Users whose row contains this literal text in *any* column.

        Casting the whole row to text searches every column at once, which is
        what turns "the plaintext password is stored nowhere" into a real
        assertion rather than a spot-check of the one column we thought of.
        """
        return self.fetch_all(
            "SELECT id, email FROM users WHERE users::text LIKE %s", (f"%{needle}%",)
        )
