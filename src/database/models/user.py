from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.consent import Consent
    from src.database.models.studio import Studio


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    language_code: Mapped[str] = mapped_column(String(10), default="ru")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    studios: Mapped[List["Studio"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )
    consents: Mapped[List["Consent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __str__(self):
        return f"@{self.username}" if self.username else f"{self.first_name} {self.last_name or ''}"
