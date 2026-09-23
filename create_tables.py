#!/usr/bin/env python3
"""Создание только файла SQLite и таблиц (без запуска бота)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import settings
from src.database import create_async_engine
from src.database.bootstrap import init_database


async def main():
    settings.SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine()
    await init_database(engine)
    await engine.dispose()
    print(f"✅ SQLite готов: {settings.SQLITE_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
