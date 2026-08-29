"""Cart operations and cart pricing.

The single most important property of this module: **the client never supplies
a price**. Cart rows hold a product id and a quantity. Every monetary value in
a cart response is recomputed from the catalogue on each read, so a tampered
request body cannot change what anything costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    CartItemNotFoundError,
    ProductNotFoundError,
    ProductUnavailableError,
)
from app.core.logging import get_logger
from app.models.cart import Cart, CartItem
from app.repositories import catalog as catalog_repo
from app.repositories import order as order_repo
from app.schemas.cart import CartItemResponse, CartResponse, CartTotals
from app.services import inventory as inventory_service
from app.services.pricing import PricedLine, PromotionRule, compute_totals

logger = get_logger(__name__)


@dataclass(slots=True)
class PricedCart:
    """A cart resolved against the live catalogue.

    Carries both the priced lines (used to compute totals) and the list of
    blocking issues, so callers can render a cart *and* decide whether checkout
    is allowed from a single pass over the data.
    """

    cart_id: int | None
    lines: list[PricedLine]
    item_rows: list[CartItemResponse]
    issues: list[str]

    @property
    def is_checkout_ready(self) -> bool:
        return bool(self.item_rows) and not self.issues


def price_cart(db: Session, cart: Cart | None) -> PricedCart:
    """Resolve cart rows against current catalogue prices and stock.

    Products that have been deactivated or whose stock has fallen below the
    requested quantity produce an issue rather than an exception: a customer
    should see their cart with a clear warning, not an error page.
    """
    if cart is None or not cart.items:
        return PricedCart(cart_id=cart.id if cart else None, lines=[], item_rows=[], issues=[])

    products = catalog_repo.get_products_by_ids(db, [item.product_id for item in cart.items])
    lines: list[PricedLine] = []
    rows: list[CartItemResponse] = []
    issues: list[str] = []

    for item in cart.items:
        product = products.get(item.product_id)
        if product is None:
            # The row's FK cascades on product delete, so this is only reachable
            # in a race between listing and deletion. Surface it, do not crash.
            issues.append(f"A product in your cart is no longer available (id {item.product_id}).")
            continue

        available = product.stock_quantity
        exceeds = item.quantity > available
        if not product.is_active:
            issues.append(f"'{product.name}' is no longer available for purchase.")
        elif exceeds:
            unit = "unit" if available == 1 else "units"
            issues.append(
                f"Only {available} {unit} of '{product.name}' are available "
                f"(your cart has {item.quantity})."
            )

        line = PricedLine(
            product_id=product.id,
            sku=product.sku,
            name=product.name,
            unit_price=Decimal(product.price),
            quantity=item.quantity,
        )
        lines.append(line)
        rows.append(
            CartItemResponse(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                image_url=product.image_url,
                unit_price=line.unit_price,
                quantity=item.quantity,
                line_total=line.line_total,
                available_stock=available,
                exceeds_stock=exceeds,
                is_active=product.is_active,
            )
        )

    return PricedCart(cart_id=cart.id, lines=lines, item_rows=rows, issues=issues)


def build_cart_response(
    priced: PricedCart, *, promotion: PromotionRule | None = None, promo_code: str | None = None
) -> CartResponse:
    totals = compute_totals(
        priced.lines,
        tax_rate=settings.tax_rate,
        shipping_flat_fee=settings.shipping_flat_fee,
        free_shipping_threshold=settings.free_shipping_threshold,
        promotion=promotion,
        currency=settings.currency,
    )
    return CartResponse(
        id=priced.cart_id,
        items=priced.item_rows,
        item_count=sum(row.quantity for row in priced.item_rows),
        distinct_item_count=len(priced.item_rows),
        totals=CartTotals(
            subtotal=totals.subtotal,
            discount_total=totals.discount_total,
            tax=totals.tax,
            shipping_fee=totals.shipping_fee,
            total=totals.total,
            currency=totals.currency,
        ),
        promo_code=promo_code,
        issues=priced.issues,
        is_checkout_ready=priced.is_checkout_ready,
    )


def get_cart_response(db: Session, user_id: int) -> CartResponse:
    return build_cart_response(price_cart(db, order_repo.get_cart(db, user_id)))


def add_item(db: Session, user_id: int, product_id: int, quantity: int) -> CartResponse:
    """Add units of a product, merging into an existing line if present.

    Validation order is deliberate: quantity sanity first (cheap, no I/O), then
    existence, then availability against the *combined* quantity. Checking the
    delta alone would let three separate "add 2" requests put 6 units in a cart
    that only has 4 in stock.
    """
    inventory_service.validate_quantity(quantity, maximum=settings.max_item_quantity)

    # Loaded including inactive products on purpose. Fetching with the default
    # `include_inactive=False` would return None for a withdrawn product and
    # collapse it into a 404, making the `is_active` branch below unreachable -
    # a withdrawn item would be indistinguishable from a broken link, which is a
    # different problem with a different fix for the customer.
    product = catalog_repo.get_product(db, product_id, include_inactive=True)
    if product is None:
        raise ProductNotFoundError(details={"product_id": product_id})
    if not product.is_active:
        raise ProductUnavailableError(
            f"'{product.name}' is no longer available for purchase.",
            details={"product_id": product_id},
        )

    cart = order_repo.get_or_create_cart(db, user_id)
    existing = order_repo.get_cart_item(db, cart.id, product_id)
    new_quantity = (existing.quantity if existing else 0) + quantity

    inventory_service.validate_quantity(new_quantity, maximum=settings.max_item_quantity)
    inventory_service.validate_availability(
        inventory_service.StockRequest(
            product_id=product.id,
            product_name=product.name,
            requested=new_quantity,
            available=product.stock_quantity,
        )
    )

    if existing:
        existing.quantity = new_quantity
    else:
        if len(cart.items) >= settings.max_cart_items:
            raise ProductUnavailableError(
                f"A cart may contain at most {settings.max_cart_items} distinct products.",
                details={"max_cart_items": settings.max_cart_items},
            )
        # Appended through the relationship rather than db.add()'d standalone, so
        # the in-memory collection stays consistent with what was persisted.
        cart.items.append(CartItem(product_id=product_id, quantity=quantity))

    db.commit()
    return get_cart_response(db, user_id)


def update_item(db: Session, user_id: int, product_id: int, quantity: int) -> CartResponse:
    """Set an absolute quantity for a line."""
    inventory_service.validate_quantity(quantity, maximum=settings.max_item_quantity)

    cart = order_repo.get_cart(db, user_id)
    if cart is None:
        raise CartItemNotFoundError(details={"product_id": product_id})

    item = order_repo.get_cart_item(db, cart.id, product_id)
    if item is None:
        raise CartItemNotFoundError(details={"product_id": product_id})

    product = catalog_repo.get_product(db, product_id, include_inactive=True)
    if product is None:
        raise ProductNotFoundError(details={"product_id": product_id})
    if not product.is_active:
        raise ProductUnavailableError(
            f"'{product.name}' is no longer available for purchase.",
            details={"product_id": product_id},
        )

    # Reducing a line is always allowed, even when the new quantity is still
    # above the available stock.
    #
    # The cart already tolerates an over-stock line - it flags it as an issue
    # and blocks checkout rather than rejecting it - so refusing a *reduction*
    # would be inconsistent: a cart holding 3 units of a 1-unit product would
    # accept staying at 3 but refuse moving to 2. That left the customer with no
    # way to fix their own cart except emptying it. Increases are still checked
    # against stock, which is what actually prevents overselling.
    if quantity > item.quantity:
        inventory_service.validate_availability(
            inventory_service.StockRequest(
                product_id=product.id,
                product_name=product.name,
                requested=quantity,
                available=product.stock_quantity,
            )
        )

    item.quantity = quantity
    db.commit()
    return get_cart_response(db, user_id)


def remove_item(db: Session, user_id: int, product_id: int) -> CartResponse:
    cart = order_repo.get_cart(db, user_id)
    if cart is None:
        raise CartItemNotFoundError(details={"product_id": product_id})

    item = order_repo.get_cart_item(db, cart.id, product_id)
    if item is None:
        raise CartItemNotFoundError(details={"product_id": product_id})

    db.delete(item)
    db.commit()
    return get_cart_response(db, user_id)


def clear(db: Session, user_id: int) -> CartResponse:
    cart = order_repo.get_cart(db, user_id)
    if cart is not None:
        order_repo.clear_cart_items(db, cart)
        db.commit()
    return get_cart_response(db, user_id)
