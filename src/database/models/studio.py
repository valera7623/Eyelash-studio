from datetime import datetime, time
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.booking import Booking
    from src.database.models.consent import Consent
    from src.database.models.payment import Payment
    from src.database.models.user import User
    from src.database.models.window import Window

TARIFF_FREE = "free"
TARIFF_STARTER = "starter"
TARIFF_PLUS = "plus"


class Studio(Base):
    """Один владелец = одна студия на старте (owner_id unique)."""

    __tablename__ = "studios"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    tariff: Mapped[str] = mapped_column(String(16), default=TARIFF_FREE)
    resource_limit: Mapped[int] = mapped_column(Integer, default=1)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    subscription_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    hold_ttl_minutes: Mapped[int] = mapped_column(Integer, default=20)
    prepay_percent: Mapped[int] = mapped_column(Integer, default=100)
    cancel_free_hours: Mapped[int] = mapped_column(Integer, default=72)
    late_cancel_retain_percent: Mapped[int] = mapped_column(Integer, default=50)

    owner: Mapped["User"] = relationship(back_populates="studios")
    resources: Mapped[List["Resource"]] = relationship(
        back_populates="studio",
        cascade="all, delete-orphan",
    )
    bookings: Mapped[List["Booking"]] = relationship(
        back_populates="studio",
        cascade="all, delete-orphan",
    )
    payments: Mapped[List["Payment"]] = relationship(back_populates="studio")
    consents: Mapped[List["Consent"]] = relationship(back_populates="studio")


class Resource(Base):
    """Зал для наращивания ресниц. Слоты считаются только внутри открытых окон."""

    __tablename__ = "resources"

    studio_id: Mapped[int] = mapped_column(
        ForeignKey("studios.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128))
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    slot_step_min: Mapped[int] = mapped_column(Integer, default=60)
    min_duration_min: Mapped[int] = mapped_column(Integer, default=60)
    buffer_min: Mapped[int] = mapped_column(Integer, default=5)
    hour_markup_percent: Mapped[int] = mapped_column(Integer, default=50)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    work_start: Mapped[time] = mapped_column(Time, default=time(10, 0))
    work_end: Mapped[time] = mapped_column(Time, default=time(22, 0))
    weekdays: Mapped[str] = mapped_column(String(32), default="1,2,3,4,5,6,7")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    price_rub: Mapped[int] = mapped_column(Integer, default=0)
    weekend_price_rub: Mapped[int] = mapped_column(Integer, default=0)
    night_price_rub: Mapped[int] = mapped_column(Integer, default=0)
    night_start: Mapped[time] = mapped_column(Time, default=time(22, 0))

    studio: Mapped["Studio"] = relationship(back_populates="resources")
    bookings: Mapped[List["Booking"]] = relationship(
        back_populates="resource",
        cascade="all, delete-orphan",
    )
    windows: Mapped[List["Window"]] = relationship(
        back_populates="resource",
        cascade="all, delete-orphan",
    )
