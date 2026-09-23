from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.payment import Payment
    from src.database.models.studio import Resource, Studio
    from src.database.models.user import User

STATUS_PENDING = "pending"
STATUS_HOLD = "hold"
STATUS_PAID = "paid"
STATUS_CANCELLED = "cancelled"
STATUS_DECLINED = "declined"
STATUS_BLOCKED = "blocked"
ACTIVE_STATUSES = (STATUS_HOLD, STATUS_PAID, STATUS_BLOCKED)
OPEN_CLIENT_STATUSES = (STATUS_PENDING, STATUS_HOLD, STATUS_PAID)


class Booking(Base):
    """Hold до оплаты с TTL. Два клиента не занимают один интервал."""

    __tablename__ = "bookings"
    __table_args__ = (
        Index(
            "uq_booking_resource_start_active",
            "resource_id",
            "starts_at",
            unique=True,
            sqlite_where=text("status IN ('hold', 'paid', 'blocked')"),
            postgresql_where=text("status IN ('hold', 'paid', 'blocked')"),
        ),
    )

    resource_id: Mapped[int] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"),
        index=True,
    )
    studio_id: Mapped[int] = mapped_column(
        ForeignKey("studios.id", ondelete="CASCADE"),
        index=True,
    )
    client_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    client_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    client_name: Mapped[str] = mapped_column(String(128))
    client_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=STATUS_HOLD, index=True)
    hold_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reminder_2h_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    quoted_price_rub: Mapped[int] = mapped_column(Integer, default=0)
    prepay_amount_rub: Mapped[int] = mapped_column(Integer, default=0)

    resource: Mapped["Resource"] = relationship(back_populates="bookings")
    studio: Mapped["Studio"] = relationship(back_populates="bookings")
    client: Mapped[Optional["User"]] = relationship()
    payments: Mapped[List["Payment"]] = relationship(back_populates="booking")
