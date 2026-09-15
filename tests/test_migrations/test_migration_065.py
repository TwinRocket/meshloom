"""Tests for migration 065: directory hop cache (its settings are dropped by 076)."""

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


class TestMigration065:
    @pytest.mark.asyncio
    async def test_adds_cache_table_and_settings(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 64)
            await conn.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY,
                    stale_contact_days INTEGER DEFAULT 0
                )
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            await run_migrations(conn)

            tables = {
                row[0]
                for row in await (
                    await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            assert "directory_hop_cache" in tables

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "directory_enabled" not in columns
            assert "directory_url" not in columns
        finally:
            await conn.close()
