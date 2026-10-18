"""Tests for migration 070: Meshloom Stats community columns on app_settings."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration070:
    @pytest.mark.asyncio
    async def test_adds_community_columns(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 69)
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
            assert applied == LATEST_SCHEMA_VERSION - 69
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "community_enabled" in columns
            assert "community_iata" in columns
            assert "community_broker_host" in columns
            assert "community_api_base" in columns

            row = await (
                await conn.execute(
                    "SELECT community_enabled, community_iata, community_broker_host, "
                    "community_api_base FROM app_settings WHERE id = 1"
                )
            ).fetchone()
            assert row["community_enabled"] == 0
            assert row["community_iata"] == ""
            assert row["community_broker_host"] == ""
            assert row["community_api_base"] == ""
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_missing_app_settings_table_does_not_fail(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 69)
            await conn.commit()

            await run_migrations(conn)
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
        finally:
            await conn.close()
