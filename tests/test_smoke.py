from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.bot.loader import get_dispatcher, load_routers, setup_middlewares
from src.database import get_session_maker
from src.database.models.user import User
from src.handlers import user_commands
from src.handlers.user_commands import cmd_start


def test_start_handler_registered():
    assert user_commands.cmd_start is not None


def test_command_routers_load_before_fsm():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "src" / "bot" / "loader.py").read_text(
        encoding="utf-8"
    )
    assert text.index("user_commands.router") < text.index("owner.router")


async def test_start_answers_welcome(session):
    user = User(telegram_id=1, first_name="Ира", language_code="ru")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    message = AsyncMock()
    command = MagicMock()
    command.args = None
    state = AsyncMock()

    await cmd_start(message, command, user, session, state)

    state.clear.assert_awaited()
    message.answer.assert_awaited()
    text = message.answer.await_args.args[0]
    assert "аренды зала" in text or "наращивания ресниц" in text
    assert "парол" not in text.lower()


async def test_help_hides_owner_sheet_for_clients(session):
    from src.handlers.user_commands import cmd_help

    user = User(telegram_id=2, first_name="Клиент", language_code="ru")
    session.add(user)
    await session.commit()
    message = AsyncMock()
    await cmd_help(message, user, session)
    text = message.answer.await_args.args[0]
    assert "/my" in text
    assert "Шпаргалка владельца" not in text


async def test_dispatcher_loads(engine):
    dp = get_dispatcher()
    setup_middlewares(dp, get_session_maker(engine))
    load_routers(dp)
    assert dp.sub_routers


async def test_tables_created(engine):
    from sqlalchemy import inspect

    def _tables(sync_conn):
        return set(inspect(sync_conn).get_table_names())

    async with engine.connect() as conn:
        names = await conn.run_sync(_tables)

    assert {"users", "studios", "resources", "windows", "bookings", "payments", "consents"} <= names


def test_landing_lists_service_prices():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "landing" / "index.html").read_text(encoding="utf-8")
    assert "Стоимость услуг" in html
    assert "{{TARIFF_STARTER_RUB}}" in html
    assert "2&nbsp;000" in html
    assert "id=\"prices\"" in html
    assert "Lash-book" in html
    assert "фотозал" not in html


async def test_landing_http_substitutes_tariffs(engine):
    from aiohttp.test_utils import TestClient, TestServer

    from src.web.app import create_web_app

    app = create_web_app(bot=None, session_maker=get_session_maker(engine))
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "490" in text
        assert "1 зал, 10 записей в месяц" in text
        assert "Плюс" not in text
        assert "фотозал" not in text
        assert "фотограф" not in text
        assert "Lash-book" in text
        assert "наращивания ресниц" in text
        assert "2\xa0000" in text or "2&nbsp;000" in text
        assert "Стоимость услуг" in text
        assert "220910861433" in text
        from src.config import settings
        from src.web.app import DEFAULT_BOT_USERNAME

        expected_bot = (settings.BOT_USERNAME or DEFAULT_BOT_USERNAME).strip().lstrip("@")
        assert f"t.me/{expected_bot}" in text
        robots = await client.get("/robots.txt")
        assert robots.status == 200
        offer = await client.get("/offer")
        assert offer.status == 200
        offer_text = await offer.text()
        assert "Публичная оферта" in offer_text
        assert "220910861433" in offer_text
        pdf = await client.get("/offer.pdf")
        assert pdf.status == 200
        assert "pdf" in (pdf.headers.get("Content-Type") or "").lower()
        ical = await client.get("/ical/missing.ics")
        assert ical.status == 404
        yoo = await client.post("/yookassa/webhook", json={"event": "payment.succeeded"})
        assert yoo.status == 403
        prod = await client.post("/prodamus/webhook", data={"order_id": "x"})
        assert prod.status == 403


async def test_admin_support_text_shows_payform_and_counts(session):
    from src.handlers.admin_commands import platform_support_text

    text = await platform_support_text(session)
    assert "Касса:" in text
    assert "Webhook:" in text
    assert "Платежи:" in text
    assert "Брони:" in text
    assert "Платных подписчиков:" in text
    assert "/superadmin" in text
    assert "Пользователей:" in text


async def test_admin_lists_user_telegram_ids(session):
    from src.handlers.admin_commands import platform_support_text

    session.add(User(telegram_id=772208133, username="me", first_name="Valera", language_code="ru"))
    session.add(User(telegram_id=8329003097, username=None, first_name="Pyari", language_code="ru"))
    await session.commit()

    text = await platform_support_text(session)
    assert "Пользователей: <b>2</b>" in text
    assert "<code>772208133</code>" in text
    assert "@me" in text
    assert "<code>8329003097</code>" in text
    assert "без username" in text


async def test_superadmin_lists_paid_subscriber_ids(session):
    from datetime import datetime, timedelta, timezone

    from src.database.models.studio import TARIFF_FREE, TARIFF_PLUS, TARIFF_STARTER, Studio
    from src.handlers.admin_commands import paid_subscribers_messages
    from src.services.tariffs import list_active_paid_studios

    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    for i, (tid, tariff, until) in enumerate(
        (
            (111001, TARIFF_STARTER, now + timedelta(days=10)),
            (111002, TARIFF_PLUS, now + timedelta(days=3)),
            (111003, TARIFF_STARTER, now - timedelta(days=1)),
            (111004, TARIFF_FREE, now + timedelta(days=30)),
        ),
        start=1,
    ):
        owner = User(telegram_id=tid, first_name=f"Owner{i}", language_code="ru")
        session.add(owner)
        await session.flush()
        session.add(
            Studio(
                slug=f"studio-{i}",
                name=f"Студия {i}",
                owner_id=owner.id,
                owner_telegram_id=tid,
                tariff=tariff,
                subscription_until=until,
            )
        )
    await session.commit()

    paid = await list_active_paid_studios(session, now=now)
    ids = {s.owner_telegram_id for s in paid}
    assert ids == {111001, 111002}

    chunks = await paid_subscribers_messages(session, now=now)
    text = "\n".join(chunks)
    assert "Платных подписчиков: <b>2</b>" in text
    assert "<code>111001</code>" in text
    assert "<code>111002</code>" in text
    assert "111003" not in text
    assert "111004" not in text


def test_go_live_runbook_has_webhook():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    go_live = (root / "docs" / "go_live.md").read_text(encoding="utf-8")
    assert "https://studiobook.com.ru/prodamus/webhook" in go_live
    assert "https://studiobook.com.ru/yookassa/webhook" in go_live
    assert "data/backups/" in go_live
    assert "studio_book.before-restore.db" in go_live
    owner = (root / "src" / "handlers" / "owner.py").read_text(encoding="utf-8")
    assert "ow:guide" in owner
    assert "ow:win" in owner


def test_landing_render_uses_studio_book_username(tmp_path):
    from src.web.app import _render_landing

    page = tmp_path / "x.html"
    page.write_text('<a href="{{BOT_LINK}}">t.me/{{BOT_USERNAME}}</a>', encoding="utf-8")
    html = _render_landing(page, "Studio_book_bot")
    assert "t.me/Studio_book_bot" in html
    assert "Saas_concept_bot" not in html


def test_owner_cheat_sheet_covers_buttons():
    from src.keyboards.inline import owner_cabinet_keyboard
    from src.services.outreach import owner_cheat_sheet

    text = owner_cheat_sheet()
    assert "Ссылка записи" in text
    assert "Брони" not in text
    assert "Открыть окно" in text
    assert "Заявки" in text
    assert "Сетка цен" not in text
    assert "iCal" not in text
    assert "Плюс" not in text
    assert len(text) < 3500
    from src.services.outreach import owner_next_steps

    assert "Ссылка записи" in owner_next_steps()
    markup = owner_cabinet_keyboard()
    datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert datas == [
        "ow:guide",
        "ow:req",
        "ow:link",
        "ow:win",
        "ow:wins",
        "ow:tariff",
    ]


async def test_legacy_db_adds_is_blocked_and_studio_slug(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    from src.database import create_async_engine, get_session_maker
    from src.database.bootstrap import init_database
    from src.database.models.studio import Studio
    from src.database.models.user import User

    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            username VARCHAR(64),
            first_name VARCHAR(64) NOT NULL,
            last_name VARCHAR(64),
            language_code VARCHAR(10) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE studios (
            id INTEGER PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            address VARCHAR(256) NOT NULL DEFAULT '',
            phone VARCHAR(32) NOT NULL DEFAULT '',
            about TEXT NOT NULL DEFAULT '',
            master_telegram_id BIGINT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY,
            studio_id INTEGER NOT NULL,
            name VARCHAR(128) NOT NULL,
            duration_min INTEGER NOT NULL DEFAULT 60,
            slot_step_min INTEGER NOT NULL DEFAULT 60,
            min_duration_min INTEGER NOT NULL DEFAULT 60,
            buffer_min INTEGER NOT NULL DEFAULT 5,
            hour_markup_percent INTEGER NOT NULL DEFAULT 50,
            timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Moscow',
            work_start TIME NOT NULL,
            work_end TIME NOT NULL,
            weekdays VARCHAR(32) NOT NULL DEFAULT '1,2,3,4,5,6,7',
            is_active BOOLEAN NOT NULL DEFAULT 1,
            price_rub INTEGER NOT NULL DEFAULT 0,
            weekend_price_rub INTEGER NOT NULL DEFAULT 0,
            night_price_rub INTEGER NOT NULL DEFAULT 0,
            night_start TIME NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE windows (
            id INTEGER PRIMARY KEY,
            starts_at DATETIME NOT NULL,
            ends_at DATETIME NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    con.execute(
        "INSERT INTO users (telegram_id, username, first_name, language_code, created_at, updated_at) "
        "VALUES (772208133, 'owner', 'Valera', 'ru', ?, ?)",
        (now, now),
    )
    con.execute(
        "INSERT INTO studios (name, address, phone, about, master_telegram_id, created_at, updated_at) "
        "VALUES ('Студия наращивания ресниц', '', '', '', 772208133, ?, ?)",
        (now, now),
    )
    con.commit()
    con.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_database(engine)
    maker = get_session_maker(engine)
    async with maker() as session:
        from sqlalchemy import select

        from src.services.studios import list_active_resources

        user = (await session.execute(select(User).where(User.telegram_id == 772208133))).scalar_one()
        assert user.is_blocked is False
        studio = (await session.execute(select(Studio))).scalar_one()
        assert studio.slug
        assert studio.owner_id == user.id
        assert studio.owner_telegram_id == 772208133
        halls = await list_active_resources(session, studio.id)
        assert len(halls) == 1
    await engine.dispose()
