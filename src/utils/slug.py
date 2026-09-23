from __future__ import annotations

import re
import unicodedata

_TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "j",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def slugify(value: str, *, max_len: int = 48) -> str:
    lowered = unicodedata.normalize("NFKC", value).strip().lower()
    chars: list[str] = []
    for ch in lowered:
        if ch in _TRANSLIT:
            chars.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            chars.append(ch)
        else:
            chars.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(chars)).strip("-")
    slug = slug[:max_len].strip("-")
    return slug or "studio"
