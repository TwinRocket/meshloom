"""Tests for multi-radio management endpoints and radio-scoping."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import (
    ContactUpsert,
    RadioCreate,
)
from app.repository import (
    ChannelRepository,
    ContactRepository,
    MessageRepository,
    RadioRepository,
)
from app.services.radio_registry import radio_registry


@pytest.fixture(autouse=True)
def _clean_non_default_radios():
    """Ensure non-default radios in registry and DB are cleaned up."""
    yield
    for rid in [k for k in list(radio_registry._instances.keys()) if k != "default"]:
        radio_registry.unregister(rid)


class TestListRadios:
    """Test GET /api/radios."""

    @pytest.mark.asyncio
    async def test_list_radios_contains_default(self, test_db, client):
        response = await client.get("/api/radios")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        default_radio = next((r for r in data if r["id"] == "default"), None)
        assert default_radio is not None
        assert default_radio["name"] in ("Primary Radio", "Default Radio")
        assert "is_connected" in default_radio
        assert "transport" in default_radio


class TestCreateRadio:
    """Test POST /api/radios."""

    @pytest.mark.asyncio
    async def test_create_radio_tcp_success(self, test_db, client):
        payload = {
            "id": "roof_tcp",
            "name": "Roof Node",
            "transport": "tcp",
            "tcp_host": "192.168.1.150",
            "tcp_port": 4000,
            "enabled": True,
            "auto_connect": False,
        }
        with patch("app.routers.radios_mgmt.broadcast_event") as mock_broadcast:
            response = await client.post("/api/radios", json=payload)
            assert response.status_code == 201
            data = response.json()
            assert data["id"] == "roof_tcp"
            assert data["name"] == "Roof Node"
            assert data["transport"] == "tcp"
            assert data["tcp_host"] == "192.168.1.150"
            assert data["tcp_port"] == 4000
            assert data["enabled"] is True
            assert data["auto_connect"] is False

            assert radio_registry.has("roof_tcp")
            instance = radio_registry.get("roof_tcp")
            assert instance.name == "Roof Node"

            mock_broadcast.assert_called_once()
            args, kwargs = mock_broadcast.call_args
            assert args[0] == "radio_created"
            assert kwargs.get("radio_id") == "roof_tcp" or args[1].get("id") == "roof_tcp"

    @pytest.mark.asyncio
    async def test_create_radio_serial_success(self, test_db, client):
        payload = {
            "id": "desk_serial",
            "name": "Desk Serial Radio",
            "transport": "serial",
            "serial_port": "/dev/ttyUSB1",
            "serial_baudrate": 115200,
            "enabled": False,
            "auto_connect": False,
        }
        response = await client.post("/api/radios", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "desk_serial"
        assert data["transport"] == "serial"
        assert data["serial_port"] == "/dev/ttyUSB1"
        assert radio_registry.has("desk_serial")

    @pytest.mark.asyncio
    async def test_create_radio_duplicate_id_returns_409(self, test_db, client):
        payload = {
            "id": "dup_radio",
            "name": "Dup Radio",
            "transport": "serial",
            "serial_port": "/dev/ttyUSB2",
            "enabled": False,
            "auto_connect": False,
        }
        resp1 = await client.post("/api/radios", json=payload)
        assert resp1.status_code == 201

        resp2 = await client.post("/api/radios", json=payload)
        assert resp2.status_code == 409
        assert "already exists" in resp2.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_radio_validation_failures(self, test_db, client):
        # Missing tcp_host for tcp
        resp = await client.post(
            "/api/radios",
            json={"id": "bad_tcp", "name": "Bad", "transport": "tcp", "tcp_port": 4000},
        )
        assert resp.status_code == 400
        assert "tcp_host is required" in resp.json()["detail"]

        # Invalid port
        resp = await client.post(
            "/api/radios",
            json={
                "id": "bad_port",
                "name": "Bad",
                "transport": "tcp",
                "tcp_host": "192.168.1.1",
                "tcp_port": 99999,
            },
        )
        assert resp.status_code == 400
        assert "tcp_port must be between" in resp.json()["detail"]

        # Missing ble_address for ble
        resp = await client.post(
            "/api/radios",
            json={"id": "bad_ble", "name": "Bad", "transport": "ble"},
        )
        assert resp.status_code == 400
        assert "ble_address is required" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_radio_ssrf_loop_prevention(self, test_db, client):
        with patch("app.radio_proxy.manager.radio_proxy_manager.would_loop_transport", return_value=True):
            resp = await client.post(
                "/api/radios",
                json={
                    "id": "loop_radio",
                    "name": "Loop",
                    "transport": "tcp",
                    "tcp_host": "127.0.0.1",
                    "tcp_port": 4000,
                },
            )
            assert resp.status_code == 400
            assert "loop detected" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_radio_ble_pin_not_leaked(self, test_db, client):
        resp = await client.post(
            "/api/radios",
            json={
                "id": "ble_node",
                "name": "BLE Node",
                "transport": "ble",
                "ble_address": "AA:BB:CC:DD:EE:FF",
                "ble_pin": "secret123",
                "enabled": False,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["ble_pin_configured"] is True
        assert "ble_pin" not in data

        get_resp = await client.get("/api/radios/ble_node")
        assert get_resp.status_code == 200
        get_data = get_resp.json()
        assert get_data["ble_pin_configured"] is True
        assert "ble_pin" not in get_data



class TestGetRadio:
    """Test GET /api/radios/{radio_id}."""

    @pytest.mark.asyncio
    async def test_get_default_radio(self, test_db, client):
        response = await client.get("/api/radios/default")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "default"
        assert data["name"] in ("Primary Radio", "Default Radio")

    @pytest.mark.asyncio
    async def test_get_nonexistent_radio_returns_404(self, test_db, client):
        response = await client.get("/api/radios/nonexistent_xyz")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestPatchRadio:
    """Test PATCH /api/radios/{radio_id}."""

    @pytest.mark.asyncio
    async def test_patch_radio_success(self, test_db, client):
        await client.post(
            "/api/radios",
            json={
                "id": "to_patch",
                "name": "Original Name",
                "transport": "tcp",
                "tcp_host": "192.168.1.10",
                "tcp_port": 4000,
                "enabled": True,
                "auto_connect": False,
            },
        )

        with patch("app.routers.radios_mgmt.broadcast_event") as mock_broadcast:
            response = await client.patch(
                "/api/radios/to_patch",
                json={"name": "Updated Name", "auto_connect": True},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["name"] == "Updated Name"
            assert data["auto_connect"] is True

            instance = radio_registry.get("to_patch")
            assert instance.name == "Updated Name"
            assert instance.auto_connect is True

            mock_broadcast.assert_called_once()
            args, kwargs = mock_broadcast.call_args
            assert args[0] == "radio_updated"

    @pytest.mark.asyncio
    async def test_patch_nonexistent_returns_404(self, test_db, client):
        response = await client.patch("/api/radios/nonexistent_xyz", json={"name": "New"})
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_ssrf_loop_prevention(self, test_db, client):
        await client.post(
            "/api/radios",
            json={
                "id": "patch_loop",
                "name": "Loop Radio",
                "transport": "tcp",
                "tcp_host": "192.168.1.10",
                "tcp_port": 4000,
                "enabled": False,
            },
        )
        with patch("app.radio_proxy.manager.radio_proxy_manager.would_loop_transport", return_value=True):
            resp = await client.patch("/api/radios/patch_loop", json={"tcp_port": 5001})
            assert resp.status_code == 400
            assert "loop detected" in resp.json()["detail"]


class TestDeleteRadio:
    """Test DELETE /api/radios/{radio_id}."""

    @pytest.mark.asyncio
    async def test_delete_default_radio_is_forbidden(self, test_db, client):
        response = await client.delete("/api/radios/default")
        assert response.status_code == 400
        assert "Cannot delete the default radio" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, test_db, client):
        response = await client.delete("/api/radios/nonexistent_xyz")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_radio_unregisters_and_deletes(self, test_db, client):
        await client.post(
            "/api/radios",
            json={
                "id": "to_delete",
                "name": "Delete Me",
                "transport": "serial",
                "serial_port": "/dev/ttyUSB3",
                "enabled": False,
            },
        )
        assert radio_registry.has("to_delete")

        with patch("app.routers.radios_mgmt.broadcast_event") as mock_broadcast:
            response = await client.delete("/api/radios/to_delete")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["purged"] is False

            assert not radio_registry.has("to_delete")
            assert await RadioRepository.get("to_delete") is None

            mock_broadcast.assert_called_once()
            args, kwargs = mock_broadcast.call_args
            assert args[0] == "radio_deleted"
            assert kwargs.get("radio_id") == "to_delete"

    @pytest.mark.asyncio
    async def test_delete_radio_with_purge_data(self, test_db, client):
        radio_id = "to_purge"
        await client.post(
            "/api/radios",
            json={
                "id": radio_id,
                "name": "Purge Me",
                "transport": "serial",
                "serial_port": "/dev/ttyUSB4",
                "enabled": False,
            },
        )

        # Insert scoped data for to_purge
        pub_key = "11" * 32
        await ContactRepository.upsert(
            ContactUpsert(
                radio_id=radio_id,
                public_key=pub_key,
                name="Purge Contact",
                type=0,
                on_radio=False,
            ),
            radio_id=radio_id,
        )
        chan_key = "22" * 16
        await ChannelRepository.upsert(key=chan_key, name="Purge Chan", radio_id=radio_id)
        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="Purge Msg",
            conversation_key=pub_key,
            sender_timestamp=1000,
            received_at=1000,
            radio_id=radio_id,
        )

        # Also insert data on default radio to verify it's NOT purged
        def_pub = "33" * 32
        await ContactRepository.upsert(
            ContactUpsert(
                radio_id="default",
                public_key=def_pub,
                name="Keep Contact",
                type=0,
                on_radio=False,
            ),
            radio_id="default",
        )

        # Purge delete
        response = await client.delete(f"/api/radios/{radio_id}?purge_data=true")
        assert response.status_code == 200
        assert response.json()["purged"] is True

        # Verify scoped data purged
        assert await ContactRepository.get_by_key(pub_key, radio_id=radio_id) is None
        assert await ChannelRepository.get_by_key(chan_key, radio_id=radio_id) is None
        assert await MessageRepository.get_by_id(msg_id) is None

        # Verify default radio data retained
        assert await ContactRepository.get_by_key(def_pub, radio_id="default") is not None


class TestRadioCandidateTestEndpoint:
    """Test POST /api/radios/test."""

    @pytest.mark.asyncio
    async def test_radio_test_ssrf_prevention(self, test_db, client):
        with patch("app.radio_proxy.manager.radio_proxy_manager.would_loop_transport", return_value=True):
            resp = await client.post(
                "/api/radios/test",
                json={"transport": "tcp", "tcp_host": "127.0.0.1", "tcp_port": 4000},
            )
            assert resp.status_code == 400
            assert "loop detected" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_radio_test_tcp_reachable(self, test_db, client):
        mock_writer = AsyncMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()
        with patch("asyncio.open_connection", return_value=(AsyncMock(), mock_writer)):
            resp = await client.post(
                "/api/radios/test",
                json={"transport": "tcp", "tcp_host": "10.0.0.1", "tcp_port": 4000},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_radio_test_tcp_unreachable(self, test_db, client):
        with patch("asyncio.open_connection", side_effect=OSError("Connection refused")):
            resp = await client.post(
                "/api/radios/test",
                json={"transport": "tcp", "tcp_host": "10.0.0.1", "tcp_port": 4000},
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is False
            assert "Connection refused" in resp.json()["message"]


class TestRadioLifecycleEndpoints:
    """Test POST /api/radios/{radio_id}/connect, disconnect, reconnect."""

    @pytest.mark.asyncio
    async def test_lifecycle_404_on_nonexistent_radio(self, test_db, client):
        for action in ["connect", "disconnect", "reconnect"]:
            resp = await client.post(f"/api/radios/nonexistent_xyz/{action}")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_connect_success(self, test_db, client):
        await client.post(
            "/api/radios",
            json={
                "id": "life_test",
                "name": "Life Test",
                "transport": "serial",
                "serial_port": "/dev/ttyUSB5",
                "enabled": True,
            },
        )
        with patch("app.routers.radios_mgmt.reconnect_and_prepare_radio", return_value=True):
            resp = await client.post("/api/radios/life_test/connect")
            assert resp.status_code == 200
            assert resp.json()["connected"] is True

    @pytest.mark.asyncio
    async def test_connect_failure_returns_423(self, test_db, client):
        await client.post(
            "/api/radios",
            json={
                "id": "life_fail",
                "name": "Life Fail",
                "transport": "serial",
                "serial_port": "/dev/ttyUSB6",
                "enabled": True,
            },
        )
        with patch("app.routers.radios_mgmt.reconnect_and_prepare_radio", return_value=False):
            resp = await client.post("/api/radios/life_fail/connect")
            assert resp.status_code == 423

    @pytest.mark.asyncio
    async def test_disconnect_and_reconnect(self, test_db, client):
        await client.post(
            "/api/radios",
            json={
                "id": "disc_test",
                "name": "Disc Test",
                "transport": "serial",
                "serial_port": "/dev/ttyUSB7",
                "enabled": True,
            },
        )
        # Disconnect
        resp = await client.post("/api/radios/disc_test/disconnect")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False

        # Reconnect
        with patch("app.routers.radios_mgmt.reconnect_and_prepare_radio", return_value=True):
            resp = await client.post("/api/radios/disc_test/reconnect")
            assert resp.status_code == 200
            assert resp.json()["connected"] is True


class TestScopedExistingEndpoints:
    """Test scoping of contacts, channels, messages, and read-state by radio_id."""

    @pytest.mark.asyncio
    async def test_scoped_contacts_endpoint(self, test_db, client):
        # Create second radio
        await RadioRepository.create(
            RadioCreate(
                id="radio_b",
                name="Radio B",
                transport="serial",
                serial_port="/dev/ttyUSB8",
            )
        )

        key_a = "aa" * 32
        key_b = "bb" * 32
        await ContactRepository.upsert(
            ContactUpsert(
                radio_id="default",
                public_key=key_a,
                name="Alice Default",
                type=0,
                on_radio=False,
            ),
            radio_id="default",
        )
        await ContactRepository.upsert(
            ContactUpsert(
                radio_id="radio_b",
                public_key=key_b,
                name="Bob Radio B",
                type=0,
                on_radio=False,
            ),
            radio_id="radio_b",
        )

        # GET /api/contacts without param -> default radio
        resp_def = await client.get("/api/contacts")
        assert resp_def.status_code == 200
        keys_def = [c["public_key"] for c in resp_def.json()]
        assert key_a in keys_def
        assert key_b not in keys_def

        # GET /api/contacts?radio_id=radio_b -> radio_b
        resp_b = await client.get("/api/contacts?radio_id=radio_b")
        assert resp_b.status_code == 200
        keys_b = [c["public_key"] for c in resp_b.json()]
        assert key_b in keys_b
        assert key_a not in keys_b

    @pytest.mark.asyncio
    async def test_scoped_channels_endpoint(self, test_db, client):
        await RadioRepository.create(
            RadioCreate(
                id="radio_c",
                name="Radio C",
                transport="serial",
                serial_port="/dev/ttyUSB9",
            )
        )

        chan_a = "aa" * 16
        chan_c = "cc" * 16
        await ChannelRepository.upsert(key=chan_a, name="Default Chan", radio_id="default")
        await ChannelRepository.upsert(key=chan_c, name="Chan C", radio_id="radio_c")

        # GET /api/channels -> default
        resp_def = await client.get("/api/channels")
        assert resp_def.status_code == 200
        keys_def = [c["key"] for c in resp_def.json()]
        assert chan_a.upper() in keys_def
        assert chan_c.upper() not in keys_def

        # GET /api/channels?radio_id=radio_c -> radio_c
        resp_c = await client.get("/api/channels?radio_id=radio_c")
        assert resp_c.status_code == 200
        keys_c = [c["key"] for c in resp_c.json()]
        assert chan_c.upper() in keys_c
        assert chan_a.upper() not in keys_c

    @pytest.mark.asyncio
    async def test_scoped_messages_endpoint(self, test_db, client):
        await RadioRepository.create(
            RadioCreate(
                id="radio_d",
                name="Radio D",
                transport="serial",
                serial_port="/dev/ttyUSB10",
            )
        )

        pub_a = "aa" * 32
        pub_d = "dd" * 32
        msg_a = await MessageRepository.create(
            msg_type="PRIV",
            text="Msg Default",
            conversation_key=pub_a,
            sender_timestamp=1000,
            received_at=1000,
            radio_id="default",
        )
        msg_d = await MessageRepository.create(
            msg_type="PRIV",
            text="Msg D",
            conversation_key=pub_d,
            sender_timestamp=1001,
            received_at=1001,
            radio_id="radio_d",
        )

        # GET /api/messages -> default only
        resp_def = await client.get("/api/messages")
        assert resp_def.status_code == 200
        ids_def = [m["id"] for m in resp_def.json()]
        assert msg_a in ids_def
        assert msg_d not in ids_def

        # GET /api/messages?radio_id=radio_d -> radio_d only
        resp_d = await client.get("/api/messages?radio_id=radio_d")
        assert resp_d.status_code == 200
        ids_d = [m["id"] for m in resp_d.json()]
        assert msg_d in ids_d
        assert msg_a not in ids_d

    @pytest.mark.asyncio
    async def test_scoped_read_state_endpoint(self, test_db, client):
        await RadioRepository.create(
            RadioCreate(
                id="radio_e",
                name="Radio E",
                transport="serial",
                serial_port="/dev/ttyUSB11",
            )
        )

        chan_e = ("ee" * 16).upper()
        await ChannelRepository.upsert(key=chan_e, name="Chan E", radio_id="radio_e")
        await ChannelRepository.update_last_read_at(chan_e, 0, radio_id="radio_e")
        await MessageRepository.create(
            msg_type="CHAN",
            text="Unread in E",
            received_at=2000,
            conversation_key=chan_e,
            sender_timestamp=2000,
            radio_id="radio_e",
        )

        # Default query does not see radio_e unreads
        resp_def = await client.get("/api/read-state/unreads")
        assert resp_def.status_code == 200
        data_def = resp_def.json()
        assert f"channel-{chan_e}" not in data_def["counts"]

        # Scoped query for radio_e sees it
        resp_e = await client.get("/api/read-state/unreads?radio_id=radio_e")
        assert resp_e.status_code == 200
        data_e = resp_e.json()
        assert data_e["counts"].get(f"channel-{chan_e}") == 1

    @pytest.mark.asyncio
    async def test_direct_message_nonexistent_radio_returns_404(self, test_db, client):
        resp = await client.post(
            "/api/messages/direct?radio_id=ghost_radio",
            json={"destination": "aa" * 32, "text": "Hello"},
        )
        assert resp.status_code == 404
        assert "Radio 'ghost_radio' not found" in resp.json()["detail"]

