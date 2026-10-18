"""Tests for migration 071: push notification defaults and conversation overrides."""

import json

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION

DEFAULT_PUSH_DEFAULTS = {
    "new_contact": True,
    "new_dm": True,
    "advert_repeater": True,
    "advert_companion": True,
    "advert_sensor": True,
}


class TestMigration071:
    @pytest.mark.asyncio
    async def test_adds_push_pref_columns_on_fresh_settings(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 70)
            await conn.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1)
                )
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 70
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "push_defaults" in columns
            assert "push_conversation_overrides" in columns
            assert "vapid_subject" in columns

            row = await (
                await conn.execute(
                    """
                    SELECT push_defaults, push_conversation_overrides, vapid_subject
                    FROM app_settings WHERE id = 1
                    """
                )
            ).fetchone()
            assert json.loads(row["push_defaults"]) == DEFAULT_PUSH_DEFAULTS
            assert json.loads(row["push_conversation_overrides"]) == {}
            assert row["vapid_subject"] == ""
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_imports_push_conversations_into_overrides(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 70)
            await conn.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    push_conversations TEXT DEFAULT '[]'
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO app_settings (id, push_conversations)
                VALUES (1, ?)
                """,
                (json.dumps(["contact-aabbccddeeff", "channel-PUBLICKEY"]),),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 70
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            row = await (
                await conn.execute(
                    """
                    SELECT push_conversations, push_conversation_overrides, push_defaults,
                           vapid_subject
                    FROM app_settings WHERE id = 1
                    """
                )
            ).fetchone()
            assert json.loads(row["push_conversations"]) == [
                "contact-aabbccddeeff",
                "channel-PUBLICKEY",
            ]
            assert json.loads(row["push_conversation_overrides"]) == {
                "contact-aabbccddeeff": True,
                "channel-PUBLICKEY": True,
            }
            assert json.loads(row["push_defaults"]) == DEFAULT_PUSH_DEFAULTS
            assert row["vapid_subject"] == ""
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_missing_app_settings_table_does_not_fail(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 70)
            await conn.commit()

            await run_migrations(conn)
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
        finally:
            await conn.close()
