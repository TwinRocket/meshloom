import json
import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION, migrations_after

_PLUGIN_TABLES = ("plugins", "plugin_kv", "plugin_secrets")


def _plugin_schema(path: str) -> dict[tuple[str, str], str]:
    """``(type, name) -> sql`` for the plugin tables and their indexes, plus each
    table's ``PRAGMA table_info`` so column order and defaults count too."""
    conn = sqlite3.connect(path)
    try:
        placeholders = ",".join("?" for _ in _PLUGIN_TABLES)
        rows = conn.execute(
            f"SELECT type, name, sql FROM sqlite_master WHERE tbl_name IN ({placeholders})",
            _PLUGIN_TABLES,
        ).fetchall()
        schema = {(kind, name): " ".join((sql or "").split()) for kind, name, sql in rows}
        for table in _PLUGIN_TABLES:
            schema[("info", table)] = repr(conn.execute(f"PRAGMA table_info({table})").fetchall())
    finally:
        conn.close()
    return schema


@pytest.mark.asyncio
async def test_fresh_database_has_the_tables_and_the_bumped_version(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 103
            async with conn.execute("SELECT store_online FROM app_settings WHERE id = 1") as cur:
                assert tuple(await cur.fetchone()) == (0,)
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 102)
        assert await run_migrations(conn) == migrations_after(102)
    finally:
        await conn.close()

    fresh_schema = _plugin_schema(str(fresh))
    assert {name for kind, name in fresh_schema if kind == "table"} == set(_PLUGIN_TABLES)
    assert ("index", "idx_plugins_plugin_id") in fresh_schema
    assert _plugin_schema(str(migrated)) == fresh_schema


@pytest.mark.asyncio
async def test_existing_database_with_bots_keeps_them_and_gets_empty_tables():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 102)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1), share_enabled INTEGER)"
        )
        await conn.execute("INSERT INTO app_settings (id, share_enabled) VALUES (1, 1)")
        await conn.execute(
            """CREATE TABLE fanout_configs (
                id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                enabled INTEGER DEFAULT 0, config TEXT NOT NULL DEFAULT '{}',
                scope TEXT NOT NULL DEFAULT '{}', sort_order INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL)"""
        )
        bot = ("bot-1", "bot", "Pling", 1, json.dumps({"code": "def bot(**k): return None"}))
        await conn.execute(
            "INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order,"
            " created_at) VALUES (?, ?, ?, ?, ?, '{}', 0, 1)",
            bot,
        )
        await conn.commit()
        before = await (await conn.execute("SELECT * FROM fanout_configs")).fetchall()

        await run_migrations(conn)

        assert await (await conn.execute("SELECT * FROM fanout_configs")).fetchall() == before
        for table in _PLUGIN_TABLES:
            cursor = await conn.execute(f"SELECT COUNT(*) FROM {table}")
            assert tuple(await cursor.fetchone()) == (0,), table
        cursor = await conn.execute("SELECT share_enabled, store_online FROM app_settings")
        assert tuple(await cursor.fetchone()) == (1, 0)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rerun_is_harmless_and_keeps_values():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 102)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        await conn.execute("UPDATE app_settings SET store_online = 1")
        await conn.execute(
            "INSERT INTO plugin_kv (instance_id, key, value, updated_at) VALUES ('i', 'k', '1', 0)"
        )
        await set_version(conn, 102)
        await run_migrations(conn)
        cursor = await conn.execute("SELECT store_online FROM app_settings")
        assert tuple(await cursor.fetchone()) == (1,)
        cursor = await conn.execute("SELECT COUNT(*) FROM plugin_kv")
        assert tuple(await cursor.fetchone()) == (1,)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_kind_is_constrained():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 102)
        await run_migrations(conn)
        insert = (
            "INSERT INTO plugins (instance_id, plugin_id, label, kind, version, installed_at,"
            " updated_at) VALUES (?, 'local.x', 'x', ?, '1.0.0', 0, 0)"
        )
        for kind in ("bundled", "store", "custom", "local"):
            await conn.execute(insert, (kind, kind))
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(insert, ("bad", "bot"))
    finally:
        await conn.close()
