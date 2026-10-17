"""Wi-Fi settings service (app/services/radio_wifi.py) on a real meshcore stack.

The radio is lot A's loopback: a real MeshCore wired to FakeCompanionFirmware,
which emulates the v1.18 companion CLI and its Wi-Fi prefs.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

import app.radio as radio_module
import app.services.companion_cli as cli_module
from app.models import RadioWifiCredentialsRequest, RadioWifiEnabledRequest
from app.radio import RadioManager
from app.repository import AppSettingsRepository
from app.services import radio_wifi
from app.services.companion_cli import (
    CliError,
    CliUnknownCommandError,
    CliUnsupportedError,
    WifiStoreOutcome,
)
from app.services.radio_wifi import WifiRequestError, wifi_reboot_required
from tests.fixtures.meshcore_loopback import FakeCompanionFirmware, make_loopback

PK = "ab" * 32
OTHER_PK = "cd" * 32
SECRET = "S3cr3t-Pw!"
REBOOT_FRAME = b"\x13reboot"


@pytest.fixture
def fast_cli(monkeypatch):
    monkeypatch.setattr(cli_module, "CLI_REPLY_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(cli_module, "CLI_PROBE_ERROR_GRACE_SECONDS", 0.05)


@pytest.fixture
async def wifi(test_db, monkeypatch):
    firmware = FakeCompanionFirmware()
    loop = await make_loopback(responder=firmware)
    manager = RadioManager()
    manager._meshcore = loop.mc
    manager.firmware_ver_code = 14
    manager.device_model = "Heltec V3"
    manager._setup_complete = True
    loop.mc._self_info = {"public_key": PK, "name": "Node"}
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    radio_wifi.clear_cache()
    broadcasts: list[tuple[str, dict, bool]] = []

    def _broadcast(event_type, data, *, realtime=True):
        broadcasts.append((event_type, data, realtime))

    monkeypatch.setattr("app.websocket.broadcast_event", _broadcast)
    yield SimpleNamespace(loop=loop, firmware=firmware, manager=manager, broadcasts=broadcasts)
    radio_wifi.clear_cache()
    await loop.close()


def _sent(env) -> list[bytes]:
    return env.loop.transport.sent_payloads


def _creds(ssid: str, password: str | None = None, **kwargs) -> RadioWifiCredentialsRequest:
    return RadioWifiCredentialsRequest(
        ssid=ssid, password=SecretStr(password) if password is not None else None, **kwargs
    )


def _pwd_reads(firmware: FakeCompanionFirmware) -> int:
    return firmware.commands.count("get wifi.pwd")


# --------------------------------------------------------------------------
# reboot_required, defined in one place
# --------------------------------------------------------------------------


def _outcome(**kwargs) -> WifiStoreOutcome:
    base = {
        "previous_ssid": "Net",
        "previously_stored": False,
        "ssid_changed": False,
        "password_changed": True,
    }
    base.update(kwargs)
    return WifiStoreOutcome(**base)


@pytest.mark.parametrize(
    ("outcome", "connected", "open_record", "open_new", "expected"),
    [
        # Stored credentials: old vs new compare.
        (_outcome(previously_stored=True, password_changed=False), True, None, False, False),
        (_outcome(previously_stored=True, password_changed=True), True, None, False, True),
        (_outcome(previously_stored=True, ssid_changed=True), False, None, False, True),
        # Nothing stored: the flasher shortcut (same SSID, joined, a password).
        (_outcome(), True, None, False, False),
        (_outcome(), False, None, False, True),
        (_outcome(ssid_changed=True), True, None, False, True),
        # "Open network" ticked for the compile-time SSID: old "" equals new "",
        # yet the effective credentials change at the next boot.
        (_outcome(password_changed=False), False, None, True, True),
        (_outcome(password_changed=False), True, None, True, True),
        # An open network recorded for the previous SSID counts as stored.
        (_outcome(password_changed=True), True, "Net", False, True),
        (_outcome(password_changed=False), True, "Net", True, False),
        (_outcome(password_changed=True), True, "Other", False, False),
    ],
)
def test_wifi_reboot_required_table(outcome, connected, open_record, open_new, expected):
    assert (
        wifi_reboot_required(
            outcome,
            connected=connected,
            open_record_ssid=open_record,
            new_password_empty=open_new,
        )
        is expected
    )


# --------------------------------------------------------------------------
# Full check and state
# --------------------------------------------------------------------------


async def test_full_check_reads_state_and_asks_when_nothing_is_stored(wifi):
    wifi.firmware.connected = True
    wifi.firmware.ip = "192.168.1.50"

    state = await radio_wifi.run_full_wifi_check()

    assert state.check == "done"
    assert state.supported is True
    assert state.ssid == "FlasherNet"
    assert state.enabled is True
    assert state.connected is True
    assert state.ip == "192.168.1.50"
    assert state.tcp_port == 5000
    assert state.credentials_stored is False
    assert state.prompt_needed is True
    assert state.radio_public_key == PK
    assert _pwd_reads(wifi.firmware) == 1
    assert wifi.broadcasts[-1][0] == "radio_wifi"
    assert wifi.broadcasts[-1][2] is False  # WS clients only
    assert radio_wifi.last_wifi_state() == state


async def test_non_empty_stored_password_reads_as_stored(wifi):
    wifi.firmware.stored_ssid = "Home"
    wifi.firmware.stored_pwd = SECRET
    state = await radio_wifi.run_full_wifi_check()
    assert state.credentials_stored is True
    assert state.prompt_needed is False
    assert SECRET not in str(wifi.broadcasts)


async def test_open_network_record_counts_only_for_its_ssid(wifi):
    await AppSettingsRepository.mark_wifi_open_network(PK, "FlasherNet")
    state = await radio_wifi.run_full_wifi_check()
    assert state.credentials_stored is True
    assert state.open_network is True
    assert state.prompt_needed is False

    wifi.firmware.fallback_ssid = "Elsewhere"
    radio_wifi.clear_cache()
    state = await radio_wifi.run_full_wifi_check()
    assert state.credentials_stored is False
    assert state.open_network is False
    assert state.prompt_needed is True


async def test_dismissal_is_per_radio(wifi):
    await AppSettingsRepository.mark_wifi_prompt_dismissed(OTHER_PK)
    assert (await radio_wifi.run_full_wifi_check()).prompt_needed is True
    await AppSettingsRepository.mark_wifi_prompt_dismissed(PK)
    radio_wifi.clear_cache()
    assert (await radio_wifi.run_full_wifi_check()).prompt_needed is False


async def test_no_prompt_when_wifi_is_disabled(wifi):
    wifi.firmware.enabled = False
    state = await radio_wifi.run_full_wifi_check()
    assert state.credentials_stored is False
    assert state.prompt_needed is False


async def test_no_prompt_without_a_network_name(wifi):
    wifi.firmware.fallback_ssid = None
    state = await radio_wifi.run_full_wifi_check()
    assert state.ssid is None
    assert state.prompt_needed is False


async def test_firmware_without_wifi_is_unsupported_no_wifi(wifi):
    wifi.firmware.wifi = False
    state = await radio_wifi.run_full_wifi_check()
    assert state.check == "done"
    assert state.supported is False
    assert state.unsupported_reason == "no_wifi"
    assert _pwd_reads(wifi.firmware) == 0


@pytest.mark.parametrize(
    ("model", "ver"), [("MeshloomProxy/abc", 10), ("Heltec V3", 13), ("Heltec V3", None)]
)
async def test_no_cli_reasons_map_to_no_cli_and_send_nothing(wifi, model, ver):
    wifi.manager.device_model = model
    wifi.manager.firmware_ver_code = ver
    state = await radio_wifi.run_full_wifi_check()
    assert state.supported is False
    assert state.unsupported_reason == "no_cli"
    assert wifi.firmware.commands == []


async def test_untagged_firmware_is_no_cli(wifi):
    wifi.firmware.echo_tags = False
    state = await radio_wifi.run_full_wifi_check()
    assert state.check == "done"
    assert state.unsupported_reason == "no_cli"


async def test_probe_timeout_gives_failed_probe_failed(wifi, fast_cli):
    wifi.firmware.silent.add("get wifi.status")
    state = await radio_wifi.run_full_wifi_check(next_retry_at=1234)
    assert state.check == "failed"
    assert state.check_error == "probe_failed"
    assert state.next_retry_at == 1234
    assert state.supported is None


async def test_read_timeout_gives_failed_cli_timeout(wifi, fast_cli):
    wifi.firmware.silent.add("get wifi.pwd")
    state = await radio_wifi.run_full_wifi_check()
    assert state.check == "failed"
    assert state.check_error == "cli_timeout"


async def test_interrupted_store_marker_overrides_a_stored_password(wifi):
    """After a dropped link the radio may hold the new SSID with the old
    password (reads as stored): the marker wins until a verified store."""
    wifi.firmware.stored_ssid = "NewNet"
    wifi.firmware.stored_pwd = "old-password"
    await AppSettingsRepository.mark_wifi_store_started(PK, "NewNet")
    radio_wifi.clear_cache()  # what a reconnect does

    state = await radio_wifi.run_full_wifi_check()

    assert state.store_incomplete is True
    assert state.store_target_ssid == "NewNet"
    assert state.credentials_stored is False
    assert state.prompt_needed is True

    await AppSettingsRepository.mark_wifi_prompt_dismissed(PK)
    radio_wifi.clear_cache()
    state = await radio_wifi.run_full_wifi_check()
    assert state.store_incomplete is True
    assert state.prompt_needed is False  # never ask again, the panel still warns


# --------------------------------------------------------------------------
# Refresh
# --------------------------------------------------------------------------


async def test_get_without_a_check_is_pending(wifi):
    state = await radio_wifi.get_wifi_state(refresh=False)
    assert state.check == "pending"
    assert wifi.firmware.commands == []


async def test_refresh_without_a_completed_check_runs_the_full_check(wifi):
    state = await radio_wifi.get_wifi_state(refresh=True)
    assert state.check == "done"
    assert state.credentials_stored is False
    assert _pwd_reads(wifi.firmware) == 1


async def test_refresh_after_a_check_never_reads_the_password(wifi):
    await radio_wifi.run_full_wifi_check()
    wifi.firmware.connected = True
    wifi.firmware.ip = "10.0.0.9"

    state = await radio_wifi.get_wifi_state(refresh=True)

    assert state.connected is True
    assert state.ip == "10.0.0.9"
    assert state.credentials_stored is False
    assert _pwd_reads(wifi.firmware) == 1


async def test_failed_check_is_retried_by_a_refresh(wifi, fast_cli):
    wifi.firmware.silent.add("get wifi.pwd")
    assert (await radio_wifi.run_full_wifi_check()).check == "failed"
    wifi.firmware.silent.clear()
    state = await radio_wifi.get_wifi_state(refresh=True)
    assert state.check == "done"


# --------------------------------------------------------------------------
# Storing credentials
# --------------------------------------------------------------------------


async def test_flasher_case_stores_without_reboot(wifi):
    """Flasher Wi-Fi-only radio: joined on compiled credentials, nothing stored."""
    wifi.firmware.connected = True
    wifi.firmware.ip = "192.168.1.50"
    await radio_wifi.run_full_wifi_check()
    reads_before = _pwd_reads(wifi.firmware)

    result = await radio_wifi.apply_wifi_credentials(_creds("FlasherNet", SECRET), public_key=PK)

    sets = [c for c in wifi.firmware.commands if c.startswith("set ")]
    assert sets == ["set wifi.ssid FlasherNet", f"set wifi.pwd {SECRET}"]
    assert wifi.firmware.stored_pwd == SECRET
    assert result.reboot_required is False
    assert result.rebooting is False
    assert result.state.credentials_stored is True
    assert result.state.prompt_needed is False
    assert result.state.reboot_pending is False
    # One read before and one read-back, both inside store_wifi_credentials.
    assert _pwd_reads(wifi.firmware) - reads_before == 2
    assert REBOOT_FRAME not in _sent(wifi)
    state = await AppSettingsRepository.get_wifi_prompt_state()
    assert "store_started_at" not in state.get(PK, {})


async def test_flasher_case_not_joined_needs_a_reboot(wifi):
    result = await radio_wifi.apply_wifi_credentials(_creds("FlasherNet", SECRET), public_key=PK)
    assert result.reboot_required is True
    assert result.rebooting is False
    assert result.state.reboot_pending is True
    assert REBOOT_FRAME not in _sent(wifi)


async def test_flasher_case_with_an_open_network_record_needs_a_reboot(wifi):
    wifi.firmware.connected = True
    await AppSettingsRepository.mark_wifi_open_network(PK, "FlasherNet")
    result = await radio_wifi.apply_wifi_credentials(_creds("FlasherNet", SECRET), public_key=PK)
    assert result.reboot_required is True
    # The password store drops the open-network record.
    assert "open_network_ssid" not in (await AppSettingsRepository.get_wifi_prompt_state())[PK]


async def test_store_marker_is_written_before_the_first_set(wifi, monkeypatch):
    seen: list[dict] = []
    original = radio_wifi.store_wifi_credentials

    async def spy(cli, ssid, password, *, typed=True):
        seen.append(dict((await AppSettingsRepository.get_wifi_prompt_state()).get(PK, {})))
        assert not any(c.startswith("set ") for c in wifi.firmware.commands)
        return await original(cli, ssid, password, typed=typed)

    monkeypatch.setattr(radio_wifi, "store_wifi_credentials", spy)
    await radio_wifi.apply_wifi_credentials(_creds("Home", SECRET), public_key=PK)

    assert seen[0]["store_target_ssid"] == "Home"
    assert "store_started_at" in seen[0]
    assert "store_started_at" not in (await AppSettingsRepository.get_wifi_prompt_state())[PK]
    assert SECRET not in str(await AppSettingsRepository.get_wifi_prompt_state())


async def test_ssid_change_reboots_only_when_confirmed(wifi):
    wifi.firmware.stored_ssid = "Old"
    wifi.firmware.stored_pwd = "old-password"

    declined = await radio_wifi.apply_wifi_credentials(_creds("New", SECRET), public_key=PK)
    assert declined.reboot_required is True
    assert declined.rebooting is False
    assert declined.state.reboot_pending is True
    assert REBOOT_FRAME not in _sent(wifi)
    assert wifi.manager.expected_reboot is None

    confirmed = await radio_wifi.apply_wifi_credentials(
        _creds("Newer", SECRET, reboot=True), public_key=PK
    )
    assert confirmed.reboot_required is True
    assert confirmed.rebooting is True
    assert REBOOT_FRAME in _sent(wifi)
    assert wifi.manager.expected_reboot == "wifi_config"
    assert wifi.manager.is_setup_complete is False


async def test_restoring_identical_stored_values_needs_no_reboot(wifi):
    wifi.firmware.stored_ssid = "Home"
    wifi.firmware.stored_pwd = SECRET
    result = await radio_wifi.apply_wifi_credentials(
        _creds("Home", SECRET, reboot=True), public_key=PK
    )
    assert result.reboot_required is False
    assert result.rebooting is False
    assert REBOOT_FRAME not in _sent(wifi)


async def test_open_network_sends_an_empty_password_and_records_the_ssid(wifi):
    wifi.firmware.stored_ssid = "Old"
    wifi.firmware.stored_pwd = "old-password"
    result = await radio_wifi.apply_wifi_credentials(
        _creds("Cafe", open_network=True), public_key=PK
    )
    assert "set wifi.pwd " in wifi.firmware.commands
    assert any(p == b"\x42" + p[1:3] + b"|set wifi.pwd " for p in wifi.firmware.raw_payloads)
    assert wifi.firmware.stored_pwd == ""
    assert result.state.credentials_stored is True
    assert result.state.open_network is True
    entry = (await AppSettingsRepository.get_wifi_prompt_state())[PK]
    assert entry["open_network_ssid"] == "Cafe"


async def test_a_dismissal_survives_a_store_and_an_open_network_record(wifi):
    await AppSettingsRepository.mark_wifi_prompt_dismissed(PK)
    await radio_wifi.apply_wifi_credentials(_creds("Cafe", open_network=True), public_key=PK)
    await radio_wifi.apply_wifi_credentials(_creds("Home", SECRET), public_key=PK)
    assert "dismissed_at" in (await AppSettingsRepository.get_wifi_prompt_state())[PK]


@pytest.mark.parametrize(
    "request_body",
    [
        {"ssid": "Home", "password": None, "open_network": False},
        {"ssid": "Home", "password": "", "open_network": False},
        {"ssid": "Home", "password": SECRET, "open_network": True},
        {"ssid": "Home", "password": "x" * 64, "open_network": False},
        {"ssid": "Home", "password": "bad\npassword", "open_network": False},
    ],
)
async def test_invalid_password_requests_send_nothing(wifi, request_body):
    body = RadioWifiCredentialsRequest.model_validate(request_body)
    with pytest.raises(WifiRequestError) as error:
        await radio_wifi.apply_wifi_credentials(body, public_key=PK)
    assert error.value.code == "wifi_invalid_password"
    assert SECRET not in str(error.value)
    assert wifi.firmware.commands == []


@pytest.mark.parametrize("ssid", ["", "x" * 33, "(not set)", "tab\there"])
async def test_invalid_ssid_requests_send_nothing(wifi, ssid):
    with pytest.raises(WifiRequestError) as error:
        await radio_wifi.apply_wifi_credentials(_creds(ssid, SECRET), public_key=PK)
    assert error.value.code == "wifi_invalid_ssid"
    assert wifi.firmware.commands == []


async def test_ssid_is_sent_untrimmed(wifi):
    await radio_wifi.apply_wifi_credentials(_creds("  My Net ", SECRET), public_key=PK)
    assert wifi.firmware.stored_ssid == "  My Net "


async def test_password_timeout_rolls_back_without_a_reboot(wifi, fast_cli):
    wifi.firmware.silent.add(f"set wifi.pwd {SECRET}")
    with pytest.raises(CliError) as error:
        await radio_wifi.apply_wifi_credentials(_creds("Home", SECRET, reboot=True), public_key=PK)
    assert error.value.code == "wifi_store_unverified"
    assert wifi.firmware.commands.count(f"set wifi.pwd {SECRET}") == 2  # retried once
    assert "set wifi.clear" in wifi.firmware.commands  # nothing was stored before
    assert REBOOT_FRAME not in _sent(wifi)
    assert "store_started_at" not in (await AppSettingsRepository.get_wifi_prompt_state()).get(
        PK, {}
    )


async def test_partial_store_keeps_the_marker_and_never_reboots(wifi, fast_cli):
    await radio_wifi.run_full_wifi_check()
    wifi.firmware.silent.update({f"set wifi.pwd {SECRET}", "set wifi.clear"})
    with pytest.raises(CliError) as error:
        await radio_wifi.apply_wifi_credentials(_creds("Home", SECRET, reboot=True), public_key=PK)
    assert error.value.code == "wifi_store_partial"
    assert REBOOT_FRAME not in _sent(wifi)
    entry = (await AppSettingsRepository.get_wifi_prompt_state())[PK]
    assert entry["store_target_ssid"] == "Home"
    cached = radio_wifi.last_wifi_state()
    assert cached is not None and cached.store_incomplete is True
    assert cached.store_target_ssid == "Home"
    assert cached.credentials_stored is False


async def test_store_refused_without_wifi_sends_no_set(wifi):
    wifi.firmware.wifi = False
    with pytest.raises(CliUnknownCommandError):
        await radio_wifi.apply_wifi_credentials(_creds("Home", SECRET), public_key=PK)
    assert not any(c.startswith("set ") for c in wifi.firmware.commands)


async def test_store_refused_without_cli_sends_nothing(wifi):
    wifi.manager.firmware_ver_code = 13
    with pytest.raises(CliUnsupportedError):
        await radio_wifi.apply_wifi_credentials(_creds("Home", SECRET), public_key=PK)
    assert wifi.firmware.commands == []


async def test_store_never_logs_the_password(wifi, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="meshcore")
    await radio_wifi.run_full_wifi_check()
    await radio_wifi.apply_wifi_credentials(_creds("Home", SECRET), public_key=PK)
    await radio_wifi.get_wifi_state(refresh=True)
    assert any("sending pkt" in r.getMessage() for r in caplog.records)
    text = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert SECRET not in text
    assert SECRET.encode().hex() not in text.lower()
    assert SECRET not in str(wifi.broadcasts)


def test_request_models_keep_the_password_out_of_validation_errors():
    """Service-side ValidationError is a 500 logged with str(e)."""
    with pytest.raises(ValidationError) as error:
        RadioWifiCredentialsRequest.model_validate({"ssid": "Home", "password": 31415926535})
    assert "31415926535" not in str(error.value)
    with pytest.raises(ValidationError) as error:
        RadioWifiCredentialsRequest.model_validate({"ssid": SECRET * 10, "password": SECRET})
    assert SECRET not in str(error.value)


# --------------------------------------------------------------------------
# Enable / disable and reboot
# --------------------------------------------------------------------------


async def test_disabling_needs_a_reboot_sent_only_when_confirmed(wifi):
    result = await radio_wifi.apply_wifi_enabled(
        RadioWifiEnabledRequest(enabled=False), public_key=PK
    )
    assert wifi.firmware.enabled is False
    assert result.reboot_required is True
    assert result.state.reboot_pending is True
    assert result.state.enabled is False
    assert REBOOT_FRAME not in _sent(wifi)

    result = await radio_wifi.apply_wifi_enabled(
        RadioWifiEnabledRequest(enabled=True, reboot=True), public_key=PK
    )
    assert result.rebooting is True
    assert REBOOT_FRAME in _sent(wifi)
    assert wifi.manager.expected_reboot == "wifi_config"


async def test_enabling_without_a_network_name_sends_no_reboot(wifi):
    wifi.firmware.fallback_ssid = None
    wifi.firmware.enabled = False
    result = await radio_wifi.apply_wifi_enabled(
        RadioWifiEnabledRequest(enabled=True, reboot=True), public_key=PK
    )
    assert wifi.firmware.enabled is True
    assert result.reboot_required is False
    assert result.rebooting is False
    assert REBOOT_FRAME not in _sent(wifi)


async def test_reboot_for_wifi_config_records_the_planned_reboot(wifi):
    await radio_wifi.apply_wifi_credentials(_creds("FlasherNet", SECRET), public_key=PK)
    assert radio_wifi.last_wifi_state().reboot_pending is True

    result = await radio_wifi.reboot_for_wifi_config()

    assert result.rebooting is True
    assert result.reboot_required is False
    assert result.state.reboot_pending is False
    assert _sent(wifi)[-1] == REBOOT_FRAME
    assert wifi.manager.expected_reboot == "wifi_config"
    assert wifi.manager.take_expected_reboot_teardown() is True


async def test_reboot_for_wifi_config_refused_before_setup(wifi):
    wifi.manager._setup_complete = False
    with pytest.raises(CliError) as error:
        await radio_wifi.reboot_for_wifi_config()
    assert error.value.code == "setup_incomplete"
    assert REBOOT_FRAME not in _sent(wifi)


# --------------------------------------------------------------------------
# Dismissal and cache ordering
# --------------------------------------------------------------------------


async def test_dismiss_records_only_for_the_connected_radio(wifi):
    await radio_wifi.run_full_wifi_check()
    with pytest.raises(WifiRequestError) as error:
        await radio_wifi.dismiss_wifi_prompt(OTHER_PK)
    assert error.value.code == "wifi_radio_changed"
    assert error.value.status == 409
    assert await AppSettingsRepository.get_wifi_prompt_state() == {}

    state = await radio_wifi.dismiss_wifi_prompt(PK.upper())
    assert state.prompt_needed is False
    assert "dismissed_at" in (await AppSettingsRepository.get_wifi_prompt_state())[PK]
    assert wifi.broadcasts[-1][1]["prompt_needed"] is False


async def test_a_check_computed_before_a_store_never_overwrites_it(wifi):
    await radio_wifi.run_full_wifi_check()
    stale_token = radio_wifi._token()
    stale = radio_wifi.last_wifi_state()
    await radio_wifi.apply_wifi_credentials(_creds("FlasherNet", SECRET), public_key=PK)

    assert radio_wifi._write_cache(stale, stale_token, raw_password_stored=False) is False
    assert radio_wifi.last_wifi_state().credentials_stored is True


async def test_status_merge_changes_only_connected_and_ip(wifi):
    await radio_wifi.apply_wifi_credentials(_creds("FlasherNet", SECRET), public_key=PK)
    before = radio_wifi.last_wifi_state()
    assert await radio_wifi._merge_link_status(radio_wifi._token(), True, "10.1.1.1") is True
    after = radio_wifi.last_wifi_state()
    assert after.connected is True and after.ip == "10.1.1.1"
    assert after.model_dump(exclude={"connected", "ip"}) == before.model_dump(
        exclude={"connected", "ip"}
    )


async def test_a_new_connection_drops_writes_from_the_old_one(wifi):
    token = radio_wifi._token()
    state = await radio_wifi.run_full_wifi_check()
    wifi.manager._reset_connected_runtime_state()  # teardown: new link, cache cleared
    assert radio_wifi.last_wifi_state() is None
    assert radio_wifi._write_cache(state, token, raw_password_stored=False) is False
    assert await radio_wifi._merge_link_status(token, True, "10.0.0.1") is False
    assert radio_wifi.last_wifi_state() is None
