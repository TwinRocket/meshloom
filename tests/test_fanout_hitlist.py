"""Tests addressing fanout hitlist gaps: migrations 036-038 and community MQTT IATA
validation. (The 4.18 bot module and its guards went with the bots in 5.0.0.)"""

import json
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest
from fastapi import HTTPException

from app.migrations import set_version

# ---------------------------------------------------------------------------
# T2: Migration 036, 037, 038 tests
# ---------------------------------------------------------------------------

# Helper to build an app_settings schema at version 35 (pre-fanout)
_APP_SETTINGS_V35 = """
CREATE TABLE app_settings (
    id INTEGER PRIMARY KEY,
    mqtt_broker_host TEXT DEFAULT '',
    mqtt_broker_port INTEGER DEFAULT 1883,
    mqtt_username TEXT DEFAULT '',
    mqtt_password TEXT DEFAULT '',
    mqtt_use_tls INTEGER DEFAULT 0,
    mqtt_tls_insecure INTEGER DEFAULT 0,
    mqtt_topic_prefix TEXT DEFAULT 'meshcore',
    mqtt_publish_messages INTEGER DEFAULT 0,
    mqtt_publish_raw_packets INTEGER DEFAULT 0,
    community_mqtt_enabled INTEGER DEFAULT 0,
    community_mqtt_iata TEXT DEFAULT '',
    community_mqtt_broker_host TEXT DEFAULT 'mqtt-us-v1.letsmesh.net',
    community_mqtt_broker_port INTEGER DEFAULT 443,
    community_mqtt_email TEXT DEFAULT '',
    bots TEXT DEFAULT '[]'
)
"""


class TestMigration036:
    """Test migration 036: create fanout_configs and migrate MQTT settings."""

    @pytest.mark.asyncio
    async def test_migrates_private_mqtt(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute(
                """INSERT INTO app_settings (id, mqtt_broker_host, mqtt_broker_port,
                   mqtt_publish_messages, mqtt_publish_raw_packets)
                   VALUES (1, 'broker.test', 8883, 1, 0)"""
            )
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute("SELECT * FROM fanout_configs WHERE type = 'mqtt_private'")
            row = await cursor.fetchone()
            assert row is not None
            config = json.loads(row["config"])
            assert config["broker_host"] == "broker.test"
            assert config["broker_port"] == 8883
            assert row["enabled"] == 1
            scope = json.loads(row["scope"])
            assert scope["messages"] == "all"
            assert scope["raw_packets"] == "none"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_migrates_enabled_community_mqtt(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute(
                """INSERT INTO app_settings (id, community_mqtt_enabled,
                   community_mqtt_iata, community_mqtt_email)
                   VALUES (1, 1, 'PDX', 'user@test.com')"""
            )
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute(
                "SELECT * FROM fanout_configs WHERE type = 'mqtt_community'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["enabled"] == 1
            config = json.loads(row["config"])
            assert config["iata"] == "PDX"
            assert config["email"] == "user@test.com"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_preserves_disabled_but_configured_community_mqtt(self):
        """B4 fix: disabled community MQTT with populated fields is preserved."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute(
                """INSERT INTO app_settings (id, community_mqtt_enabled,
                   community_mqtt_iata, community_mqtt_email)
                   VALUES (1, 0, 'SEA', 'test@test.com')"""
            )
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute(
                "SELECT * FROM fanout_configs WHERE type = 'mqtt_community'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["enabled"] == 0  # Preserved as disabled
            config = json.loads(row["config"])
            assert config["iata"] == "SEA"
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_skips_empty_settings(self):
        """No fanout rows created when MQTT is unconfigured."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 35)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            from app.migrations._036_create_fanout_configs import (
                migrate as _migrate_036_create_fanout_configs,
            )

            await _migrate_036_create_fanout_configs(conn)

            cursor = await conn.execute("SELECT COUNT(*) FROM fanout_configs")
            row = await cursor.fetchone()
            assert row[0] == 0
        finally:
            await conn.close()


class TestMigration037:
    """Test migration 037: migrate bots to fanout_configs."""

    @pytest.mark.asyncio
    async def test_migrates_bots(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 36)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fanout_configs (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                    enabled INTEGER DEFAULT 0, config TEXT NOT NULL DEFAULT '{}',
                    scope TEXT NOT NULL DEFAULT '{}', sort_order INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT 0
                )
            """)
            bots = [
                {"name": "Echo", "enabled": True, "code": "def bot(**k): return k['message_text']"},
                {"name": "Silent", "enabled": False, "code": "def bot(**k): pass"},
            ]
            await conn.execute(
                "INSERT INTO app_settings (id, bots) VALUES (1, ?)",
                (json.dumps(bots),),
            )
            await conn.commit()

            from app.migrations._037_bots_to_fanout import migrate as _migrate_037_bots_to_fanout

            await _migrate_037_bots_to_fanout(conn)

            cursor = await conn.execute(
                "SELECT * FROM fanout_configs WHERE type = 'bot' ORDER BY sort_order"
            )
            rows = await cursor.fetchall()
            assert len(rows) == 2
            assert rows[0]["name"] == "Echo"
            assert rows[0]["enabled"] == 1
            assert rows[1]["name"] == "Silent"
            assert rows[1]["enabled"] == 0
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_empty_bots_is_noop(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 36)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS fanout_configs (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,
                    enabled INTEGER DEFAULT 0, config TEXT NOT NULL DEFAULT '{}',
                    scope TEXT NOT NULL DEFAULT '{}', sort_order INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT 0
                )
            """)
            await conn.execute("INSERT INTO app_settings (id, bots) VALUES (1, '[]')")
            await conn.commit()

            from app.migrations._037_bots_to_fanout import migrate as _migrate_037_bots_to_fanout

            await _migrate_037_bots_to_fanout(conn)

            cursor = await conn.execute("SELECT COUNT(*) FROM fanout_configs")
            row = await cursor.fetchone()
            assert row[0] == 0
        finally:
            await conn.close()


class TestMigration038:
    """Test migration 038: drop legacy columns from app_settings."""

    @pytest.mark.asyncio
    async def test_drops_legacy_columns(self):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 37)
            await conn.execute(_APP_SETTINGS_V35)
            await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
            await conn.commit()

            from app.migrations._038_drop_legacy_columns import (
                migrate as _migrate_038_drop_legacy_columns,
            )

            await _migrate_038_drop_legacy_columns(conn)

            cursor = await conn.execute("PRAGMA table_info(app_settings)")
            remaining = {row[1] for row in await cursor.fetchall()}
            assert "mqtt_broker_host" not in remaining
            assert "bots" not in remaining
            assert "community_mqtt_enabled" not in remaining
            # id should remain
            assert "id" in remaining
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_handles_already_dropped_columns(self):
        """Migration handles columns already dropped (idempotent)."""
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        try:
            await set_version(conn, 37)
            # Minimal table with only id — all legacy columns already gone
            await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY)")
            await conn.commit()

            from app.migrations._038_drop_legacy_columns import (
                migrate as _migrate_038_drop_legacy_columns,
            )

            # Should not raise
            await _migrate_038_drop_legacy_columns(conn)
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# Q4: Community MQTT IATA validation
# ---------------------------------------------------------------------------


class TestCommunityMqttIataValidation:
    """Verify community MQTT requires valid IATA when enabled."""

    def test_empty_iata_rejected(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": ""})
        assert exc_info.value.status_code == 400
        assert "IATA" in exc_info.value.detail

    def test_missing_iata_rejected(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({})
        assert exc_info.value.status_code == 400

    def test_valid_iata_accepted(self):
        from app.routers.fanout import _validate_mqtt_community_config

        # Should not raise
        _validate_mqtt_community_config({"iata": "PDX"})

    def test_invalid_iata_format_rejected(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException):
            _validate_mqtt_community_config({"iata": "PD"})

        with pytest.raises(HTTPException):
            _validate_mqtt_community_config({"iata": "pdx1"})

    def test_topic_template_defaults_when_missing(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX"}
        _validate_mqtt_community_config(config)
        assert config["broker_host"] == "mqtt-us-v1.letsmesh.net"
        assert config["broker_port"] == 443
        assert config["transport"] == "websockets"
        assert config["use_tls"] is True
        assert config["tls_verify"] is True
        assert config["auth_mode"] == "token"
        assert config["username"] == ""
        assert config["password"] == ""
        assert config["token_audience"] == ""
        assert config["topic_template"] == "meshcore/{IATA}/{PUBLIC_KEY}/packets"

    def test_topic_template_normalizes_placeholder_capitalization(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX", "topic_template": "mesh2mqtt/{iata}/node/{Public_Key}"}
        _validate_mqtt_community_config(config)
        assert config["topic_template"] == "mesh2mqtt/{IATA}/node/{PUBLIC_KEY}"

    def test_topic_template_rejects_unknown_placeholder(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config(
                {"iata": "PDX", "topic_template": "meshcore/{foo}/{PUBLIC_KEY}/packets"}
            )
        assert exc_info.value.status_code == 400
        assert "topic_template" in exc_info.value.detail

    def test_transport_rejects_unknown_value(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "transport": "udp"})
        assert exc_info.value.status_code == 400
        assert "transport" in exc_info.value.detail

    def test_blank_token_audience_is_preserved(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX", "token_audience": "   "}
        _validate_mqtt_community_config(config)
        assert config["token_audience"] == ""

    def test_auth_mode_rejects_unknown_value(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "auth_mode": "jwt"})
        assert exc_info.value.status_code == 400
        assert "auth_mode" in exc_info.value.detail

    def test_password_auth_requires_credentials(self):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "auth_mode": "password"})
        assert exc_info.value.status_code == 400
        assert "username and password" in exc_info.value.detail


class TestFanoutConfigMutationInvariant:
    """Persisted fanout rows should always be valid and canonical."""

    @pytest.mark.asyncio
    async def test_disabled_create_still_validates_config(self, test_db):
        from app.routers.fanout import FanoutConfigCreate, create_fanout_config

        with pytest.raises(HTTPException) as exc_info:
            await create_fanout_config(
                FanoutConfigCreate(
                    type="mqtt_community",
                    name="Invalid draft",
                    config={},
                    scope={},
                    enabled=False,
                )
            )

        assert exc_info.value.status_code == 400
        assert "IATA" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_enable_only_patch_persists_normalized_config(self, test_db):
        from app.repository.fanout import FanoutConfigRepository
        from app.routers.fanout import FanoutConfigUpdate, update_fanout_config

        cfg = await FanoutConfigRepository.create(
            config_type="mqtt_community",
            name="Community MQTT",
            config={
                "iata": "PDX",
                "broker_host": " mqtt.example.com ",
                "topic_template": "mesh2mqtt/{iata}/node/{Public_Key}",
            },
            scope={"messages": "none", "raw_packets": "all"},
            enabled=False,
        )

        with patch("app.fanout.manager.fanout_manager.reload_config", new_callable=AsyncMock):
            updated = await update_fanout_config(
                cfg["id"],
                FanoutConfigUpdate(enabled=True),
            )

        assert updated["enabled"] is True
        assert updated["config"]["broker_host"] == "mqtt.example.com"
        assert updated["config"]["topic_template"] == "mesh2mqtt/{IATA}/node/{PUBLIC_KEY}"
        assert updated["config"]["transport"] == "websockets"
        assert updated["config"]["auth_mode"] == "token"


class TestCommunityMqttWebsocketPathAndEmail:
    """websocket_path and email are validated like the other connection fields."""

    def test_defaults_are_filled_in(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX"}
        _validate_mqtt_community_config(config)
        assert config["websocket_path"] == "/"
        assert config["email"] == ""

    def test_valid_values_are_kept_and_trimmed(self):
        from app.routers.fanout import _validate_mqtt_community_config

        config = {"iata": "PDX", "websocket_path": " /mqtt ", "email": " a@b.example "}
        _validate_mqtt_community_config(config)
        assert config["websocket_path"] == "/mqtt"
        assert config["email"] == "a@b.example"

    @pytest.mark.parametrize("path", ["mqtt", "/a b", "/a\tb"])
    def test_bad_websocket_path_rejected(self, path):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "websocket_path": path})
        assert exc_info.value.status_code == 400
        assert "websocket_path" in exc_info.value.detail

    @pytest.mark.parametrize(
        "email", ["nope", "a@b", "a b@c.example", "a@@b.example", "x" * 250 + "@b.co"]
    )
    def test_bad_email_rejected(self, email):
        from app.routers.fanout import _validate_mqtt_community_config

        with pytest.raises(HTTPException) as exc_info:
            _validate_mqtt_community_config({"iata": "PDX", "email": email})
        assert exc_info.value.status_code == 400
        assert "email" in exc_info.value.detail
