"""Настройки бота аренды зала для наращивания ресниц."""

from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    BOT_TOKEN: str = Field(default="", description="Токен бота от @BotFather")
    BOT_USERNAME: str = Field(default="", description="Username бота без @")
    TELEGRAM_PROXY: str = Field(
        default="",
        description="socks5://127.0.0.1:1080 или http://proxy:8080",
    )

    # Саппорт продукта, не контент справочника
    ADMINS: List[int] = Field(default_factory=list)
    # Superadmin: платные подписчики (кол-во и Telegram ID). Пусто = те же, что ADMINS.
    SUPERADMINS: List[int] = Field(default_factory=list)

    SQLITE_PATH: Path = Field(
        default=PROJECT_ROOT / "data" / "eyelash.db",
        description="Путь к файлу SQLite",
    )

    THROTTLE_RATE_LIMIT: int = 8
    THROTTLE_TTL: int = 3

    USE_REDIS: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"

    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "bot.log"

    HOLD_TTL_MINUTES: int = 20
    HOLD_TTL_MIN: int = 20
    HOLD_TTL_MAX: int = 24 * 60
    REMINDER_HOURS: int = 24
    REMINDER_2H_HOURS: int = 2

    FREE_RESOURCE_LIMIT: int = 1
    PLUS_RESOURCE_LIMIT: int = 1
    FREE_BOOKINGS_PER_MONTH: int = 10
    TARIFF_STARTER_RUB: int = 490
    TARIFF_PLUS_RUB: int = 990

    CONSENT_PDN_VERSION: str = "1"

    PRODAMUS_SECRET: str = ""
    PRODAMUS_SHOP_ID: str = ""
    PRODAMUS_PAYFORM_URL: str = ""

    # ЮKassa: отдельный магазин под studiobook.com.ru, не shopId agentops/gameforge.
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    # auto = ЮKassa если ключи есть, иначе Prodamus
    PAYMENT_PROVIDER: str = "auto"

    HTTP_PORT: int = 8088
    PUBLIC_BASE_URL: str = ""

    # HMAC iCal: не BOT_TOKEN, иначе смена токена ломает подписки календаря.
    ICAL_FEED_SECRET: str = ""
    # Вторая копия бэкапа SQLite (другой диск/каталог в РФ). Пусто — только data/backups/.
    BACKUP_OFFSITE_DIR: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("ADMINS", "SUPERADMINS", mode="before")
    @classmethod
    def parse_int_list(cls, v):
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                import json

                return json.loads(v)
            except json.JSONDecodeError:
                return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @property
    def admin_ids(self) -> frozenset[int]:
        return frozenset(list(self.ADMINS or []) + list(self.SUPERADMINS or []))

    @property
    def superadmin_ids(self) -> frozenset[int]:
        return frozenset(self.SUPERADMINS or self.ADMINS or [])

    @property
    def sqlite_dsn(self) -> str:
        path = self.SQLITE_PATH.resolve()
        return f"sqlite+aiosqlite:///{path}"

    @property
    def consent_pdn_path(self) -> Path:
        return PROJECT_ROOT / "data" / "legal" / "consent_pdn.md"

    @property
    def cancel_rules_path(self) -> Path:
        return PROJECT_ROOT / "data" / "legal" / "cancel_rules.md"


settings = Settings()
