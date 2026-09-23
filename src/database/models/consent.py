from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base, utcnow

if TYPE_CHECKING:
    from src.database.models.studio import Studio
    from src.database.models.user import User

DOCUMENT_PDN_PLATFORM = "pdn_platform"
DOCUMENT_PDN_STUDIO = "pdn_studio"


class Consent(Base):
    """Факт согласия на обработку ПДн (отдельный документ, не оферта)."""

    __tablename__ = "consents"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    studio_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("studios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    document_code: Mapped[str] = mapped_column(String(32))
    document_version: Mapped[str] = mapped_column(String(16))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="consents")
    studio: Mapped[Optional["Studio"]] = relationship(back_populates="consents")
