"""Саппорт платформы: сводка, не контент. Superadmin — платные подписчики."""

from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models.booking import Booking
from src.database.models.payment import Payment
from src.database.models.studio import Studio
from src.database.models.user import User
from src.filters import AdminFilter, SuperadminFilter
from src.services import prodamus, yookassa
from src.services.payments import active_provider, is_pay_configured
from src.services.tariffs import list_active_paid_studios, tariff_label

router = Router()

TELEGRAM_MESSAGE_LIMIT = 4000


def _count_lines(rows: list[tuple[str, int]], empty: str = "—") -> str:
    if not rows:
        return empty
    return ", ".join(f"{status} {n}" for status, n in rows)


def _until_label(studio: Studio) -> str:
    until = studio.subscription_until
    if until is None:
        return "—"
    return until.strftime("%d.%m.%Y")


def _chunk_messages(header: str, lines: list[str], limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if not lines:
        return [header]
    chunks: list[str] = []
    current = header
    for line in lines:
        candidate = f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        chunks.append(current)
        current = f"{header}\n{line}"
    chunks.append(current)
    return chunks


def format_user_line(user: User) -> str:
    handle = f"@{escape(user.username)}" if user.username else "без username"
    name = escape((user.first_name or "").strip() or "—")
    if user.last_name:
        name = f"{name} {escape(user.last_name)}"
    return f"• <code>{user.telegram_id}</code> {handle} · {name}"


async def list_platform_users(session: AsyncSession) -> list[User]:
    rows = await session.execute(select(User).order_by(User.id.asc()))
    return list(rows.scalars().all())


async def platform_support_messages(session: AsyncSession) -> list[str]:
    users = await list_platform_users(session)
    studios_n = await session.scalar(select(func.count()).select_from(Studio)) or 0
    paid = await list_active_paid_studios(session)
    pay_rows = (
        await session.execute(
            select(Payment.status, func.count()).group_by(Payment.status)
        )
    ).all()
    book_rows = (
        await session.execute(
            select(Booking.status, func.count()).group_by(Booking.status)
        )
    ).all()
    provider = active_provider()
    if provider == "yookassa":
        cashier = "ЮKassa"
        webhook_path = "/yookassa/webhook"
    elif provider == "prodamus":
        cashier = "Prodamus"
        webhook_path = "/prodamus/webhook"
    else:
        cashier = "нет"
        webhook_path = ""
    extras = []
    if yookassa.is_configured() and provider != "yookassa":
        extras.append("ЮKassa ключи есть, но PAYMENT_PROVIDER не auto/yookassa")
    if prodamus.is_configured() and provider != "prodamus":
        extras.append("Prodamus в запас")
    extra = f" ({'; '.join(extras)})" if extras else ""
    webhook = ""
    if settings.PUBLIC_BASE_URL.strip() and webhook_path:
        webhook = settings.PUBLIC_BASE_URL.rstrip("/") + webhook_path
    elif not is_pay_configured():
        webhook = "задайте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY"
    header = (
        "🛠️ <b>Саппорт платформы</b>\n\n"
        f"Касса: <b>{cashier}</b>{extra}\n"
        f"Webhook: <code>{webhook or 'задайте PUBLIC_BASE_URL'}</code>\n"
        f"👥 Пользователей: <b>{len(users)}</b>"
    )
    lines = [format_user_line(user) for user in users]
    footer = (
        f"🏠 Студий: <b>{studios_n}</b>\n"
        f"💳 Платных подписчиков: <b>{len(paid)}</b> — /superadmin\n"
        f"Платежи: {_count_lines([(str(s), int(n)) for s, n in pay_rows])}\n"
        f"Брони: {_count_lines([(str(s), int(n)) for s, n in book_rows])}\n\n"
        "<i>Только для ID из ADMINS / SUPERADMINS. Это не кабинет владельца студии.</i>"
    )
    chunks = _chunk_messages(header + "\n", lines)
    last = chunks[-1]
    candidate = f"{last}\n{footer}"
    if len(candidate) <= TELEGRAM_MESSAGE_LIMIT:
        chunks[-1] = candidate
    else:
        chunks.append(footer)
    return chunks


async def platform_support_text(session: AsyncSession) -> str:
    return "\n".join(await platform_support_messages(session))


def paid_subscribers_header(count: int) -> str:
    return f"👑 <b>Superadmin</b>\n\nПлатных подписчиков: <b>{count}</b>"


def format_paid_subscriber_line(studio: Studio) -> str:
    name = escape(studio.name or "—")
    slug = escape(studio.slug or "—")
    return (
        f"• TG <code>{studio.owner_telegram_id}</code> · студия #{studio.id} "
        f"{name} (<code>{slug}</code>)\n"
        f"  {tariff_label(studio.tariff)} · до {_until_label(studio)}"
    )


async def paid_subscribers_messages(session: AsyncSession, now=None) -> list[str]:
    studios = await list_active_paid_studios(session, now=now)
    header = paid_subscribers_header(len(studios))
    if not studios:
        return [header + "\n\nНет активных Старт / Плюс."]
    lines = [format_paid_subscriber_line(studio) for studio in studios]
    return _chunk_messages(header + "\n", lines)


@router.message(Command("admin"), AdminFilter())
async def cmd_admin(message: Message, session: AsyncSession):
    for chunk in await platform_support_messages(session):
        await message.answer(chunk)


@router.message(Command("superadmin"), SuperadminFilter())
async def cmd_superadmin(message: Message, session: AsyncSession):
    for chunk in await paid_subscribers_messages(session):
        await message.answer(chunk)
