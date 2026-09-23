from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from src.config import settings
from src.database.base import utcnow
from src.database.models.booking import STATUS_PAID, Booking
from src.database.models.studio import Resource, Studio
from src.services.formatters import booking_summary
from src.services.slots import expire_holds
from src.utils.qr_code import studio_start_link

logger = logging.getLogger(__name__)


async def job_expire_holds(bot, session_maker) -> None:
    async with session_maker() as session:
        expired = await expire_holds(session)
        if expired:
            logger.info("expired holds: %s", len(expired))
        for booking in expired:
            studio = await session.get(Studio, booking.studio_id)
            resource = await session.get(Resource, booking.resource_id)
            if not studio or not resource:
                continue
            link = studio_start_link(studio.slug)
            text = (
                "⏳ Время на оплату истекло, слот снова свободен.\n"
                + booking_summary(booking, studio, resource)
                + f"\n\nЗаписаться снова: {link}"
            )
            for chat_id in (booking.client_telegram_id, studio.owner_telegram_id):
                try:
                    await bot.send_message(chat_id, text)
                except Exception:
                    logger.exception("hold expiry notify %s", chat_id)


async def collect_due_reminders(session, now=None) -> list[tuple[Booking, str]]:
    """Пары (бронь, '24h'|'2h') для ещё не отправленных окон."""
    now = now or utcnow()
    horizon_24 = now + timedelta(hours=settings.REMINDER_HOURS)
    horizon_2 = now + timedelta(hours=settings.REMINDER_2H_HOURS)
    stmt = select(Booking).where(
        Booking.status == STATUS_PAID,
        Booking.starts_at > now,
        Booking.starts_at <= horizon_24,
    )
    rows = (await session.execute(stmt)).scalars().all()
    due: list[tuple[Booking, str]] = []
    for booking in rows:
        start = booking.starts_at
        if (
            booking.reminder_2h_sent_at is None
            and start <= horizon_2
        ):
            due.append((booking, "2h"))
        elif (
            booking.reminder_sent_at is None
            and start > horizon_2
            and start <= horizon_24
        ):
            due.append((booking, "24h"))
    return due


async def job_reminders(bot, session_maker) -> None:
    now = utcnow()
    async with session_maker() as session:
        due = await collect_due_reminders(session, now)
        for booking, kind in due:
            studio = await session.get(Studio, booking.studio_id)
            resource = await session.get(Resource, booking.resource_id)
            if not studio or not resource:
                continue
            label = "за 2 часа" if kind == "2h" else "за 24 часа"
            text = f"🔔 Напоминание о записи ({label})\n" + booking_summary(booking, studio, resource)
            for chat_id in (booking.client_telegram_id, studio.owner_telegram_id):
                try:
                    await bot.send_message(chat_id, text)
                except Exception:
                    logger.exception("reminder send %s", chat_id)
            if kind == "2h":
                booking.reminder_2h_sent_at = now
            else:
                booking.reminder_sent_at = now
        if due:
            await session.commit()


def backup_sqlite() -> Path | None:
    src = Path(settings.SQLITE_PATH)
    if not src.exists():
        return None
    dest_dir = src.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M")
    dest = dest_dir / f"studio_book-{stamp}.db"
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(dest)
    try:
        source.backup(dest_conn)
        dest_conn.commit()
    finally:
        dest_conn.close()
        source.close()
    keep = sorted(dest_dir.glob("studio_book-*.db"), reverse=True)
    for old in keep[14:]:
        old.unlink(missing_ok=True)
    logger.info("sqlite backup: %s", dest)
    _copy_backup_offsite(dest)
    return dest


def _copy_backup_offsite(local: Path) -> None:
    raw = (settings.BACKUP_OFFSITE_DIR or "").strip()
    if not raw:
        return
    dest_dir = Path(raw)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / local.name
        shutil.copy2(local, dest)
        keep = sorted(dest_dir.glob("studio_book-*.db"), reverse=True)
        for old in keep[14:]:
            old.unlink(missing_ok=True)
        logger.info("sqlite offsite backup: %s", dest)
    except OSError:
        logger.exception("offsite backup failed dir=%s", dest_dir)


def job_backup_sqlite() -> None:
    backup_sqlite()
