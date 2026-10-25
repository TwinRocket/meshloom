"""Migration 114: telemetry alert media that lived only in ``telemetry_alert_rules.channels``."""

import json

import aiosqlite
import pytest

from app.database import Database
from app.migrations import _114_telemetry_alert_media, get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION
from .test_migration_113 import FIXTURE, _make_418_database


async def _read(path) -> tuple[dict, dict]:
    conn = await aiosqlite.connect(path)
    try:
        cursor = await conn.execute(
            "SELECT push_defaults, notification_rules FROM app_settings WHERE id = 1"
        )
        row = await cursor.fetchone()
        return json.loads(row[0]), json.loads(row[1])
    finally:
        await conn.close()


async def _418_without_telemetry_key(path, channels: dict | None) -> None:
    settings = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del settings["push_defaults"]["telemetry_alert"]
    await _make_418_database(path, settings)
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute(
            "UPDATE app_settings SET telemetry_alert_rules = ? WHERE id = 1",
            (json.dumps({"channels": channels, "rules": {}}) if channels else "{}",),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _migrate(path) -> None:
    conn = await aiosqlite.connect(path)
    try:
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 111
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_418_install_keeps_its_telemetry_channels(tmp_path):
    path = tmp_path / "418.db"
    await _418_without_telemetry_key(path, {"push": False, "email": True, "webhook": True})
    await _migrate(path)
    defaults, rules = await _read(path)
    assert defaults["telemetry_alert"] == {
        "push": False,
        "email": True,
        "webhook": True,
        "apprise": False,
    }
    # The fixture configures SMTP, a webhook and Apprise, so both channels exist.
    assert rules["events"]["telemetry.alert"]["channels"] == ["legacy:email", "legacy:webhook"]


@pytest.mark.asyncio
async def test_nothing_changes_without_stored_channels_or_with_the_key_set(tmp_path):
    path = tmp_path / "plain.db"
    await _418_without_telemetry_key(path, None)
    await _migrate(path)
    defaults, rules = await _read(path)
    assert "telemetry_alert" not in defaults
    assert rules["events"]["telemetry.alert"]["channels"] == ["builtin:push"]

    keyed = tmp_path / "keyed.db"
    await _make_418_database(keyed, json.loads(FIXTURE.read_text(encoding="utf-8")))
    await _migrate(keyed)
    before = await _read(keyed)
    assert before[0]["telemetry_alert"]["email"] is True  # the fixture's own value


@pytest.mark.asyncio
async def test_current_database_and_a_second_run_are_left_alone(tmp_path):
    path = tmp_path / "current.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    before = await _read(path)
    conn = await aiosqlite.connect(path)
    try:
        await _114_telemetry_alert_media.migrate(conn)
        await _114_telemetry_alert_media.migrate(conn)
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
    finally:
        await conn.close()
    assert await _read(path) == before

    stored = tmp_path / "stored.db"
    await _418_without_telemetry_key(stored, {"push": True, "email": True})
    await _migrate(stored)
    once = await _read(stored)
    conn = await aiosqlite.connect(stored)
    try:
        await _114_telemetry_alert_media.migrate(conn)
    finally:
        await conn.close()
    assert await _read(stored) == once


@pytest.mark.asyncio
async def test_migrations_stay_contiguous(tmp_path):
    from pathlib import Path

    numbers = sorted(
        int(p.name[1:4])
        for p in Path(_114_telemetry_alert_media.__file__).parent.glob("_[0-9]*_*.py")
    )
    assert numbers == list(range(1, LATEST_SCHEMA_VERSION + 1))
    conn = await aiosqlite.connect(tmp_path / "x.db")
    try:
        await set_version(conn, LATEST_SCHEMA_VERSION - 1)
        assert await run_migrations(conn) == 1
    finally:
        await conn.close()
