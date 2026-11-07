"""Migration 115: the ``message.mention`` rule copied from ``message.dm``."""

import json

import aiosqlite
import pytest

from app.database import Database
from app.migrations import _115_notification_mention_rule, get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


async def _database_at_114(path, rules: str | None):
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute("UPDATE app_settings SET notification_rules = ? WHERE id = 1", (rules,))
        await conn.commit()
        await set_version(conn, 114)
    finally:
        await conn.close()


async def _rules(path) -> dict | None:
    conn = await aiosqlite.connect(path)
    try:
        cursor = await conn.execute("SELECT notification_rules FROM app_settings WHERE id = 1")
        raw = (await cursor.fetchone())[0]
        return json.loads(raw) if raw else None
    finally:
        await conn.close()


async def _migrate(path) -> None:
    conn = await aiosqlite.connect(path)
    try:
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 114
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mention_follows_the_channels_of_message_dm(tmp_path):
    path = tmp_path / "a.db"
    rules = {"events": {"message.dm": {"channels": ["builtin:push", "legacy:email"]}}}
    await _database_at_114(path, json.dumps(rules))
    await _migrate(path)
    stored = await _rules(path)
    assert stored["events"]["message.mention"] == {
        "channels": ["builtin:push", "legacy:email"],
        "always_deliver": False,
    }
    assert stored["events"]["message.dm"]["channels"] == ["builtin:push", "legacy:email"]


@pytest.mark.asyncio
async def test_an_existing_entry_even_empty_is_kept(tmp_path):
    path = tmp_path / "b.db"
    rules = {
        "events": {
            "message.dm": {"channels": ["builtin:push"]},
            "message.mention": {"channels": [], "always_deliver": True},
        }
    }
    await _database_at_114(path, json.dumps(rules))
    await _migrate(path)
    assert (await _rules(path))["events"]["message.mention"] == {
        "channels": [],
        "always_deliver": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "", "not json", "[]", '{"events": []}'])
async def test_missing_or_unreadable_rules_are_left_alone(tmp_path, raw):
    path = tmp_path / "c.db"
    await _database_at_114(path, raw)
    await _migrate(path)
    conn = await aiosqlite.connect(path)
    try:
        cursor = await conn.execute("SELECT notification_rules FROM app_settings WHERE id = 1")
        assert (await cursor.fetchone())[0] == raw
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_second_run_changes_nothing(tmp_path):
    path = tmp_path / "d.db"
    await _database_at_114(path, json.dumps({"events": {"message.dm": {"channels": ["x"]}}}))
    await _migrate(path)
    once = await _rules(path)
    conn = await aiosqlite.connect(path)
    try:
        await _115_notification_mention_rule.migrate(conn)
    finally:
        await conn.close()
    assert await _rules(path) == once
