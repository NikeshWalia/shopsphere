"""Unit tests for money arithmetic.

The base of the testing pyramid. These run in milliseconds with no database, no
HTTP and no browser, and they cover the rules that are most expensive to get
wrong: an error here is money.

Every case states an exact expected value rather than re-deriving it from the
same formula the code uses - a test that recomputes the implementation is a
tautology that passes even when the rule itself is wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import allure
import pytest

from app.models.enums import PromotionType
from app.schemas.common import quantize_money
from app.services.pricing import (
    OrderTotals,
    PricedLine,
    PromotionRule,
    compute_discount,
    compute_shipping,
    compute_subtotal,
    compute_tax,
    compute_totals,
    promotion_error,
)

pytestmark = [pytest.mark.unit, allure.epic("Commerce"), allure.feature("Pricing")]

TAX_RATE = Decimal("0.08")
SHIPPING_FEE = Decimal("9.99")
FREE_SHIPPING_AT = Decimal("100.00")


def line(price: str, quantity: int = 1, product_id: int = 1) -> PricedLine:
    return PricedLine(
        product_id=product_id,
        sku=f"SKU-{product_id}",
        name=f"Product {product_id}",
        unit_price=Decimal(price),
        quantity=quantity,
    )


def totals_for(lines: list[PricedLine], promotion: PromotionRule | None = None) -> OrderTotals:
    return compute_totals(
        lines,
        tax_rate=TAX_RATE,
        shipping_flat_fee=SHIPPING_FEE,
        free_shipping_threshold=FREE_SHIPPING_AT,
        promotion=promotion,
    )


# ---------------------------------------------------------------------------
@allure.story("Rounding")
class TestQuantizeMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("10", "10.00"),
            ("10.1", "10.10"),
            ("10.005", "10.01"),
            ("10.004", "10.00"),
            # Python's default is banker's rounding, which would give 2.67 and
            # 0.12. Retail arithmetic expects half-up, so these two cases are
            # the whole reason quantize_money exists.
            ("2.675", "2.68"),
            ("0.125", "0.13"),
            ("0.1", "0.10"),
            ("0", "0.00"),
            ("999999.999", "1000000.00"),
        ],
    )
    def test_rounds_half_up_to_two_places(self, value: str, expected: str) -> None:
        assert quantize_money(value) == Decimal(expected)

    def test_accepts_int_and_float_inputs(self) -> None:
        assert quantize_money(10) == Decimal("10.00")
        assert quantize_money(10.5) == Decimal("10.50")


# ---------------------------------------------------------------------------
@allure.story("Line totals and subtotal")
class TestSubtotal:
    @pytest.mark.parametrize(
        ("price", "quantity", "expected"),
        [
            ("19.99", 1, "19.99"),
            ("19.99", 3, "59.97"),
            ("0.01", 7, "0.07"),
            ("1249.00", 2, "2498.00"),
            ("33.33", 3, "99.99"),
            ("0.00", 5, "0.00"),
        ],
    )
    def test_line_total_is_price_times_quantity(
        self, price: str, quantity: int, expected: str
    ) -> None:
        assert line(price, quantity).line_total == Decimal(expected)

    def test_subtotal_sums_every_line(self) -> None:
        lines = [line("10.00", 2, 1), line("5.50", 3, 2), line("0.99", 1, 3)]
        # 20.00 + 16.50 + 0.99
        assert compute_subtotal(lines) == Decimal("37.49")

    def test_empty_basket_has_zero_subtotal(self) -> None:
        assert compute_subtotal([]) == Decimal("0.00")

    def test_rounds_per_line_not_only_at_the_end(self) -> None:
        """Each line is rounded as it is produced.

        Rounding once at the end would let sub-cent error accumulate and produce
        a subtotal that does not equal the sum of the line totals shown to the
        customer - a discrepancy that is very hard to explain on an invoice.
        """
        lines = [line("0.335", 1, i) for i in range(1, 4)]
        assert all(item.line_total == Decimal("0.34") for item in lines)
        assert compute_subtotal(lines) == Decimal("1.02")


# ---------------------------------------------------------------------------
@allure.story("Tax")
class TestTax:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("100.00", "8.00"),
            ("50.00", "4.00"),
            ("19.99", "1.60"),  # 1.5992 -> 1.60
            ("0.10", "0.01"),  # 0.008  -> 0.01
            ("0.06", "0.00"),  # 0.0048 -> 0.00
            ("1249.00", "99.92"),
        ],
    )
    def test_tax_is_the_rate_applied_to_the_amount(self, amount: str, expected: str) -> None:
        assert compute_tax(Decimal(amount), TAX_RATE) == Decimal(expected)

    def test_zero_and_negative_amounts_are_not_taxed(self) -> None:
        assert compute_tax(Decimal("0.00"), TAX_RATE) == Decimal("0.00")
        assert compute_tax(Decimal("-10.00"), TAX_RATE) == Decimal("0.00")

    def test_zero_rate_produces_no_tax(self) -> None:
        assert compute_tax(Decimal("100.00"), Decimal("0")) == Decimal("0.00")


# ---------------------------------------------------------------------------
@allure.story("Shipping")
class TestShipping:
    @pytest.mark.parametrize(
        ("subtotal", "expected"),
        [
            ("0.00", "0.00"),  # nothing to ship
            ("0.01", "9.99"),
            ("50.00", "9.99"),
            ("99.99", "9.99"),
            ("100.00", "0.00"),  # exactly at the threshold ships free
            ("100.01", "0.00"),
            ("5000.00", "0.00"),
        ],
    )
    def test_free_at_or_above_the_threshold(self, subtotal: str, expected: str) -> None:
        result = compute_shipping(
            Decimal(subtotal), flat_fee=SHIPPING_FEE, free_threshold=FREE_SHIPPING_AT
        )
        assert result == Decimal(expected)

    def test_threshold_is_measured_after_discount(self) -> None:
        """A discount that drops the basket below the threshold reinstates shipping.

        Charging against the pre-discount subtotal would give away free shipping
        on an order the customer did not actually qualify for.
        """
        lines = [line("110.00", 1)]
        promotion = PromotionRule(
            code="TWENTY", discount_type=PromotionType.FIXED, value=Decimal("20.00")
        )
        totals = totals_for(lines, promotion)
        assert totals.subtotal == Decimal("110.00")
        assert totals.discount_total == Decimal("20.00")
        assert totals.shipping_fee == Decimal("9.99")


# ---------------------------------------------------------------------------
@allure.story("Discounts")
class TestDiscount:
    def test_no_promotion_means_no_discount(self) -> None:
        assert compute_discount(Decimal("100.00"), None) == Decimal("0.00")

    @pytest.mark.parametrize(
        ("subtotal", "percent", "expected"),
        [
            ("100.00", "10", "10.00"),
            ("199.99", "10", "20.00"),  # 19.999 -> 20.00
            ("33.33", "15", "5.00"),  # 4.9995 -> 5.00
            ("100.00", "100", "100.00"),
            ("100.00", "0", "0.00"),
        ],
    )
    def test_percentage_discount(self, subtotal: str, percent: str, expected: str) -> None:
        rule = PromotionRule(
            code="PCT", discount_type=PromotionType.PERCENTAGE, value=Decimal(percent)
        )
        assert compute_discount(Decimal(subtotal), rule) == Decimal(expected)

    def test_fixed_discount(self) -> None:
        rule = PromotionRule(code="FIX", discount_type=PromotionType.FIXED, value=Decimal("15.00"))
        assert compute_discount(Decimal("100.00"), rule) == Decimal("15.00")

    def test_fixed_discount_is_capped_at_the_subtotal(self) -> None:
        """A $20-off code on a $5 basket discounts $5, never $20.

        Without the clamp the total would go negative, which downstream means
        refunding a customer for shopping.
        """
        rule = PromotionRule(code="FIX", discount_type=PromotionType.FIXED, value=Decimal("20.00"))
        assert compute_discount(Decimal("5.00"), rule) == Decimal("5.00")

    def test_percentage_discount_respects_its_cap(self) -> None:
        rule = PromotionRule(
            code="BIG",
            discount_type=PromotionType.PERCENTAGE,
            value=Decimal("20"),
            max_discount=Decimal("50.00"),
        )
        # 20% of 1000 is 200, capped at 50.
        assert compute_discount(Decimal("1000.00"), rule) == Decimal("50.00")
        # 20% of 100 is 20, below the cap, so the cap does not bite.
        assert compute_discount(Decimal("100.00"), rule) == Decimal("20.00")

    def test_discount_is_never_negative(self) -> None:
        rule = PromotionRule(code="NEG", discount_type=PromotionType.FIXED, value=Decimal("-10.00"))
        assert compute_discount(Decimal("100.00"), rule) == Decimal("0.00")

    def test_total_never_goes_below_zero(self) -> None:
        lines = [line("10.00", 1)]
        rule = PromotionRule(
            code="HUGE", discount_type=PromotionType.FIXED, value=Decimal("999.00")
        )
        totals = totals_for(lines, rule)
        assert totals.discount_total == Decimal("10.00")
        assert totals.total == Decimal("0.00")


# ---------------------------------------------------------------------------
@allure.story("Promotion validity")
class TestPromotionValidity:
    def test_a_valid_promotion_reports_no_error(self) -> None:
        rule = PromotionRule(code="OK", discount_type=PromotionType.FIXED, value=Decimal("5.00"))
        assert promotion_error(rule, Decimal("100.00")) is None

    def test_inactive_promotion_is_rejected(self) -> None:
        rule = PromotionRule(
            code="OFF", discount_type=PromotionType.FIXED, value=Decimal("5.00"), is_active=False
        )
        reason = promotion_error(rule, Decimal("100.00"))
        assert reason is not None and "no longer active" in reason

    def test_expired_promotion_is_rejected(self) -> None:
        rule = PromotionRule(
            code="OLD",
            discount_type=PromotionType.FIXED,
            value=Decimal("5.00"),
            valid_to=datetime.now(UTC) - timedelta(days=1),
        )
        reason = promotion_error(rule, Decimal("100.00"))
        assert reason is not None and "expired" in reason

    def test_future_promotion_is_rejected(self) -> None:
        rule = PromotionRule(
            code="SOON",
            discount_type=PromotionType.FIXED,
            value=Decimal("5.00"),
            valid_from=datetime.now(UTC) + timedelta(days=1),
        )
        reason = promotion_error(rule, Decimal("100.00"))
        assert reason is not None and "not valid yet" in reason

    def test_minimum_subtotal_is_enforced(self) -> None:
        rule = PromotionRule(
            code="MIN",
            discount_type=PromotionType.FIXED,
            value=Decimal("15.00"),
            min_subtotal=Decimal("150.00"),
        )
        assert promotion_error(rule, Decimal("149.99")) is not None
        # Exactly at the minimum must qualify - an off-by-one here is a support ticket.
        assert promotion_error(rule, Decimal("150.00")) is None

    def test_usage_limit_is_enforced(self) -> None:
        rule = PromotionRule(
            code="LIMIT",
            discount_type=PromotionType.FIXED,
            value=Decimal("5.00"),
            usage_limit=10,
            times_used=10,
        )
        reason = promotion_error(rule, Decimal("100.00"))
        assert reason is not None and "usage limit" in reason

    def test_promotion_below_its_usage_limit_is_accepted(self) -> None:
        rule = PromotionRule(
            code="LIMIT",
            discount_type=PromotionType.FIXED,
            value=Decimal("5.00"),
            usage_limit=10,
            times_used=9,
        )
        assert promotion_error(rule, Decimal("100.00")) is None

    def test_rejection_reason_names_the_code(self) -> None:
        """The message must be actionable, not just 'invalid'."""
        rule = PromotionRule(
            code="SUMMER24",
            discount_type=PromotionType.FIXED,
            value=Decimal("5.00"),
            min_subtotal=Decimal("200.00"),
        )
        reason = promotion_error(rule, Decimal("10.00"))
        assert reason is not None
        assert "SUMMER24" in reason
        assert "200.00" in reason


# ---------------------------------------------------------------------------
@allure.story("Complete order totals")
class TestComputeTotals:
    def test_empty_basket_is_entirely_zero(self) -> None:
        totals = totals_for([])
        assert totals.subtotal == Decimal("0.00")
        assert totals.tax == Decimal("0.00")
        # Shipping on an empty basket would make a nothing-order cost money.
        assert totals.shipping_fee == Decimal("0.00")
        assert totals.total == Decimal("0.00")

    def test_single_item_below_the_free_shipping_threshold(self) -> None:
        totals = totals_for([line("50.00", 1)])
        assert totals.subtotal == Decimal("50.00")
        assert totals.discount_total == Decimal("0.00")
        assert totals.tax == Decimal("4.00")
        assert totals.shipping_fee == Decimal("9.99")
        assert totals.total == Decimal("63.99")

    def test_order_above_the_threshold_ships_free(self) -> None:
        totals = totals_for([line("120.00", 1)])
        assert totals.subtotal == Decimal("120.00")
        assert totals.tax == Decimal("9.60")
        assert totals.shipping_fee == Decimal("0.00")
        assert totals.total == Decimal("129.60")

    def test_multiple_lines_with_a_percentage_promotion(self) -> None:
        lines = [line("40.00", 2, 1), line("30.00", 1, 2)]  # 80 + 30 = 110
        rule = PromotionRule(
            code="TEN", discount_type=PromotionType.PERCENTAGE, value=Decimal("10")
        )
        totals = totals_for(lines, rule)
        assert totals.subtotal == Decimal("110.00")
        assert totals.discount_total == Decimal("11.00")
        # Tax is charged on the discounted 99.00, not on 110.00.
        assert totals.tax == Decimal("7.92")
        # 99.00 is below the 100.00 threshold, so shipping is charged.
        assert totals.shipping_fee == Decimal("9.99")
        assert totals.total == Decimal("116.91")

    def test_tax_is_not_charged_on_shipping(self) -> None:
        totals = totals_for([line("50.00", 1)])
        # 8% of 50.00 = 4.00. Taxing 59.99 would give 4.80.
        assert totals.tax == Decimal("4.00")

    @pytest.mark.parametrize(
        "lines",
        [
            [line("19.99", 3)],
            [line("0.01", 1)],
            [line("1249.00", 2), line("9.99", 5)],
            [line("33.33", 3), line("66.67", 1)],
        ],
        ids=["odd-price", "one-cent", "expensive-mixed", "repeating-decimals"],
    )
    def test_total_always_equals_the_sum_of_its_parts(self, lines: list[PricedLine]) -> None:
        """The invariant that makes an invoice add up.

        Whatever the inputs, total must equal subtotal - discount + tax +
        shipping exactly. This is the property most likely to break if rounding
        is ever moved or reordered.
        """
        totals = totals_for(lines)
        expected = totals.subtotal - totals.discount_total + totals.tax + totals.shipping_fee
        assert totals.total == expected

    def test_every_amount_has_exactly_two_decimal_places(self) -> None:
        totals = totals_for([line("19.999", 3)])
        for field_name in ("subtotal", "discount_total", "tax", "shipping_fee", "total"):
            value = getattr(totals, field_name)
            assert value.as_tuple().exponent == -2, f"{field_name} is not quantised: {value}"

    def test_currency_is_carried_through(self) -> None:
        totals = compute_totals(
            [line("10.00")],
            tax_rate=TAX_RATE,
            shipping_flat_fee=SHIPPING_FEE,
            free_shipping_threshold=FREE_SHIPPING_AT,
            currency="EUR",
        )
        assert totals.currency == "EUR"

    def test_large_quantity_does_not_lose_precision(self) -> None:
        totals = totals_for([line("9.99", 99)])
        assert totals.subtotal == Decimal("989.01")
        assert totals.shipping_fee == Decimal("0.00")
