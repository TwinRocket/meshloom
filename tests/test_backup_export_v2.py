"""Full backup export (meshloom-backup-v2) against a fake radio on the real
meshcore stack: every field, the identity rule, Wi-Fi, warnings, no secret in
any log line."""

from __future__ import annotations

import pytest

import app.keystore as keystore
from app.fanout.manager import SYSTEM_MESHLOOM_STATS_ID
from app.models import BACKUP_SECRET_MASK, ContactUpsert
from app.repository import AppSettingsRepository, ChannelRepository, ContactRepository
from app.repository.fanout import FanoutConfigRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.radio_proxy import RadioProxyRepository
from app.services.backup import export_backup_v2, export_document, export_json
from tests.fixtures.fake_node import FakeNode, connected_node
from tests.fixtures.log_capture import assert_no_secret_logged

WIFI_PASSWORD = " p@ss w0rd "


@pytest.fixture(autouse=True)
def _clean_keystore():
    keystore.clear_keys()
    yield
    keystore.clear_keys()


def _node(**changes) -> FakeNode:
    node = FakeNode()
    node.stored_ssid = "HomeNet"
    node.stored_pwd = WIFI_PASSWORD
    node.tuning = (1500, 2250)
    node.custom_vars = {"gps": "1", "gps_interval": "60"}
    node.default_scope = ("#fr", bytes(range(16)))
    node.autoadd = (3, 2)
    node.repeat = True
    node.path_hash_mode = 1
    node.other = {
        "manual_add_contacts": 1,
        "telemetry": 0b000110,
        "adv_loc_policy": 1,
        "multi_acks": 2,
    }
    for key, value in changes.items():
        setattr(node, key, value)
    return node


@pytest.mark.asyncio
async def test_export_reads_the_radio_and_includes_the_identity(backup_db):
    node = _node()
    keystore.set_private_key(node.private_key)
    async with connected_node(node):
        doc = await export_backup_v2()

    assert doc.format == "meshloom-backup-v2"
    assert doc.identity is not None
    assert doc.identity.public_key == node.public_key
    assert doc.identity.private_key_status == "included"
    radio = doc.radio
    assert radio is not None
    assert (radio.name, radio.tx_power, radio.max_tx_power) == ("Base", 20, 22)
    assert radio.radio is not None and (radio.radio.freq, radio.radio.sf) == (869.525, 11)
    assert radio.client_repeat is True and radio.path_hash_mode == 1
    assert radio.other is not None and radio.other.multi_acks == 2
    assert (radio.other.telemetry_mode_base, radio.other.telemetry_mode_loc) == (2, 1)
    assert radio.tuning is not None and radio.tuning.rx_delay_raw == 1500
    assert radio.custom_vars == {"gps": "1", "gps_interval": "60"}
    assert radio.default_flood_scope is not None
    assert radio.default_flood_scope.key_hex == bytes(range(16)).hex()
    assert radio.autoadd is not None and radio.autoadd.max_hops == 2
    assert radio.ble_pin == 123456
    assert radio.cli_prefs == {"tz.offset": "0", "cad": "off", "int.thresh": "14", "txdelay": "0.5"}
    assert doc.wifi is not None and doc.wifi.ssid == "HomeNet" and doc.wifi.enabled is True
    assert doc.warnings == []

    exported = export_document(doc)
    assert exported["identity"]["private_key"] == node.private_key.hex()
    assert exported["wifi"]["password"] == WIFI_PASSWORD  # byte-exact, spaces kept
    masked = doc.model_dump(mode="json")
    assert masked["identity"]["private_key"] == BACKUP_SECRET_MASK
    assert masked["wifi"]["password"] == BACKUP_SECRET_MASK


@pytest.mark.asyncio
async def test_out_of_range_values_never_abort_the_export(backup_db):
    # An odd board's uint32 PIN, and -9 dBm read as the unsigned byte 247.
    node = _node(ble_pin=4_000_000_000, tx_power=247)
    async with connected_node(node):
        doc = await export_backup_v2()
    assert doc.radio is not None
    assert doc.radio.ble_pin is None
    assert "radio_read_failed:ble_pin" in doc.warnings
    assert doc.radio.tx_power == -9
    assert doc.radio.name == "Base"


@pytest.mark.asyncio
async def test_identity_only_when_the_key_derives_to_the_live_radio(backup_db):
    node = _node()
    keystore.set_private_key(bytes(range(100, 164)))  # another radio's key
    async with connected_node(node):
        doc = await export_backup_v2()
    assert doc.identity is not None
    assert doc.identity.private_key is None
    assert doc.identity.private_key_status == "unavailable"
    assert "private_key_unavailable" in doc.warnings


@pytest.mark.asyncio
async def test_export_disabled_firmware_says_so(backup_db):
    node = _node(export_disabled=True)
    async with connected_node(node) as (loop, _node_):
        assert await keystore.export_and_store_private_key(loop.mc) is False
        doc = await export_backup_v2()
    assert doc.identity is not None and doc.identity.private_key_status == "export_disabled"
    assert "private_key_export_disabled" in doc.warnings


@pytest.mark.asyncio
async def test_no_wifi_block_without_a_stored_password(backup_db):
    node = _node(stored_pwd="")
    async with connected_node(node):
        doc = await export_backup_v2()
    assert doc.wifi is None
    assert "wifi_not_stored" in doc.warnings


@pytest.mark.asyncio
async def test_no_wifi_interface_no_wifi_and_no_warning(backup_db):
    node = _node(wifi=False)
    async with connected_node(node):
        doc = await export_backup_v2()
    assert doc.wifi is None
    assert "wifi_not_stored" not in doc.warnings
    assert doc.radio is not None and doc.radio.cli_prefs  # the CLI itself works


@pytest.mark.asyncio
async def test_firmware_without_cli_skips_cli_prefs_and_wifi(backup_db):
    node = _node(fw_ver=12)
    async with connected_node(node):
        doc = await export_backup_v2()
    assert doc.radio is not None and doc.radio.cli_prefs is None
    assert doc.wifi is None
    assert not any(command.startswith("get wifi") for command in node.commands)


@pytest.mark.asyncio
async def test_radio_disconnected_exports_the_meshloom_part(backup_db):
    from app.radio import radio_manager

    assert radio_manager.meshcore is None
    await ContactRepository.upsert(ContactUpsert(public_key="11" * 32, name="Alice", type=1))
    doc = await export_backup_v2()
    assert doc.identity is None and doc.radio is None and doc.wifi is None
    assert "radio_not_connected" in doc.warnings
    assert "private_key_unavailable" in doc.warnings
    assert [c.public_key for c in doc.contacts] == ["11" * 32]


@pytest.mark.asyncio
async def test_setup_incomplete_is_treated_as_disconnected(backup_db):
    from app.radio import radio_manager

    node = _node()
    async with connected_node(node):
        radio_manager._setup_complete = False
        doc = await export_backup_v2()
    assert doc.radio is None
    assert "radio_not_ready" in doc.warnings
    assert node.log == []  # nothing sent


@pytest.mark.asyncio
async def test_v2_is_a_superset_of_v1(backup_db):
    await ChannelRepository.upsert("AB" * 16, "#ops", is_hashtag=True)
    v1 = (await export_json()).model_dump(mode="json")
    v2 = export_document(await export_backup_v2())
    for key, value in v1.items():
        if key in ("format", "exported_at"):
            continue
        assert v2[key] == value, key


@pytest.mark.asyncio
async def test_meshloom_extra(backup_db):
    await AppSettingsRepository.set_path_hash_one_byte_opt_in(True)
    await AppSettingsRepository.set_vapid_keys("priv-vapid", "pub-vapid")
    await PushSubscriptionRepository.create("https://push.example/1", "p256", "auth", label="Phone")
    await RadioProxyRepository.update(enabled=True, port=5001)
    await FanoutConfigRepository.create(
        config_type="webhook",
        name="Hook",
        config={"url": "https://example.org/hook"},
        scope={"messages": "all", "raw_packets": "none"},
        enabled=True,
        config_id="hook-1",
    )
    await FanoutConfigRepository.create(
        config_type="meshloom_stats",
        name="Stats",
        config={},
        scope={},
        config_id=SYSTEM_MESHLOOM_STATS_ID,
    )
    doc = await export_backup_v2()
    extra = doc.meshloom_extra
    assert extra is not None
    assert extra.path_hash_one_byte_opt_in is True
    assert extra.vapid is not None and extra.vapid.public_key == "pub-vapid"
    assert export_document(doc)["meshloom_extra"]["vapid"]["private_key"] == "priv-vapid"
    assert doc.model_dump()["meshloom_extra"]["vapid"]["private_key"] == BACKUP_SECRET_MASK
    assert [s.endpoint for s in extra.push_subscriptions] == ["https://push.example/1"]
    assert extra.radio_proxy is not None and extra.radio_proxy.port == 5001
    assert [c.id for c in extra.fanout_configs] == ["hook-1"]


@pytest.mark.asyncio
async def test_export_logs_no_secret_at_debug(backup_db, secret_debug_logs):
    node = _node()
    keystore.set_private_key(node.private_key)
    async with connected_node(node) as (loop, _n):
        doc = await export_backup_v2()
        await loop.settle()
    assert doc.wifi is not None
    assert any("get wifi.pwd" in m for m in (r.getMessage() for r in secret_debug_logs.records))
    assert_no_secret_logged(secret_debug_logs, WIFI_PASSWORD.strip(), node.private_key)
