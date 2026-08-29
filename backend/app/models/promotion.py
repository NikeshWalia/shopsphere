"""Promotion codes.

Kept in the database rather than hardcoded so that discount rules can be
exercised as data: a test can create an expired code, a code below its minimum
spend, or a capped percentage code without touching application code.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Money, TimestampMixin, intpk
from app.models.enums import PromotionType

_promotion_type = Enum(
    PromotionType,
    name="promotion_type",
    native_enum=False,
    length=20,
    values_callable=lambda enum: [member.value for member in enum],
)


class Promotion(Base, TimestampMixin):
    __tablename__ = "promotions"

    id: Mapped[intpk]
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    discount_type: Mapped[PromotionType] = mapped_column(_promotion_type, nullable=False)
    # Percentage codes store 10 for "10%"; fixed codes store 15.00 for "$15 off".
    value: Mapped[Money] = mapped_column(nullable=False)
    min_subtotal: Mapped[Money] = mapped_column(nullable=False, default=0)
    # Caps a percentage discount, e.g. "20% off, up to $50".
    max_discount: Mapped[Money | None] = mapped_column()
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        CheckConstraint("value >= 0", name="value_non_negative"),
        CheckConstraint("min_subtotal >= 0", name="min_subtotal_non_negative"),
        CheckConstraint("times_used >= 0", name="times_used_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<Promotion {self.code} {self.discount_type}:{self.value}>"
