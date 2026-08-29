"""Stock validation and adjustment.

Overselling is the defect this module exists to prevent, and it is defended in
three independent layers:

1. **Validation** - :func:`validate_availability` rejects a request that asks
   for more than is on the shelf. Pure, no I/O, exhaustively unit tested.
2. **Row locking** - :func:`lock_inventory_rows` takes ``SELECT ... FOR UPDATE``
   on every line before any stock is decremented, so two concurrent checkouts
   for the last unit serialise instead of both reading "1 available".
3. **A database CHECK constraint** - ``quantity >= 0`` on the inventory table.
   If layers 1 and 2 were ever bypassed, PostgreSQL still refuses.

Layer 3 is the one that matters most: it holds even against a bug in this file.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import InsufficientInventoryError, InvalidQuantityError
from app.models.catalog import Inventory


@dataclass(frozen=True, slots=True)
class StockRequest:
    """A requested quantity of one product, with the stock actually available."""

    product_id: int
    product_name: str
    requested: int
    available: int

    @property
    def is_satisfiable(self) -> bool:
        return 0 < self.requested <= self.available


def validate_quantity(quantity: int, *, maximum: int) -> None:
    """Reject quantities that are not sane, independent of stock levels.

    Zero, negatives and absurd values are rejected here rather than being left
    to fail later with a confusing message. ``bool`` is excluded explicitly
    because ``True`` is an ``int`` in Python and would otherwise pass as 1.
    """
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidQuantityError("Quantity must be a whole number.")
    if quantity <= 0:
        raise InvalidQuantityError("Quantity must be at least 1.", details={"requested": quantity})
    if quantity > maximum:
        raise InvalidQuantityError(
            f"Quantity must not exceed {maximum} units per item.",
            details={"requested": quantity, "maximum": maximum},
        )


def validate_availability(request: StockRequest) -> None:
    """Raise :class:`InsufficientInventoryError` when stock cannot cover the request."""
    if request.requested <= 0:
        raise InvalidQuantityError(details={"requested": request.requested})
    if request.available <= 0:
        raise InsufficientInventoryError(
            f"'{request.product_name}' is out of stock.",
            details={
                "product_id": request.product_id,
                "requested": request.requested,
                "available": 0,
            },
        )
    if request.requested > request.available:
        unit = "unit" if request.available == 1 else "units"
        raise InsufficientInventoryError(
            f"Only {request.available} {unit} of '{request.product_name}' are available.",
            details={
                "product_id": request.product_id,
                "requested": request.requested,
                "available": request.available,
            },
        )


def validate_all(requests: list[StockRequest]) -> None:
    """Validate a whole basket, reporting the first shortfall.

    Requests are checked in product-id order so the error a customer sees is
    deterministic rather than dependent on dict iteration order - flaky error
    messages make for flaky tests.
    """
    for request in sorted(requests, key=lambda r: r.product_id):
        validate_availability(request)


def lock_inventory_rows(db: Session, product_ids: list[int]) -> dict[int, Inventory]:
    """Take row locks on the given products' inventory, ordered by product id.

    Two details here are load-bearing, and both were found by a concurrency test
    rather than by reading the code.

    **Ordering.** Two transactions locking the same two rows in opposite orders
    deadlock. Acquiring them in a globally consistent order means the second
    transaction simply waits.

    **``populate_existing``.** Without it, this function acquires the lock
    correctly and still returns stale data. Checkout has already loaded these
    rows once (``price_cart`` joins inventory to price the basket), so they sit
    in the session's identity map. ``SELECT ... FOR UPDATE`` then blocks until
    the competing transaction commits - exactly as intended - but SQLAlchemy
    hands back the *cached* object rather than the freshly-read row, because by
    default it will not overwrite attributes already loaded in the session.

    The result was a textbook lost update: with one unit in stock, two
    simultaneous checkouts each read ``quantity == 1`` from their own cache,
    each passed validation, each wrote ``quantity = 0``, and both customers were
    charged for the same unit. ``populate_existing=True`` forces the attributes
    to be refreshed from the locked row, so the second transaction sees the
    zero the first one wrote and is correctly refused.
    """
    if not product_ids:
        return {}

    ordered_ids = sorted(set(product_ids))
    rows = (
        db.execute(
            select(Inventory)
            .where(Inventory.product_id.in_(ordered_ids))
            .order_by(Inventory.product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    return {row.product_id: row for row in rows}


def decrement(inventory: Inventory, quantity: int, *, product_name: str) -> None:
    """Reduce stock, re-checking availability against the freshly locked row.

    The caller has already validated availability, but that read may predate the
    lock. Re-checking here is what closes the race window.
    """
    if quantity > inventory.quantity:
        raise InsufficientInventoryError(
            f"Only {inventory.quantity} available for '{product_name}'.",
            details={
                "product_id": inventory.product_id,
                "requested": quantity,
                "available": inventory.quantity,
            },
        )
    inventory.quantity -= quantity


def increment(inventory: Inventory, quantity: int) -> None:
    """Return stock to the shelf (payment failure, cancellation)."""
    if quantity < 0:
        raise InvalidQuantityError("Cannot restore a negative quantity.")
    inventory.quantity += quantity
