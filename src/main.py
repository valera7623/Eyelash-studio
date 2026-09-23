#!/usr/bin/env python3
"""Точка входа: Telegram-бот аренды зала для наращивания ресниц (aiogram 3.x, SQLite)."""

import asyncio
import logging
import socket
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError

from src.bot.loader import get_dispatcher, load_routers, setup_middlewares
from src.config import settings
from src.database import create_async_engine, get_session_maker
from src.database.bootstrap import init_database
from src.services.scheduler import build_scheduler
from src.utils.logging import setup_logging
from src.web.app import start_http


def _assert_proxy_reachable(proxy_url: str) -> None:
    parsed = urlparse(proxy_url)
    host, port = parsed.hostname, parsed.port
    if not host or not port:
        logging.error("TELEGRAM_PROXY должен быть вида socks5://host:port")
        raise SystemExit(1)
    try:
        with socket.create_connection((host, port), timeout=3):
            return
    except OSError:
        logging.error(
            "Прокси %s:%s не отвечает (порт закрыт). "
            "На Windows включите VPN-клиент и Allow LAN / inbound для WSL. "
            "Проверка из WSL: nc -zv %s %s",
            host,
            port,
            host,
            port,
        )
        raise SystemExit(1)


async def main():
    setup_logging()

    if not settings.BOT_TOKEN.strip():
        logging.error(
            "Не задан BOT_TOKEN. Создайте .env с BOT_TOKEN=... "
            "(скопируйте из .env.example). Для только создания таблиц: python create_tables.py"
        )
        raise SystemExit(1)

    settings.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine()
    await init_database(engine)
    session_maker = get_session_maker(engine)

    proxy_url = settings.TELEGRAM_PROXY.strip()
    if proxy_url:
        _assert_proxy_reachable(proxy_url)
    session = AiohttpSession(proxy=proxy_url) if proxy_url else None
    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = get_dispatcher()
    setup_middlewares(dp, session_maker)
    load_routers(dp)

    web_runner = await start_http(bot, session_maker)
    scheduler = build_scheduler(bot, session_maker)
    scheduler.start()

    try:
        me = await bot.get_me()
        logging.info("Polling @%s (id=%s)", me.username or "?", me.id)
    except Exception:
        logging.exception("getMe при старте не удался")

    proxy_hint = proxy_url or "(не задан)"
    logging.info("Бот запущен (polling), TELEGRAM_PROXY=%s", proxy_hint)
    try:
        await dp.start_polling(bot)
    except TelegramNetworkError:
        logging.error(
            "Нет доступа к api.telegram.org (таймаут). "
            "Токен принят, но Telegram с этой сети не открывается. "
            "Поднимите SOCKS/HTTP-прокси (v2rayN / Clash / SSH -D) и в .env укажите, например:\n"
            "  TELEGRAM_PROXY=socks5://127.0.0.1:1080\n"
            "Если клиент VPN на Windows — включите Allow LAN и укажите IP хоста из WSL "
            "(ip route | awk '/default/{print $3}'): socks5://<этот_IP>:10808"
        )
        raise SystemExit(1)
    finally:
        scheduler.shutdown(wait=False)
        if web_runner is not None:
            await web_runner.cleanup()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановка бота (Ctrl+C)")
