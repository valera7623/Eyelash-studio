from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine as sa_create_async_engine,
)

from src.config import settings


def create_async_engine(dsn: str | None = None):
    """Async engine. SQLite сейчас; DSN можно сменить на Postgres без смены моделей."""
    url = dsn or settings.sqlite_dsn
    kwargs: dict = {"echo": False, "future": True}
    if url.startswith("sqlite"):
        kwargs["isolation_level"] = None
        kwargs["connect_args"] = {"timeout": 30}
    eng = sa_create_async_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(eng.sync_engine, "connect")
        def _sqlite_pragma(dbapi_conn, _):
            dbapi_conn.isolation_level = None
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        @event.listens_for(eng.sync_engine, "begin")
        def _sqlite_begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    return eng


def get_session_maker(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
