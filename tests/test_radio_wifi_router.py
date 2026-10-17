"""/api/radio/wifi routes on the loopback radio (lot A's FakeCompanionFirmware)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import app.radio as radio_module
import app.services.companion_cli as cli_module
from app.database import Database
from app.radio import RadioManager
from app.repository import AppSettingsRepository
from app.services import radio_wifi
from tests.fixtures.meshcore_loopback import FakeCompanionFirmware, make_loopback

PK = "ab" * 32
SECRET = "S3cr3t-Pw!"
REBOOT_FRAME = b"\x13reboot"


async def _make_radio(monkeypatch):
    firmware = FakeCompanionFirmware()
    loop = await make_loopback(responder=firmware)
    loop.mc.connection_manager._is_connected = True
    loop.mc._self_info = {"public_key": PK, "name": "Node"}
    manager = RadioManager()
    manager._meshcore = loop.mc
    manager.firmware_ver_code = 14
    manager.device_model = "Heltec V3"
    manager._setup_complete = True
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    radio_wifi.clear_cache()
    broadcasts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "app.websocket.broadcast_event",
        lambda event_type, data, *, realtime=True: broadcasts.append((event_type, data)),
    )
    return SimpleNamespace(loop=loop, firmware=firmware, manager=manager, broadcasts=broadcasts)


@pytest.fixture
async def wifi(test_db, monkeypatch):
    env = await _make_radio(monkeypatch)
    yield env
    radio_wifi.clear_cache()
    await env.loop.close()


@pytest.fixture
async def api():
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _detail_code(response: httpx.Response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


async def test_disconnected_radio_is_423(test_db, api, monkeypatch):
    manager = RadioManager()
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    for method, path, body in [
        ("GET", "/api/radio/wifi", None),
        ("PUT", "/api/radio/wifi/credentials", {"ssid": "Home", "password": SECRET}),
        ("PUT", "/api/radio/wifi/enabled", {"enabled": False}),
        ("POST", "/api/radio/wifi/reboot", None),
        ("POST", "/api/radio/wifi/prompt/dismiss", {"radio_public_key": PK}),
    ]:
        response = await api.request(method, path, json=body)
        assert response.status_code == 423, (method, path)
        assert _detail_code(response) == "radio_not_connected"


async def test_connected_with_an_empty_cache_is_pending(wifi, api):
    response = await api.get("/api/radio/wifi")
    assert response.status_code == 200
    assert response.json()["check"] == "pending"
    assert wifi.firmware.commands == []


async def test_refresh_runs_the_full_check(wifi, api):
    response = await api.get("/api/radio/wifi", params={"refresh": "true"})
    assert response.status_code == 200
    body = response.json()
    assert body["check"] == "done"
    assert body["prompt_needed"] is True
    assert body["ssid"] == "FlasherNet"
    assert "password" not in json.dumps(body)


async def test_put_credentials_stores_and_never_echoes_the_password(wifi, api):
    response = await api.put(
        "/api/radio/wifi/credentials", json={"ssid": "FlasherNet", "password": SECRET}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"]["credentials_stored"] is True
    assert body["reboot_required"] is True
    assert body["rebooting"] is False
    assert SECRET not in response.text
    assert wifi.firmware.stored_pwd == SECRET
    assert wifi.broadcasts and wifi.broadcasts[-1][0] == "radio_wifi"
    assert SECRET not in json.dumps(wifi.broadcasts)


async def test_invalid_request_422_never_echoes_the_password(wifi, api):
    # A model error: the 422 body (lot A's handler) carries no input.
    response = await api.put(
        "/api/radio/wifi/credentials", json={"ssid": "x" * 65, "password": SECRET}
    )
    assert response.status_code == 422
    assert SECRET not in response.text
    # A service-side check: coded, nothing sent.
    response = await api.put(
        "/api/radio/wifi/credentials",
        json={"ssid": "Home", "password": SECRET, "open_network": True},
    )
    assert response.status_code == 422
    assert _detail_code(response) == "wifi_invalid_password"
    assert SECRET not in response.text
    assert wifi.firmware.commands == []


async def test_invalid_ssid_is_coded(wifi, api):
    response = await api.put(
        "/api/radio/wifi/credentials", json={"ssid": "(not set)", "password": SECRET}
    )
    assert response.status_code == 422
    assert _detail_code(response) == "wifi_invalid_ssid"


async def test_unsupported_firmware_is_409(wifi, api):
    wifi.firmware.wifi = False
    response = await api.put("/api/radio/wifi/enabled", json={"enabled": False})
    assert response.status_code == 409
    assert _detail_code(response) == "wifi_unsupported"


async def test_cli_timeout_is_422(wifi, api, monkeypatch):
    monkeypatch.setattr(cli_module, "CLI_REPLY_TIMEOUT_SECONDS", 0.1)
    async with cli_module.companion_cli_session("probe") as cli:
        await cli.capabilities()
    wifi.firmware.silent.add("get wifi.enabled")
    response = await api.put("/api/radio/wifi/enabled", json={"enabled": False})
    assert response.status_code == 422
    assert _detail_code(response) == "wifi_cli_timeout"


@pytest.mark.parametrize(
    ("silent", "code"),
    [
        ({f"set wifi.pwd {SECRET}"}, "wifi_store_unverified"),
        ({f"set wifi.pwd {SECRET}", "set wifi.clear"}, "wifi_store_partial"),
    ],
)
async def test_store_failures_are_coded_and_never_reboot(wifi, api, monkeypatch, silent, code):
    monkeypatch.setattr(cli_module, "CLI_REPLY_TIMEOUT_SECONDS", 0.1)
    wifi.firmware.silent.update(silent)
    response = await api.put(
        "/api/radio/wifi/credentials",
        json={"ssid": "Home", "password": SECRET, "reboot": True},
    )
    assert response.status_code == 422
    assert _detail_code(response) == code
    assert SECRET not in response.text
    assert REBOOT_FRAME not in wifi.loop.transport.sent_payloads


async def test_put_enabled_with_reboot(wifi, api):
    response = await api.put("/api/radio/wifi/enabled", json={"enabled": False, "reboot": True})
    assert response.status_code == 200
    assert response.json()["rebooting"] is True
    assert REBOOT_FRAME in wifi.loop.transport.sent_payloads
    assert wifi.manager.expected_reboot == "wifi_config"


async def test_post_reboot_records_the_wifi_reason(wifi, api):
    response = await api.post("/api/radio/wifi/reboot")
    assert response.status_code == 200
    assert response.json()["rebooting"] is True
    assert wifi.loop.transport.sent_payloads[-1] == REBOOT_FRAME
    assert wifi.manager.expected_reboot == "wifi_config"


async def test_post_reboot_423_while_setup_is_incomplete(wifi, api):
    wifi.manager._setup_complete = False
    response = await api.post("/api/radio/wifi/reboot")
    assert response.status_code == 423
    assert REBOOT_FRAME not in wifi.loop.transport.sent_payloads


async def test_dismiss_for_another_radio_is_409_and_records_nothing(wifi, api):
    response = await api.post(
        "/api/radio/wifi/prompt/dismiss", json={"radio_public_key": "cd" * 32}
    )
    assert response.status_code == 409
    assert _detail_code(response) == "wifi_radio_changed"
    assert await AppSettingsRepository.get_wifi_prompt_state() == {}

    response = await api.post("/api/radio/wifi/prompt/dismiss", json={"radio_public_key": PK})
    assert response.status_code == 200
    assert response.json()["prompt_needed"] is False
    assert "dismissed_at" in (await AppSettingsRepository.get_wifi_prompt_state())[PK]


def test_ws_payload_validates_through_dump_ws_event():
    from app.events import dump_ws_event
    from app.models import RadioWifiState

    state = RadioWifiState(check="done", supported=True, ssid="Home", enabled=True)
    serialized = json.loads(dump_ws_event("radio_wifi", state.model_dump()))
    assert serialized["type"] == "radio_wifi"
    assert serialized["data"]["ssid"] == "Home"
    assert serialized["data"]["check"] == "done"


async def test_wifi_password_never_reaches_the_database_files(tmp_path, api, monkeypatch):
    """File-backed WAL database: neither the main file nor -wal/-shm, nor the
    app_settings row, ever holds the password, after a store and a full check."""
    import app.repository.settings as settings_repo

    db = Database(str(tmp_path / "meshloom.db"))
    await db.connect()
    monkeypatch.setattr(settings_repo, "db", db)
    env = await _make_radio(monkeypatch)
    try:
        response = await api.put(
            "/api/radio/wifi/credentials",
            json={"ssid": "Cafe", "password": SECRET, "reboot": False},
        )
        assert response.status_code == 200, response.text
        await api.put("/api/radio/wifi/credentials", json={"ssid": "Bar", "open_network": True})
        await api.get("/api/radio/wifi", params={"refresh": "true"})
        await api.post("/api/radio/wifi/prompt/dismiss", json={"radio_public_key": PK})

        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM app_settings") as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
        dump = json.dumps(rows, default=str)
        assert SECRET not in dump
        assert "Bar" in dump  # the open-network record is there, so the scan reads the row

        blobs = []
        for suffix in ("", "-wal", "-shm"):
            path = tmp_path / f"meshloom.db{suffix}"
            if path.exists():
                blobs.append(path.read_bytes())
        async with db.tx() as conn:
            await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        blobs.append((tmp_path / "meshloom.db").read_bytes())
        for blob in blobs:
            assert SECRET.encode() not in blob
            assert SECRET.encode("utf-16-le") not in blob
    finally:
        radio_wifi.clear_cache()
        await env.loop.close()
        await db.disconnect()
