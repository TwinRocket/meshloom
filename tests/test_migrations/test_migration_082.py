import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration082:
    @pytest.mark.asyncio
    async def test_adds_membership_and_rejected_channels(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 81)
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
                """
                CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.execute(
                "INSERT INTO channels (key, name) VALUES (?, ?)",
                ("AA" * 16, "#old"),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 81

            channel_columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(channels)")).fetchall()
            }
            settings_columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "membership" in channel_columns
            assert "rejected_channels" in settings_columns

            row = await (
                await conn.execute("SELECT membership FROM channels WHERE key = ?", ("AA" * 16,))
            ).fetchone()
            assert row["membership"] == "adopted"
        finally:
            await conn.close()
