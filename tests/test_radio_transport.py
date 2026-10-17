from unittest.mock import AsyncMock

import pytest

from app.models import RadioTransportSnapshot
from app.repository.radio_transport import RadioTransportRepository, validate_transport_update
from app.services.radio_identity import wipe_mesh_identity_data
from app.services.radio_transport import (
    maybe_import_legacy_env,
    probe_ble_available,
    scan_ble_devices,
    serial_ports_unavailable_reason,
)


def test_validate_transport_update_requires_tcp_host() -> None:
    with pytest.raises(ValueError, match="tcp_host"):
        validate_transport_update("tcp", tcp_host="")


def test_validate_transport_update_requires_ble_pin() -> None:
    with pytest.raises(ValueError, match="ble_pin"):
        validate_transport_update("ble", ble_address="AA:BB:CC:DD:EE:FF", ble_pin="")


@pytest.mark.asyncio
async def test_radio_transport_roundtrip(test_db):
    saved = await RadioTransportRepository.save(
        RadioTransportSnapshot(transport="tcp", tcp_host="192.168.1.8", tcp_port=5000)
    )
    loaded = await RadioTransportRepository.get()
    assert loaded.transport == "tcp"
    assert loaded.tcp_host == "192.168.1.8"
    assert loaded.configured is True
    assert saved.ble_pin == ""


@pytest.mark.asyncio
async def test_env_import_ignored_on_new_database(test_db, monkeypatch):
    monkeypatch.setenv("MESHCORE_TCP_HOST", "10.0.0.9")
    snapshot = await maybe_import_legacy_env(existing_database=False)
    assert snapshot.transport is None
    assert snapshot.env_imported is True


@pytest.mark.asyncio
async def test_env_import_once_on_existing_database(test_db, monkeypatch):
    monkeypatch.setenv("MESHCORE_TCP_HOST", "10.0.0.9")
    monkeypatch.setenv("MESHCORE_TCP_PORT", "4403")
    first = await maybe_import_legacy_env(existing_database=True)
    assert first.transport == "tcp"
    assert first.tcp_host == "10.0.0.9"
    assert first.tcp_port == 4403
    assert first.env_imported is True

    await RadioTransportRepository.save(RadioTransportSnapshot(transport=None, env_imported=True))
    monkeypatch.setenv("MESHCORE_TCP_HOST", "10.0.0.10")
    second = await maybe_import_legacy_env(existing_database=True)
    assert second.transport is None
    assert second.env_imported is True


@pytest.mark.asyncio
async def test_wipe_mesh_identity_data_keeps_channels(test_db, monkeypatch):
    import app.services.radio_identity as identity

    monkeypatch.setattr(identity, "db", test_db)
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO contacts (public_key, name) VALUES (?, ?)",
            ("a" * 64, "Alice"),
        )
        await conn.execute(
            """
            INSERT INTO messages (type, conversation_key, text, received_at, outgoing)
            VALUES ('PRIV', ?, 'hi', 1, 0)
            """,
            ("a" * 64,),
        )
    await wipe_mesh_identity_data()
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM contacts") as cursor:
            assert (await cursor.fetchone())[0] == 0
        async with conn.execute("SELECT COUNT(*) FROM messages") as cursor:
            assert (await cursor.fetchone())[0] == 0
        async with conn.execute("SELECT COUNT(*) FROM channels") as cursor:
            assert (await cursor.fetchone())[0] >= 0


def test_serial_reason_mentions_dialout_on_host(monkeypatch) -> None:
    monkeypatch.setattr("app.services.radio_transport._running_in_container", lambda: False)
    reason = serial_ports_unavailable_reason()
    assert "dialout" in reason
    assert "container" not in reason.lower()


def test_serial_reason_mentions_container_when_containerized(monkeypatch) -> None:
    monkeypatch.setattr("app.services.radio_transport._running_in_container", lambda: True)
    reason = serial_ports_unavailable_reason()
    assert "container" in reason.lower()


@pytest.mark.asyncio
async def test_probe_ble_available_does_not_scan(monkeypatch) -> None:
    scanned = False

    class _Scanner:
        @staticmethod
        async def discover(**_kwargs):
            nonlocal scanned
            scanned = True
            return []

    monkeypatch.setattr("app.services.radio_transport.linux_hci_adapters", lambda: None)
    import bleak

    monkeypatch.setattr(bleak, "BleakScanner", _Scanner, raising=False)
    available, reason = await probe_ble_available()
    assert available is True
    assert reason is None
    assert scanned is False


@pytest.mark.asyncio
async def test_scan_ble_devices_maps_bleak_devices(monkeypatch) -> None:
    from bleak.backends.device import BLEDevice

    devices = [
        BLEDevice("AA:BB:CC:DD:EE:FF", "MeshCore-node", None),
        BLEDevice("11:22:33:44:55:66", None, None),
        BLEDevice("", "no-address", None),
    ]
    calls: list[dict] = []

    class _Scanner:
        @staticmethod
        async def discover(**kwargs):
            calls.append(kwargs)
            return devices

    import bleak

    monkeypatch.setattr(bleak, "BleakScanner", _Scanner, raising=False)
    found = await scan_ble_devices()
    assert found == [("AA:BB:CC:DD:EE:FF", "MeshCore-node"), ("11:22:33:44:55:66", None)]
    assert calls == [{"timeout": 5.0}]


def test_meshcore_ble_transport_imports_installed_bleak() -> None:
    # meshcore swallows bleak import errors and silently disables BLE; fail
    # loudly if a bleak upgrade moves a symbol meshcore.ble_cx imports.
    from meshcore import ble_cx

    assert ble_cx.BLEAK_AVAILABLE is True


@pytest.mark.asyncio
async def test_probe_ble_unavailable_without_adapter(monkeypatch) -> None:
    monkeypatch.setattr("app.services.radio_transport.linux_hci_adapters", lambda: [])
    available, reason = await probe_ble_available()
    assert available is False
    assert reason == "No Bluetooth adapter"


@pytest.mark.asyncio
async def test_build_transport_response_keeps_all_transports_selectable(
    test_db, monkeypatch
) -> None:
    from app.services.radio_transport import build_transport_response

    monkeypatch.setattr(
        "app.services.radio_transport.probe_serial_ports",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.radio_transport.probe_ble_available",
        AsyncMock(return_value=(False, "No Bluetooth adapter")),
    )
    response = await build_transport_response()
    assert response.capabilities.tcp is True
    assert response.capabilities.serial is True
    assert response.capabilities.ble is True
    assert response.capabilities.serial_unavailable_reason is not None
    assert response.capabilities.ble_unavailable_reason == "No Bluetooth adapter"
