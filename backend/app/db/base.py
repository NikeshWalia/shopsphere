"""Declarative base, shared column types and mixins."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import DateTime, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint naming. Without this, PostgreSQL invents names, Alembic
# cannot reliably drop them in a downgrade, and database tests have nothing
# stable to assert against.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Money is NUMERIC(10,2) everywhere. Never float: binary floating point cannot
# represent 0.10 exactly, and totals that are off by a cent are a real class of
# e-commerce defect this project sets out to demonstrate testing for.
Money = Annotated[Decimal, mapped_column(Numeric(10, 2))]

intpk = Annotated[int, mapped_column(primary_key=True)]


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the database itself.

    Server-side defaults mean rows written by migrations, the seeder or raw SQL
    get correct timestamps too, not only rows written through the ORM.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
