from typing import Union

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from src.config import settings


class AdminFilter(BaseFilter):
    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        return event.from_user.id in settings.admin_ids


class SuperadminFilter(BaseFilter):
    async def __call__(self, event: Union[Message, CallbackQuery]) -> bool:
        return event.from_user.id in settings.superadmin_ids
