"""BLE pairing against a fake BlueZ on a private dbus-daemon.

The real D-Bus protocol runs end to end (agent export, RegisterAgent and Pair on
one connection, the agent callback, NameOwnerChanged), without the host's
system bus or Bluetooth hardware.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="BlueZ pairing is Linux-only"
)

ADDRESS = "AA:BB:CC:DD:EE:01"
PIN = "123456"

_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:path={socket}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


@pytest.fixture(scope="module")
def dbus_address(tmp_path_factory):
    daemon = shutil.which("dbus-daemon")
    if daemon is None:
        if os.environ.get("CI"):
            pytest.fail("dbus-daemon is required in CI for the BLE pairing tests")
        pytest.skip("dbus-daemon is not installed")
    root = tmp_path_factory.mktemp("dbus")
    socket_path = root / "bus"
    config = root / "bus.conf"
    config.write_text(_CONFIG.format(socket=socket_path))
    process = subprocess.Popen(
        [daemon, "--nofork", "--nopidfile", f"--config-file={config}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not socket_path.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            stderr = process.stderr.read().decode() if process.stderr else ""
            process.kill()
            pytest.fail(f"dbus-daemon did not start: {stderr}")
        time.sleep(0.02)
    yield f"unix:path={socket_path}"
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


@pytest.fixture
def pairing(monkeypatch, dbus_address):
    from app.services import ble_pairing

    async def _open_private_bus():
        return await ble_pairing._connect_bus(dbus_address, system=False)

    monkeypatch.setattr(ble_pairing, "_open_bus", _open_private_bus)
    monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
    monkeypatch.setattr(ble_pairing, "DISCOVERY_TIMEOUT", 0.6)
    monkeypatch.setattr(ble_pairing, "DISCOVERY_POLL", 0.05)
    monkeypatch.setattr(ble_pairing, "DISCONNECT_WAIT", 0.5)
    monkeypatch.setattr(ble_pairing, "DISCONNECT_POLL", 0.02)
    monkeypatch.setattr(ble_pairing, "PAIR_TIMEOUT", 2.0)
    monkeypatch.setattr(ble_pairing, "OVERALL_TIMEOUT", 5.0)
    return ble_pairing


@pytest.fixture
async def bluez(dbus_address):
    from tests.fake_bluez import FakeBlueZ

    fake = FakeBlueZ(dbus_address)
    await fake.start()
    try:
        yield fake
    finally:
        await fake.stop()


async def _settle(fake, member: str, timeout: float = 1.0) -> None:
    """Wait until the fake has seen `member` (calls sent after a reply)."""
    deadline = time.monotonic() + timeout
    while member not in fake.members() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


class TestFreshPairing:
    async def test_pairs_trusts_and_releases_the_link(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        path = bluez.add_device(adapter, ADDRESS, passkey=123456)

        outcome = await pairing.ensure_paired(ADDRESS.lower(), PIN)

        assert outcome.state == "paired"
        assert outcome.address == ADDRESS
        assert outcome.adapter_path == adapter
        assert outcome.device_path == path
        props = bluez.devices[path]
        assert props["Paired"] and props["Bonded"] and props["Trusted"]
        assert props["Connected"] is False
        assert outcome.device_props["Connected"] is False
        register = bluez.calls_to("RegisterAgent")
        assert len(register) == 1
        assert register[0].body[1] == "KeyboardOnly"
        assert register[0].body[0].startswith("/org/meshloom/ble_agent/")
        assert bluez.calls_to("RequestDefaultAgent") == []
        members = bluez.members()
        assert members.index("RegisterAgent") < members.index("Pair")
        assert members.index("Pair") < members.index("Disconnect")
        assert members.index("Disconnect") < members.index("UnregisterAgent")
        assert bluez.agents == {}
        # Known device: no discovery.
        assert "StartDiscovery" not in members
        # The same connection registered the agent and paired.
        assert register[0].sender == bluez.calls_to("Pair")[0].sender

    async def test_late_release_after_unregister_is_harmless(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS)

        await pairing.ensure_paired(ADDRESS, PIN)
        deadline = time.monotonic() + 1
        while not any(m == "Release" for m, _ in bluez.agent_calls):
            assert time.monotonic() < deadline, "Release was never sent"
            await asyncio.sleep(0.01)

        # Depending on timing the agent answers or is already gone (error reply);
        # either way BlueZ gets a reply and nothing raises on our side.
        assert ("RequestPasskey", "METHOD_RETURN") in bluez.agent_calls
        release = [reply for member, reply in bluez.agent_calls if member == "Release"]
        assert release in (["ERROR"], ["METHOD_RETURN"])

    async def test_session_only_pairing_pairs_again(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS, Paired=True, Bonded=False)

        outcome = await pairing.ensure_paired(ADDRESS, PIN)

        assert outcome.state == "paired"
        assert len(bluez.calls_to("Pair")) == 1

    async def test_leading_zero_pin(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS, passkey=12345)

        outcome = await pairing.ensure_paired(ADDRESS, "012345")

        assert outcome.state == "paired"


class TestAlreadyPaired:
    async def test_bonded_device_registers_no_agent(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        path = bluez.add_device(adapter, ADDRESS, Paired=True, Bonded=True, Trusted=False)

        outcome = await pairing.ensure_paired(ADDRESS, PIN)

        assert outcome.state == "already_paired"
        assert "RegisterAgent" not in bluez.members()
        assert "Pair" not in bluez.members()
        assert bluez.devices[path]["Trusted"] is True

    @pytest.mark.parametrize("pin", ["1234567", "none", "0000-0"])
    @pytest.mark.parametrize("explicit", [False, True])
    async def test_bonded_device_ignores_the_saved_pin_format(self, pairing, bluez, pin, explicit):
        # Bonded earlier with bluetoothctl, any placeholder saved as the PIN: still
        # connects, as it did before Meshloom paired by itself.
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS, Paired=True, Bonded=True, Trusted=True)

        outcome = await pairing.ensure_paired(ADDRESS, pin, explicit=explicit)

        assert outcome.state == "already_paired"
        assert "RegisterAgent" not in bluez.members()
        assert pairing._last_failure is None

    async def test_bonded_and_connected_releases_the_orphan_link(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        path = bluez.add_device(
            adapter, ADDRESS, Paired=True, Bonded=True, Trusted=True, Connected=True
        )

        await pairing.ensure_paired(ADDRESS, PIN)

        assert "Disconnect" in bluez.members()
        assert bluez.devices[path]["Connected"] is False

    async def test_paired_without_bonded_property_counts_as_bonded(self, pairing, bluez):
        bluez.bonded_property = False
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS, Paired=True, Trusted=True)

        outcome = await pairing.ensure_paired(ADDRESS, PIN)

        assert outcome.state == "already_paired"

    async def test_bond_on_another_adapter_is_ignored(self, pairing, bluez):
        hci0 = bluez.add_adapter("hci0")
        hci1 = bluez.add_adapter("hci1")
        bluez.add_device(hci1, ADDRESS, Paired=True, Bonded=True, Trusted=True)
        path0 = bluez.add_device(hci0, ADDRESS)

        outcome = await pairing.ensure_paired(ADDRESS, PIN)

        assert outcome.state == "paired"
        assert outcome.adapter_path == hci0
        assert outcome.device_path == path0
        assert bluez.calls_to("Pair")[0].path == path0


class TestDiscovery:
    async def test_unknown_device_is_discovered_then_paired(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_hidden_device(adapter, ADDRESS, delay=0.1)

        outcome = await pairing.ensure_paired(ADDRESS, PIN)

        assert outcome.state == "paired"
        members = bluez.members()
        assert members.index("SetDiscoveryFilter") < members.index("StartDiscovery")
        assert members.index("StopDiscovery") < members.index("RegisterAgent")
        assert bluez.discovering[adapter] == set()

    async def test_device_never_seen(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_hidden_device(adapter, ADDRESS, delay=None)

        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, PIN)

        assert excinfo.value.code == "ble_pair_device_not_found"
        assert "StopDiscovery" in bluez.members()
        assert "RegisterAgent" not in bluez.members()
        assert bluez.discovering[adapter] == set()

    async def test_adapter_off(self, pairing, bluez):
        bluez.add_adapter("hci0", powered=False)

        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, PIN)

        assert excinfo.value.code == "ble_pair_adapter_off"
        assert excinfo.value.fallback is False


class TestFailures:
    async def test_invalid_pin_on_a_known_device_registers_no_agent(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS, passkey=123456)

        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, "1234567")

        assert excinfo.value.code == "ble_pair_invalid_pin"
        assert "RegisterAgent" not in bluez.members()
        assert "Pair" not in bluez.members()
        assert pairing.auto_pair_blocked(ADDRESS, "1234567")

    async def test_invalid_pin_on_an_unknown_device_skips_discovery(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_hidden_device(adapter, ADDRESS, delay=0.1)

        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, "none")

        assert excinfo.value.code == "ble_pair_invalid_pin"
        assert "StartDiscovery" not in bluez.members()
        assert "RegisterAgent" not in bluez.members()

    async def test_wrong_pin(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        path = bluez.add_device(adapter, ADDRESS, passkey=654321)

        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, PIN)

        assert excinfo.value.code == "ble_pair_wrong_pin"
        assert bluez.passkey_requests == [path]
        assert "UnregisterAgent" in bluez.members()
        assert bluez.agents == {}
        # The link the failed attempt left up is released.
        assert bluez.devices[path]["Connected"] is False
        assert pairing.auto_pair_blocked(ADDRESS, PIN)

        # Sticky: the next automatic attempt sends nothing.
        before = len(bluez.calls)
        with pytest.raises(pairing.BlePairingError) as again:
            await pairing.ensure_paired(ADDRESS, PIN)
        assert again.value.code == "ble_pair_wrong_pin"
        assert again.value.already_reported is True
        assert len(bluez.calls) == before

    async def test_refused_without_passkey_request(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS)
        bluez.pair_error = "org.bluez.Error.AuthenticationFailed"

        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, PIN)

        assert excinfo.value.code == "ble_pair_rejected"

    async def test_pair_timeout_cancels_pairing_then_disconnects(self, pairing, bluez, monkeypatch):
        monkeypatch.setattr(pairing, "PAIR_TIMEOUT", 0.2)
        adapter = bluez.add_adapter("hci0")
        path = bluez.add_device(adapter, ADDRESS)
        bluez.pair_delay = 5.0

        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, PIN)

        assert excinfo.value.code == "ble_pair_timeout"
        members = bluez.members()
        assert members.index("CancelPairing") < members.index("UnregisterAgent")
        assert "Disconnect" in members
        assert bluez.devices[path]["Connected"] is False

    async def test_cancellation_during_pair_does_no_round_trip(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS)
        bluez.pair_delay = 10.0

        task = asyncio.create_task(pairing.ensure_paired(ADDRESS, PIN))
        await _settle(bluez, "Pair")
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.monotonic() - started < 1.0

        sender = bluez.calls_to("Pair")[0].sender
        deadline = time.monotonic() + 2
        while sender not in bluez.lost_names and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert sender in bluez.lost_names
        assert bluez.agents == {}
        assert [c.member for c in bluez.calls if c.sender == sender].count("UnregisterAgent") == 0
        assert "CancelPairing" not in bluez.members()
        assert "StopDiscovery" not in bluez.members()
        assert bluez.calls_after_lost == []
        assert not pairing.in_progress()

    async def test_bluez_not_running(self, pairing):
        with pytest.raises(pairing.BlePairingError) as excinfo:
            await pairing.ensure_paired(ADDRESS, PIN)

        assert excinfo.value.code == "ble_pair_bluez_unavailable"
        assert excinfo.value.fallback is True


class TestForget:
    async def test_forget_removes_the_device_on_every_adapter(self, pairing, bluez):
        hci0 = bluez.add_adapter("hci0")
        hci1 = bluez.add_adapter("hci1")
        path0 = bluez.add_device(hci0, ADDRESS, Paired=True, Bonded=True, Connected=True)
        path1 = bluez.add_device(hci1, ADDRESS, Paired=True, Bonded=True)

        removed = await pairing.forget(ADDRESS)

        assert removed == 2
        assert path0 not in bluez.devices and path1 not in bluez.devices
        members = bluez.members()
        assert members.index("Disconnect") < members.index("RemoveDevice")

    async def test_forget_clears_a_sticky_failure(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")
        bluez.add_device(adapter, ADDRESS, passkey=1)
        with pytest.raises(pairing.BlePairingError):
            await pairing.ensure_paired(ADDRESS, PIN)
        assert pairing.auto_pair_blocked(ADDRESS, PIN)

        await pairing.forget(ADDRESS)

        assert not pairing.auto_pair_blocked(ADDRESS, PIN)


class TestStatus:
    async def test_states(self, pairing, bluez):
        adapter = bluez.add_adapter("hci0")

        status = await pairing.pairing_status(ADDRESS, PIN, True)
        assert status.state == "unknown_device"
        assert status.adapter == "hci0"

        path = bluez.add_device(adapter, ADDRESS)
        pairing._status_cache.clear()
        status = await pairing.pairing_status(ADDRESS, PIN, True)
        assert status.state == "not_paired"
        assert status.paired is False

        bluez.devices[path].update(Paired=True, Bonded=True, Trusted=True)
        pairing._status_cache.clear()
        status = await pairing.pairing_status(ADDRESS, PIN, True)
        assert status.state == "paired"
        assert status.paired and status.trusted
        assert "PIN" not in status.model_dump_json() and PIN not in status.model_dump_json()

    async def test_cached_within_a_second(self, pairing, bluez):
        bluez.add_adapter("hci0")

        await pairing.pairing_status(ADDRESS, PIN, True)
        await pairing.pairing_status(ADDRESS, PIN, True)

        assert bluez.members().count("GetManagedObjects") == 1

    async def test_not_ble_reads_nothing(self, pairing, bluez):
        bluez.add_adapter("hci0")

        status = await pairing.pairing_status(ADDRESS, PIN, False)

        assert status.state == "not_configured"
        assert bluez.calls == []

    async def test_bluez_absent_is_unavailable_not_an_error(self, pairing):
        status = await pairing.pairing_status(ADDRESS, PIN, True)

        assert status.state == "unavailable"
        assert status.error_code == "ble_pair_bluez_unavailable"


async def test_logs_never_contain_the_pin(pairing, bluez, caplog):
    caplog.set_level(logging.DEBUG)
    adapter = bluez.add_adapter("hci0")
    bluez.add_device(adapter, ADDRESS, passkey=987654)
    await pairing.ensure_paired(ADDRESS, "987654")
    pairing.reset_for_tests()
    bluez.devices.clear()
    bluez.add_device(adapter, ADDRESS, passkey=1)
    with pytest.raises(pairing.BlePairingError):
        await pairing.ensure_paired(ADDRESS, "987654")

    assert "987654" not in caplog.text
