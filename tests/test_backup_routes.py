"""Full backup routes: export (plain and encrypted), inspect, and their errors.

No secret in a response body other than the export file, and none in any log
line, even at DEBUG and on validation failures.
"""

from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

import app.keystore as keystore
import app.main  # noqa: F401  (setup_logging runs at import, never inside a test)
import app.services.backup_crypto as crypto
from app.repository.radio_transport import RadioTransportRepository
from app.services.backup_crypto import decrypt_backup
from tests.fixtures.fake_node import FakeNode, connected_node
from tests.fixtures.log_capture import assert_no_secret_logged

WIFI_PASSWORD = "Wifi-S3cret"
PASSPHRASE = "a long passphrase"


@pytest.fixture(autouse=True)
def _fast_kdf(monkeypatch):
    monkeypatch.setattr(crypto, "SCRYPT_N", 2**4)
    monkeypatch.setattr(crypto, "SCRYPT_MIN_N", 2**4)


@pytest.fixture(autouse=True)
def _clean_keystore():
    keystore.clear_keys()
    yield
    keystore.clear_keys()


def _node(name: str = "Base") -> FakeNode:
    node = FakeNode()
    node.name = name
    node.stored_ssid = "HomeNet"
    node.stored_pwd = WIFI_PASSWORD
    return node


async def _export(client, node: FakeNode, body: dict | None = None):
    keystore.set_private_key(node.private_key)
    async with connected_node(node):
        return await client.post("/api/settings/backup/export", json=body or {})


@pytest.mark.asyncio
async def test_export_plain_file_holds_the_real_secrets(backup_db, client):
    node = _node()
    response = await _export(client, node)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["format"] == "meshloom-backup-v2"
    assert body["identity"]["private_key"] == node.private_key.hex()
    assert body["wifi"]["password"] == WIFI_PASSWORD


@pytest.mark.asyncio
async def test_export_without_radio_still_works(backup_db, client):
    response = await client.post("/api/settings/backup/export")
    assert response.status_code == 200
    body = response.json()
    assert body["identity"] is None and body["radio"] is None
    assert "radio_not_connected" in body["warnings"]
    assert "meshloom-backup-meshloom-" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_export_filename_is_safe(backup_db, client):
    node = _node(name='Ré"seau;  Nord')
    response = await _export(client, node)
    disposition = response.headers["content-disposition"]
    ascii_part = disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert ascii_part.startswith("meshloom-backup-Re-seau-Nord-")
    assert all(ch.isalnum() or ch in "._-" for ch in ascii_part)
    utf8_part = disposition.split("filename*=UTF-8''", 1)[1]
    assert '"' not in utf8_part and ";" not in utf8_part and " " not in utf8_part
    assert unquote(utf8_part).startswith('meshloom-backup-Ré"seau;  Nord-')


@pytest.mark.asyncio
async def test_export_encrypted(backup_db, client):
    node = _node()
    response = await _export(client, node, {"passphrase": PASSPHRASE})
    assert response.status_code == 200
    envelope = response.json()
    assert envelope["format"] == "meshloom-backup-v2-encrypted"
    assert envelope["public_key"] == node.public_key
    assert node.private_key.hex() not in response.text
    assert WIFI_PASSWORD not in response.text
    assert PASSPHRASE not in response.text
    document = decrypt_backup(envelope, PASSPHRASE)
    assert document["identity"]["private_key"] == node.private_key.hex()


@pytest.mark.asyncio
async def test_export_refuses_a_short_passphrase(backup_db, client):
    response = await client.post("/api/settings/backup/export", json={"passphrase": "short"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "backup_passphrase_too_short"
    assert "short" not in json.dumps(response.json()["detail"]["params"])


# -- inspect -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_accepts_a_v1_file_without_format(backup_db, client):
    response = await client.post(
        "/api/settings/backup/inspect",
        json={"backup": {"exported_at": 5, "contacts": [{"public_key": "11" * 32}]}},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["format"] == "meshloom-backup-v1"
    assert result["counts"]["contacts"] == 1
    assert result["identity_relation"] == "no_identity"


@pytest.mark.asyncio
async def test_inspect_refuses_an_unknown_format(backup_db, client):
    response = await client.post(
        "/api/settings/backup/inspect", json={"backup": {"format": "something-else"}}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "backup_invalid"


@pytest.mark.asyncio
async def test_inspect_a_full_backup_with_the_radio_connected(backup_db, client):
    node = _node()
    exported = (await _export(client, node)).json()
    exported["meshloom_extra"]["fanout_configs"] = [
        {"id": "bot-1", "type": "bot", "name": "Greeter", "config": {"code": "x"}}
    ]
    await RadioTransportRepository.bind_public_key(node.public_key)
    async with connected_node(node):
        response = await client.post("/api/settings/backup/inspect", json={"backup": exported})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["format"] == "meshloom-backup-v2"
    assert result["has_private_key"] is True
    assert result["has_wifi"] is True
    assert result["radio_name"] == "Base"
    assert result["bot_names"] == ["Greeter"]
    assert result["identity_relation"] == "same"
    assert result["live_radio_public_key"] == node.public_key
    assert WIFI_PASSWORD not in response.text
    assert node.private_key.hex() not in response.text


@pytest.mark.asyncio
async def test_inspect_names_a_different_live_radio(backup_db, client):
    node = _node()
    exported = (await _export(client, node)).json()
    other = FakeNode(private_key=bytes(range(100, 164)))
    await RadioTransportRepository.bind_public_key(other.public_key)
    async with connected_node(other):
        result = (
            await client.post("/api/settings/backup/inspect", json={"backup": exported})
        ).json()
    assert result["identity_relation"] == "replaces_live"
    assert result["identity_requires"] == {"replace_live_identity": True, "wipe_history": False}
    assert result["live_radio_public_key"] == other.public_key


@pytest.mark.asyncio
async def test_inspect_flags_another_ssid_on_tcp(backup_db, client):
    node = _node()
    exported = (await _export(client, node)).json()
    snapshot = await RadioTransportRepository.get()
    snapshot.transport = "tcp"
    snapshot.tcp_host = "10.0.0.2"
    await RadioTransportRepository.save(snapshot)
    node.stored_ssid = "OtherNet"
    async with connected_node(node):
        result = (
            await client.post("/api/settings/backup/inspect", json={"backup": exported})
        ).json()
    assert result["wifi_ssid_differs_on_tcp"] is True


@pytest.mark.asyncio
async def test_inspect_flags_another_ble_pin_on_ble(backup_db, client):
    node = _node()
    exported = (await _export(client, node)).json()
    snapshot = await RadioTransportRepository.get()
    snapshot.transport = "ble"
    snapshot.ble_address = "AA:BB:CC:DD:EE:FF"
    snapshot.ble_pin = "654321"
    await RadioTransportRepository.save(snapshot)
    result = (await client.post("/api/settings/backup/inspect", json={"backup": exported})).json()
    assert result["ble_pin_differs_on_ble"] is True


@pytest.mark.asyncio
async def test_inspect_an_encrypted_file(backup_db, client):
    node = _node()
    envelope = (await _export(client, node, {"passphrase": PASSPHRASE})).json()
    missing = await client.post("/api/settings/backup/inspect", json={"backup": envelope})
    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "backup_passphrase_required"
    wrong = await client.post(
        "/api/settings/backup/inspect", json={"backup": envelope, "passphrase": "wrong passphrase"}
    )
    assert wrong.json()["detail"]["code"] == "backup_bad_passphrase"
    right = await client.post(
        "/api/settings/backup/inspect", json={"backup": envelope, "passphrase": PASSPHRASE}
    )
    assert right.status_code == 200
    assert right.json()["encrypted"] is True and right.json()["has_private_key"] is True


@pytest.mark.asyncio
async def test_invalid_secrets_never_reach_a_body_or_a_log(backup_db, client, secret_debug_logs):
    node = _node()
    exported = (await _export(client, node)).json()
    bad_key = "zz" + node.private_key.hex()[2:]
    long_password = WIFI_PASSWORD * 8
    exported["identity"]["private_key"] = bad_key
    exported["wifi"]["password"] = long_password
    response = await client.post("/api/settings/backup/inspect", json={"backup": exported})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "backup_invalid"
    assert set(detail["params"]["fields"]) >= {
        "backup.meshloom-backup-v2.identity.private_key",
        "backup.meshloom-backup-v2.wifi.password",
    }
    assert bad_key not in response.text and long_password not in response.text
    assert_no_secret_logged(secret_debug_logs, bad_key, long_password)


@pytest.mark.asyncio
async def test_a_key_that_does_not_match_its_public_key_is_refused(backup_db, client):
    node = _node()
    exported = (await _export(client, node)).json()
    exported["identity"]["public_key"] = "ee" * 32
    response = await client.post("/api/settings/backup/inspect", json={"backup": exported})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "backup_identity_invalid"


@pytest.mark.asyncio
async def test_inspect_refuses_the_mask(backup_db, client):
    node = _node()
    exported = (await _export(client, node)).json()
    exported["wifi"]["password"] = "**********"
    response = await client.post("/api/settings/backup/inspect", json={"backup": exported})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "backup_invalid"
