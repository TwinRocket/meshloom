"""Migration 128: ``store_repositories.readme`` (MSHL-198)."""

import aiosqlite
import pytest

from app.database import Database
from app.migrations import _128_store_repository_readme as migration
from app.migrations import run_migrations, set_version
from app.migrations._126_store_repositories import _STATEMENT


@pytest.mark.asyncio
async def test_upgrade_adds_an_empty_column_and_keeps_rows():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 126)
        await conn.execute(_STATEMENT)
        await conn.execute(
            "INSERT INTO store_repositories (repository, display_name, added_at)"
            " VALUES ('acme/echo', 'Acme/Echo', 1)"
        )
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT repository, readme FROM store_repositories")
        assert await cursor.fetchall() == [("acme/echo", None)]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_skips_without_the_table_and_is_idempotent():
    conn = await aiosqlite.connect(":memory:")
    try:
        await migration.migrate(conn)
        await conn.execute(_STATEMENT)
        await migration.migrate(conn)
        await migration.migrate(conn)
        cursor = await conn.execute("PRAGMA table_info(store_repositories)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("readme") == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_database_has_the_column(tmp_path):
    database = Database(str(tmp_path / "fresh.db"))
    await database.connect()
    try:
        async with database.readonly() as conn:
            async with conn.execute("SELECT readme FROM store_repositories") as cursor:
                assert await cursor.fetchall() == []
    finally:
        await database.disconnect()
