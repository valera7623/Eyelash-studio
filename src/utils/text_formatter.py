from html import escape


def format_welcome_message(user) -> str:
    name = user.first_name or "гость"
    return (
        f"👋 Привет, <b>{escape(name)}</b>!\n\n"
        "Это бот <b>аренды зала для наращивания ресниц</b>: мастер создаёт студию, "
        "открывает окна на нужные дни, клиент записывается по ссылке.\n\n"
        "💅 <b>Клиенту</b> — откройте ссылку студии (её пришлёт владелец).\n"
        "🏠 <b>Владельцу</b> — /studio: создайте студию и получите ссылку записи.\n"
        "Команды: /help"
    )


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
