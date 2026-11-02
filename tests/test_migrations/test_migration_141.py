"""Migration 141: e-mail sign-in settings and per-account e-mail factor (MSHL-426)."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_creates_both_tables_and_is_idempotent():
    from app.migrations import _141_email_sign_in as migration

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 140)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 140
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute("SELECT id, channel_id FROM email_sign_in") as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == [(1, None)]
        await conn.execute(
            "INSERT INTO user_email_factor (user_id, address, verified_at, updated_at)"
            " VALUES (1, 'a@example.org', 1, 1)"
        )
        await migration.migrate(conn)  # a second run keeps the rows
        async with conn.execute(
            "SELECT otp_enabled, magic_link_enabled FROM user_email_factor"
        ) as cursor:
            assert [tuple(row) for row in await cursor.fetchall()] == [(0, 0)]
        async with conn.execute("SELECT COUNT(*) FROM email_sign_in") as cursor:
            assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()
