"""Migration 118: push subscriptions gain an owner, existing ones go to the first admin."""

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

_OLD_TABLE = """
CREATE TABLE push_subscriptions (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'fr',
    created_at INTEGER NOT NULL,
    last_success_at INTEGER,
    failure_count INTEGER DEFAULT 0,
    UNIQUE(endpoint)
)
"""


async def _database_at_117(path, users: list[tuple[str, str, int]]) -> None:
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute("DROP INDEX IF EXISTS idx_push_subscriptions_user")
        await conn.execute("DROP TABLE push_subscriptions")
        await conn.execute(_OLD_TABLE)
        for name, role, disabled in users:
            await conn.execute(
                "INSERT INTO users (username, username_key, role, disabled, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 0, 0)",
                (name, name, role, disabled),
            )
        for n in range(2):
            await conn.execute(
                "INSERT INTO push_subscriptions (id, endpoint, p256dh, auth, created_at)"
                " VALUES (?, ?, 'p', 'a', 0)",
                (f"sub-{n}", f"https://push.example/{n}"),
            )
        await conn.commit()
        await set_version(conn, 117)
    finally:
        await conn.close()


async def _migrate(path) -> list[tuple]:
    conn = await aiosqlite.connect(path)
    try:
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 117
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        columns = {
            row[1] for row in await conn.execute_fetchall("PRAGMA table_info(push_subscriptions)")
        }
        assert "user_id" in columns
        indexes = {
            row[1] for row in await conn.execute_fetchall("PRAGMA index_list(push_subscriptions)")
        }
        assert "idx_push_subscriptions_user" in indexes
        fks = await conn.execute_fetchall("PRAGMA foreign_key_list(push_subscriptions)")
        assert [(fk[2], fk[3], fk[6]) for fk in fks] == [("users", "user_id", "CASCADE")]
        return list(
            await conn.execute_fetchall("SELECT id, user_id FROM push_subscriptions ORDER BY id")
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_existing_devices_go_to_the_first_enabled_admin(tmp_path):
    path = tmp_path / "a.db"
    # ids: 1 disabled admin, 2 member, 3 enabled admin
    await _database_at_117(path, [("old", "admin", 1), ("bob", "member", 0), ("alice", "admin", 0)])
    assert await _migrate(path) == [("sub-0", 3), ("sub-1", 3)]


@pytest.mark.asyncio
async def test_without_an_admin_devices_stay_unowned(tmp_path):
    path = tmp_path / "b.db"
    await _database_at_117(path, [("bob", "member", 0)])
    assert await _migrate(path) == [("sub-0", None), ("sub-1", None)]
