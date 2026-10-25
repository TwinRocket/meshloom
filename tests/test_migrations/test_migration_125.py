"""Migration 125: ``plugin_kept_data`` (MSHL-366)."""

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
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'plugin_kept_data'"
        ).fetchone()
        assert row is not None
        info = conn.execute("PRAGMA table_info(plugin_kept_data)").fetchall()
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
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 125
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 124)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 124
    finally:
        await conn.close()
    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_rows_left_by_earlier_uninstalls_start_their_retention(tmp_path):
    path = tmp_path / "a.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute("DROP TABLE plugin_kept_data")
        await conn.execute(
            "INSERT INTO plugins (instance_id, plugin_id, label, kind, version, installed_at,"
            " updated_at) VALUES ('live', 'acme.echo', 'Echo', 'custom', '1', 0, 0)"
        )
        for iid in ("live", "gone-kv", "gone-both"):
            await conn.execute(
                "INSERT INTO plugin_kv (instance_id, key, value, updated_at) VALUES (?, 'k', '1', 0)",
                (iid,),
            )
        for iid in ("gone-both", "gone-secret"):
            await conn.execute(
                "INSERT INTO plugin_secrets (instance_id, field, value, updated_at)"
                " VALUES (?, 'token', 'x', 0)",
                (iid,),
            )
        await set_version(conn, 124)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 124
        cursor = await conn.execute(
            "SELECT instance_id, plugin_id, kind, repository, kept_at > 0"
            " FROM plugin_kept_data ORDER BY instance_id"
        )
        assert await cursor.fetchall() == [
            ("gone-both", "", "", None, 1),
            ("gone-kv", "", "", None, 1),
            ("gone-secret", "", "", None, 1),
        ]
        # Nothing is deleted by the migration itself.
        cursor = await conn.execute("SELECT COUNT(*) FROM plugin_kv")
        assert (await cursor.fetchone())[0] == 3
    finally:
        await conn.close()
