"""Money arithmetic.

This module is deliberately free of database, framework and network imports.
Every function is pure, which is what makes the pricing rules exhaustively
unit-testable without a running system - and pricing is precisely where an
e-commerce bug costs real money.

Rules implemented here, stated once so the tests and the README agree:

1. A line total is ``unit_price * quantity``, rounded to cents half-up.
2. The subtotal is the sum of line totals.
3. A discount applies to the subtotal and can never exceed it, so a total is
   never negative.
4. Tax is charged on the discounted subtotal, not on shipping.
5. Shipping is free at or above the configured threshold, measured against the
   discounted subtotal; otherwise a flat fee applies.
6. ``total = subtotal - discount + tax + shipping``.

Every intermediate value is quantised to two decimal places as it is produced.
Rounding once at the end instead would let sub-cent error accumulate across
lines and produce a total that does not match the sum of its parts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.models.enums import PromotionType
from app.schemas.common import quantize_money

ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class PricedLine:
    """One priced cart/order line. ``unit_price`` always comes from the server."""

    product_id: int
    sku: str
    name: str
    unit_price: Decimal
    quantity: int

    @property
    def line_total(self) -> Decimal:
        return quantize_money(self.unit_price * self.quantity)


@dataclass(frozen=True, slots=True)
class PromotionRule:
    """Framework-free view of a promotion.

    Built from the ORM row by :func:`promotion_rule_from_model` so that the
    pricing functions never import a model and can be tested with plain data.
    """

    code: str
    discount_type: PromotionType
    value: Decimal
    min_subtotal: Decimal = ZERO
    max_discount: Decimal | None = None
    is_active: bool = True
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    usage_limit: int | None = None
    times_used: int = 0
    description: str = ""


@dataclass(frozen=True, slots=True)
class OrderTotals:
    subtotal: Decimal
    discount_total: Decimal
    tax: Decimal
    shipping_fee: Decimal
    total: Decimal
    currency: str = "USD"


def compute_subtotal(lines: list[PricedLine]) -> Decimal:
    """Sum of line totals, rounded per line before summing."""
    return quantize_money(sum((line.line_total for line in lines), ZERO))


def promotion_error(
    rule: PromotionRule, subtotal: Decimal, *, now: datetime | None = None
) -> str | None:
    """Return why ``rule`` cannot be applied, or ``None`` if it can.

    Returning the reason rather than a bare boolean means the API can tell the
    customer *why* their code was refused, which is both better UX and a
    stronger assertion target for tests.
    """
    moment = now or datetime.now(UTC)

    if not rule.is_active:
        return f"Promotion code {rule.code} is no longer active."
    if rule.valid_from and moment < rule.valid_from:
        return f"Promotion code {rule.code} is not valid yet."
    if rule.valid_to and moment > rule.valid_to:
        return f"Promotion code {rule.code} has expired."
    if rule.usage_limit is not None and rule.times_used >= rule.usage_limit:
        return f"Promotion code {rule.code} has reached its usage limit."
    if subtotal < rule.min_subtotal:
        return (
            f"Promotion code {rule.code} requires a minimum subtotal of "
            f"{rule.min_subtotal:.2f}."
        )
    return None


def compute_discount(subtotal: Decimal, rule: PromotionRule | None) -> Decimal:
    """Discount amount for ``subtotal`` under ``rule``.

    Assumes the rule has already been accepted by :func:`promotion_error`;
    validity is a separate concern from arithmetic. The result is clamped to the
    subtotal so a $20-off code on a $5 basket discounts $5, not $20.
    """
    if rule is None:
        return ZERO

    if rule.discount_type is PromotionType.PERCENTAGE:
        raw = subtotal * (rule.value / Decimal("100"))
    else:
        raw = rule.value

    discount = quantize_money(raw)
    if rule.max_discount is not None:
        discount = min(discount, quantize_money(rule.max_discount))
    return max(ZERO, min(discount, quantize_money(subtotal)))


def compute_tax(taxable_amount: Decimal, tax_rate: Decimal) -> Decimal:
    """Tax on the discounted subtotal. Shipping is not taxed."""
    if taxable_amount <= ZERO:
        return ZERO
    return quantize_money(taxable_amount * tax_rate)


def compute_shipping(
    discounted_subtotal: Decimal,
    *,
    flat_fee: Decimal,
    free_threshold: Decimal,
) -> Decimal:
    """Flat fee below the free-shipping threshold, nothing at or above it.

    An empty basket ships for free: charging shipping on a zero-value order
    would produce a total greater than zero for nothing.
    """
    if discounted_subtotal <= ZERO:
        return ZERO
    if discounted_subtotal >= free_threshold:
        return ZERO
    return quantize_money(flat_fee)


def compute_totals(
    lines: list[PricedLine],
    *,
    tax_rate: Decimal,
    shipping_flat_fee: Decimal,
    free_shipping_threshold: Decimal,
    promotion: PromotionRule | None = None,
    currency: str = "USD",
) -> OrderTotals:
    """Compute the complete money breakdown for a set of lines.

    This is the single function that decides what a customer pays. The checkout
    endpoint and the quote endpoint both call it, so a preview can never
    disagree with the amount actually charged.
    """
    subtotal = compute_subtotal(lines)
    discount = compute_discount(subtotal, promotion)
    discounted = quantize_money(subtotal - discount)
    tax = compute_tax(discounted, tax_rate)
    shipping = compute_shipping(
        discounted, flat_fee=shipping_flat_fee, free_threshold=free_shipping_threshold
    )
    total = quantize_money(discounted + tax + shipping)

    return OrderTotals(
        subtotal=subtotal,
        discount_total=discount,
        tax=tax,
        shipping_fee=shipping,
        total=total,
        currency=currency,
    )
