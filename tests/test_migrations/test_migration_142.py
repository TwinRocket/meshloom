"""Migration 142: cached Community attestations (MSHL-445)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_creates_the_table_and_is_idempotent():
    from app.migrations import _142_community_attestations as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 141)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 141
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        await conn.execute(
            "INSERT INTO community_attestations"
            " (audience, instance_key, token, issued_at, expires_at, fetched_at)"
            " VALUES ('links', 'AB', 't', 1, 2, 1)"
        )
        await migration.migrate(conn)  # a second run keeps the row
        async with conn.execute("SELECT COUNT(*) FROM community_attestations") as cursor:
            assert (await cursor.fetchone())[0] == 1
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO community_attestations"
                " (audience, instance_key, token, issued_at, expires_at, fetched_at)"
                " VALUES ('other', 'AB', 't', 1, 2, 1)"
            )
    finally:
        await conn.close()
