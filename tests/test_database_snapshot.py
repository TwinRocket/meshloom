"""The per-test database (loaded from a template image) equals a really migrated one.

``patched_database`` serializes one fully migrated ``:memory:`` database per
process and loads it into each test. This pins that shortcut to the real
``Database.connect()`` path: same schema, same migration state, same pragmas, and
a test cannot alter the template.
"""

import pytest

from app.database import Database
from tests.conftest import patched_database


async def _state(db: Database) -> dict:
    async with db.readonly() as conn:
        async with conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ) as c:
            master = [tuple(r) for r in await c.fetchall()]
        pragmas = {}
        for name in ("user_version", "foreign_keys", "busy_timeout", "auto_vacuum", "temp_store"):
            async with conn.execute(f"PRAGMA {name}") as c:
                pragmas[name] = (await c.fetchone())[0]
    return {"master": master, "pragmas": pragmas}


@pytest.mark.asyncio
async def test_template_database_matches_a_really_migrated_one():
    real = Database(":memory:")
    await real.connect()
    try:
        expected = await _state(real)
    finally:
        await real.disconnect()
    assert expected["master"], "the real migration path produced no schema"

    async with patched_database() as templated:
        assert await _state(templated) == expected


@pytest.mark.asyncio
async def test_a_test_cannot_leak_changes_into_the_next_database():
    async with patched_database() as first:
        async with first.tx() as conn:
            await conn.execute("CREATE TABLE leak_probe (id INTEGER)")
    async with patched_database() as second:
        async with second.readonly() as conn:
            async with conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'leak_probe'") as c:
                assert await c.fetchone() is None


@pytest.mark.asyncio
async def test_snapshot_loading_refuses_file_paths(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    with pytest.raises(ValueError):
        await db.connect_from_snapshot(b"")
