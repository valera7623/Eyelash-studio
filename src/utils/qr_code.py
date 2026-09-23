"""QR и deep-link записи: t.me/bot?start=book_<slug>."""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

import qrcode
from qrcode.constants import ERROR_CORRECT_M

logger = logging.getLogger(__name__)

_START_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_start_payload(payload: str) -> bool:
    return bool(payload and _START_PAYLOAD_RE.fullmatch(payload))


def build_bot_deep_link(bot_username: str, payload: str) -> str:
    username = bot_username.lstrip("@").strip()
    if not username:
        raise ValueError("bot_username не может быть пустым")
    if not is_valid_start_payload(payload):
        raise ValueError("payload для /start: 1–64 символа (латиница, цифры, _ и -)")
    return f"https://t.me/{username}?start={payload}"


def studio_start_link(slug: str, bot_username: str | None = None) -> str:
    from src.config import settings

    username = (bot_username or settings.BOT_USERNAME or "eyelashstudiobot").lstrip("@").strip()
    username = username or "eyelashstudiobot"
    payload = slug if is_valid_start_payload(slug) else f"book_{slug}"[:64]
    return build_bot_deep_link(username, payload)


def generate_qr_png_bytes(data: str, *, box_size: int = 10, border: int = 2) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_booking_qr(bot_username: str, payload: str) -> tuple[str, bytes]:
    deep_link = build_bot_deep_link(bot_username, payload)
    return deep_link, generate_qr_png_bytes(deep_link)


async def resolve_bot_username(bot, configured_username: Optional[str] = None) -> str:
    """Username живого токена (getMe). Env — запасной, если API недоступен."""
    fallback = (configured_username or "").lstrip("@").strip()
    try:
        me = await bot.get_me()
        if me.username:
            live = me.username.lstrip("@").strip()
            if fallback and live.lower() != fallback.lower():
                logger.warning(
                    "BOT_USERNAME=%s не совпадает с getMe @%s — берём живой username",
                    fallback,
                    live,
                )
            return live
    except Exception:
        logger.exception("getMe не вернул username")
    if fallback:
        return fallback
    raise RuntimeError(
        "У бота нет username. Задайте его в @BotFather или BOT_USERNAME в .env"
    )
