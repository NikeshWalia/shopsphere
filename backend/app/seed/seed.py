"""Idempotent database seeder.

    python -m app.seed.seed              # create or update the seed data
    python -m app.seed.seed --reset      # wipe application tables first
    python -m app.seed.seed --summary    # print what is currently in the database

Idempotency is the point: running it twice must not create 126 products or a
second admin. Every entity is looked up by its natural key (email, slug, SKU,
promo code) and updated in place if it already exists. That makes it safe to run
on every container start, which is exactly what the Docker entrypoint does.

Sample orders are the one exception - they are only created when a demo customer
has none, because re-running the seeder should not keep inflating order history.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, normalise_email
from app.db.session import session_scope
from app.models.cart import Cart, CartItem
from app.models.catalog import Category, Inventory, Product
from app.models.enums import OrderStatus, PaymentStatus, PromotionType, RoleName
from app.models.order import Order, OrderItem, Payment
from app.models.promotion import Promotion
from app.models.user import Address, Role, User
from app.repositories.order import generate_order_number
from app.seed.data import CATEGORIES, CUSTOMERS, PRODUCTS, PROMOTIONS, image_url_for
from app.services.pricing import PricedLine, compute_totals

# Truncated in dependency order. CASCADE handles the rest.
RESET_TABLES = (
    "payments",
    "order_items",
    "orders",
    "cart_items",
    "carts",
    "addresses",
    "inventory",
    "products",
    "categories",
    "promotions",
    "users",
    "roles",
)


def log(message: str) -> None:
    print(message, flush=True)


def reset(db: Session) -> None:
    """Truncate every application table and restart identity sequences.

    TRUNCATE ... RESTART IDENTITY is used rather than DELETE so that a reseeded
    database produces the same ids every time, which keeps demo screenshots and
    documentation examples stable.
    """
    log("Resetting application tables...")
    db.execute(text(f"TRUNCATE TABLE {', '.join(RESET_TABLES)} RESTART IDENTITY CASCADE"))
    db.flush()


def seed_roles(db: Session) -> dict[str, Role]:
    descriptions = {
        RoleName.CUSTOMER: "Can browse, buy and manage their own orders.",
        RoleName.ADMIN: "Full access to catalogue, inventory, orders and users.",
    }
    roles: dict[str, Role] = {}
    for name, description in descriptions.items():
        role = db.execute(select(Role).where(Role.name == str(name))).scalar_one_or_none()
        if role is None:
            role = Role(name=str(name), description=description)
            db.add(role)
            db.flush()
        else:
            role.description = description
        roles[str(name)] = role
    log(f"  roles         : {len(roles)}")
    return roles


def _upsert_user(
    db: Session, *, email: str, full_name: str, password: str, role: Role
) -> tuple[User, bool]:
    normalised = normalise_email(email)
    user = db.execute(select(User).where(User.email == normalised)).scalar_one_or_none()
    if user is not None:
        # Reset the demo password on every run so documented credentials always
        # work, even if a test changed them.
        user.password_hash = hash_password(password)
        user.full_name = full_name
        user.role_id = role.id
        user.is_active = True
        return user, False

    user = User(
        email=normalised,
        full_name=full_name,
        password_hash=hash_password(password),
        role_id=role.id,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user, True


def seed_users(db: Session, roles: dict[str, Role]) -> tuple[User, list[User]]:
    admin, _ = _upsert_user(
        db,
        email=settings.seed_admin_email,
        full_name="Sam Rivera",
        password=settings.seed_admin_password,
        role=roles[RoleName.ADMIN],
    )

    customers: list[User] = []
    for email, full_name in CUSTOMERS:
        user, _ = _upsert_user(
            db,
            email=email,
            full_name=full_name,
            password=settings.seed_customer_password,
            role=roles[RoleName.CUSTOMER],
        )
        customers.append(user)

    log(f"  users         : 1 admin, {len(customers)} customers")
    return admin, customers


def seed_addresses(db: Session, customers: list[User]) -> None:
    cities = [
        ("12 Analytical Way", "Cambridge", "MA", "02139"),
        ("88 Harbour Road", "Seattle", "WA", "98101"),
        ("5 Rue Lumiere", "Austin", "TX", "73301"),
        ("240 Kestrel Lane", "Denver", "CO", "80202"),
    ]
    created = 0
    for index, user in enumerate(customers):
        existing = db.execute(
            select(func.count(Address.id)).where(Address.user_id == user.id)
        ).scalar_one()
        if existing:
            continue
        line1, city, state, postal = cities[index % len(cities)]
        db.add(
            Address(
                user_id=user.id,
                label="Home",
                full_name=user.full_name,
                line1=line1,
                city=city,
                state=state,
                postal_code=postal,
                country="US",
                phone=f"+1-555-01{index:02d}",
                is_default=True,
            )
        )
        created += 1
    db.flush()
    log(f"  addresses     : {created} created")


def seed_categories(db: Session) -> dict[str, Category]:
    result: dict[str, Category] = {}
    for entry in CATEGORIES:
        category = db.execute(
            select(Category).where(Category.slug == entry.slug)
        ).scalar_one_or_none()
        if category is None:
            category = Category(name=entry.name, slug=entry.slug, description=entry.description)
            db.add(category)
            db.flush()
        else:
            category.name = entry.name
            category.description = entry.description
        result[entry.slug] = category
    log(f"  categories    : {len(result)}")
    return result


def seed_products(db: Session, categories: dict[str, Category]) -> int:
    created = updated = 0
    for entry in PRODUCTS:
        product = db.execute(select(Product).where(Product.sku == entry.sku)).scalar_one_or_none()
        if product is None:
            product = Product(sku=entry.sku)
            db.add(product)
            created += 1
        else:
            updated += 1

        product.name = entry.name
        product.description = entry.description
        product.price = entry.price
        product.brand = entry.brand
        product.rating = entry.rating
        product.category_id = categories[entry.category_slug].id
        product.image_url = image_url_for(entry.sku)
        product.is_active = entry.is_active
        db.flush()

        inventory = db.execute(
            select(Inventory).where(Inventory.product_id == product.id)
        ).scalar_one_or_none()
        if inventory is None:
            db.add(Inventory(product_id=product.id, quantity=entry.stock))
        else:
            # Reset stock to the seeded level so a reseed restores a known state
            # after test runs have drawn it down.
            inventory.quantity = entry.stock

    db.flush()
    log(f"  products      : {created} created, {updated} updated")
    return created + updated


def seed_promotions(db: Session) -> None:
    now = datetime.now(UTC)
    for entry in PROMOTIONS:
        code = str(entry["code"])
        promotion = db.execute(select(Promotion).where(Promotion.code == code)).scalar_one_or_none()
        if promotion is None:
            promotion = Promotion(code=code)
            db.add(promotion)

        promotion.description = str(entry["description"])
        promotion.discount_type = PromotionType(str(entry["discount_type"]))
        promotion.value = cast("Decimal", entry["value"])
        promotion.min_subtotal = cast("Decimal", entry["min_subtotal"])
        promotion.max_discount = cast("Decimal | None", entry["max_discount"])
        promotion.is_active = not entry.get("inactive", False)
        promotion.valid_from = now - timedelta(days=30)
        # The expired code is backdated so the "promotion has expired" branch has
        # a real row to exercise rather than needing one built at test time.
        promotion.valid_to = (
            now - timedelta(days=1) if entry.get("expired") else now + timedelta(days=365)
        )
        promotion.usage_limit = None
        promotion.times_used = 0

    db.flush()
    log(f"  promotions    : {len(PROMOTIONS)}")


def seed_sample_orders(db: Session, customers: list[User]) -> None:
    """Give the first two demo customers some order history.

    Skipped for a customer who already has orders, so repeated seeding does not
    keep growing their history.
    """
    if not customers:
        return

    recipes = [
        (customers[0], ["LAP-1004", "ACC-4002"], OrderStatus.DELIVERED, PaymentStatus.PAID, 21),
        (customers[0], ["ELE-3002"], OrderStatus.SHIPPED, PaymentStatus.PAID, 5),
        (
            customers[1 % len(customers)],
            ["HOM-5003", "BOK-7002"],
            OrderStatus.CONFIRMED,
            PaymentStatus.PAID,
            2,
        ),
        (
            customers[1 % len(customers)],
            ["PHN-2005"],
            OrderStatus.CANCELLED,
            PaymentStatus.FAILED,
            9,
        ),
    ]

    # Decided once, before anything is inserted. Checking inside the loop would
    # mean the first recipe for a customer suppresses their remaining ones.
    already_has_orders = {
        user.id
        for user in customers
        if db.execute(select(func.count(Order.id)).where(Order.user_id == user.id)).scalar_one()
    }

    created = 0
    for user, skus, status, payment_status, days_ago in recipes:
        if user.id in already_has_orders:
            continue

        address = (
            db.execute(select(Address).where(Address.user_id == user.id).order_by(Address.id))
            .scalars()
            .first()
        )
        if address is None:
            continue

        found = [
            db.execute(select(Product).where(Product.sku == sku)).scalar_one_or_none()
            for sku in skus
        ]
        products: list[Product] = [p for p in found if p is not None]
        if not products:
            continue

        lines = [
            PricedLine(
                product_id=product.id,
                sku=product.sku,
                name=product.name,
                unit_price=Decimal(product.price),
                quantity=1,
            )
            for product in products
        ]
        # Uses the same pricing function as checkout, so seeded order history is
        # arithmetically consistent with orders the application creates.
        totals = compute_totals(
            lines,
            tax_rate=settings.tax_rate,
            shipping_flat_fee=settings.shipping_flat_fee,
            free_shipping_threshold=settings.free_shipping_threshold,
            currency=settings.currency,
        )
        placed_at = datetime.now(UTC) - timedelta(days=days_ago)

        order = Order(
            order_number=generate_order_number(placed_at),
            user_id=user.id,
            status=status,
            payment_status=payment_status,
            subtotal=totals.subtotal,
            discount_total=totals.discount_total,
            tax=totals.tax,
            shipping_fee=totals.shipping_fee,
            total=totals.total,
            currency=totals.currency,
            shipping_address_id=address.id,
            shipping_full_name=address.full_name,
            shipping_line1=address.line1,
            shipping_city=address.city,
            shipping_state=address.state,
            shipping_postal_code=address.postal_code,
            shipping_country=address.country,
            shipping_phone=address.phone,
            created_at=placed_at,
            updated_at=placed_at,
            cancelled_reason="Payment declined" if status is OrderStatus.CANCELLED else None,
        )
        db.add(order)
        db.flush()

        for line in lines:
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=line.product_id,
                    product_name=line.name,
                    sku=line.sku,
                    unit_price=line.unit_price,
                    quantity=line.quantity,
                    line_total=line.line_total,
                )
            )

        db.add(
            Payment(
                order_id=order.id,
                provider_reference=f"txn_seed{order.id:016d}",
                amount=order.total,
                currency=order.currency,
                status=payment_status,
                method="card",
                card_last4="1111" if payment_status is PaymentStatus.PAID else "0002",
                card_brand="visa",
                failure_code=None if payment_status is PaymentStatus.PAID else "insufficient_funds",
                failure_message=(
                    None if payment_status is PaymentStatus.PAID else "Insufficient funds."
                ),
                attempt=1,
                created_at=placed_at,
                updated_at=placed_at,
            )
        )
        created += 1

    db.flush()
    log(f"  sample orders : {created} created")


def seed_demo_cart(db: Session, customers: list[User]) -> None:
    """Leave one demo customer with a non-empty cart so the UI has something to show."""
    if not customers:
        return
    user = customers[0]
    cart = db.execute(select(Cart).where(Cart.user_id == user.id)).scalar_one_or_none()
    if cart is None:
        cart = Cart(user_id=user.id)
        db.add(cart)
        db.flush()

    if db.execute(select(func.count(CartItem.id)).where(CartItem.cart_id == cart.id)).scalar_one():
        return

    for sku, quantity in (("ACC-4001", 1), ("BOK-7003", 2)):
        product = db.execute(select(Product).where(Product.sku == sku)).scalar_one_or_none()
        if product is not None:
            db.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=quantity))
    db.flush()
    log("  demo cart     : populated")


def summary(db: Session) -> None:
    counts = {
        "roles": db.execute(select(func.count(Role.id))).scalar_one(),
        "users": db.execute(select(func.count(User.id))).scalar_one(),
        "addresses": db.execute(select(func.count(Address.id))).scalar_one(),
        "categories": db.execute(select(func.count(Category.id))).scalar_one(),
        "products": db.execute(select(func.count(Product.id))).scalar_one(),
        "products_active": db.execute(
            select(func.count(Product.id)).where(Product.is_active.is_(True))
        ).scalar_one(),
        "inventory_units": db.execute(
            select(func.coalesce(func.sum(Inventory.quantity), 0))
        ).scalar_one(),
        "promotions": db.execute(select(func.count(Promotion.id))).scalar_one(),
        "orders": db.execute(select(func.count(Order.id))).scalar_one(),
        "payments": db.execute(select(func.count(Payment.id))).scalar_one(),
    }
    log("\nDatabase contents")
    log("-" * 34)
    for key, value in counts.items():
        log(f"  {key:<16} {value:>10,}")


def run(*, do_reset: bool = False) -> None:
    with session_scope() as db:
        if do_reset:
            reset(db)

        log("Seeding ShopSphere...")
        roles = seed_roles(db)
        _, customers = seed_users(db, roles)
        seed_addresses(db, customers)
        categories = seed_categories(db)
        seed_products(db, categories)
        seed_promotions(db)
        seed_sample_orders(db, customers)
        seed_demo_cart(db, customers)

    with session_scope() as db:
        summary(db)

    log("\nDemo accounts")
    log("-" * 34)
    log(f"  admin     {settings.seed_admin_email} / {settings.seed_admin_password}")
    log(f"  customer  {CUSTOMERS[0][0]} / {settings.seed_customer_password}")
    log("\nSeed complete.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the ShopSphere database.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate all application tables before seeding (destructive).",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Only print current row counts; make no changes.",
    )
    args = parser.parse_args(argv)

    if args.summary:
        with session_scope() as db:
            summary(db)
        return 0

    run(do_reset=args.reset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
