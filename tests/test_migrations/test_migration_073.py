"""Tests for migration 073: radio proxy listen settings on app_settings."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration073:
    @pytest.mark.asyncio
    async def test_adds_radio_proxy_columns(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 72)
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
            assert applied == LATEST_SCHEMA_VERSION - 72
            assert await get_version(conn) == LATEST_SCHEMA_VERSION

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "radio_proxy_enabled" in columns
            assert "radio_proxy_bind" in columns
            assert "radio_proxy_port" in columns
            assert "radio_proxy_max_clients" in columns

            row = await (
                await conn.execute(
                    """
                    SELECT radio_proxy_enabled, radio_proxy_bind, radio_proxy_port,
                           radio_proxy_max_clients
                    FROM app_settings WHERE id = 1
                    """
                )
            ).fetchone()
            assert row["radio_proxy_enabled"] == 0
            assert row["radio_proxy_bind"] == "0.0.0.0"
            assert row["radio_proxy_port"] == 5001
            assert row["radio_proxy_max_clients"] == 8
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_missing_app_settings_table_does_not_fail(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 72)
            await conn.commit()
            await run_migrations(conn)
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
        finally:
            await conn.close()
