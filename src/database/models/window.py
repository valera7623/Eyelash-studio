from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base

if TYPE_CHECKING:
    from src.database.models.studio import Resource


class Window(Base):
    """Интервал, который владелец открыл вручную. Повторяющегося расписания нет."""

    __tablename__ = "windows"

    resource_id: Mapped[int] = mapped_column(
        ForeignKey("resources.id", ondelete="CASCADE"),
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    resource: Mapped["Resource"] = relationship(back_populates="windows")
