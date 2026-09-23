"""Импорт моделей, создание таблиц и лёгкие миграции SQLite."""

from sqlalchemy import text


def _table_exists(sync_conn, name: str) -> bool:
    row = sync_conn.execute(
        text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name LIMIT 1"
        ),
        {"name": name},
    ).fetchone()
    return bool(row)


def _columns(sync_conn, table: str) -> set[str]:
    return {r[1] for r in sync_conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _add_column(sync_conn, table: str, cols: set[str], name: str, ddl: str) -> None:
    if name not in cols:
        sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def _add_missing_columns(sync_conn) -> None:
    if _table_exists(sync_conn, "users"):
        cols = _columns(sync_conn, "users")
        _add_column(sync_conn, "users", cols, "is_blocked", "is_blocked BOOLEAN DEFAULT 0")
    if _table_exists(sync_conn, "bookings"):
        cols = _columns(sync_conn, "bookings")
        _add_column(sync_conn, "bookings", cols, "reminder_sent_at", "reminder_sent_at DATETIME")
        _add_column(sync_conn, "bookings", cols, "reminder_2h_sent_at", "reminder_2h_sent_at DATETIME")
        _add_column(sync_conn, "bookings", cols, "quoted_price_rub", "quoted_price_rub INTEGER DEFAULT 0")
        _add_column(sync_conn, "bookings", cols, "prepay_amount_rub", "prepay_amount_rub INTEGER DEFAULT 0")
    if _table_exists(sync_conn, "studios"):
        cols = _columns(sync_conn, "studios")
        _add_column(sync_conn, "studios", cols, "slug", "slug VARCHAR(64)")
        _add_column(sync_conn, "studios", cols, "owner_id", "owner_id INTEGER")
        _add_column(sync_conn, "studios", cols, "owner_telegram_id", "owner_telegram_id BIGINT")
        _add_column(sync_conn, "studios", cols, "tariff", "tariff VARCHAR(16) DEFAULT 'free'")
        _add_column(sync_conn, "studios", cols, "resource_limit", "resource_limit INTEGER DEFAULT 1")
        _add_column(sync_conn, "studios", cols, "timezone", "timezone VARCHAR(64) DEFAULT 'Europe/Moscow'")
        _add_column(sync_conn, "studios", cols, "subscription_until", "subscription_until DATETIME")
        _add_column(sync_conn, "studios", cols, "hold_ttl_minutes", "hold_ttl_minutes INTEGER DEFAULT 20")
        _add_column(sync_conn, "studios", cols, "prepay_percent", "prepay_percent INTEGER DEFAULT 100")
        _add_column(sync_conn, "studios", cols, "cancel_free_hours", "cancel_free_hours INTEGER DEFAULT 72")
        _add_column(
            sync_conn,
            "studios",
            cols,
            "late_cancel_retain_percent",
            "late_cancel_retain_percent INTEGER DEFAULT 50",
        )
    if _table_exists(sync_conn, "windows"):
        cols = _columns(sync_conn, "windows")
        _add_column(sync_conn, "windows", cols, "resource_id", "resource_id INTEGER")
    if _table_exists(sync_conn, "resources"):
        cols = _columns(sync_conn, "resources")
        _add_column(sync_conn, "resources", cols, "slot_step_min", "slot_step_min INTEGER DEFAULT 60")
        _add_column(sync_conn, "resources", cols, "min_duration_min", "min_duration_min INTEGER DEFAULT 60")
        _add_column(sync_conn, "resources", cols, "buffer_min", "buffer_min INTEGER DEFAULT 5")
        _add_column(
            sync_conn,
            "resources",
            cols,
            "hour_markup_percent",
            "hour_markup_percent INTEGER DEFAULT 50",
        )
        _add_column(sync_conn, "resources", cols, "weekend_price_rub", "weekend_price_rub INTEGER DEFAULT 0")
        _add_column(sync_conn, "resources", cols, "night_price_rub", "night_price_rub INTEGER DEFAULT 0")
        _add_column(sync_conn, "resources", cols, "night_start", "night_start TIME")
    if _table_exists(sync_conn, "payments"):
        cols = _columns(sync_conn, "payments")
        _add_column(sync_conn, "payments", cols, "refunded_at", "refunded_at DATETIME")
        _add_column(sync_conn, "payments", cols, "refund_amount_rub", "refund_amount_rub INTEGER DEFAULT 0")
        _add_column(sync_conn, "payments", cols, "provider", "provider VARCHAR(16) DEFAULT ''")
        _add_column(
            sync_conn,
            "payments",
            cols,
            "provider_payment_id",
            "provider_payment_id VARCHAR(128)",
        )


def _ensure_unique_slug(sync_conn, base: str, studio_id: int) -> str:
    from src.utils.slug import slugify

    candidate = slugify(base or "studio")
    suffix = 2
    while True:
        row = sync_conn.execute(
            text("SELECT id FROM studios WHERE slug = :slug AND id != :id LIMIT 1"),
            {"slug": candidate, "id": studio_id},
        ).fetchone()
        if row is None:
            return candidate
        candidate = f"{slugify(base)}-{suffix}"[:64]
        suffix += 1


def _backfill_legacy_studio(sync_conn) -> None:
    """Старая одностудийная база: нет slug/owner, окна без resource_id."""
    if not _table_exists(sync_conn, "studios") or not _table_exists(sync_conn, "users"):
        return
    cols = _columns(sync_conn, "studios")
    if "slug" not in cols or "owner_id" not in cols:
        return
    sync_conn.execute(text("UPDATE studios SET tariff = 'free' WHERE tariff IS NULL"))
    sync_conn.execute(text("UPDATE studios SET resource_limit = 1 WHERE resource_limit IS NULL"))
    sync_conn.execute(text("UPDATE studios SET timezone = 'Europe/Moscow' WHERE timezone IS NULL"))
    sync_conn.execute(text("UPDATE studios SET hold_ttl_minutes = 20 WHERE hold_ttl_minutes IS NULL"))
    sync_conn.execute(text("UPDATE studios SET prepay_percent = 100 WHERE prepay_percent IS NULL"))
    sync_conn.execute(text("UPDATE studios SET cancel_free_hours = 72 WHERE cancel_free_hours IS NULL"))
    sync_conn.execute(
        text("UPDATE studios SET late_cancel_retain_percent = 50 WHERE late_cancel_retain_percent IS NULL")
    )

    has_master = "master_telegram_id" in cols
    select_sql = "SELECT id, name, slug, owner_id, owner_telegram_id"
    if has_master:
        select_sql += ", master_telegram_id"
    studios = sync_conn.execute(text(select_sql + " FROM studios")).fetchall()
    users = list(sync_conn.execute(text("SELECT id, telegram_id FROM users ORDER BY id")).fetchall())
    users_by_tg = {int(row[1]): int(row[0]) for row in users if row[1] is not None}

    for studio in studios:
        mapping = studio._mapping
        studio_id = int(mapping["id"])
        owner_id = mapping.get("owner_id")
        owner_tg = mapping.get("owner_telegram_id")
        if has_master and owner_tg is None:
            owner_tg = mapping.get("master_telegram_id")
        if owner_id is None:
            if owner_tg is not None and int(owner_tg) in users_by_tg:
                owner_id = users_by_tg[int(owner_tg)]
            elif users:
                owner_id = int(users[0][0])
                if owner_tg is None:
                    owner_tg = users[0][1]
        if owner_id is not None:
            sync_conn.execute(
                text(
                    "UPDATE studios SET owner_id = :owner_id, owner_telegram_id = :owner_tg "
                    "WHERE id = :id AND (owner_id IS NULL OR owner_telegram_id IS NULL)"
                ),
                {"owner_id": owner_id, "owner_tg": int(owner_tg or 0), "id": studio_id},
            )
        if not mapping.get("slug"):
            slug = _ensure_unique_slug(sync_conn, mapping.get("name") or "studio", studio_id)
            sync_conn.execute(
                text("UPDATE studios SET slug = :slug WHERE id = :id AND (slug IS NULL OR slug = '')"),
                {"slug": slug, "id": studio_id},
            )

    if _table_exists(sync_conn, "resources") and studios:
        sync_conn.execute(
            text(
                "INSERT INTO resources ("
                "studio_id, name, duration_min, slot_step_min, min_duration_min, buffer_min, "
                "hour_markup_percent, timezone, work_start, work_end, weekdays, is_active, "
                "price_rub, weekend_price_rub, night_price_rub, night_start, created_at, updated_at"
                ") SELECT id, 'Зал', 60, 60, 60, 5, 50, COALESCE(timezone, 'Europe/Moscow'), "
                "'10:00:00', '22:00:00', '1,2,3,4,5,6,7', 1, 0, 0, 0, '22:00:00', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM studios "
                "WHERE NOT EXISTS (SELECT 1 FROM resources WHERE resources.studio_id = studios.id)"
            )
        )

    sync_conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_studios_slug ON studios (slug)"))
    sync_conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_studios_owner_id ON studios (owner_id)"))
    if _table_exists(sync_conn, "windows"):
        sync_conn.execute(text("CREATE INDEX IF NOT EXISTS ix_windows_resource_id ON windows (resource_id)"))


def _rebuild_booking_active_index(sync_conn) -> None:
    if not _table_exists(sync_conn, "bookings"):
        return
    sync_conn.execute(text("DROP INDEX IF EXISTS uq_booking_resource_start_active"))
    sync_conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_resource_start_active "
            "ON bookings (resource_id, starts_at) "
            "WHERE status IN ('hold', 'paid', 'blocked')"
        )
    )
    sync_conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_bookings_hold_expire "
            "ON bookings (status, hold_expires_at)"
        )
    )
    sync_conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_bookings_status_start ON bookings (status, starts_at)")
    )


def _register_models_and_create_all(sync_conn):
    from src.database.base import Base
    from src.database.models import booking  # noqa: F401
    from src.database.models import consent  # noqa: F401
    from src.database.models import payment  # noqa: F401
    from src.database.models import studio  # noqa: F401
    from src.database.models import user  # noqa: F401
    from src.database.models import window  # noqa: F401

    Base.metadata.create_all(sync_conn)
    if sync_conn.dialect.name == "sqlite":
        _add_missing_columns(sync_conn)
        _backfill_legacy_studio(sync_conn)
        _rebuild_booking_active_index(sync_conn)


async def init_database(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_register_models_and_create_all)
