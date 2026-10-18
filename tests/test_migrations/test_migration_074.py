"""Tests for migration 074: radio proxy listen settings on app_settings."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version
from tests.test_migrations.conftest import LATEST_SCHEMA_VERSION


class TestMigration074:
    @pytest.mark.asyncio
    async def test_adds_radio_proxy_columns(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 73)
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
            assert applied == LATEST_SCHEMA_VERSION - 73
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

            table_rows = await (
                await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
            tables = {r[0] for r in table_rows}
            assert "repeater_pane_cache" in tables
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_upgrades_v73_repeater_cache_databases_that_never_got_proxy_columns(self):
        """Production shape after 073 was pane-cache: user_version=73, no proxy cols."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 73)
            await conn.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1)
                )
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.execute(
                """
                CREATE TABLE repeater_pane_cache (
                    public_key TEXT NOT NULL,
                    pane TEXT NOT NULL,
                    data TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (public_key, pane)
                )
                """
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 73
            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert {
                "radio_proxy_enabled",
                "radio_proxy_bind",
                "radio_proxy_port",
                "radio_proxy_max_clients",
            } <= columns
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_missing_app_settings_table_does_not_fail(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 73)
            await conn.commit()
            await run_migrations(conn)
            assert await get_version(conn) == LATEST_SCHEMA_VERSION
        finally:
            await conn.close()
