from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Update
from cachetools import TTLCache


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit: int = 8, ttl: int = 3):
        super().__init__()
        self.cache = TTLCache(maxsize=10000, ttl=ttl)
        self.rate_limit = rate_limit

    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        if event.message:
            user_id = event.message.from_user.id
        elif event.callback_query:
            user_id = event.callback_query.from_user.id
        else:
            return await handler(event, data)

        if user_id in self.cache:
            if self.cache[user_id] >= self.rate_limit:
                if event.message:
                    await event.message.answer("⚠️ Слишком много запросов! Подождите немного.")
                elif event.callback_query:
                    await event.callback_query.answer(
                        "⚠️ Слишком много запросов!",
                        show_alert=True,
                    )
                return
            self.cache[user_id] += 1
        else:
            self.cache[user_id] = 1

        return await handler(event, data)
