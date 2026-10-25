"""Migration 122: 4.18.x bots become local plugins waiting for review (MSHL-192)."""

import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version
from app.migrations._122_bots_to_plugins import migrate
from app.plugins import bot_conversion
from app.plugins.manifest import parse_manifest
from app.services.legacy_env_settings import ENV_BOT_LOCK

from .conftest import LATEST_SCHEMA_VERSION

DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "default_bot_4_18.txt"
).read_text(encoding="utf-8")
ECHO = "def bot(**kwargs):\n    return kwargs['message_text']\n"


@pytest.fixture(autouse=True)
def _no_lock(monkeypatch):
    monkeypatch.delenv(ENV_BOT_LOCK, raising=False)


def _schema(path: str) -> tuple[str, str]:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (bot_conversion.ARCHIVE_TABLE,),
        ).fetchone()
        assert row is not None
        info = conn.execute(f"PRAGMA table_info({bot_conversion.ARCHIVE_TABLE})").fetchall()
        return " ".join(row[0].split()), repr(info)
    finally:
        conn.close()


async def test_fresh_and_migrated_databases_have_the_same_archive_table(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    try:
        async with database.readonly() as conn:
            assert await get_version(conn) == LATEST_SCHEMA_VERSION >= 122
    finally:
        await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 121)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 121
    finally:
        await conn.close()

    assert _schema(str(migrated)) == _schema(str(fresh))


async def _database_with_bots(tmp_path, bots: list[tuple]) -> aiosqlite.Connection:
    """A full database at the latest version, then 4.18 bot rows put back in."""
    path = tmp_path / "bots.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    for config_id, name, enabled, code, scope, order in bots:
        await conn.execute(
            "INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order,"
            " created_at) VALUES (?, 'bot', ?, ?, ?, ?, ?, 100)",
            (config_id, name, enabled, json.dumps({"code": code}), json.dumps(scope), order),
        )
    await conn.commit()
    return conn


async def _plugins(conn: aiosqlite.Connection) -> list[dict]:
    conn.row_factory = aiosqlite.Row
    async with conn.execute("SELECT * FROM plugins ORDER BY sort_order") as cursor:
        rows = [dict(row) for row in await cursor.fetchall()]
    conn.row_factory = None
    return rows


async def test_each_bot_becomes_a_local_plugin_waiting_for_review(tmp_path):
    conn = await _database_with_bots(
        tmp_path,
        [
            ("b1", "Echo", 1, ECHO, {"messages": "all"}, 0),
            ("b2", "Default", 0, DEFAULT_TEMPLATE, {"messages": {"channels": ["abc"]}}, 1),
        ],
    )
    try:
        await migrate(conn)
        async with conn.execute("SELECT COUNT(*) FROM fanout_configs WHERE type = 'bot'") as cur:
            assert (await cur.fetchone())[0] == 0
        plugins = [p for p in await _plugins(conn) if p["kind"] == "local"]
        assert [p["label"] for p in plugins] == ["Echo", "Default"]
        echo, default = plugins
        assert echo["source"] == ECHO and default["source"] == DEFAULT_TEMPLATE
        assert echo["plugin_id"].startswith("local.")
        assert (echo["enabled"], default["enabled"]) == (1, 0)
        assert {p["status"] for p in plugins} == {"needs_attention"}
        assert json.loads(echo["scope"]) == {}
        assert json.loads(default["scope"]) == {"messages": {"channels": ["abc"]}}
        assert json.loads(echo["granted_permissions"]) == bot_conversion.CONVERTED_PERMISSIONS

        attention = json.loads(default["attention"])
        assert attention["type"] == "review_permissions" and attention["converted_bot"]
        # The shipped 4.18 template carries nothing the scan flags.
        assert attention["reasons"] == []

        manifest = json.loads(echo["manifest"])
        assert manifest["entry"] == bot_conversion.COMPAT_ENTRY
        parsed = parse_manifest(manifest, origin="local")
        assert parsed.id == echo["plugin_id"]

        async with conn.execute(
            f"SELECT id, plugin_instance_id, notice_acknowledged FROM"
            f" {bot_conversion.ARCHIVE_TABLE} ORDER BY sort_order"
        ) as cur:
            archived = await cur.fetchall()
        assert archived == [
            ("b1", echo["instance_id"], 0),
            ("b2", default["instance_id"], 0),
        ]
    finally:
        await conn.close()


async def test_the_old_lock_stores_every_bot_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_BOT_LOCK, "true")
    conn = await _database_with_bots(tmp_path, [("b1", "Echo", 1, ECHO, {}, 0)])
    try:
        await migrate(conn)
        plugins = [p for p in await _plugins(conn) if p["kind"] == "local"]
        assert [(p["label"], p["enabled"]) for p in plugins] == [("Echo", 0)]
    finally:
        await conn.close()


async def test_no_bot_changes_nothing(tmp_path):
    conn = await _database_with_bots(tmp_path, [])
    try:
        before = await _plugins(conn)
        await migrate(conn)
        assert await _plugins(conn) == before
    finally:
        await conn.close()
