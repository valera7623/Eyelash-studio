from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.config import settings
from src.middlewares import (
    DatabaseMiddleware,
    LoggingMiddleware,
    ThrottlingMiddleware,
    UserMiddleware,
)


def get_dispatcher() -> Dispatcher:
    if settings.USE_REDIS:
        try:
            from aiogram.fsm.storage.redis import RedisStorage

            storage = RedisStorage.from_url(str(settings.REDIS_URL))
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Redis FSM недоступен, MemoryStorage")
            storage = MemoryStorage()
    else:
        storage = MemoryStorage()
    return Dispatcher(storage=storage)


def setup_middlewares(dp: Dispatcher, session_maker):
    dp.update.middleware(LoggingMiddleware())
    dp.update.middleware(
        ThrottlingMiddleware(
            rate_limit=settings.THROTTLE_RATE_LIMIT,
            ttl=settings.THROTTLE_TTL,
        )
    )
    dp.update.middleware(DatabaseMiddleware(session_maker))
    dp.update.middleware(UserMiddleware())


def load_routers(dp: Dispatcher):
    from src.handlers import admin_commands, booking, owner, profile, user_commands

    for router in (
        user_commands.router,
        admin_commands.router,
        profile.router,
        owner.router,
        booking.router,
    ):
        dp.include_router(router)
