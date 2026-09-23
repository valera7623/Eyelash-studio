import re
from typing import Optional, Tuple


def validate_phone(phone: str) -> Tuple[bool, Optional[str]]:
    if not phone:
        return False, "Номер телефона не может быть пустым"

    cleaned = re.sub(r"[^\d+]", "", phone)

    if cleaned.startswith("8"):
        cleaned = "+7" + cleaned[1:]
    elif cleaned.startswith("7"):
        cleaned = "+" + cleaned

    if not re.match(r"^\+7\d{10}$", cleaned):
        return False, "Неверный формат номера. Используйте +79991234567"

    return True, cleaned


def validate_name(name: str) -> Tuple[bool, Optional[str]]:
    if not name:
        return False, "Имя не может быть пустым"

    name = name.strip()

    if len(name) < 2:
        return False, "Имя должно содержать не менее 2 символов"

    if len(name) > 50:
        return False, "Имя не может быть длиннее 50 символов"

    if not re.match(r"^[a-zA-Zа-яА-ЯёЁ\s-]+$", name):
        return False, "Имя может содержать только буквы, пробелы и дефисы"

    return True, name
