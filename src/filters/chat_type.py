from aiogram.filters import BaseFilter
from aiogram.types import Message


class ChatTypeFilter(BaseFilter):
    def __init__(self, chat_type: str):
        self.chat_type = chat_type

    async def __call__(self, message: Message) -> bool:
        return message.chat.type == self.chat_type
