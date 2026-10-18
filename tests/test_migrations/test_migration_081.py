import json

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version
from app.migrations._081_telemetry_alert_v2 import migrate as migrate_081

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration081:
    @pytest.mark.asyncio
    async def test_adds_destinations_and_rewrites_rules(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 80)
            await conn.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    telemetry_alert_rules TEXT,
                    push_defaults TEXT,
                    tracked_telemetry_repeaters TEXT,
                    tracked_telemetry_contacts TEXT
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO app_settings (
                    id, telemetry_alert_rules, push_defaults,
                    tracked_telemetry_repeaters, tracked_telemetry_contacts
                ) VALUES (1, ?, ?, ?, ?)
                """,
                (
                    json.dumps(
                        {
                            "battery_volts_min": 3.4,
                            "noise_floor_max_dbm": -88,
                            "misses_before_alert": 3,
                            "overrides": {},
                        }
                    ),
                    json.dumps({"telemetry_alert": False}),
                    json.dumps(["aa" * 32]),
                    json.dumps([]),
                ),
            )
            await conn.commit()

            await migrate_081(conn)
            await set_version(conn, 81)

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "notification_destinations" in columns

            row = await (
                await conn.execute(
                    "SELECT telemetry_alert_rules, notification_destinations "
                    "FROM app_settings WHERE id = 1"
                )
            ).fetchone()
            rules = json.loads(row["telemetry_alert_rules"])
            assert "battery_volts_min" not in rules
            assert rules["channels"]["push"] is False
            assert rules["channels"]["email"] is False
            assert rules["rules"]["battery"]["threshold"] == 3.4
            assert rules["rules"]["noise"]["threshold"] == -88
            assert rules["rules"]["silence"]["threshold"] == 3
            assert rules["overrides"]["aa" * 32]["alerting"] is True
            dest = json.loads(row["notification_destinations"])
            assert dest["email"]["mode"] == "starttls"
            assert dest["webhook"]["url"] == ""
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_full_migrate_from_80_reaches_latest(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 80)
            await conn.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    telemetry_alert_rules TEXT DEFAULT '{}',
                    push_defaults TEXT,
                    tracked_telemetry_repeaters TEXT DEFAULT '[]',
                    tracked_telemetry_contacts TEXT DEFAULT '[]'
                )
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()
            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 80
        finally:
            await conn.close()
