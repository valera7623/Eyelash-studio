from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from src.database.models.booking import STATUS_HOLD, STATUS_PAID, Booking
from src.database.models.studio import Resource, Studio


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
    visit_at = format_slot_local(booking.starts_at, tz)
    duration = int((booking.ends_at - booking.starts_at).total_seconds() // 60) or 60
    price_line = ""
    if booking.quoted_price_rub:
        price_line = f"\n💳 {booking.quoted_price_rub} ₽ · оплата у мастера"
    status_line = ""
    if booking.status == STATUS_HOLD:
        status_line = "\n⏳ Запись ожидает подтверждения"
    elif booking.status == STATUS_PAID:
        status_line = "\n✅ Запись подтверждена"
    elif booking.status == "pending":
        status_line = "\n📨 Заявка, ждёт мастера"
    elif booking.status == "declined":
        status_line = "\n❌ Отклонена"
    return (
        f"🏠 <b>{escape(studio.name)}</b>\n"
        f"🎬 {escape(resource.name)}\n"
        f"🕒 Визит {visit_at}\n"
        f"⏱ Длительность {duration} мин\n"
        f"👤 {escape(booking.client_name)}\n"
        f"📞 {escape(booking.client_phone or '—')}"
        f"{price_line}"
        f"{status_line}"
    )
