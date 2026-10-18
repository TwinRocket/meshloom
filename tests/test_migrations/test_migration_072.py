"""Tests for migration 072: seed #meshloom on databases that already ran 033."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from app.migrations._072_seed_meshloom_channel import MESHLOOM_CHANNEL_KEY
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration072:
    @pytest.mark.asyncio
    async def test_seeds_meshloom_when_only_legacy_remoteterm_exists(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 71)
            await conn.execute(
                """
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    is_hashtag INTEGER DEFAULT 0,
                    on_radio INTEGER DEFAULT 0
                )
                """
            )
            await conn.execute(
                "INSERT INTO channels (key, name, is_hashtag, on_radio) VALUES (?, ?, ?, ?)",
                ("8959AE053F2201801342A1DBDDA184F6", "#remoteterm", 1, 0),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 71
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            row = await (
                await conn.execute(
                    "SELECT name, is_hashtag, on_radio FROM channels WHERE key = ?",
                    (MESHLOOM_CHANNEL_KEY,),
                )
            ).fetchone()
            assert row is not None
            assert row["name"] == "#meshloom"
            assert row["is_hashtag"] == 1
            assert row["on_radio"] == 0

            legacy = await (
                await conn.execute(
                    "SELECT name FROM channels WHERE key = ?",
                    ("8959AE053F2201801342A1DBDDA184F6",),
                )
            ).fetchone()
            assert legacy["name"] == "#remoteterm"
        finally:
            await conn.close()
