import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration084:
    @pytest.mark.asyncio
    async def test_adds_pinned_to_contacts_and_channels(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 83)
            await conn.execute(
                """
                CREATE TABLE contacts (
                    public_key TEXT PRIMARY KEY,
                    name TEXT,
                    favorite INTEGER DEFAULT 0
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE channels (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    favorite INTEGER DEFAULT 0
                )
                """
            )
            await conn.execute(
                "INSERT INTO contacts (public_key, name) VALUES (?, ?)",
                ("aa" * 32, "Alice"),
            )
            await conn.execute(
                "INSERT INTO channels (key, name) VALUES (?, ?)",
                ("AA" * 16, "#old"),
            )
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 83

            contact_columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(contacts)")).fetchall()
            }
            channel_columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(channels)")).fetchall()
            }
            assert "pinned" in contact_columns
            assert "pinned" in channel_columns
        finally:
            await conn.close()
