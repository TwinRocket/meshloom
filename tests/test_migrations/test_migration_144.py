"""Migration 144: shared image previews default to ``always``."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

_POLICY = "SELECT share_preview_policy FROM app_settings"


async def _at_143(terms_version: int, policy: str, enabled: int = 0) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    await set_version(conn, 143)
    await conn.execute(
        "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
        " share_enabled INTEGER DEFAULT 0, share_terms_version INTEGER DEFAULT 0,"
        " share_preview_policy TEXT DEFAULT 'private')"
    )
    await conn.execute(
        "INSERT INTO app_settings (id, share_enabled, share_terms_version, share_preview_policy)"
        " VALUES (1, ?, ?, ?)",
        (enabled, terms_version, policy),
    )
    await conn.commit()
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terms_version", "before", "after"),
    [
        (0, "private", "always"),  # terms never accepted: no choice was made
        (0, "never", "never"),
        (1, "private", "private"),  # terms accepted once: the stored value stands
        (1, "never", "never"),
    ],
)
async def test_flips_only_where_the_terms_were_never_accepted(terms_version, before, after):
    conn = await _at_143(terms_version, before)
    try:
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 143
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute(_POLICY) as cursor:
            assert (await cursor.fetchone())[0] == after
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_keeps_the_value_of_an_instance_with_sharing_on():
    conn = await _at_143(0, "private", enabled=1)
    try:
        await run_migrations(conn)
        async with conn.execute(_POLICY) as cursor:
            assert (await cursor.fetchone())[0] == "private"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_second_run_changes_nothing():
    from app.migrations import _144_share_previews_default_always as migration

    conn = await _at_143(0, "private")
    try:
        await migration.migrate(conn)
        await conn.execute("UPDATE app_settings SET share_preview_policy = 'never'")
        await migration.migrate(conn)
        async with conn.execute(_POLICY) as cursor:
            assert (await cursor.fetchone())[0] == "never"
    finally:
        await conn.close()
