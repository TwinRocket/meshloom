"""Migration 143: shared image previews default to ``always``."""

import aiosqlite
import pytest

from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

_POLICY = "SELECT share_preview_policy FROM app_settings"


async def _at_142(enabled: int, policy: str) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    await set_version(conn, 142)
    await conn.execute(
        "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
        " share_enabled INTEGER DEFAULT 0, share_preview_policy TEXT DEFAULT 'private')"
    )
    await conn.execute(
        "INSERT INTO app_settings (id, share_enabled, share_preview_policy) VALUES (1, ?, ?)",
        (enabled, policy),
    )
    await conn.commit()
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "before", "after"),
    [
        (0, "private", "always"),  # sharing never turned on: no choice was made
        (0, "never", "never"),
        (1, "private", "private"),  # sharing on: the stored value stands
        (1, "never", "never"),
    ],
)
async def test_flips_only_the_default_of_an_instance_without_sharing(enabled, before, after):
    conn = await _at_142(enabled, before)
    try:
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 142
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        async with conn.execute(_POLICY) as cursor:
            assert (await cursor.fetchone())[0] == after
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_second_run_changes_nothing():
    from app.migrations import _143_share_previews_default_always as migration

    conn = await _at_142(0, "private")
    try:
        await migration.migrate(conn)
        await conn.execute("UPDATE app_settings SET share_preview_policy = 'never'")
        await migration.migrate(conn)
        async with conn.execute(_POLICY) as cursor:
            assert (await cursor.fetchone())[0] == "never"
    finally:
        await conn.close()
