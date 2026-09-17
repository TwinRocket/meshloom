import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration079:
    @pytest.mark.asyncio
    async def test_adds_auto_update_window_defaults(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 78)
            await conn.execute(
                """
                CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 78

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "auto_update_window_start" in columns
            assert "auto_update_window_end" in columns
            assert "auto_update_weekdays" in columns
            assert "last_notified_update_version" in columns

            row = await (
                await conn.execute(
                    """
                    SELECT auto_update_window_start, auto_update_window_end,
                           auto_update_weekdays, last_notified_update_version
                    FROM app_settings WHERE id = 1
                    """
                )
            ).fetchone()
            assert row["auto_update_window_start"] == "00:00"
            assert row["auto_update_window_end"] == "00:00"
            assert row["auto_update_weekdays"] == "[0,1,2,3,4,5,6]"
            assert row["last_notified_update_version"] is None
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_runs_on_a_database_without_app_settings(self):
        """A database that never had the table must not fail the upgrade."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 78)
            await conn.commit()
            await run_migrations(conn)
        finally:
            await conn.close()
