"""Migration 118: the share terms acceptance columns."""

import aiosqlite

from app.migrations import _116_share_terms_acceptance


async def _columns(conn: aiosqlite.Connection) -> dict[str, tuple]:
    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    return {row[1]: row for row in await cursor.fetchall()}


async def test_adds_both_columns_at_zero_for_an_existing_row():
    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1), share_enabled INTEGER)"
        )
        await conn.execute("INSERT INTO app_settings (id, share_enabled) VALUES (1, 1)")
        await _116_share_terms_acceptance.migrate(conn)
        cursor = await conn.execute(
            "SELECT share_enabled, share_terms_version, share_terms_accepted_at FROM app_settings"
        )
        # Sharing was on, but no acceptance was ever recorded: version 0 asks again.
        assert tuple(await cursor.fetchone()) == (1, 0, 0)


async def test_is_idempotent():
    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY)")
        await _116_share_terms_acceptance.migrate(conn)
        await _116_share_terms_acceptance.migrate(conn)
        assert {"share_terms_version", "share_terms_accepted_at"} <= set(await _columns(conn))


async def test_tolerates_a_database_without_app_settings():
    async with aiosqlite.connect(":memory:") as conn:
        await _116_share_terms_acceptance.migrate(conn)
