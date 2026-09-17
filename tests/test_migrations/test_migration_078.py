import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration078:
    @pytest.mark.asyncio
    async def test_adds_auto_update_default_off(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 77)
            await conn.execute(
                """
                CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 77

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "auto_update" in columns

            row = await (
                await conn.execute("SELECT auto_update FROM app_settings WHERE id = 1")
            ).fetchone()
            assert row["auto_update"] == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_runs_on_a_database_without_app_settings(self):
        """A database that never had the table must not fail the upgrade."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 77)
            await conn.commit()
            await run_migrations(conn)
        finally:
            await conn.close()
