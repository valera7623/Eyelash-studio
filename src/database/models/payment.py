from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.booking import Booking
    from src.database.models.studio import Studio

KIND_SLOT_PREPAY = "slot_prepay"
KIND_OWNER_SUBSCRIPTION = "owner_subscription"

PAYMENT_PENDING = "pending"
PAYMENT_PAID = "paid"
PAYMENT_FAILED = "failed"
PAYMENT_REFUND_PENDING = "refund_pending"
PAYMENT_REFUNDED = "refunded"


class Payment(Base):
    """Счёт кассы: (a) предоплата клиента за слот, (b) подписка владельца."""

    __tablename__ = "payments"

    kind: Mapped[str] = mapped_column(String(32), index=True)
    booking_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("bookings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    studio_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("studios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    prodamus_invoice_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(16), default="")
    provider_payment_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    amount_rub: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    status: Mapped[str] = mapped_column(String(16), default=PAYMENT_PENDING, index=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    refunded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    refund_amount_rub: Mapped[int] = mapped_column(Integer, default=0)

    booking: Mapped[Optional["Booking"]] = relationship(back_populates="payments")
    studio: Mapped[Optional["Studio"]] = relationship(back_populates="payments")
