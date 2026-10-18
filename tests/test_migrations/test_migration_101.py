import sqlite3

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION

_AUTH_TABLES = ("users", "sessions", "auth_audit", "auth_settings")


def _auth_schema(path: str) -> dict[tuple[str, str], str]:
    """``(type, name) -> sql`` for the auth tables and their indexes, plus each
    table's ``PRAGMA table_info`` so column order and defaults count too."""
    conn = sqlite3.connect(path)
    try:
        placeholders = ",".join("?" for _ in _AUTH_TABLES)
        rows = conn.execute(
            f"SELECT type, name, sql FROM sqlite_master WHERE tbl_name IN ({placeholders})",
            _AUTH_TABLES,
        ).fetchall()
        schema = {(kind, name): " ".join((sql or "").split()) for kind, name, sql in rows}
        for table in _AUTH_TABLES:
            schema[("info", table)] = repr(conn.execute(f"PRAGMA table_info({table})").fetchall())
    finally:
        conn.close()
    return schema


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_auth_schema(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    await database.disconnect()

    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 100)
        applied = await run_migrations(conn)
        assert applied == 1
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
    finally:
        await conn.close()

    fresh_schema = _auth_schema(str(fresh))
    assert {name for kind, name in fresh_schema if kind == "table"} == set(_AUTH_TABLES)
    assert {"idx_sessions_user", "idx_sessions_expires", "idx_auth_audit_ts"} <= {
        name for kind, name in fresh_schema if kind == "index"
    }
    assert _auth_schema(str(migrated)) == fresh_schema


@pytest.mark.asyncio
async def test_settings_row_has_the_defaults_and_rerun_is_harmless():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 100)
        await run_migrations(conn)
        await conn.execute("UPDATE auth_settings SET audit_retention_days = 30")
        await set_version(conn, 100)
        await run_migrations(conn)
        cursor = await conn.execute(
            "SELECT COUNT(*), host_guard_mode, ingress_default_role, session_lifetime_days,"
            " audit_retention_days, basic_imported, setup_code_hash FROM auth_settings"
        )
        assert tuple(await cursor.fetchone()) == (1, "observe", "member", 30, 30, 0, None)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_role_and_provider_are_constrained():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 100)
        await run_migrations(conn)
        for role in ("admin", "member", "viewer", "kiosk"):
            await conn.execute(
                "INSERT INTO users (username, username_key, role, created_at, updated_at)"
                " VALUES (?, ?, ?, 0, 0)",
                (role, role, role),
            )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO users (username, username_key, role, created_at, updated_at)"
                " VALUES ('x', 'x', 'owner', 0, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO users (username, username_key, role, provider, created_at,"
                " updated_at) VALUES ('y', 'y', 'admin', 'ldap', 0, 0)"
            )
    finally:
        await conn.close()
