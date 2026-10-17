"""Full backup models (v2): secrets revealed only in the export document,
masks refused, inputs never echoed; and the radio settings snapshot."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from meshcore import EventType
from meshcore.events import Event
from pydantic import TypeAdapter, ValidationError

import app.keystore as keystore
from app.models import (
    BACKUP_SECRET_MASK,
    BackupDocument,
    BackupExportV2,
    BackupIdentity,
    BackupWifi,
)
from app.services.backup_radio import read_radio_node_settings
from tests.fixtures.log_capture import assert_no_secret_logged

PRIVATE_KEY_HEX = "ab" * 64
WIFI_PASSWORD = " S3cr3t Pw! "
EXPORT = {"backup_export": True}


def _document() -> BackupExportV2:
    return BackupExportV2(
        exported_at=1,
        identity=BackupIdentity(
            public_key="CD" * 32, private_key=PRIVATE_KEY_HEX, private_key_status="included"
        ),  # type: ignore[arg-type]
        wifi=BackupWifi(ssid="Home", password=WIFI_PASSWORD, enabled=True),  # type: ignore[arg-type]
    )


def test_secrets_only_in_the_export_context():
    doc = _document()
    exported = doc.model_dump(mode="json", context=EXPORT)
    assert exported["identity"]["private_key"] == PRIVATE_KEY_HEX
    assert exported["wifi"]["password"] == WIFI_PASSWORD
    assert exported["identity"]["public_key"] == "cd" * 32

    for dumped in (
        doc.model_dump(mode="json"),
        doc.model_dump(),
        json.loads(doc.model_dump_json()),
    ):
        assert dumped["identity"]["private_key"] == BACKUP_SECRET_MASK
        assert dumped["wifi"]["password"] == BACKUP_SECRET_MASK
    for text in (repr(doc), str(doc)):
        assert PRIVATE_KEY_HEX not in text and WIFI_PASSWORD not in text


def test_export_document_round_trips():
    exported = _document().model_dump(mode="json", context=EXPORT)
    parsed = BackupExportV2.model_validate(exported)
    assert parsed.wifi is not None and parsed.wifi.password.get_secret_value() == WIFI_PASSWORD
    assert parsed.identity is not None and parsed.identity.private_key is not None
    assert parsed.identity.private_key.get_secret_value() == PRIVATE_KEY_HEX


@pytest.mark.parametrize("path", [("identity", "private_key"), ("wifi", "password")])
def test_the_mask_is_refused(path):
    exported = _document().model_dump(mode="json", context=EXPORT)
    exported[path[0]][path[1]] = BACKUP_SECRET_MASK
    with pytest.raises(ValidationError):
        BackupExportV2.model_validate(exported)


def test_validation_errors_never_echo_secrets():
    exported = _document().model_dump(mode="json", context=EXPORT)
    bad_key = "zz" + PRIVATE_KEY_HEX[2:]
    exported["identity"]["private_key"] = bad_key
    exported["wifi"]["password"] = WIFI_PASSWORD * 10  # over 63 bytes
    with pytest.raises(ValidationError) as exc:
        BackupExportV2.model_validate(exported)
    assert bad_key not in str(exc.value)
    assert WIFI_PASSWORD not in str(exc.value)
    locs = {".".join(str(part) for part in error["loc"]) for error in exc.value.errors()}
    assert locs == {"identity.private_key", "wifi.password"}


def test_service_side_validation_error_logs_no_secret(secret_debug_logs):
    """The 500 path: a model built in service code logs its ValidationError."""
    exported = _document().model_dump(mode="json", context=EXPORT)
    exported["wifi"]["password"] = WIFI_PASSWORD * 10
    try:
        BackupExportV2.model_validate(exported)
    except ValidationError:
        logging.getLogger("app.example").exception("Unhandled exception on POST /x")
    else:  # pragma: no cover
        pytest.fail("expected a ValidationError")
    assert_no_secret_logged(secret_debug_logs, WIFI_PASSWORD * 10, PRIVATE_KEY_HEX)


def test_document_union_dispatches_on_format():
    adapter = TypeAdapter(BackupDocument)
    assert type(adapter.validate_python({"format": "meshloom-backup-v1"})).__name__ == (
        "BackupV1Document"
    )
    assert type(adapter.validate_python({"format": "remoteterm-backup-v1"})).__name__ == (
        "BackupV1Document"
    )
    assert type(adapter.validate_python({"format": "meshloom-backup-v2"})).__name__ == (
        "BackupExportV2"
    )
    envelope = {
        "format": "meshloom-backup-v2-encrypted",
        "exported_at": 0,
        "kdf": {"name": "scrypt", "n": 1, "r": 8, "p": 1, "salt": ""},
        "cipher": {"name": "aes-256-gcm", "nonce": ""},
        "ciphertext": "",
    }
    assert type(adapter.validate_python(envelope)).__name__ == "BackupEnvelope"
    with pytest.raises(ValidationError):
        adapter.validate_python({"format": "something-else"})


def test_keystore_remembers_a_disabled_export():
    keystore.clear_keys()
    assert keystore.last_export_disabled() is False


@pytest.mark.asyncio
async def test_keystore_flags_disabled_then_clears():
    mc = MagicMock()
    mc.commands.export_private_key = AsyncMock(return_value=Event(EventType.DISABLED, {}))
    assert await keystore.export_and_store_private_key(mc) is False
    assert keystore.last_export_disabled() is True
    key = bytes(range(64))
    mc.commands.export_private_key = AsyncMock(
        return_value=Event(EventType.PRIVATE_KEY, {"private_key": key})
    )
    assert await keystore.export_and_store_private_key(mc) is True
    assert keystore.last_export_disabled() is False
    keystore.clear_keys()


# -- radio snapshot ---------------------------------------------------------

SELF_INFO = {
    "public_key": "EF" * 32,
    "name": "Base",
    "adv_lat": 48.85,
    "adv_lon": 2.35,
    "tx_power": 20,
    "max_tx_power": 22,
    "radio_freq": 869.525,
    "radio_bw": 250.0,
    "radio_sf": 11,
    "radio_cr": 5,
    "manual_add_contacts": True,
    "telemetry_mode_base": 2,
    "telemetry_mode_loc": 1,
    "telemetry_mode_env": 0,
    "adv_loc_policy": 1,
    "multi_acks": 2,
}
DEVICE_INFO = {
    "fw ver": 14,
    "max_contacts": 350,
    "max_channels": 40,
    "ble_pin": 123456,
    "fw_build": "01 Jan 2026",
    "model": "Heltec V3",
    "ver": "v1.18.0",
    "repeat": True,
    "path_hash_mode": 1,
}


def fake_mc(**overrides) -> MagicMock:
    mc = MagicMock()
    responses = {
        "send_appstart": Event(EventType.SELF_INFO, dict(SELF_INFO)),
        "send_device_query": Event(EventType.DEVICE_INFO, dict(DEVICE_INFO)),
        "get_tuning": Event(EventType.TUNING_PARAMS, {"rx_delay": 1500, "airtime_factor": 2250}),
        "get_custom_vars": Event(EventType.CUSTOM_VARS, {"gps": "1", "gps_interval": "60"}),
        "get_default_flood_scope": Event(
            EventType.DEFAULT_FLOOD_SCOPE, {"scope_name": "#fr", "scope_key": "12" * 16}
        ),
        "get_autoadd_config": Event(EventType.AUTOADD_CONFIG, {"config": 3, "max_hops": 2}),
    }
    responses.update(overrides)
    for name, value in responses.items():
        if isinstance(value, Exception):
            setattr(mc.commands, name, AsyncMock(side_effect=value))
        else:
            setattr(mc.commands, name, AsyncMock(return_value=value))
    return mc


class FakeCli:
    def __init__(self, prefs: dict[str, str]):
        self.prefs = prefs

    async def get(self, name: str, *, sensitive: bool = False):
        from app.services.companion_cli import CliUnknownCommandError

        if name not in self.prefs:
            raise CliUnknownCommandError("cli_unknown_command", verb="get", name=name)
        return SimpleNamespace(value=self.prefs[name])


@pytest.mark.asyncio
async def test_every_radio_field_read_from_its_command():
    snapshot = await read_radio_node_settings(fake_mc(), FakeCli({"tz.offset": "2", "cad": "on"}))  # type: ignore[arg-type]
    s = snapshot.settings
    assert snapshot.public_key == "ef" * 32
    assert snapshot.warnings == []
    assert (s.name, s.lat, s.lon, s.tx_power, s.max_tx_power) == ("Base", 48.85, 2.35, 20, 22)
    assert s.radio is not None and (s.radio.freq, s.radio.bw, s.radio.sf, s.radio.cr) == (
        869.525,
        250.0,
        11,
        5,
    )
    assert s.client_repeat is True
    assert s.path_hash_mode == 1
    assert s.other is not None and s.other.multi_acks == 2 and s.other.manual_add_contacts == 1
    assert s.tuning is not None and (s.tuning.rx_delay_raw, s.tuning.airtime_factor_raw) == (
        1500,
        2250,
    )
    assert s.ble_pin == 123456
    assert s.custom_vars == {"gps": "1", "gps_interval": "60"}
    assert s.default_flood_scope is not None
    assert (s.default_flood_scope.name, s.default_flood_scope.key_hex) == ("#fr", "12" * 16)
    assert s.default_flood_scope_read is True
    assert s.autoadd is not None and (s.autoadd.config, s.autoadd.max_hops) == (3, 2)
    assert s.cli_prefs == {"tz.offset": "2", "cad": "on"}
    assert s.firmware is not None and s.firmware.ver_code == 14 and s.firmware.max_contacts == 350


@pytest.mark.asyncio
async def test_unanswered_reads_become_warnings_never_failures():
    mc = fake_mc(
        get_tuning=Event(EventType.ERROR, {}),
        get_default_flood_scope=TimeoutError(),
        get_autoadd_config=Event(EventType.ERROR, {}),
    )
    snapshot = await read_radio_node_settings(mc, None)
    assert snapshot.settings.tuning is None
    assert snapshot.settings.default_flood_scope is None
    assert snapshot.settings.default_flood_scope_read is False
    assert snapshot.settings.autoadd is None
    assert snapshot.settings.cli_prefs is None
    assert snapshot.warnings == [
        "radio_read_failed:tuning",
        "radio_read_failed:default_flood_scope",
        "radio_read_failed:autoadd",
    ]


@pytest.mark.asyncio
async def test_empty_default_scope_is_read_as_none_set():
    snapshot = await read_radio_node_settings(
        fake_mc(get_default_flood_scope=Event(EventType.DEFAULT_FLOOD_SCOPE, {})), None
    )
    assert snapshot.settings.default_flood_scope is None
    assert snapshot.settings.default_flood_scope_read is True


def test_wifi_block_pairs_ssid_and_password_and_is_never_empty():
    from app.models import BackupWifi

    assert BackupWifi(enabled=False).has_credentials is False
    assert BackupWifi(ssid="HomeNet", password="secret-pw").has_credentials is True  # type: ignore[arg-type]
    for bad in ({"ssid": "HomeNet"}, {"password": "secret-pw"}, {}):
        with pytest.raises(ValidationError):
            BackupWifi(**bad)  # type: ignore[arg-type]
