from pathlib import Path

import pytest_asyncio

from src.database import create_async_engine, get_session_maker
from src.database.bootstrap import init_database


@pytest_asyncio.fixture
async def engine(tmp_path: Path):
    db_path = tmp_path / "test.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    await init_database(eng)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = get_session_maker(engine)
    async with maker() as sess:
        yield sess
