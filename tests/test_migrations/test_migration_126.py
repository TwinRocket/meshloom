"""Migration 126: ``store_repositories`` (MSHL-196)."""

import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


def _schema(path: str) -> tuple[str, str]:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'store_repositories'"
        ).fetchone()
        assert row is not None
        info = conn.execute("PRAGMA table_info(store_repositories)").fetchall()
        return " ".join(row[0].split()), repr(info)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 126
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 125)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 125
    finally:
        await conn.close()
    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_migration_is_idempotent_on_an_existing_table(tmp_path):
    path = tmp_path / "a.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute(
            "INSERT INTO store_repositories (repository, display_name, added_at)"
            " VALUES ('acme/weather', 'Acme/Weather', 1)"
        )
        await set_version(conn, 125)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 125
        cursor = await conn.execute("SELECT repository, display_name FROM store_repositories")
        assert await cursor.fetchall() == [("acme/weather", "Acme/Weather")]
    finally:
        await conn.close()
