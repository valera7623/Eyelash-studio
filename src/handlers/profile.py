from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.database.models.user import User
from src.keyboards.inline import profile_keyboard

router = Router()


@router.message(Command("profile"))
async def show_profile(message: Message, user: User):
    profile_text = (
        f"<b>👤 Профиль</b>\n\n"
        f"<b>Telegram ID:</b> <code>{user.telegram_id}</code>\n"
        f"<b>Имя:</b> {user.first_name} {user.last_name or ''}\n"
        f"<b>Username:</b> @{user.username or 'не указан'}"
    )
    await message.answer(profile_text, reply_markup=profile_keyboard())


@router.callback_query(F.data == "profile_close")
async def profile_close(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()
