import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration080:
    @pytest.mark.asyncio
    async def test_adds_path_hash_one_byte_opt_in_default(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 79)
            await conn.execute(
                """
                CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 79

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "path_hash_one_byte_opt_in" in columns

            row = await (
                await conn.execute(
                    "SELECT path_hash_one_byte_opt_in FROM app_settings WHERE id = 1"
                )
            ).fetchone()
            assert row["path_hash_one_byte_opt_in"] == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_runs_on_a_database_without_app_settings(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 79)
            await conn.commit()
            await run_migrations(conn)
        finally:
            await conn.close()
