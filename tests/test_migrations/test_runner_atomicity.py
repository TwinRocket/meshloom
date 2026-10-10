"""The migration runner applies each migration and its version bump atomically."""

import types
from unittest.mock import patch

import aiosqlite
import pytest

import app.migrations as migrations
from app.migrations import get_version, run_migrations, set_version


def _fake_modules(*mods: types.ModuleType):
    infos = [types.SimpleNamespace(name=m.__name__) for m in mods]
    by_name = {f"{migrations.__name__}.{m.__name__}": m for m in mods}
    return (
        patch.object(migrations.pkgutil, "iter_modules", return_value=infos),
        patch.object(migrations.importlib, "import_module", side_effect=by_name.__getitem__),
    )


def _module(name: str, body) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.migrate = body  # type: ignore[attr-defined]
    return mod


async def _tables(conn) -> set[str]:
    cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_failing_migration_rolls_back_schema_data_and_version():
    async def good(conn):
        await conn.execute("CREATE TABLE ok_table (id INTEGER)")
        await conn.commit()

    async def bad(conn):
        await conn.execute("CREATE TABLE half_done (id INTEGER)")
        await conn.execute("INSERT INTO ok_table (id) VALUES (1)")
        await conn.commit()  # deferred: must not persist the partial work
        raise RuntimeError("boom")

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 1)
        await conn.commit()
        p1, p2 = _fake_modules(_module("_002_good", good), _module("_003_bad", bad))
        with p1, p2, pytest.raises(RuntimeError):
            await run_migrations(conn)

        assert await get_version(conn) == 2
        tables = await _tables(conn)
        assert "ok_table" in tables
        assert "half_done" not in tables
        cursor = await conn.execute("SELECT COUNT(*) FROM ok_table")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_non_transactional_migration_runs_outside_a_transaction():
    seen: list[bool] = []

    async def vacuumish(conn):
        seen.append(conn.in_transaction)
        await conn.execute("VACUUM")

    mod = _module("_002_vacuum", vacuumish)
    mod.TRANSACTIONAL = False  # type: ignore[attr-defined]

    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 1)
        await conn.commit()
        p1, p2 = _fake_modules(mod)
        with p1, p2:
            assert await run_migrations(conn) == 1
        assert seen == [False]
        assert await get_version(conn) == 2
    finally:
        await conn.close()
