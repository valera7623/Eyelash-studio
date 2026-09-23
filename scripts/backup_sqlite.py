#!/usr/bin/env python3
"""Разовая копия SQLite (также вызывается планировщиком в 03:15 MSK)."""

from src.services.jobs import backup_sqlite

if __name__ == "__main__":
    path = backup_sqlite()
    print(path or "no sqlite file")
