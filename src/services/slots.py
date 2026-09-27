from __future__ import annotations

import asyncio
import math
import re
import threading
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.base import utcnow
from src.database.models.booking import (
    ACTIVE_STATUSES,
    STATUS_BLOCKED,
    STATUS_DECLINED,
    STATUS_HOLD,
    STATUS_PAID,
    STATUS_PENDING,
    Booking,
)
from src.database.models.studio import Resource, Studio
from src.database.models.window import Window

# Ключ: (id(event_loop), resource_id) — иначе pytest/новый loop ломает Lock.
_resource_locks: dict[tuple[int, int], asyncio.Lock] = {}
_resource_lock_guards: dict[int, asyncio.Lock] = {}
_locks_meta = threading.Lock()


async def _lock_for_resource(resource_id: int) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    with _locks_meta:
        guard = _resource_lock_guards.get(loop_id)
        if guard is None:
            guard = asyncio.Lock()
            _resource_lock_guards[loop_id] = guard
    async with guard:
        with _locks_meta:
            key = (loop_id, resource_id)
            lock = _resource_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                _resource_locks[key] = lock
            return lock


class Slot:
    __slots__ = ("starts_at", "ends_at", "duration_min", "price_rub")

    def __init__(
        self,
        starts_at: datetime,
        ends_at: datetime,
        duration_min: int = 60,
        price_rub: int = 0,
    ):
        self.starts_at = starts_at
        self.ends_at = ends_at
        self.duration_min = duration_min
        self.price_rub = price_rub


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_hours(text: str) -> tuple[time, time] | None:
    raw = text.strip().replace("–", "-").replace("—", "-")
    parts = re.split(r"[\s-]+", raw)
    if len(parts) < 2:
        return None
    try:
        start_h, start_m = _parse_hm(parts[0])
        end_h, end_m = _parse_hm(parts[1])
        start = time(start_h, start_m)
        end = time(end_h, end_m)
    except ValueError:
        return None
    if start >= end:
        return None
    return start, end


def parse_block_interval(text: str, *, tz_name: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """«01.09.2026 14:00 16:00» или «01.09 14:00 16:00» (текущий год)."""
    raw = (text or "").strip().replace("–", " ").replace("—", " ")
    bits = raw.split()
    if len(bits) < 3:
        return None
    date_s, start_s, end_s = bits[0], bits[1], bits[2]
    tz = ZoneInfo(tz_name)
    today = (now or datetime.now(tz)).date()
    day = None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%d.%m", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(date_s, fmt)
            if fmt == "%d.%m":
                parsed = parsed.replace(year=today.year)
            day = parsed.date()
            break
        except ValueError:
            continue
    if day is None:
        return None
    times = parse_hours(f"{start_s} {end_s}")
    if times is None:
        return None
    start_t, end_t = times
    start = datetime.combine(day, start_t, tzinfo=tz)
    end = datetime.combine(day, end_t, tzinfo=tz)
    if end <= start:
        return None
    return _as_utc(start), _as_utc(end)


def _parse_hm(value: str) -> tuple[int, int]:
    bits = value.replace(".", ":").split(":")
    hour = int(bits[0])
    minute = int(bits[1]) if len(bits) > 1 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("bad time")
    return hour, minute


def clamp_hold_ttl(minutes: int | None) -> int:
    fallback = settings.HOLD_TTL_MINUTES
    value = int(minutes or fallback)
    return max(settings.HOLD_TTL_MIN, min(settings.HOLD_TTL_MAX, value))


def buffer_delta(resource: Resource) -> timedelta:
    return timedelta(minutes=int(resource.buffer_min or 0))


def occupancy_end(ends_at: datetime, resource: Resource) -> datetime:
    return _as_utc(ends_at) + buffer_delta(resource)


def intervals_overlap(
    a_start: datetime,
    a_end: datetime,
    b_start: datetime,
    b_end: datetime,
    *,
    buffer: timedelta,
) -> bool:
    a_occ = _as_utc(a_end) + buffer
    b_occ = _as_utc(b_end) + buffer
    return _as_utc(a_start) < b_occ and _as_utc(b_start) < a_occ


def allowed_durations(resource: Resource) -> list[int]:
    step = resource.slot_step_min or 60
    if step not in (30, 60):
        step = 60
    min_d = resource.min_duration_min or 60
    if min_d not in (30, 60, 90, 120, 180, 240):
        min_d = 60
    max_d = 240
    durations: list[int] = []
    value = min_d
    while value <= max_d:
        durations.append(value)
        value += step
    if min_d > 60 and 60 not in durations:
        durations.insert(0, 60)
    return durations or [60]


def _is_night_local(local: datetime, resource: Resource) -> bool:
    night_start = resource.night_start or time(22, 0)
    clock = local.time()
    return clock >= night_start or clock < time(10, 0)


def hourly_rate_rub(resource: Resource, starts_at: datetime) -> int:
    tz = ZoneInfo(resource.timezone or "Europe/Moscow")
    local = _as_utc(starts_at).astimezone(tz)
    base = int(resource.price_rub or 0)
    if _is_night_local(local, resource) and int(resource.night_price_rub or 0) > 0:
        return int(resource.night_price_rub)
    if local.weekday() >= 5 and int(resource.weekend_price_rub or 0) > 0:
        return int(resource.weekend_price_rub)
    return base


def quote_price_rub(resource: Resource, starts_at: datetime, duration_min: int) -> int:
    rate = hourly_rate_rub(resource, starts_at)
    hours = max(duration_min, 1) / 60.0
    min_d = resource.min_duration_min or 60
    if duration_min < min_d:
        markup = (resource.hour_markup_percent or 50) / 100.0
        rate = int(round(rate * (1 + markup)))
    return int(math.ceil(rate * hours))


def prepay_amount_rub(studio: Studio, price: int) -> int:
    if price <= 0:
        return 0
    percent = int(studio.prepay_percent or 100)
    if percent >= 100:
        return price
    if percent <= 0:
        return 0
    return int(math.ceil(price * percent / 100.0))


def shoot_minutes(resource: Resource, duration_min: int) -> int:
    buffer = int(resource.buffer_min or 0)
    if duration_min >= 60 and buffer and buffer < duration_min:
        return duration_min - buffer
    return duration_min


async def expire_holds(session: AsyncSession) -> list[Booking]:
    now = utcnow()
    stmt = select(Booking).where(
        Booking.status == STATUS_HOLD,
        Booking.hold_expires_at.is_not(None),
        Booking.hold_expires_at <= now,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    for booking in rows:
        booking.status = "cancelled"
        booking.cancel_reason = "hold_expired"
    if rows:
        await session.commit()
    return rows


async def occupied_intervals(
    session: AsyncSession,
    resource_id: int,
    day_start: datetime,
    day_end: datetime,
) -> list[tuple[datetime, datetime]]:
    stmt = select(Booking.starts_at, Booking.ends_at).where(
        Booking.resource_id == resource_id,
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at < day_end,
        Booking.ends_at > day_start,
    )
    rows = (await session.execute(stmt)).all()
    return [(_as_utc(start), _as_utc(end)) for start, end in rows]


async def has_overlap(
    session: AsyncSession,
    *,
    resource: Resource,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: int | None = None,
) -> bool:
    starts_at = _as_utc(starts_at)
    ends_at = _as_utc(ends_at)
    buffer = buffer_delta(resource)
    occ_end = ends_at + buffer
    stmt = select(Booking).where(
        Booking.resource_id == resource.id,
        Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at < occ_end,
    )
    if exclude_id is not None:
        stmt = stmt.where(Booking.id != exclude_id)
    rows = (await session.execute(stmt)).scalars().all()
    for booking in rows:
        if intervals_overlap(starts_at, ends_at, booking.starts_at, booking.ends_at, buffer=buffer):
            return True
    return False


def align_up(moment: datetime, step_min: int, tz: ZoneInfo) -> datetime:
    if step_min <= 0:
        raise ValueError("step_min")
    local_exact = _as_utc(moment).astimezone(tz)
    truncated = local_exact.replace(second=0, microsecond=0)
    minutes = truncated.hour * 60 + truncated.minute
    remainder = minutes % step_min
    if remainder:
        truncated += timedelta(minutes=step_min - remainder)
    elif truncated < local_exact:
        truncated += timedelta(minutes=step_min)
    return truncated.astimezone(timezone.utc)


def parse_hhmm(value: str) -> time | None:
    raw = (value or "").strip().replace(".", ":")
    if not raw:
        return None
    if raw.isdigit() and len(raw) <= 2:
        hour, minute = int(raw), 0
    elif raw.isdigit() and len(raw) == 4:
        hour, minute = int(raw[:2]), int(raw[2:])
    else:
        bits = raw.split(":")
        if len(bits) != 2:
            return None
        try:
            hour, minute = int(bits[0]), int(bits[1])
        except ValueError:
            return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def combine_local(day, clock: time, tz: ZoneInfo) -> datetime:
    return _as_utc(datetime.combine(day, clock, tzinfo=tz))


def covered_by_windows(
    start: datetime,
    end: datetime,
    windows: list[tuple[datetime, datetime]],
) -> bool:
    start, end = _as_utc(start), _as_utc(end)
    return any(_as_utc(win_start) <= start and end <= _as_utc(win_end) for win_start, win_end in windows)


def generate_slots_for_day(
    resource: Resource,
    day,
    duration_min: int | None = None,
    windows: list[tuple[datetime, datetime]] | None = None,
) -> list[Slot]:
    """Слоты только внутри открытых окон. Недельные часы не используются."""
    tz = ZoneInfo(resource.timezone or "Europe/Moscow")
    duration_min = int(duration_min or resource.duration_min or 60)
    step_min = int(resource.slot_step_min or resource.duration_min or 60)
    duration = timedelta(minutes=duration_min)
    found: dict[datetime, Slot] = {}
    for raw_start, raw_end in windows or []:
        window_start, window_end = _as_utc(raw_start), _as_utc(raw_end)
        cursor = align_up(window_start, step_min, tz)
        while cursor + duration <= window_end:
            local_day = cursor.astimezone(tz).date()
            if local_day > day:
                break
            if local_day == day and cursor >= window_start:
                start_utc = cursor
                end_utc = cursor + duration
                found[start_utc] = Slot(
                    start_utc,
                    end_utc,
                    duration_min=duration_min,
                    price_rub=quote_price_rub(resource, start_utc, duration_min),
                )
            cursor += timedelta(minutes=step_min)
    return [found[key] for key in sorted(found)]


async def list_windows_for_resource(
    session: AsyncSession,
    resource_id: int,
    *,
    day=None,
    tz_name: str | None = None,
) -> list[tuple[datetime, datetime]]:
    stmt = select(Window).where(Window.resource_id == resource_id)
    if day is not None:
        tz = ZoneInfo(tz_name or "Europe/Moscow")
        day_start = _as_utc(datetime.combine(day, time.min, tzinfo=tz))
        day_end = _as_utc(datetime.combine(day, time.min, tzinfo=tz) + timedelta(days=1))
        stmt = stmt.where(Window.starts_at < day_end, Window.ends_at > day_start)
    rows = list((await session.execute(stmt.order_by(Window.starts_at))).scalars().all())
    return [(_as_utc(row.starts_at), _as_utc(row.ends_at)) for row in rows]


async def list_open_windows(
    session: AsyncSession,
    resource_id: int,
    *,
    now: datetime | None = None,
    limit: int = 20,
) -> list[Window]:
    moment = _as_utc(now or utcnow())
    stmt = (
        select(Window)
        .where(Window.resource_id == resource_id, Window.ends_at > moment)
        .order_by(Window.starts_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def add_window(session: AsyncSession, resource: Resource, start: datetime, end: datetime) -> Window:
    row = Window(resource_id=resource.id, starts_at=_as_utc(start), ends_at=_as_utc(end))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_window(session: AsyncSession, window_id: int, *, resource_id: int | None = None) -> bool:
    row = await session.get(Window, window_id)
    if row is None:
        return False
    if resource_id is not None and row.resource_id != resource_id:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def available_slots(
    session: AsyncSession,
    resource: Resource,
    day,
    duration_min: int | None = None,
) -> list[Slot]:
    await expire_holds(session)
    duration_min = int(duration_min or resource.duration_min or 60)
    tz = ZoneInfo(resource.timezone or "Europe/Moscow")
    day_start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    day_end = datetime.combine(day, time.max, tzinfo=tz).astimezone(timezone.utc)
    taken = await occupied_intervals(session, resource.id, day_start, day_end)
    windows = await list_windows_for_resource(session, resource.id, day=day, tz_name=resource.timezone)
    buffer = buffer_delta(resource)
    now = utcnow()
    result: list[Slot] = []
    for slot in generate_slots_for_day(resource, day, duration_min, windows):
        if slot.starts_at <= now:
            continue
        if any(
            intervals_overlap(slot.starts_at, slot.ends_at, occ_s, occ_e, buffer=buffer)
            for occ_s, occ_e in taken
        ):
            continue
        result.append(slot)
    return result


async def classify_interval(
    session: AsyncSession,
    resource: Resource,
    start: datetime,
    end: datetime,
) -> str:
    start, end = _as_utc(start), _as_utc(end)
    if start <= utcnow():
        return "past"
    if await has_overlap(session, resource=resource, starts_at=start, ends_at=end):
        return "taken"
    windows = await list_windows_for_resource(session, resource.id)
    if covered_by_windows(start, end, windows):
        return "instant"
    return "request"


async def create_hold(
    session: AsyncSession,
    *,
    resource: Resource,
    starts_at: datetime,
    ends_at: datetime,
    client_telegram_id: int,
    client_name: str,
    client_phone: str | None,
    client_user_id: int | None,
    quoted_price_rub: int = 0,
    prepay_amount_rub: int = 0,
    studio: Studio | None = None,
) -> Booking | None:
    lock = await _lock_for_resource(resource.id)
    async with lock:
        last_exc: OperationalError | None = None
        for attempt in range(5):
            try:
                return await _create_hold_locked(
                    session,
                    resource=resource,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    client_telegram_id=client_telegram_id,
                    client_name=client_name,
                    client_phone=client_phone,
                    client_user_id=client_user_id,
                    quoted_price_rub=quoted_price_rub,
                    prepay_amount_rub=prepay_amount_rub,
                    studio=studio,
                )
            except OperationalError as exc:
                last_exc = exc
                if session.in_transaction():
                    await session.rollback()
                await asyncio.sleep(0.05 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        return None


async def _create_hold_locked(
    session: AsyncSession,
    *,
    resource: Resource,
    starts_at: datetime,
    ends_at: datetime,
    client_telegram_id: int,
    client_name: str,
    client_phone: str | None,
    client_user_id: int | None,
    quoted_price_rub: int,
    prepay_amount_rub: int,
    studio: Studio | None,
) -> Booking | None:
    await expire_holds(session)
    starts_at = _as_utc(starts_at)
    ends_at = _as_utc(ends_at)
    if await has_overlap(session, resource=resource, starts_at=starts_at, ends_at=ends_at):
        return None
    if studio is None:
        studio = await session.get(Studio, resource.studio_id)
    ttl = clamp_hold_ttl(studio.hold_ttl_minutes if studio else None)
    booking = Booking(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        client_user_id=client_user_id,
        client_telegram_id=client_telegram_id,
        client_name=client_name,
        client_phone=client_phone,
        starts_at=starts_at,
        ends_at=ends_at,
        status=STATUS_HOLD,
        hold_expires_at=utcnow() + timedelta(minutes=ttl),
        quoted_price_rub=quoted_price_rub,
        prepay_amount_rub=prepay_amount_rub,
    )
    session.add(booking)
    try:
        await session.flush()
        if await has_overlap(
            session,
            resource=resource,
            starts_at=starts_at,
            ends_at=ends_at,
            exclude_id=booking.id,
        ):
            await session.rollback()
            return None
        await session.commit()
        await session.refresh(booking)
        return booking
    except IntegrityError:
        await session.rollback()
        return None


async def create_block(
    session: AsyncSession,
    *,
    resource: Resource,
    starts_at: datetime,
    ends_at: datetime,
    owner_telegram_id: int,
    note: str = "Блок",
) -> Booking | None:
    lock = await _lock_for_resource(resource.id)
    async with lock:
        return await _create_block_locked(
            session,
            resource=resource,
            starts_at=starts_at,
            ends_at=ends_at,
            owner_telegram_id=owner_telegram_id,
            note=note,
        )


async def _create_block_locked(
    session: AsyncSession,
    *,
    resource: Resource,
    starts_at: datetime,
    ends_at: datetime,
    owner_telegram_id: int,
    note: str,
) -> Booking | None:
    await expire_holds(session)
    starts_at = _as_utc(starts_at)
    ends_at = _as_utc(ends_at)
    if ends_at <= starts_at:
        return None
    if await has_overlap(session, resource=resource, starts_at=starts_at, ends_at=ends_at):
        return None
    booking = Booking(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        client_telegram_id=owner_telegram_id,
        client_name=note[:128],
        client_phone=None,
        starts_at=starts_at,
        ends_at=ends_at,
        status=STATUS_BLOCKED,
        hold_expires_at=None,
    )
    session.add(booking)
    try:
        await session.flush()
        if await has_overlap(
            session,
            resource=resource,
            starts_at=starts_at,
            ends_at=ends_at,
            exclude_id=booking.id,
        ):
            await session.rollback()
            return None
        await session.commit()
        await session.refresh(booking)
        return booking
    except IntegrityError:
        await session.rollback()
        return None


async def confirm_hold(session: AsyncSession, booking: Booking) -> bool:
    """Владелец принимает hold без кассы (нал / перевод)."""
    if booking.status != STATUS_HOLD:
        return False
    booking.status = STATUS_PAID
    booking.hold_expires_at = None
    await session.commit()
    return True


async def create_request(
    session: AsyncSession,
    *,
    resource: Resource,
    starts_at: datetime,
    ends_at: datetime,
    client_telegram_id: int,
    client_name: str,
    client_phone: str | None,
    client_user_id: int | None,
    quoted_price_rub: int = 0,
    prepay_amount_rub: int = 0,
) -> Booking | None:
    starts_at, ends_at = _as_utc(starts_at), _as_utc(ends_at)
    if ends_at <= starts_at or starts_at <= utcnow():
        return None
    if await has_overlap(session, resource=resource, starts_at=starts_at, ends_at=ends_at):
        return None
    booking = Booking(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        client_user_id=client_user_id,
        client_telegram_id=client_telegram_id,
        client_name=client_name,
        client_phone=client_phone,
        starts_at=starts_at,
        ends_at=ends_at,
        status=STATUS_PENDING,
        hold_expires_at=None,
        quoted_price_rub=quoted_price_rub,
        prepay_amount_rub=prepay_amount_rub,
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


async def confirm_request(session: AsyncSession, booking: Booking) -> Booking | None:
    if booking.status != STATUS_PENDING:
        return None
    resource = await session.get(Resource, booking.resource_id)
    if resource is None:
        return None
    if await has_overlap(session, resource=resource, starts_at=booking.starts_at, ends_at=booking.ends_at):
        return None
    studio = await session.get(Studio, booking.studio_id)
    prepay = int(booking.prepay_amount_rub or 0)
    if prepay <= 0:
        booking.status = STATUS_PAID
        booking.hold_expires_at = None
    else:
        booking.status = STATUS_HOLD
        ttl = clamp_hold_ttl(studio.hold_ttl_minutes if studio else None)
        booking.hold_expires_at = utcnow() + timedelta(minutes=ttl)
    await session.commit()
    await session.refresh(booking)
    return booking


async def decline_request(session: AsyncSession, booking: Booking) -> bool:
    if booking.status != STATUS_PENDING:
        return False
    booking.status = STATUS_DECLINED
    await session.commit()
    return True
