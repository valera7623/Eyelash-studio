from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from src.database.models.booking import STATUS_HOLD, STATUS_PAID, Booking
from src.database.models.studio import Resource, Studio
from src.services.slots import shoot_minutes


WEEKDAYS_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def format_day_label(day) -> str:
    return f"{day.strftime('%d.%m')} ({WEEKDAYS_RU[day.weekday()]})"


def format_slot_local(starts_at: datetime, tz_name: str = "Europe/Moscow") -> str:
    tz = ZoneInfo(tz_name)
    local = starts_at.astimezone(tz) if starts_at.tzinfo else starts_at.replace(tzinfo=timezone.utc).astimezone(tz)
    return local.strftime("%d.%m.%Y %H:%M")


def format_interval_local(starts_at: datetime, ends_at: datetime, tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    start = starts_at.astimezone(tz) if starts_at.tzinfo else starts_at.replace(tzinfo=timezone.utc).astimezone(tz)
    end = ends_at.astimezone(tz) if ends_at.tzinfo else ends_at.replace(tzinfo=timezone.utc).astimezone(tz)
    return f"{start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')}"


def booking_summary(booking: Booking, studio: Studio, resource: Resource) -> str:
    tz = resource.timezone or studio.timezone
    when = format_interval_local(booking.starts_at, booking.ends_at, tz)
    duration = int((booking.ends_at - booking.starts_at).total_seconds() // 60) or 60
    shoot = shoot_minutes(resource, duration)
    buffer = int(resource.buffer_min or 0)
    studio_hour = ""
    if buffer:
        studio_hour = f"\n⏱ Аренда {shoot} мин + {buffer} мин пауза"
    price_line = ""
    if booking.quoted_price_rub:
        prepay = booking.prepay_amount_rub or booking.quoted_price_rub
        price_line = f"\n💳 {booking.quoted_price_rub} ₽, предоплата {prepay} ₽"
    status_line = ""
    if booking.status == STATUS_HOLD:
        until = ""
        if booking.hold_expires_at:
            until = f" до {format_slot_local(booking.hold_expires_at, tz)}"
        status_line = f"\n⏳ Не оплачено{until}"
    elif booking.status == STATUS_PAID:
        status_line = "\n✅ Оплачено"
    elif booking.status == "pending":
        status_line = "\n📨 Заявка, ждёт владельца"
    elif booking.status == "declined":
        status_line = "\n❌ Отклонена"
    return (
        f"🏠 <b>{escape(studio.name)}</b>\n"
        f"🎬 {escape(resource.name)}\n"
        f"🕒 {when}{studio_hour}\n"
        f"👤 {escape(booking.client_name)}\n"
        f"📞 {escape(booking.client_phone or '—')}"
        f"{price_line}"
        f"{status_line}"
    )
