"""Migration 113: notification channels, journal tables, rules, and the legacy import."""

import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from app.database import Database
from app.migrations import get_version, run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION, opened_secrets

FIXTURE = Path(__file__).parent.parent / "fixtures" / "notifications_418_settings.json"
TABLES = (
    "notification_channels",
    "notification_deliveries",
    "notification_counters",
    "notification_state",
)


def _schema(path: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN (?, ?, ?, ?, ?) ORDER BY name",
            (*TABLES, "idx_notification_deliveries_channel"),
        ).fetchall()
        return [(name, " ".join(sql.split())) for name, sql in rows]
    finally:
        conn.close()


async def _make_418_database(path: Path, settings: dict | None) -> None:
    """A database as 4.18.x left it: the legacy columns filled, nothing of 113 yet."""
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    try:
        for table in TABLES:
            await conn.execute(f"DROP TABLE {table}")
        await conn.execute("DROP INDEX IF EXISTS idx_notification_deliveries_channel")
        await conn.execute("ALTER TABLE app_settings DROP COLUMN notification_rules")
        if settings is not None:
            await conn.execute(
                "UPDATE app_settings SET push_defaults = ?, push_conversation_overrides = ?,"
                " notification_destinations = ? WHERE id = 1",
                (
                    json.dumps(settings["push_defaults"]),
                    json.dumps(settings["push_conversation_overrides"]),
                    json.dumps(settings["notification_destinations"]),
                ),
            )
        await set_version(conn, 111)
        await conn.commit()
    finally:
        await conn.close()


async def _migrate(path: Path) -> dict:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    try:
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 111
        assert await get_version(conn) == LATEST_SCHEMA_VERSION
        cursor = await conn.execute("SELECT * FROM notification_channels ORDER BY sort_order")
        channels = {row["id"]: dict(row) for row in await cursor.fetchall()}
        cursor = await conn.execute("SELECT notification_rules FROM app_settings WHERE id = 1")
        rules = json.loads((await cursor.fetchone())[0])
        return {"channels": channels, "rules": rules}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_and_migrated_databases_have_the_same_schema(tmp_path):
    fresh = tmp_path / "fresh.db"
    database = Database(str(fresh))
    await database.connect()
    await database.disconnect()
    migrated = tmp_path / "migrated.db"
    conn = await aiosqlite.connect(migrated)
    try:
        await set_version(conn, 110)
        await run_migrations(conn)
    finally:
        await conn.close()
    assert len(_schema(str(fresh))) == 5
    assert _schema(str(migrated)) == _schema(str(fresh))


@pytest.mark.asyncio
async def test_fresh_database_gets_only_builtin_push_with_the_current_defaults(tmp_path):
    path = tmp_path / "fresh.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    try:
        cursor = await conn.execute("SELECT * FROM notification_channels")
        rows = [dict(r) for r in await cursor.fetchall()]
        cursor = await conn.execute("SELECT notification_rules FROM app_settings")
        rules = json.loads((await cursor.fetchone())[0])
    finally:
        await conn.close()
    assert [r["id"] for r in rows] == ["builtin:push"]
    assert rows[0]["type"] == "web_push" and rows[0]["enabled"] == 1
    events = rules["events"]
    assert set(events) == {
        "message.dm",
        "message.channel",
        "message.mention",
        "contact.first_seen",
        "channel.found",
        "telemetry.alert",
        "app.update",
        "radio.offline",
        "radio.online",
        "app.started",
    }
    assert all(e["channels"] == ["builtin:push"] for e in events.values())
    assert events["radio.offline"]["always_deliver"] is True
    assert events["message.dm"]["always_deliver"] is False
    assert events["contact.first_seen"]["contact_types"] == {
        "companion": ["builtin:push"],
        "repeater": ["builtin:push"],
        "sensor": ["builtin:push"],
    }
    assert rules["conversations"] == {} and rules["version"] == 1


@pytest.mark.asyncio
async def test_418_database_with_smtp_apprise_and_overrides_is_imported_losslessly(tmp_path):
    settings = json.loads(FIXTURE.read_text())
    path = tmp_path / "418.db"
    await _make_418_database(path, settings)
    result = await _migrate(path)
    channels, rules = result["channels"], result["rules"]

    assert list(channels) == ["builtin:push", "legacy:email", "legacy:webhook", "legacy:apprise"]
    email = channels["legacy:email"]
    assert email["type"] == "email" and email["enabled"] == 1
    assert json.loads(email["config"]) == {
        "host": "smtp.example.org",
        "port": 465,
        "mode": "ssl",
        "user": "meshloom",
        "from": "meshloom@example.org",
        "to": "me@example.org,you@example.org",
    }
    assert opened_secrets(email["secrets"], "legacy:email") == {"password": "smtp-pass-1"}
    webhook = channels["legacy:webhook"]
    assert opened_secrets(webhook["secrets"], "legacy:webhook") == {
        "url": "http://192.168.1.20:8080/hook?token=abc",
        "hmac_secret": "hmac-secret-1",
    }
    assert webhook["allow_private_network"] == 1 and webhook["quiet_hours_exempt"] == 0
    apprise = channels["legacy:apprise"]
    assert opened_secrets(apprise["secrets"], "legacy:apprise") == {
        "urls": "ntfy://ntfy.example.org/topic-secret\ntgram://123456:AAbbCC/987654"
    }
    assert json.loads(apprise["config"]) == {"format": "markdown"}
    assert apprise["include_message_text"] == "never" and apprise["allow_private_network"] == 0

    events = rules["events"]
    assert events["message.dm"]["channels"] == ["builtin:push", "legacy:email", "legacy:apprise"]
    assert events["telemetry.alert"]["channels"] == [
        "builtin:push",
        "legacy:email",
        "legacy:webhook",
    ]
    assert events["radio.offline"] == {
        "channels": ["builtin:push", "legacy:email", "legacy:apprise"],
        "always_deliver": True,
    }
    assert events["app.update"]["channels"] == []
    assert events["app.started"]["channels"] == []
    assert events["radio.online"]["channels"] == ["builtin:push"]
    # new_contact (push) OR advert_companion (webhook) for a companion.
    assert events["contact.first_seen"]["contact_types"] == {
        "companion": ["builtin:push", "legacy:webhook"],
        "repeater": [],
        "sensor": ["builtin:push"],
    }
    assert events["contact.first_seen"]["channels"] == ["builtin:push", "legacy:webhook"]
    assert rules["conversations"] == {
        "contact-aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899": {
            "builtin:push": False,
            "legacy:email": True,
        },
        "channel-8B3387E9C5CDEA6AC9E5EDBAA115CD72": {"builtin:push": True},
    }


@pytest.mark.asyncio
async def test_unconfigured_media_create_no_channel_and_no_reference(tmp_path):
    settings = json.loads(FIXTURE.read_text())
    settings["notification_destinations"] = {
        "email": {"host": "", "to": ""},
        "webhook": {"url": ""},
        "apprise": {"urls": ""},
    }
    path = tmp_path / "bare.db"
    await _make_418_database(path, settings)
    result = await _migrate(path)
    assert list(result["channels"]) == ["builtin:push"]
    blob = json.dumps(result["rules"])
    assert "legacy:" not in blob
    assert result["rules"]["conversations"] == {
        "contact-aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899": {
            "builtin:push": False
        },
        "channel-8B3387E9C5CDEA6AC9E5EDBAA115CD72": {"builtin:push": True},
    }


@pytest.mark.asyncio
async def test_webhook_saved_before_the_guard_reads_as_grandfathered(tmp_path):
    settings = json.loads(FIXTURE.read_text())
    settings["notification_destinations"] = {"webhook": {"url": "http://nas.local/hook"}}
    path = tmp_path / "old.db"
    await _make_418_database(path, settings)
    webhook = (await _migrate(path))["channels"]["legacy:webhook"]
    assert webhook["allow_private_network"] == 1
    assert json.loads(webhook["config"]) == {"private_network_grandfathered": True}


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_keeps_existing_rules(tmp_path):
    from app.migrations import _113_notification_channels as migration

    settings = json.loads(FIXTURE.read_text())
    path = tmp_path / "again.db"
    await _make_418_database(path, settings)
    first = await _migrate(path)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute(
            "UPDATE notification_channels SET name = 'Mine' WHERE id = 'legacy:email'"
        )
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
            (json.dumps({"version": 1, "events": {}}),),
        )
        await migration.migrate(conn)
        await migration.migrate(conn)
        cursor = await conn.execute(
            "SELECT id, name FROM notification_channels ORDER BY sort_order"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]
        cursor = await conn.execute("SELECT notification_rules FROM app_settings")
        rules = json.loads((await cursor.fetchone())[0])
    finally:
        await conn.close()
    assert len(rows) == len(first["channels"]) == 4
    assert ("legacy:email", "Mine") in rows
    assert rules == {"version": 1, "events": {}}


@pytest.mark.asyncio
async def test_migration_leaves_the_legacy_columns_in_place(tmp_path):
    settings = json.loads(FIXTURE.read_text())
    path = tmp_path / "cols.db"
    await _make_418_database(path, settings)
    await _migrate(path)
    conn = await aiosqlite.connect(path)
    try:
        cursor = await conn.execute(
            "SELECT push_defaults, push_conversation_overrides, notification_destinations "
            "FROM app_settings"
        )
        row = await cursor.fetchone()
    finally:
        await conn.close()
    assert json.loads(row[0]) == settings["push_defaults"]
    assert json.loads(row[1]) == settings["push_conversation_overrides"]
    assert json.loads(row[2]) == settings["notification_destinations"]


@pytest.mark.asyncio
async def test_without_app_settings_only_the_tables_are_created():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 112)
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 112
        cursor = await conn.execute("SELECT COUNT(*) FROM notification_channels")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()
