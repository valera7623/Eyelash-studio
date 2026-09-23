from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Update
from sqlalchemy import select

from src.database.models.user import User


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any],
    ) -> Any:
        session = data["session"]

        if event.message:
            from_user = event.message.from_user
        elif event.callback_query:
            from_user = event.callback_query.from_user
        else:
            return await handler(event, data)

        stmt = select(User).where(User.telegram_id == from_user.id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                telegram_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name or "—",
                last_name=from_user.last_name,
                language_code=from_user.language_code or "ru",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

        data["user"] = user
        return await handler(event, data)
