"""Migration 123: ``user_notification_rules`` (MSHL-321)."""

import json
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
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_notification_rules'"
        ).fetchone()
        assert row is not None
        info = conn.execute("PRAGMA table_info(user_notification_rules)").fetchall()
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
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 123
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 122)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 122
    finally:
        await conn.close()
    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_the_current_rules_stay_the_defaults_and_no_account_gets_a_row(tmp_path):
    path = tmp_path / "a.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    rules = {"events": {"message.dm": {"channels": ["builtin:push"]}}}
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute("DROP TABLE user_notification_rules")
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1", (json.dumps(rules),)
        )
        await set_version(conn, 122)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 122
        cursor = await conn.execute("SELECT notification_rules FROM app_settings WHERE id = 1")
        assert json.loads((await cursor.fetchone())[0]) == rules
        cursor = await conn.execute("SELECT COUNT(*) FROM user_notification_rules")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()
