from __future__ import annotations

from datetime import datetime, time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models.booking import (
    STATUS_BLOCKED,
    STATUS_HOLD,
    STATUS_PAID,
    STATUS_PENDING,
    Booking,
)
from src.database.models.studio import TARIFF_STARTER, Resource, Studio
from src.database.models.user import User
from src.keyboards.inline import (
    bookings_keyboard,
    confirm_cancel_keyboard,
    grid_keyboard,
    home_keyboard,
    owner_cabinet_keyboard,
    owner_resource_pick_keyboard,
    pay_keyboard,
    rules_keyboard,
    slot_settings_keyboard,
    tariff_keyboard,
    windows_keyboard,
)
from src.services import payments as payment_svc
from src.services.cancellations import cancel_booking, cancel_rules_text, preview_cancel
from src.services.formatters import booking_summary, format_interval_local, format_slot_local
from src.services.ical import build_calendar, feed_url
from src.services.outreach import owner_cheat_sheet, owner_copy_pack, owner_next_steps
from src.services.slots import (
    add_window,
    confirm_hold,
    confirm_request,
    create_block,
    decline_request,
    delete_window,
    list_open_windows,
    parse_block_interval,
)
from src.services.studios import (
    get_owner_studio,
    get_primary_resource,
    list_active_resources,
    unique_slug,
)
from src.services.tariffs import can_add_resource, tariff_label
from src.states.booking import OwnerStates
from src.utils.qr_code import generate_booking_qr, resolve_bot_username

router = Router()
_NOT_COMMAND = F.text & ~F.text.startswith("/")


def _copy_slot_fields(primary: Resource | None) -> dict:
    if primary is None:
        return {
            "duration_min": 60,
            "slot_step_min": 60,
            "min_duration_min": 60,
            "buffer_min": 5,
            "hour_markup_percent": 50,
            "timezone": "Europe/Moscow",
            "work_start": time(10, 0),
            "work_end": time(22, 0),
            "price_rub": 0,
            "weekend_price_rub": 0,
            "night_price_rub": 0,
            "night_start": time(22, 0),
        }
    return {
        "duration_min": primary.duration_min,
        "slot_step_min": primary.slot_step_min,
        "min_duration_min": primary.min_duration_min,
        "buffer_min": primary.buffer_min,
        "hour_markup_percent": primary.hour_markup_percent,
        "timezone": primary.timezone,
        "work_start": primary.work_start,
        "work_end": primary.work_end,
        "weekdays": primary.weekdays,
        "price_rub": primary.price_rub,
        "weekend_price_rub": primary.weekend_price_rub,
        "night_price_rub": primary.night_price_rub,
        "night_start": primary.night_start,
    }


async def show_cabinet(message: Message, session: AsyncSession, user: User) -> None:
    studio = await get_owner_studio(session, user)
    if studio is None:
        await message.answer(
            "Студии ещё нет. Нажмите «Создать студию» или /studio.",
        )
        return
    resources = await list_active_resources(session, studio.id)
    res_lines = []
    for resource in resources:
        res_lines.append(
            f"• {resource.name}: {resource.price_rub} ₽/ч, "
            f"шаг {resource.slot_step_min} мин, буфер {resource.buffer_min} мин"
        )
    halls = "\n".join(res_lines) if res_lines else "—"
    text = (
        f"🏠 <b>{studio.name}</b>\n"
        f"Ссылка: t.me/{settings.BOT_USERNAME or '…'}?start={studio.slug}\n"
        f"Тариф: {tariff_label(studio.tariff)} · 1 зал\n\n"
        f"{halls}\n\n"
        "Жёсткого расписания нет: откройте окно на день — клиенты займут его сразу. "
        "Вне окна приходят заявки."
    )
    await message.answer(text, reply_markup=owner_cabinet_keyboard())


@router.message(Command("studio"))
async def cmd_studio(message: Message, session: AsyncSession, user: User, state: FSMContext):
    await state.clear()
    studio = await get_owner_studio(session, user)
    if studio is None:
        await message.answer("Как называется студия?")
        await state.set_state(OwnerStates.waiting_studio_name)
        return
    await show_cabinet(message, session, user)


@router.callback_query(F.data == "ow:new")
async def cb_new_studio(callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    if await get_owner_studio(session, user):
        await callback.answer("Студия уже создана")
        await show_cabinet(callback.message, session, user)
        return
    await state.set_state(OwnerStates.waiting_studio_name)
    await callback.message.answer("Как называется студия?")
    await callback.answer()


@router.callback_query(F.data == "ow:cab")
async def cb_cabinet(callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    await state.clear()
    await show_cabinet(callback.message, session, user)
    await callback.answer()


@router.message(OwnerStates.waiting_studio_name, _NOT_COMMAND)
async def owner_studio_name(
    message: Message,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    bot: Bot,
):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое.")
        return
    if await get_owner_studio(session, user):
        await state.clear()
        await show_cabinet(message, session, user)
        return
    slug = await unique_slug(session, name)
    studio = Studio(
        slug=slug,
        name=name,
        owner_id=user.id,
        owner_telegram_id=user.telegram_id,
        timezone="Europe/Moscow",
        resource_limit=1,
        hold_ttl_minutes=20,
        prepay_percent=100,
        cancel_free_hours=72,
        late_cancel_retain_percent=50,
    )
    session.add(studio)
    await session.flush()
    session.add(
        Resource(
            studio_id=studio.id,
            name="Зал",
            duration_min=60,
            slot_step_min=60,
            min_duration_min=60,
            buffer_min=5,
            timezone="Europe/Moscow",
            price_rub=0,
        )
    )
    await session.commit()
    await state.clear()
    await message.answer(
        f"Студия создана. Free: 1 зал, {settings.FREE_BOOKINGS_PER_MONTH} записей в месяц. "
        f"Старт {settings.TARIFF_STARTER_RUB} ₽ — без лимита.\n"
        "Откройте окно на нужный день — иначе клиенты будут оставлять заявки."
    )
    await message.answer(owner_next_steps())
    await _send_booking_link(message, bot, studio)
    await _send_outreach(message, bot, studio)
    await show_cabinet(message, session, user)


async def _deep_link(bot: Bot, studio: Studio) -> tuple[str, bytes]:
    username = await resolve_bot_username(bot, settings.BOT_USERNAME)
    return generate_booking_qr(username, studio.slug)


async def _send_booking_link(message: Message, bot: Bot, studio: Studio) -> None:
    deep_link, png = await _deep_link(bot, studio)
    photo = BufferedInputFile(png, filename=f"{studio.slug}.png")
    await message.answer_photo(
        photo,
        caption=(
            f"Ссылка записи для клиентов:\n<code>{deep_link}</code>\n\n"
            "Отправьте её в чат студии или напечатайте QR."
        ),
    )


async def _send_outreach(message: Message, bot: Bot, studio: Studio) -> None:
    deep_link, _ = await _deep_link(bot, studio)
    await message.answer(owner_copy_pack(studio, deep_link))


@router.callback_query(F.data == "ow:link")
async def cb_link(callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    await _send_booking_link(callback.message, bot, studio)
    await callback.answer()


@router.callback_query(F.data == "ow:txt")
async def cb_texts(callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    await _send_outreach(callback.message, bot, studio)
    await callback.answer()


@router.callback_query(F.data == "ow:guide")
async def cb_owner_guide(callback: CallbackQuery, session: AsyncSession, user: User):
    if not await get_owner_studio(session, user):
        await callback.answer("Нет студии", show_alert=True)
        return
    await callback.message.answer(
        owner_cheat_sheet(),
        reply_markup=owner_cabinet_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "ow:win")
async def cb_window_start(callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    studio = await get_owner_studio(session, user)
    resources = await list_active_resources(session, studio.id) if studio else []
    if not resources:
        await callback.answer("Нет зала", show_alert=True)
        return
    if len(resources) == 1:
        await state.update_data(window_resource_id=resources[0].id)
        await state.set_state(OwnerStates.waiting_window)
        await callback.message.answer(
            "Окно на один день, без повтора по неделям.\n"
            "Формат: <code>25.09 12:00 18:00</code>\n"
            "Клиенты займут это время сразу, если услуга помещается."
        )
        await callback.answer()
        return
    await callback.message.answer(
        "Для какого зала открыть окно?",
        reply_markup=owner_resource_pick_keyboard(resources, "ow:wr"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ow:wr:"))
async def cb_window_resource(callback: CallbackQuery, state: FSMContext):
    await state.update_data(window_resource_id=int(callback.data.split(":")[2]))
    await state.set_state(OwnerStates.waiting_window)
    await callback.message.answer("Формат: <code>25.09 12:00 18:00</code>")
    await callback.answer()


@router.message(OwnerStates.waiting_window, _NOT_COMMAND)
async def owner_window(message: Message, session: AsyncSession, user: User, state: FSMContext):
    data = await state.get_data()
    resource = await session.get(Resource, data.get("window_resource_id"))
    studio = await get_owner_studio(session, user)
    if not resource or not studio or resource.studio_id != studio.id:
        await message.answer("Зал не найден.")
        await state.clear()
        return
    parsed = parse_block_interval(message.text or "", tz_name=resource.timezone)
    if parsed is None:
        await message.answer("Не разобрала. Пример: 25.09 12:00-18:00")
        return
    start, end = parsed
    now = datetime.now(start.tzinfo)
    if end <= now:
        await message.answer("Это окно уже закончилось.")
        return
    if (end - start).total_seconds() < 30 * 60:
        await message.answer("Окно короче 30 минут.")
        return
    if (end - start).total_seconds() > 18 * 3600:
        await message.answer("Окно длиннее 18 часов — разбейте на части.")
        return
    await add_window(session, resource, start, end)
    await state.clear()
    await message.answer(
        "Окно открыто. Подходящие интервалы клиенты займут сразу, остальное — заявками.",
        reply_markup=owner_cabinet_keyboard(),
    )


@router.callback_query(F.data == "ow:wins")
async def cb_windows(callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    studio = await get_owner_studio(session, user)
    resources = await list_active_resources(session, studio.id) if studio else []
    if not resources:
        await callback.answer("Нет зала", show_alert=True)
        return
    await state.clear()
    rows = []
    for resource in resources:
        rows.extend(await list_open_windows(session, resource.id))
    if not rows:
        await callback.message.answer(
            "Открытых окон нет. Клиенты могут оставить заявку на своё время.",
            reply_markup=home_keyboard(),
        )
        await callback.answer()
        return
    tz = resources[0].timezone
    await callback.message.answer(
        "Крестик закрывает окно. Уже созданные брони остаются.",
        reply_markup=windows_keyboard(rows, tz),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ow:wd:"))
async def cb_close_window(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    removed = await delete_window(session, int(callback.data.split(":")[2]))
    await callback.answer("Окно закрыто" if removed else "Окно уже закрыто")
    resources = await list_active_resources(session, studio.id)
    rows = []
    for resource in resources:
        rows.extend(await list_open_windows(session, resource.id))
    if callback.message is None:
        return
    if rows:
        await callback.message.edit_reply_markup(reply_markup=windows_keyboard(rows, resources[0].timezone))
    else:
        await callback.message.edit_text("Открытых окон больше нет.", reply_markup=home_keyboard())


@router.callback_query(F.data == "ow:price")
async def cb_price(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OwnerStates.waiting_price_edit)
    await callback.message.answer("Новая цена часа в будни для всех залов, рубли")
    await callback.answer()


async def _set_all_prices(session, studio: Studio, field: str, value: int) -> None:
    resources = await list_active_resources(session, studio.id)
    for resource in resources:
        setattr(resource, field, value)
    await session.commit()


@router.message(OwnerStates.waiting_price_edit, _NOT_COMMAND)
async def owner_price_edit(message: Message, session: AsyncSession, user: User, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число.")
        return
    studio = await get_owner_studio(session, user)
    if not studio:
        await message.answer("Студия не найдена.")
        await state.clear()
        return
    await _set_all_prices(session, studio, "price_rub", int(raw))
    await state.clear()
    await message.answer("Цена будней обновлена для всех залов.")
    await show_cabinet(message, session, user)


@router.callback_query(F.data == "ow:grid")
async def cb_grid(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    resource = await get_primary_resource(session, studio.id) if studio else None
    if not resource:
        await callback.answer("Нет зала", show_alert=True)
        return
    text = (
        "Сетка: будни / выходные / ночь (с 22:00). 0 — как будни.\n"
        f"Будни: {resource.price_rub} ₽\n"
        f"Выходные: {resource.weekend_price_rub or 'как будни'}\n"
        f"Ночь: {resource.night_price_rub or 'как будни'}"
    )
    await callback.message.answer(text, reply_markup=grid_keyboard())
    await callback.answer()


@router.callback_query(F.data == "ow:wknd")
async def cb_weekend(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OwnerStates.waiting_weekend_price)
    await callback.message.answer("Цена часа в выходные для всех залов. 0 — как в будни.")
    await callback.answer()


@router.message(OwnerStates.waiting_weekend_price, _NOT_COMMAND)
async def owner_weekend_price(message: Message, session: AsyncSession, user: User, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число.")
        return
    studio = await get_owner_studio(session, user)
    if not studio:
        await state.clear()
        return
    await _set_all_prices(session, studio, "weekend_price_rub", int(raw))
    await state.clear()
    await message.answer("Цена выходных обновлена для всех залов.")
    await show_cabinet(message, session, user)


@router.callback_query(F.data == "ow:night")
async def cb_night(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OwnerStates.waiting_night_price)
    await callback.message.answer("Цена часа ночью (с 22:00) для всех залов. 0 — как дневная.")
    await callback.answer()


@router.message(OwnerStates.waiting_night_price, _NOT_COMMAND)
async def owner_night_price(message: Message, session: AsyncSession, user: User, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Введите число.")
        return
    studio = await get_owner_studio(session, user)
    if not studio:
        await state.clear()
        return
    await _set_all_prices(session, studio, "night_price_rub", int(raw))
    await state.clear()
    await message.answer("Ночная цена обновлена для всех залов.")
    await show_cabinet(message, session, user)


@router.callback_query(F.data == "ow:rules")
async def cb_rules(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    snippet = cancel_rules_text()
    await callback.message.answer(
        snippet[:1500] + "\n\nНастройки этой студии:",
        reply_markup=rules_keyboard(studio),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ow:hold:"))
async def cb_hold_ttl(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    studio.hold_ttl_minutes = int(callback.data.split(":")[2])
    await session.commit()
    await callback.message.answer(f"Окно оплаты: {studio.hold_ttl_minutes} мин.", reply_markup=rules_keyboard(studio))
    await callback.answer()


@router.callback_query(F.data.startswith("ow:prepay:"))
async def cb_prepay(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    studio.prepay_percent = int(callback.data.split(":")[2])
    await session.commit()
    await callback.message.answer(f"Предоплата: {studio.prepay_percent}%.", reply_markup=rules_keyboard(studio))
    await callback.answer()


@router.callback_query(F.data.startswith("ow:cxh:"))
async def cb_cancel_hours(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    studio.cancel_free_hours = int(callback.data.split(":")[2])
    await session.commit()
    await callback.message.answer(
        f"Бесплатная отмена за {studio.cancel_free_hours} ч.",
        reply_markup=rules_keyboard(studio),
    )
    await callback.answer()


@router.callback_query(F.data == "ow:slot")
async def cb_slot_settings(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    resource = await get_primary_resource(session, studio.id) if studio else None
    if not resource:
        await callback.answer("Нет зала", show_alert=True)
        return
    text = (
        f"Шаг {resource.slot_step_min} мин, минимум {resource.min_duration_min} мин, "
        f"буфер {resource.buffer_min} мин. Наценка за 1 ч при минимуме 2 ч — "
        f"{resource.hour_markup_percent}%."
    )
    await callback.message.answer(text, reply_markup=slot_settings_keyboard(resource))
    await callback.answer()


async def _set_slot_field(callback, session, user, field: str, value: int) -> Resource | None:
    studio = await get_owner_studio(session, user)
    resources = await list_active_resources(session, studio.id) if studio else []
    if not resources:
        await callback.answer("Нет зала", show_alert=True)
        return None
    for resource in resources:
        setattr(resource, field, value)
        if field == "min_duration_min":
            resource.duration_min = value
    await session.commit()
    return resources[0]


@router.callback_query(F.data.startswith("ow:step:"))
async def cb_step(callback: CallbackQuery, session: AsyncSession, user: User):
    resource = await _set_slot_field(callback, session, user, "slot_step_min", int(callback.data.split(":")[2]))
    if resource:
        await callback.message.answer("Шаг обновлён.", reply_markup=slot_settings_keyboard(resource))
        await callback.answer()


@router.callback_query(F.data.startswith("ow:mind:"))
async def cb_min_duration(callback: CallbackQuery, session: AsyncSession, user: User):
    resource = await _set_slot_field(callback, session, user, "min_duration_min", int(callback.data.split(":")[2]))
    if resource:
        await callback.message.answer("Минимум обновлён.", reply_markup=slot_settings_keyboard(resource))
        await callback.answer()


@router.callback_query(F.data.startswith("ow:buf:"))
async def cb_buffer(callback: CallbackQuery, session: AsyncSession, user: User):
    resource = await _set_slot_field(callback, session, user, "buffer_min", int(callback.data.split(":")[2]))
    if resource:
        await callback.message.answer("Буфер обновлён.", reply_markup=slot_settings_keyboard(resource))
        await callback.answer()


@router.callback_query(F.data == "ow:block")
async def cb_block(callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    studio = await get_owner_studio(session, user)
    resources = await list_active_resources(session, studio.id) if studio else []
    if not resources:
        await callback.answer("Нет зала", show_alert=True)
        return
    if len(resources) == 1:
        await state.update_data(block_resource_id=resources[0].id)
        await state.set_state(OwnerStates.waiting_block_interval)
        await callback.message.answer(
            f"Интервал: {datetime.now().strftime('%d.%m.%Y')} 14:00 16:00"
        )
        await callback.answer()
        return
    await callback.message.answer(
        "Какой зал закрыть?",
        reply_markup=owner_resource_pick_keyboard(resources, "ow:br"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ow:br:"))
async def cb_block_resource(callback: CallbackQuery, state: FSMContext):
    resource_id = int(callback.data.split(":")[2])
    await state.update_data(block_resource_id=resource_id)
    await state.set_state(OwnerStates.waiting_block_interval)
    await callback.message.answer(
        f"Интервал: {datetime.now().strftime('%d.%m.%Y')} 14:00 16:00"
    )
    await callback.answer()


@router.message(OwnerStates.waiting_block_interval, _NOT_COMMAND)
async def owner_block_interval(message: Message, session: AsyncSession, user: User, state: FSMContext):
    data = await state.get_data()
    resource = await session.get(Resource, data.get("block_resource_id"))
    studio = await get_owner_studio(session, user)
    if not resource or not studio or resource.studio_id != studio.id:
        await message.answer("Зал не найден.")
        await state.clear()
        return
    parsed = parse_block_interval(message.text or "", tz_name=resource.timezone)
    if parsed is None:
        await message.answer("Формат: 01.09.2026 14:00 16:00")
        return
    start, end = parsed
    block = await create_block(
        session,
        resource=resource,
        starts_at=start,
        ends_at=end,
        owner_telegram_id=user.telegram_id,
        note="Блок",
    )
    await state.clear()
    if block is None:
        await message.answer("Интервал пересекается с бронью или некорректен.")
        return
    when = format_interval_local(block.starts_at, block.ends_at, resource.timezone)
    await message.answer(f"Закрыто: {resource.name} {when}")
    await show_cabinet(message, session, user)


@router.callback_query(F.data == "ow:book")
async def cb_bookings(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    stmt = (
        select(Booking)
        .where(
            Booking.studio_id == studio.id,
            Booking.status.in_((STATUS_PENDING, STATUS_HOLD, STATUS_PAID, STATUS_BLOCKED)),
        )
        .order_by(Booking.starts_at.asc())
        .limit(20)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        await callback.message.answer("Активных броней нет.", reply_markup=owner_cabinet_keyboard())
        await callback.answer()
        return
    lines = ["📋 <b>Брони</b>\n"]
    buttons: list[tuple[int, str, str]] = []
    for booking in rows:
        resource = await session.get(Resource, booking.resource_id)
        tz = resource.timezone if resource else studio.timezone
        when = format_slot_local(booking.starts_at, tz)
        hall = resource.name if resource else "зал"
        if booking.status == STATUS_HOLD:
            mark = "⏳"
        elif booking.status == STATUS_PENDING:
            mark = "📨"
        elif booking.status == STATUS_BLOCKED:
            mark = "🚫"
        else:
            mark = "✅"
        lines.append(f"{mark} {hall} {when} — {booking.client_name} ({booking.client_phone or '—'})")
        buttons.append((booking.id, f"{hall} {when}", booking.status))
    await callback.message.answer("\n".join(lines), reply_markup=bookings_keyboard(buttons))
    await callback.answer()


@router.callback_query(F.data == "ow:req")
async def cb_requests(callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    stmt = (
        select(Booking)
        .where(Booking.studio_id == studio.id, Booking.status == STATUS_PENDING)
        .order_by(Booking.starts_at.asc())
        .limit(10)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        await callback.message.answer("Заявок нет.", reply_markup=home_keyboard())
        await callback.answer()
        return
    from src.keyboards.inline import owner_request_keyboard

    await callback.message.answer(f"Заявки: {len(rows)}")
    for booking in rows:
        resource = await session.get(Resource, booking.resource_id)
        if resource is None:
            continue
        await callback.message.answer(
            booking_summary(booking, studio, resource),
            reply_markup=owner_request_keyboard(booking.id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ow:ok:"))
async def cb_confirm_hold(callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    studio = await get_owner_studio(session, user)
    booking_id = int(callback.data.split(":")[2])
    booking = await session.get(Booking, booking_id)
    if not studio or not booking or booking.studio_id != studio.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    resource = await session.get(Resource, booking.resource_id)
    if booking.status == STATUS_PENDING:
        confirmed = await confirm_request(session, booking)
        if confirmed is None:
            await callback.answer("Это время уже занято другой бронью", show_alert=True)
            return
        extra = "\n" + booking_summary(booking, studio, resource) if resource else ""
        if booking.status == STATUS_HOLD:
            await callback.message.answer("Заявка принята. Клиент должен внести предоплату.")
            client_text = "Владелец подтвердил заявку. Внесите предоплату, чтобы закрепить слот." + extra
        else:
            await callback.message.answer("Заявка подтверждена.")
            client_text = "✅ Владелец студии подтвердил вашу бронь." + extra
        if booking.client_telegram_id != studio.owner_telegram_id:
            try:
                await bot.send_message(booking.client_telegram_id, client_text)
            except Exception:
                pass
        await callback.answer("Подтверждено")
        return
    if not await confirm_hold(session, booking):
        await callback.answer("Подтвердить можно только заявку или неоплаченный hold", show_alert=True)
        return
    await callback.message.answer("Бронь подтверждена (без оплаты в боте).")
    if booking.client_telegram_id != studio.owner_telegram_id:
        extra = ""
        if resource:
            extra = "\n" + booking_summary(booking, studio, resource)
        try:
            await bot.send_message(
                booking.client_telegram_id,
                "✅ Владелец студии подтвердил вашу бронь." + extra,
            )
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("ow:no:"))
async def cb_decline_request(callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    studio = await get_owner_studio(session, user)
    booking_id = int(callback.data.split(":")[2])
    booking = await session.get(Booking, booking_id)
    if not studio or not booking or booking.studio_id != studio.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    if not await decline_request(session, booking):
        await callback.answer("Отклонить можно только заявку", show_alert=True)
        return
    await callback.answer("Отклонено")
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    if booking.client_telegram_id != studio.owner_telegram_id:
        try:
            await bot.send_message(
                booking.client_telegram_id,
                "Владелец не может принять это время. Выберите другое по ссылке записи.",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("ow:cok:"))
async def cb_cancel_booking_ok(callback: CallbackQuery, session: AsyncSession, user: User, bot: Bot):
    studio = await get_owner_studio(session, user)
    booking_id = int(callback.data.split(":")[2])
    booking = await session.get(Booking, booking_id)
    if not studio or not booking or booking.studio_id != studio.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    result = await cancel_booking(session, booking, studio, by="owner")
    await callback.message.answer(result.message)
    if result.ok and booking.client_telegram_id != studio.owner_telegram_id:
        try:
            await bot.send_message(
                booking.client_telegram_id,
                "Владелец студии отменил вашу бронь. " + result.message,
            )
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("ow:c:"))
async def cb_cancel_booking(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    booking_id = int(callback.data.split(":")[2])
    booking = await session.get(Booking, booking_id)
    if not studio or not booking or booking.studio_id != studio.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    _, _, text = await preview_cancel(session, booking, studio, by="owner")
    await callback.message.answer(text, reply_markup=confirm_cancel_keyboard(booking.id, owner=True))
    await callback.answer()


@router.callback_query(F.data == "ow:res")
async def cb_add_resource(callback: CallbackQuery, session: AsyncSession, user: User, state: FSMContext):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    ok, reason = await can_add_resource(session, studio)
    if not ok:
        await callback.message.answer(reason, reply_markup=tariff_keyboard())
        await callback.answer()
        return
    await state.set_state(OwnerStates.waiting_extra_resource)
    await callback.message.answer("Название зала?")
    await callback.answer()


@router.message(OwnerStates.waiting_extra_resource, _NOT_COMMAND)
async def owner_extra_resource(message: Message, session: AsyncSession, user: User, state: FSMContext):
    studio = await get_owner_studio(session, user)
    if not studio:
        await state.clear()
        await message.answer("Студия не найдена. Сначала /studio.")
        return
    ok, reason = await can_add_resource(session, studio)
    if not ok:
        await message.answer(reason, reply_markup=tariff_keyboard())
        await state.clear()
        return
    primary = await get_primary_resource(session, studio.id)
    name = (message.text or "").strip() or "Зал 2"
    resource = Resource(studio_id=studio.id, name=name, **_copy_slot_fields(primary))
    session.add(resource)
    await session.commit()
    await state.clear()
    await message.answer(f"Зал «{name}» добавлен.")
    await show_cabinet(message, session, user)


@router.callback_query(F.data == "ow:tariff")
async def cb_tariff(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    until = studio.subscription_until.strftime("%d.%m.%Y") if studio.subscription_until else "—"
    text = (
        f"Текущий тариф: <b>{tariff_label(studio.tariff)}</b>\n"
        f"Оплачен до: {until}\n\n"
        f"Free — 1 зал, {settings.FREE_BOOKINGS_PER_MONTH} записей/мес.\n"
        f"Старт {settings.TARIFF_STARTER_RUB} ₽ — 1 зал, без лимита записей.\n\n"
        "Кабинет для одного мастера, один зал. "
        "Оплата подписки — ЮKassa (если ключи заданы) или Prodamus."
    )
    await callback.message.answer(text, reply_markup=tariff_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("ow:pay:"))
async def cb_pay_tariff(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    kind = callback.data.split(":")[2]
    if kind != "starter":
        await callback.answer("Доступен только тариф Старт", show_alert=True)
        return
    tariff, amount = TARIFF_STARTER, settings.TARIFF_STARTER_RUB
    if not payment_svc.is_pay_configured():
        await callback.message.answer(
            "Оплата подписки пока недоступна. Напишите в поддержку — включим кассу."
        )
        await callback.answer()
        return
    payment = await payment_svc.create_subscription_invoice(
        session, studio, tariff=tariff, amount_rub=amount
    )
    try:
        url = await payment_svc.create_checkout_url(
            session,
            payment,
            description=f"Подписка studio-book {tariff} {studio.slug}",
        )
    except Exception:
        await callback.message.answer("Не удалось открыть оплату. Попробуйте ещё раз чуть позже.")
        await callback.answer()
        return
    await callback.message.answer(
        f"Счёт на {amount} ₽. После оплаты тариф обновится автоматически.",
        reply_markup=pay_keyboard(url),
    )
    await callback.answer()


@router.callback_query(F.data == "ow:ical")
async def cb_ical(callback: CallbackQuery, session: AsyncSession, user: User):
    studio = await get_owner_studio(session, user)
    if not studio:
        await callback.answer("Нет студии", show_alert=True)
        return
    resources = await list_active_resources(session, studio.id)
    if not resources:
        await callback.message.answer("Нет ресурса для календаря.")
        await callback.answer()
        return
    stmt = select(Booking).where(
        Booking.studio_id == studio.id,
        Booking.status.in_((STATUS_PAID, STATUS_BLOCKED)),
    )
    rows = (await session.execute(stmt)).scalars().all()
    ics = build_calendar(studio, resources, list(rows))
    document = BufferedInputFile(ics.encode("utf-8"), filename=f"{studio.slug}.ics")
    caption = "Импортируйте файл в Google Calendar / отдайте агрегатору занятость."
    if settings.PUBLIC_BASE_URL.strip():
        url = feed_url(studio.slug, settings.PUBLIC_BASE_URL)
        caption += f"\nПодписка (не публикуйте ссылку): {url}"
    await callback.message.answer_document(document, caption=caption)
    await callback.answer()
