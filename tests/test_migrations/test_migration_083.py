import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration083:
    @pytest.mark.asyncio
    async def test_adds_muted_until(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 82)
            await conn.execute(
                """
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    muted INTEGER DEFAULT 0
                )
                """
            )
            await conn.execute(
                "INSERT INTO channels (key, name, muted) VALUES (?, ?, 1)",
                ("AA" * 16, "#old"),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 82

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(channels)")).fetchall()
            }
            assert "muted_until" in columns
        finally:
            await conn.close()
