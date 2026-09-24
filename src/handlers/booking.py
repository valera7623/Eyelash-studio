from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.booking import OPEN_CLIENT_STATUSES, STATUS_HOLD, STATUS_PAID, Booking
from src.database.models.studio import Resource, Studio
from src.database.models.user import User
from src.keyboards.inline import (
    client_booking_keyboard,
    confirm_cancel_keyboard,
    consent_keyboard,
    date_keyboard,
    owner_hold_keyboard,
    owner_request_keyboard,
    resource_keyboard,
    slot_keyboard,
)
from src.services.cancellations import cancel_booking, cancel_rules_text, preview_cancel
from src.services.consents import consent_text, record_consent
from src.services.formatters import booking_summary, format_slot_local
from src.services.slots import (
    available_slots,
    classify_interval,
    combine_local,
    create_hold,
    create_request,
    parse_hhmm,
    quote_price_rub,
)
from src.services.studios import get_studio_by_slug, list_active_resources
from src.services.tariffs import can_create_booking
from src.states.booking import BookingStates
from src.utils.qr_code import studio_start_link
from src.utils.validators import validate_name, validate_phone

router = Router()
_NOT_COMMAND = F.text & ~F.text.startswith("/")
BOOKING_HORIZON_DAYS = 14


def _days_ahead(tz_name: str, n: int = BOOKING_HORIZON_DAYS) -> list[date]:
    today = datetime.now(ZoneInfo(tz_name)).date()
    return [today + timedelta(days=i) for i in range(n)]


async def _set_message(message: Message, text: str, reply_markup=None, *, edit: bool = False) -> None:
    if edit:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=reply_markup)


async def _ask_dates(message: Message, studio: Studio, resource: Resource, *, edit: bool = False) -> None:
    tz_name = resource.timezone or studio.timezone
    await _set_message(
        message,
        f"📅 <b>{studio.name}</b>\nЗал: {resource.name}\n"
        "Выберите дату. Если окна нет — напишите своё время заявкой.",
        date_keyboard(resource.id, _days_ahead(tz_name), tz_name),
        edit=edit,
    )


async def start_public_booking(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    payload: str,
) -> bool:
    slug = payload
    if payload.startswith("book_"):
        slug = payload[5:]
    studio = await get_studio_by_slug(session, slug)
    if studio is None:
        return False
    resources = await list_active_resources(session, studio.id)
    if not resources:
        await message.answer("У студии пока нет зала для записи.")
        return True
    ok, reason = await can_create_booking(session, studio)
    if not ok:
        await message.answer(reason)
        return True
    await state.clear()
    await state.update_data(studio_id=studio.id)
    if len(resources) == 1:
        resource = resources[0]
        await state.update_data(resource_id=resource.id)
        await _ask_dates(message, studio, resource)
        return True
    await message.answer(
        f"📅 <b>{studio.name}</b>\nВыберите зал:",
        reply_markup=resource_keyboard(studio.id, resources),
    )
    return True


@router.callback_query(F.data.startswith("bk:r:"))
async def cb_pick_resource(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, _, studio_id_s, resource_id_s = callback.data.split(":")
    studio = await session.get(Studio, int(studio_id_s))
    resource = await session.get(Resource, int(resource_id_s))
    if not studio or not resource or resource.studio_id != studio.id:
        await callback.answer("Зал недоступен", show_alert=True)
        return
    await state.update_data(studio_id=studio.id, resource_id=resource.id)
    await _ask_dates(callback.message, studio, resource, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("bk:d:"))
async def cb_pick_date(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    _, _, resource_id_s, day_s = callback.data.split(":", 3)
    resource = await session.get(Resource, int(resource_id_s))
    studio = await session.get(Studio, resource.studio_id) if resource else None
    if not studio or not resource:
        await callback.answer("Студия недоступна", show_alert=True)
        return
    day = date.fromisoformat(day_s)
    duration_min = int(resource.duration_min or 60)
    await state.update_data(
        studio_id=studio.id,
        resource_id=resource.id,
        day=day_s,
        duration_min=duration_min,
    )
    await _show_slots(callback.message, session, state, resource, day, duration_min, edit=True)
    await callback.answer()


async def _show_slots(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    resource: Resource,
    day,
    duration_min: int,
    *,
    edit: bool = False,
) -> None:
    slots = await available_slots(session, resource, day, duration_min)
    await state.update_data(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        day=day.isoformat(),
        duration_min=duration_min,
    )
    await state.set_state(BookingStates.waiting_time)
    if slots:
        text = (
            f"Время визита на {day.strftime('%d.%m.%Y')} "
            f"(длительность {duration_min} мин).\n"
            "Нажмите время или напишите своё, например 19:30 — вне окна это будет заявка."
        )
        await _set_message(
            message,
            text,
            slot_keyboard(resource.id, slots, resource.timezone, duration_min),
            edit=edit,
        )
        return
    await _set_message(
        message,
        f"На {day.strftime('%d.%m.%Y')} открытых окон нет.\n"
        "Напишите желаемое время визита, например 14:30 — мастер подтвердит заявку.",
        edit=edit,
    )


@router.callback_query(F.data.startswith("bk:n:"))
async def cb_pick_duration(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    """Старые кнопки длительности: сразу к выбору времени с длительностью зала."""
    _, _, resource_id_s, day_s, _dur_s = callback.data.split(":")
    resource = await session.get(Resource, int(resource_id_s))
    if resource is None:
        await callback.answer("Слот недоступен", show_alert=True)
        return
    day = date.fromisoformat(day_s)
    duration_min = int(resource.duration_min or 60)
    await state.update_data(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        day=day_s,
        duration_min=duration_min,
    )
    await _show_slots(callback.message, session, state, resource, day, duration_min, edit=True)
    await callback.answer()


@router.callback_query(F.data == "bk:back")
async def cb_back_dates(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    resource = await session.get(Resource, data["resource_id"]) if data.get("resource_id") else None
    studio = await session.get(Studio, data["studio_id"]) if data.get("studio_id") else None
    if not studio or not resource:
        await callback.answer("Сессия устарела", show_alert=True)
        return
    await _ask_dates(callback.message, studio, resource, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("bk:s:"))
async def cb_pick_slot(callback: CallbackQuery, session: AsyncSession, state: FSMContext):
    parts = callback.data.split(":")
    resource_id_s, ts_s = parts[2], parts[3]
    resource = await session.get(Resource, int(resource_id_s))
    if resource is None:
        await callback.answer("Слот недоступен", show_alert=True)
        return
    duration_min = int(resource.duration_min or 60)
    starts_at = datetime.fromtimestamp(int(ts_s), tz=timezone.utc)
    await state.update_data(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        starts_ts=int(ts_s),
        duration_min=duration_min,
    )
    when = format_slot_local(starts_at, resource.timezone)
    kind = await classify_interval(
        session, resource, starts_at, starts_at + timedelta(minutes=duration_min)
    )
    if kind == "past":
        await callback.answer("Это время уже прошло", show_alert=True)
        return
    if kind == "taken":
        await callback.answer("Это время уже занято", show_alert=True)
        return
    hint = (
        "Окно открыто — запись закрепится сразу."
        if kind == "instant"
        else "Окна нет — заявка уйдёт мастеру."
    )
    await state.set_state(BookingStates.waiting_name)
    await _set_message(
        callback.message,
        f"Визит {when}, {duration_min} мин. {hint}\nКак вас зовут?",
        edit=True,
    )
    await callback.answer()


@router.message(BookingStates.waiting_time, _NOT_COMMAND)
async def booking_time_text(message: Message, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    clock = parse_hhmm(message.text or "")
    resource = await session.get(Resource, data.get("resource_id")) if data.get("resource_id") else None
    if clock is None or resource is None or "day" not in data:
        await message.answer("Напишите время как 14:30 или выберите кнопку.")
        return
    day = date.fromisoformat(data["day"])
    duration_min = int(resource.duration_min or 60)
    starts_at = combine_local(day, clock, ZoneInfo(resource.timezone or "Europe/Moscow"))
    ends_at = starts_at + timedelta(minutes=duration_min)
    kind = await classify_interval(session, resource, starts_at, ends_at)
    if kind == "past":
        await message.answer("Это время уже прошло. Выберите другое.")
        return
    if kind == "taken":
        await message.answer("Это время уже занято. Выберите другое.")
        return
    await state.update_data(
        resource_id=resource.id,
        studio_id=resource.studio_id,
        starts_ts=int(starts_at.timestamp()),
        duration_min=duration_min,
    )
    when = format_slot_local(starts_at, resource.timezone)
    hint = (
        "Окно открыто — запись закрепится сразу."
        if kind == "instant"
        else "Окна нет — заявка уйдёт мастеру."
    )
    await state.set_state(BookingStates.waiting_name)
    await message.answer(f"Визит {when}, {duration_min} мин. {hint}\nКак вас зовут?")


@router.message(BookingStates.waiting_name, _NOT_COMMAND)
async def booking_name(message: Message, state: FSMContext):
    ok, value = validate_name(message.text or "")
    if not ok:
        await message.answer(value)
        return
    await state.update_data(client_name=value)
    await state.set_state(BookingStates.waiting_phone)
    await message.answer("Номер телефона (например +79991234567)")


@router.message(BookingStates.waiting_phone, _NOT_COMMAND)
async def booking_phone(message: Message, state: FSMContext):
    ok, value = validate_phone(message.text or "")
    if not ok:
        await message.answer(value)
        return
    await state.update_data(client_phone=value)
    await state.set_state(BookingStates.waiting_consent)
    await message.answer(consent_text(), reply_markup=consent_keyboard())


@router.callback_query(F.data == "bk:cancel")
async def cb_booking_abort(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Запись отменена.")
    await callback.answer()


@router.callback_query(F.data == "bk:consent", BookingStates.waiting_consent)
async def cb_consent(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
):
    data = await state.get_data()
    resource = await session.get(Resource, data.get("resource_id"))
    studio = await session.get(Studio, data.get("studio_id")) if data.get("studio_id") else None
    if not resource or not studio:
        await state.clear()
        await callback.answer("Сессия устарела", show_alert=True)
        return
    ok, reason = await can_create_booking(session, studio)
    if not ok:
        await callback.message.answer(reason)
        await state.clear()
        await callback.answer()
        return
    starts_at = datetime.fromtimestamp(int(data["starts_ts"]), tz=timezone.utc)
    duration_min = int(data.get("duration_min") or resource.duration_min or 60)
    ends_at = starts_at + timedelta(minutes=duration_min)
    price = quote_price_rub(resource, starts_at, duration_min)
    await record_consent(session, user, studio_id=studio.id)
    kind = await classify_interval(session, resource, starts_at, ends_at)
    if kind in {"past", "taken"}:
        await callback.message.answer("Это время уже нельзя занять. Выберите другое.")
        await state.clear()
        await callback.answer()
        return
    if kind == "request":
        booking = await create_request(
            session,
            resource=resource,
            starts_at=starts_at,
            ends_at=ends_at,
            client_telegram_id=user.telegram_id,
            client_name=data["client_name"],
            client_phone=data.get("client_phone"),
            client_user_id=user.id,
            quoted_price_rub=price,
            prepay_amount_rub=0,
        )
        await state.clear()
        if booking is None:
            await callback.message.answer("Это время уже занято. Выберите другое.")
            await callback.answer()
            return
        summary = booking_summary(booking, studio, resource)
        await callback.message.answer(
            "Заявка отправлена. Мастер подтвердит время сообщением.\n\n" + summary
        )
        await _notify_owner(bot, studio, booking, resource, paid=False, request=True)
        await callback.answer()
        return
    booking = await create_hold(
        session,
        resource=resource,
        starts_at=starts_at,
        ends_at=ends_at,
        client_telegram_id=user.telegram_id,
        client_name=data["client_name"],
        client_phone=data.get("client_phone"),
        client_user_id=user.id,
        quoted_price_rub=price,
        prepay_amount_rub=0,
        studio=studio,
    )
    await state.clear()
    if booking is None:
        link = studio_start_link(studio.slug)
        await callback.message.answer(
            "Это время только что заняли. Выберите другое:\n" + link
        )
        await callback.answer()
        return
    await _confirm_booking_without_payment(callback.message, session, bot, booking, studio, resource)
    await callback.answer()


async def _confirm_booking_without_payment(
    message: Message,
    session: AsyncSession,
    bot: Bot,
    booking,
    studio: Studio,
    resource: Resource,
) -> None:
    """Оплата у мастера напрямую — в боте только запись."""
    booking.status = STATUS_PAID
    booking.hold_expires_at = None
    booking.prepay_amount_rub = 0
    await session.commit()
    summary = booking_summary(booking, studio, resource)
    await message.answer(
        "✅ Запись подтверждена. Оплата — у мастера.\n\n" + summary,
        reply_markup=client_booking_keyboard(booking.id),
    )
    await _notify_owner(bot, studio, booking, resource, paid=True)


async def _notify_owner(
    bot: Bot,
    studio: Studio,
    booking,
    resource: Resource,
    *,
    paid: bool,
    request: bool = False,
) -> None:
    text = booking_summary(booking, studio, resource)
    markup = None
    if request and booking.status == STATUS_PENDING:
        markup = owner_request_keyboard(booking.id)
        text = "Новая заявка (вне открытого окна)\n\n" + text
    elif not paid and booking.status == STATUS_HOLD:
        markup = owner_hold_keyboard(booking.id)
    try:
        await bot.send_message(studio.owner_telegram_id, text, reply_markup=markup)
    except Exception:
        pass


@router.callback_query(F.data.startswith("bk:pay:"))
async def cb_retry_pay(callback: CallbackQuery):
    await callback.answer("Оплата в боте отключена — платите мастеру напрямую", show_alert=True)


@router.message(Command("my"))
async def cmd_my(message: Message, session: AsyncSession, user: User):
    stmt = (
        select(Booking)
        .where(
            Booking.client_telegram_id == user.telegram_id,
            Booking.status.in_(OPEN_CLIENT_STATUSES),
        )
        .order_by(Booking.starts_at.asc())
        .limit(10)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        await message.answer("Активных записей нет.")
        return
    for booking in rows:
        studio = await session.get(Studio, booking.studio_id)
        resource = await session.get(Resource, booking.resource_id)
        if not studio or not resource:
            continue
        await message.answer(
            booking_summary(booking, studio, resource),
            reply_markup=client_booking_keyboard(booking.id),
        )


@router.callback_query(F.data.startswith("bk:cxok:"))
async def cb_client_cancel_ok(callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    booking_id = int(callback.data.split(":")[2])
    booking = await session.get(Booking, booking_id)
    if not booking or booking.client_telegram_id != user.telegram_id:
        await callback.answer("Не найдено", show_alert=True)
        return
    studio = await session.get(Studio, booking.studio_id)
    resource = await session.get(Resource, booking.resource_id)
    if not studio:
        await callback.answer("Студия недоступна", show_alert=True)
        return
    result = await cancel_booking(session, booking, studio, by="client")
    await callback.message.answer(result.message)
    if result.ok and studio.owner_telegram_id:
        try:
            extra = booking_summary(booking, studio, resource) if resource else ""
            await bot.send_message(studio.owner_telegram_id, f"Клиент отменил бронь.\n{extra}")
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("bk:cxno:"))
async def cb_client_cancel_no(callback: CallbackQuery):
    await callback.answer("Отмена не выполнена")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data.startswith("bk:cx:"))
async def cb_client_cancel(callback: CallbackQuery, session: AsyncSession, user: User):
    booking_id = int(callback.data.split(":")[2])
    booking = await session.get(Booking, booking_id)
    if not booking or booking.client_telegram_id != user.telegram_id:
        await callback.answer("Не найдено", show_alert=True)
        return
    studio = await session.get(Studio, booking.studio_id)
    if not studio:
        await callback.answer("Студия недоступна", show_alert=True)
        return
    _, _, text = await preview_cancel(session, booking, studio, by="client")
    await callback.message.answer(text, reply_markup=confirm_cancel_keyboard(booking.id))
    await callback.answer()


@router.message(Command("rules"))
async def cmd_rules(message: Message):
    await message.answer(cancel_rules_text()[:3500])
