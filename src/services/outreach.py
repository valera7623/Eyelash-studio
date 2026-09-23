"""Готовые тексты для сторис, канала и клиентов (онбординг за вечер)."""

from html import escape

from src.config import settings
from src.database.models.studio import Studio


def client_broadcast(studio: Studio, deep_link: str) -> str:
    return (
        f"Теперь записаться в {studio.name} можно через бот:\n"
        f"{deep_link}\n"
        "Выберите время. Если окно открыто — слот закрепится сразу, "
        "иначе заявка уйдёт мастеру. Напомним за сутки и за 2 часа."
    )


def stories_text(studio: Studio, deep_link: str) -> str:
    return f"Свободные окна и запись без переписки — {studio.name}: {deep_link}"


def channel_text(studio: Studio, deep_link: str) -> str:
    return (
        f"Запись в {studio.name} по ссылке, без «напишите в личку»:\n"
        f"{deep_link}"
    )


def owner_copy_pack(studio: Studio, deep_link: str) -> str:
    return (
        "📣 <b>Тексты для клиентов</b>\n"
        "Скопируйте и отправьте как есть.\n\n"
        "<b>Клиентам</b>\n"
        f"<code>{escape(client_broadcast(studio, deep_link))}</code>\n\n"
        "<b>Сторис</b>\n"
        f"<code>{escape(stories_text(studio, deep_link))}</code>\n\n"
        "<b>Канал / чат студии</b>\n"
        f"<code>{escape(channel_text(studio, deep_link))}</code>\n\n"
        f"Ссылка: <code>{escape(deep_link)}</code>"
    )


def owner_next_steps() -> str:
    return (
        "Что дальше:\n"
        "1. «Ссылка записи» — отправьте клиентам.\n"
        "2. «Открыть окно» на день — клиенты займут его сразу.\n"
        "3. «Сетка цен» — будни / выходные / ночь.\n"
        f"4. Free: 1 зал, {settings.FREE_BOOKINGS_PER_MONTH} записей в месяц. "
        f"Старт {settings.TARIFF_STARTER_RUB} ₽ — без лимита."
    )


def owner_cheat_sheet() -> str:
    """Один экран: что нажимать. Кабинет для мастера-одиночки, один зал."""
    return (
        "❓ <b>Шпаргалка</b>\n"
        "Один зал, без недельного графика. Клиент записывается по ссылке.\n\n"
        "<b>Каждый день</b>\n"
        "• <b>Ссылка записи</b> — кинуть клиенту (или QR).\n"
        "• <b>Открыть окно</b> — «25.09 12:00 18:00». Внутри окна запись сразу.\n"
        "• <b>Открытые окна</b> — закрыть лишнее крестиком.\n"
        "• <b>Заявки</b> — время вне окна: подтвердить или отклонить.\n\n"
        "<b>Настроили один раз</b>\n"
        "• <b>Сетка цен</b> — будни / выходные / ночь.\n"
        f"• <b>Тариф</b> — Free: {settings.FREE_BOOKINGS_PER_MONTH} записей/мес, "
        f"Старт {settings.TARIFF_STARTER_RUB} ₽ без лимита. Везде 1 зал.\n\n"
        "Клиент отменяет сам: /my. Напоминания за сутки и за 2 часа.\n"
        "Кабинет: /studio"
    )
