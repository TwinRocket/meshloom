import json

import aiosqlite
import pytest

from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


class TestMigration077:
    @pytest.mark.asyncio
    async def test_adds_rules_column_and_state_table(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 76)
            await conn.execute(
                """
                CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))
                """
            )
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            applied = await run_migrations(conn)
            assert applied == LATEST_SCHEMA_VERSION - 76

            columns = {
                row[1]
                for row in await (await conn.execute("PRAGMA table_info(app_settings)")).fetchall()
            }
            assert "telemetry_alert_rules" in columns

            tables = {
                row[0]
                for row in await (
                    await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            assert "telemetry_alert_state" in tables

            row = await (
                await conn.execute("SELECT telemetry_alert_rules FROM app_settings WHERE id = 1")
            ).fetchone()
            rules = json.loads(row["telemetry_alert_rules"])
            battery = rules.get("rules", {}).get("battery") or {}
            assert battery.get("threshold") == 3.5 or rules.get("battery_volts_min") == 3.5
        finally:
            await conn.close()
