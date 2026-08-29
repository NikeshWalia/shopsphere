"""Identity, access control and shipping addresses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, intpk
from app.models.enums import RoleName

if TYPE_CHECKING:
    from app.models.cart import Cart
    from app.models.order import Order


class Role(Base):
    """A named permission bucket. Two rows in practice: customer and admin."""

    __tablename__ = "roles"

    id: Mapped[intpk]
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    users: Mapped[list[User]] = relationship(back_populates="role")

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[intpk]
    # Stored already normalised to lowercase (see core.security.normalise_email)
    # so the UNIQUE constraint is genuinely case-insensitive.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")
    addresses: Mapped[list[Address]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    cart: Mapped[Cart | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    orders: Mapped[list[Order]] = relationship(back_populates="user")

    __table_args__ = (Index("ix_users_role_id", "role_id"),)

    @property
    def role_name(self) -> str:
        return self.role.name

    @property
    def is_admin(self) -> bool:
        return self.role.name == RoleName.ADMIN

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email}>"


class Address(Base, TimestampMixin):
    __tablename__ = "addresses"

    id: Mapped[intpk]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(50), default="Home", nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    line1: Mapped[str] = mapped_column(String(160), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str] = mapped_column(String(2), default="US", nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="addresses")

    __table_args__ = (Index("ix_addresses_user_id", "user_id"),)

    def __repr__(self) -> str:
        return f"<Address {self.id} user={self.user_id}>"
