"""Unit tests for inventory validation.

Overselling is the defect the inventory rules exist to prevent, and these tests
cover the layer that can be checked without any I/O: the pure validation
functions. The row-locking and CHECK-constraint layers are exercised by the
database and integration suites, because those genuinely need a database.
"""

from __future__ import annotations

import allure
import pytest

from app.core.errors import InsufficientInventoryError, InvalidQuantityError
from app.services.inventory import (
    StockRequest,
    validate_all,
    validate_availability,
    validate_quantity,
)

pytestmark = [pytest.mark.unit, allure.epic("Commerce"), allure.feature("Inventory")]

MAX_QUANTITY = 99


def request_for(requested: int, available: int, product_id: int = 1) -> StockRequest:
    return StockRequest(
        product_id=product_id,
        product_name="Aurora 14 Ultrabook",
        requested=requested,
        available=available,
    )


@allure.story("Quantity sanity")
class TestValidateQuantity:
    @pytest.mark.parametrize("quantity", [1, 2, 50, 99])
    def test_accepts_quantities_within_the_limit(self, quantity: int) -> None:
        validate_quantity(quantity, maximum=MAX_QUANTITY)

    @pytest.mark.parametrize("quantity", [0, -1, -99, -1_000_000])
    def test_rejects_zero_and_negative(self, quantity: int) -> None:
        with pytest.raises(InvalidQuantityError) as exc:
            validate_quantity(quantity, maximum=MAX_QUANTITY)
        assert exc.value.error_code == "INVALID_QUANTITY"

    @pytest.mark.parametrize("quantity", [100, 1_000, 1_000_000])
    def test_rejects_quantities_above_the_limit(self, quantity: int) -> None:
        with pytest.raises(InvalidQuantityError) as exc:
            validate_quantity(quantity, maximum=MAX_QUANTITY)
        assert exc.value.details["maximum"] == MAX_QUANTITY

    @pytest.mark.parametrize("quantity", ["2", 2.5, None, [2], {"quantity": 2}])
    def test_rejects_non_integers(self, quantity: object) -> None:
        with pytest.raises(InvalidQuantityError):
            validate_quantity(quantity, maximum=MAX_QUANTITY)  # type: ignore[arg-type]

    def test_rejects_booleans(self) -> None:
        """``True`` is an ``int`` in Python and would otherwise pass as 1.

        A JSON body of {"quantity": true} must be a validation error, not a
        silent purchase of one unit.
        """
        with pytest.raises(InvalidQuantityError):
            validate_quantity(True, maximum=MAX_QUANTITY)  # type: ignore[arg-type]


@allure.story("Availability")
class TestValidateAvailability:
    @pytest.mark.parametrize(
        ("requested", "available"),
        [(1, 1), (1, 100), (5, 5), (3, 10), (99, 99)],
    )
    def test_accepts_requests_within_stock(self, requested: int, available: int) -> None:
        validate_availability(request_for(requested, available))

    @pytest.mark.parametrize(
        ("requested", "available"),
        [(2, 1), (6, 5), (100, 99), (1_000, 1)],
    )
    def test_rejects_requests_above_stock(self, requested: int, available: int) -> None:
        with pytest.raises(InsufficientInventoryError) as exc:
            validate_availability(request_for(requested, available))
        assert exc.value.error_code == "INSUFFICIENT_INVENTORY"
        assert exc.value.details["requested"] == requested
        assert exc.value.details["available"] == available

    def test_exactly_all_remaining_stock_is_allowed(self) -> None:
        """Buying the last unit must succeed.

        An off-by-one here would leave stock permanently unsellable.
        """
        validate_availability(request_for(1, 1))

    def test_out_of_stock_says_so(self) -> None:
        with pytest.raises(InsufficientInventoryError) as exc:
            validate_availability(request_for(1, 0))
        assert "out of stock" in exc.value.message.lower()
        assert exc.value.details["available"] == 0

    def test_message_names_the_product_and_the_number_available(self) -> None:
        """The customer must be told what to do, not merely that it failed."""
        with pytest.raises(InsufficientInventoryError) as exc:
            validate_availability(request_for(5, 2))
        assert "Aurora 14 Ultrabook" in exc.value.message
        assert "2" in exc.value.message

    def test_singular_and_plural_are_both_correct(self) -> None:
        with pytest.raises(InsufficientInventoryError) as exc:
            validate_availability(request_for(3, 1))
        assert "1 unit of" in exc.value.message

        with pytest.raises(InsufficientInventoryError) as exc:
            validate_availability(request_for(5, 2))
        assert "2 units of" in exc.value.message

    def test_zero_or_negative_requested_is_a_quantity_error(self) -> None:
        """Distinguishes 'you asked for a nonsense amount' from 'we are short'."""
        with pytest.raises(InvalidQuantityError):
            validate_availability(request_for(0, 10))
        with pytest.raises(InvalidQuantityError):
            validate_availability(request_for(-1, 10))


@allure.story("Whole-basket validation")
class TestValidateAll:
    def test_a_satisfiable_basket_passes(self) -> None:
        validate_all([request_for(1, 5, 1), request_for(2, 10, 2), request_for(3, 3, 3)])

    def test_one_short_line_fails_the_basket(self) -> None:
        with pytest.raises(InsufficientInventoryError) as exc:
            validate_all([request_for(1, 5, 1), request_for(99, 2, 2)])
        assert exc.value.details["product_id"] == 2

    def test_the_reported_failure_is_deterministic(self) -> None:
        """Two short lines must always report the same one.

        Requests are checked in product-id order, so the error a customer sees
        does not depend on dict iteration order. A message that varies between
        runs makes for a test that fails intermittently on its assertion.
        """
        requests = [request_for(10, 1, 7), request_for(10, 1, 3), request_for(10, 1, 5)]

        for _ in range(5):
            with pytest.raises(InsufficientInventoryError) as exc:
                validate_all(requests)
            assert exc.value.details["product_id"] == 3

    def test_an_empty_basket_is_vacuously_valid(self) -> None:
        validate_all([])


@allure.story("StockRequest")
class TestStockRequest:
    @pytest.mark.parametrize(
        ("requested", "available", "expected"),
        [(1, 1, True), (1, 0, False), (0, 5, False), (-1, 5, False), (5, 4, False), (4, 5, True)],
    )
    def test_is_satisfiable(self, requested: int, available: int, expected: bool) -> None:
        assert request_for(requested, available).is_satisfiable is expected
