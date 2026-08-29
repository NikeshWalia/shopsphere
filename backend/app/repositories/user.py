"""User, role and address queries."""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.security import normalise_email
from app.models.enums import RoleName
from app.models.user import Address, Role, User


def get_role(db: Session, name: RoleName | str) -> Role | None:
    return db.execute(select(Role).where(Role.name == str(name))).scalar_one_or_none()


def get_user(db: Session, user_id: int) -> User | None:
    return (
        db.execute(select(User).options(joinedload(User.role)).where(User.id == user_id))
        .unique()
        .scalar_one_or_none()
    )


def get_user_by_email(db: Session, email: str) -> User | None:
    """Look a user up by email.

    Emails are stored already normalised, so this is an equality match on an
    indexed column rather than a `lower()` scan.
    """
    return (
        db.execute(
            select(User).options(joinedload(User.role)).where(User.email == normalise_email(email))
        )
        .unique()
        .scalar_one_or_none()
    )


def email_exists(db: Session, email: str) -> bool:
    return (
        db.execute(
            select(func.count(User.id)).where(User.email == normalise_email(email))
        ).scalar_one()
        > 0
    )


def _user_search_stmt(search: str | None, role: str | None, is_active: bool | None) -> Select:
    stmt = select(User).options(joinedload(User.role))
    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.email).like(pattern) | func.lower(User.full_name).like(pattern)
        )
    if role:
        stmt = stmt.join(Role, User.role_id == Role.id).where(Role.name == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    return stmt


def list_users(
    db: Session,
    *,
    search: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[User], int]:
    stmt = _user_search_stmt(search, role, is_active)
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = (
        db.execute(stmt.order_by(User.id.asc()).offset((page - 1) * page_size).limit(page_size))
        .unique()
        .scalars()
        .all()
    )
    return list(rows), total


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
def list_addresses(db: Session, user_id: int) -> list[Address]:
    """Default address first, then newest - the order a checkout form wants."""
    return list(
        db.execute(
            select(Address)
            .where(Address.user_id == user_id)
            .order_by(Address.is_default.desc(), Address.id.desc())
        )
        .scalars()
        .all()
    )


def get_address(db: Session, address_id: int, *, user_id: int | None = None) -> Address | None:
    """Fetch an address, optionally scoped to its owner.

    Passing ``user_id`` makes ownership part of the query rather than a check
    the caller might forget - the difference between an IDOR bug and a 404.
    """
    stmt = select(Address).where(Address.id == address_id)
    if user_id is not None:
        stmt = stmt.where(Address.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def clear_default_addresses(db: Session, user_id: int, *, except_id: int | None = None) -> None:
    """Demote any other default address so at most one can hold the flag."""
    for address in db.execute(
        select(Address).where(Address.user_id == user_id, Address.is_default.is_(True))
    ).scalars():
        if except_id is None or address.id != except_id:
            address.is_default = False


def get_default_address(db: Session, user_id: int) -> Address | None:
    return db.execute(
        select(Address).where(Address.user_id == user_id, Address.is_default.is_(True))
    ).scalar_one_or_none()
