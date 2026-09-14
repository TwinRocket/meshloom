import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration075:
    """Interface preferences move off each browser and onto the instance."""

    @pytest.mark.asyncio
    async def test_adds_ui_preferences_column(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 74)
            await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 74

            cursor = await conn.execute("PRAGMA table_info(app_settings)")
            columns = {row[1] for row in await cursor.fetchall()}
            assert "ui_preferences" in columns
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_runs_on_a_database_without_app_settings(self):
        """A database that never had the table must not fail the upgrade."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 74)
            await conn.commit()
            await run_migrations(conn)
        finally:
            await conn.close()
