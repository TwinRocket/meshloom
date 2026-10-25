"""Unit tests for BLE pairing: classification, agent, retry policy, status hints."""

from __future__ import annotations

import asyncio
import logging
import sys
from types import SimpleNamespace

import pytest

from app.services import ble_pairing
from app.services.ble_pairing import BlePairingError, PairOutcome

ADDRESS = "AA:BB:CC:DD:EE:01"
PIN = "123456"


def _agent(**flags):
    base = {"invalid_pin": False, "unexpected_method": None, "passkey_requested": False}
    base.update(flags)
    return SimpleNamespace(**base)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(ble_pairing, "_monotonic", fake)
    return fake


@pytest.fixture
def session(monkeypatch):
    """Replace the D-Bus session with a scripted one; record the calls."""
    calls: list[tuple[str, str]] = []
    script: list[object] = []

    timeouts: list[float | None] = []

    async def _fake(address, pin, timeout=None):
        calls.append((address, pin))
        timeouts.append(timeout)
        item = script.pop(0) if script else PairOutcome("paired", address, "/a", "/a/d", {})
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(ble_pairing, "_pair_session", _fake)
    return SimpleNamespace(calls=calls, script=script, timeouts=timeouts)


class TestSupportAndValidation:
    def test_non_linux_is_unsupported(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert ble_pairing.pairing_supported() == (False, "ble_pair_unsupported")
        assert ble_pairing.should_prepair(ADDRESS) is False

    def test_missing_dbus_fast_is_unsupported(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(ble_pairing.importlib.util, "find_spec", lambda name: None)
        assert ble_pairing.pairing_supported() == (False, "ble_pair_unsupported")

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
    def test_linux_with_dbus_fast_is_supported(self):
        assert ble_pairing.pairing_supported() == (True, None)

    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            (ADDRESS, True),
            ("aa:bb:cc:dd:ee:01", True),
            (" AA:BB:CC:DD:EE:01 ", True),
            ("AA-BB-CC-DD-EE-01", False),
            ("MeshCore-1234", False),
            ("", False),
            ("12345678-1234-1234-1234-123456789ABC", False),  # macOS CoreBluetooth UUID
        ],
    )
    def test_is_mac_address(self, address, expected):
        assert ble_pairing.is_mac_address(address) is expected

    @pytest.mark.parametrize(
        ("pin", "expected"),
        [("123456", True), ("0", True), ("012345", True), ("1234567", False), ("12a4", False)],
    )
    def test_is_valid_pin(self, pin, expected):
        assert ble_pairing.is_valid_pin(pin) is expected

    def test_should_prepair_needs_a_mac(self, monkeypatch):
        monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
        assert ble_pairing.should_prepair(ADDRESS)
        assert not ble_pairing.should_prepair("MeshCore-1234")


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("name", "text", "context", "flags", "code", "fallback"),
        [
            (
                "org.bluez.Error.AuthenticationFailed",
                "",
                "pair",
                {"passkey_requested": True},
                "ble_pair_wrong_pin",
                False,
            ),
            ("org.bluez.Error.AuthenticationFailed", "", "pair", {}, "ble_pair_rejected", False),
            ("org.bluez.Error.AuthenticationRejected", "", "pair", {}, "ble_pair_rejected", False),
            (
                "org.bluez.Error.AuthenticationCanceled",
                "",
                "pair",
                {"passkey_requested": True},
                "ble_pair_unreachable",
                False,
            ),
            (
                "org.bluez.Error.Failed",
                "",
                "pair",
                {"invalid_pin": True},
                "ble_pair_invalid_pin",
                False,
            ),
            (
                "org.bluez.Error.AuthenticationFailed",
                "",
                "pair",
                {"unexpected_method": "RequestConfirmation"},
                "ble_pair_failed",
                False,
            ),
            (
                "org.bluez.Error.Failed",
                "le-connection-abort-by-local",
                "pair",
                {},
                "ble_pair_unreachable",
                False,
            ),
            (
                "org.bluez.Error.Failed",
                "Software caused connection abort",
                "pair",
                {},
                "ble_pair_unreachable",
                False,
            ),
            (
                "org.bluez.Error.ConnectionAttemptFailed",
                "",
                "pair",
                {},
                "ble_pair_unreachable",
                False,
            ),
            ("org.bluez.Error.NotAvailable", "", "pair", {}, "ble_pair_unreachable", False),
            ("org.freedesktop.DBus.Error.NoReply", "", "pair", {}, "ble_pair_timeout", False),
            ("org.bluez.Error.AuthenticationTimeout", "", "pair", {}, "ble_pair_timeout", False),
            ("org.bluez.Error.InProgress", "", "pair", {}, "ble_pair_in_progress", False),
            ("org.bluez.Error.Blocked", "", "pair", {}, "ble_pair_device_blocked", False),
            ("org.bluez.Error.DoesNotExist", "", "pair", {}, "ble_pair_device_not_found", False),
            (
                "org.freedesktop.DBus.Error.UnknownObject",
                "",
                "pair",
                {},
                "ble_pair_device_not_found",
                False,
            ),
            ("org.bluez.Error.NotReady", "", "lookup", {}, "ble_pair_adapter_off", False),
            (
                "org.freedesktop.DBus.Error.UnknownMethod",
                "",
                "register",
                {},
                "ble_pair_bluez_unavailable",
                True,
            ),
            ("org.bluez.Error.AlreadyExists", "", "register", {}, "ble_pair_failed", False),
            (
                "org.freedesktop.DBus.Error.ServiceUnknown",
                "",
                "lookup",
                {},
                "ble_pair_bluez_unavailable",
                True,
            ),
            (
                "org.freedesktop.DBus.Error.AccessDenied",
                "",
                "register",
                {},
                "ble_pair_permission_denied",
                True,
            ),
            ("org.example.Weird", "", "pair", {}, "ble_pair_failed", False),
        ],
    )
    def test_table(self, name, text, context, flags, code, fallback):
        error = ble_pairing._map_dbus_error(name, text, context=context, agent=_agent(**flags))
        assert error.code == code
        assert error.fallback is fallback

    def test_retry_policies(self):
        assert BlePairingError("ble_pair_wrong_pin").retry == "sticky"
        assert BlePairingError("ble_pair_rejected").retry == "sticky"
        assert BlePairingError("ble_pair_device_blocked").retry == "sticky"
        assert BlePairingError("ble_pair_unreachable").retry == "backoff"
        assert BlePairingError("ble_pair_device_not_found").retry == "backoff"
        assert BlePairingError("ble_pair_in_progress").retry == "backoff"
        assert BlePairingError("ble_pair_dbus_unavailable").retry == "none"
        unexpected = ble_pairing._map_dbus_error(
            "org.bluez.Error.AuthenticationFailed",
            context="pair",
            agent=_agent(unexpected_method="RequestAuthorization"),
        )
        assert unexpected.retry == "sticky"
        # The D-Bus method name, never English prose shown as is in the FR UI.
        assert unexpected.params == {"error": "RequestAuthorization"}
        assert "RequestAuthorization" in unexpected.message

    def test_unknown_error_names_itself(self):
        error = ble_pairing._map_dbus_error("org.example.Weird", context="pair")
        assert error.params == {"error": "org.example.Weird"}
        assert error.http_status == 502


class TestAgent:
    @pytest.fixture(autouse=True)
    def _require_dbus_fast(self):
        # dbus-fast is a Linux-only dependency (pyproject.toml): skip elsewhere (macOS).
        pytest.importorskip("dbus_fast")

    @pytest.fixture
    def agent(self, _require_dbus_fast):
        from app.services.ble_agent import PairingAgent

        return PairingAgent("/org/bluez/hci0/dev_AA", PIN)

    def test_passkey(self, agent):
        assert agent._passkey_for("/org/bluez/hci0/dev_AA") == 123456
        assert agent.passkey_requested

    def test_leading_zero(self):
        from app.services.ble_agent import PairingAgent

        agent = PairingAgent("/d", "012345")
        assert agent._passkey_for("/d") == 12345

    @pytest.mark.parametrize("pin", ["1234567", "12a4", "", " 12"])
    def test_invalid_passkey_is_rejected(self, pin):
        from dbus_fast import DBusError

        from app.services.ble_agent import PairingAgent

        agent = PairingAgent("/d", pin)
        with pytest.raises(DBusError) as excinfo:
            agent._passkey_for("/d")
        assert excinfo.value.type == "org.bluez.Error.Rejected"
        assert agent.invalid_pin

    def test_pin_code(self, agent):
        assert agent._pin_code_for("/org/bluez/hci0/dev_AA") == PIN

    def test_foreign_device_is_rejected(self, agent):
        from dbus_fast import DBusError

        with pytest.raises(DBusError):
            agent._passkey_for("/org/bluez/hci0/dev_OTHER")
        assert agent.foreign_requests == 1
        assert not agent.passkey_requested

    @pytest.mark.parametrize("method", ["RequestConfirmation", "RequestAuthorization"])
    def test_confirmation_and_authorization_always_rejected(self, agent, method):
        from dbus_fast import DBusError

        # Even when the comparison value happens to equal the PIN.
        with pytest.raises(DBusError):
            agent._reject_method("/org/bluez/hci0/dev_AA", method)
        assert agent.unexpected_method == method

    def test_authorize_service_rejected(self, agent):
        from dbus_fast import DBusError

        with pytest.raises(DBusError):
            agent._authorize_service("/org/bluez/hci0/dev_AA")

    def test_repr_hides_the_pin(self, agent):
        assert PIN not in repr(agent)


class TestRetryPolicy:
    @pytest.mark.parametrize("pin", ["1234567", "none", "0000-0"])
    async def test_invalid_pin_still_reaches_the_bond_lookup(self, session, pin):
        # A radio bonded earlier (bluetoothctl) needs no PIN: the format is checked
        # by the session only when a Pair() is needed (test_ble_pairing_dbus).
        session.script.append(PairOutcome("already_paired", ADDRESS, "/a", "/a/d", {}))

        outcome = await ble_pairing.ensure_paired(ADDRESS, pin)

        assert outcome.state == "already_paired"
        assert session.calls == [(ADDRESS, pin)]

    async def test_invalid_pin_is_sticky(self, session):
        session.script.append(BlePairingError("ble_pair_invalid_pin"))
        with pytest.raises(BlePairingError) as first:
            await ble_pairing.ensure_paired(ADDRESS, "1234567")
        with pytest.raises(BlePairingError) as second:
            await ble_pairing.ensure_paired(ADDRESS, "1234567")
        assert first.value.code == second.value.code == "ble_pair_invalid_pin"
        assert first.value.already_reported is False
        assert second.value.already_reported is True
        assert len(session.calls) == 1

    async def test_wrong_pin_is_sticky_for_that_pin_only(self, session):
        session.script.append(BlePairingError("ble_pair_wrong_pin"))
        with pytest.raises(BlePairingError) as first:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert first.value.already_reported is False

        with pytest.raises(BlePairingError) as second:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert second.value.already_reported is True
        assert len(session.calls) == 1

        # A new PIN is a new fingerprint: it is tried.
        outcome = await ble_pairing.ensure_paired(ADDRESS, "654321")
        assert outcome.state == "paired"
        assert len(session.calls) == 2

    async def test_explicit_lifts_the_block(self, session):
        session.script.append(BlePairingError("ble_pair_wrong_pin"))
        with pytest.raises(BlePairingError):
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert ble_pairing.auto_pair_blocked(ADDRESS, PIN)

        await ble_pairing.ensure_paired(ADDRESS, PIN, explicit=True)

        assert not ble_pairing.auto_pair_blocked(ADDRESS, PIN)
        assert len(session.calls) == 2

    async def test_explicit_waits_for_a_cancelled_bonding_to_settle(self, session, monkeypatch):
        import time

        monkeypatch.setattr(ble_pairing, "CANCEL_SETTLE_SECONDS", 0.3)
        ble_pairing._last_cancelled_at = ble_pairing._monotonic()
        started = time.monotonic()
        await ble_pairing.ensure_paired(ADDRESS, PIN, explicit=True)
        assert time.monotonic() - started >= 0.2
        assert ble_pairing._last_cancelled_at is None

        # Consumed: the next explicit attempt does not wait.
        started = time.monotonic()
        await ble_pairing.ensure_paired(ADDRESS, PIN, explicit=True)
        assert time.monotonic() - started < 0.2

    async def test_deadline_shortens_the_overall_timeout(self, session, clock):
        # Time spent waiting for the radio before pairing comes off the session.
        deadline = ble_pairing.request_deadline()
        await ble_pairing.ensure_paired(ADDRESS, PIN, explicit=True, deadline=deadline)
        clock.now += 20
        await ble_pairing.ensure_paired(ADDRESS, PIN, explicit=True, deadline=deadline)
        clock.now += 60
        assert ble_pairing.time_left(deadline) == 0.0
        await ble_pairing.ensure_paired(ADDRESS, PIN, explicit=True)
        unhindered = (
            ble_pairing.REQUEST_BUDGET - ble_pairing.CLEANUP_BUDGET - ble_pairing.STATUS_TIMEOUT
        )
        assert session.timeouts == [
            min(ble_pairing.OVERALL_TIMEOUT, unhindered),
            unhindered - 20,
            ble_pairing.OVERALL_TIMEOUT,
        ]

    def test_request_budget_fits_an_unhindered_pairing(self):
        # Two status reads, the settle, the full session and its cleanup.
        unhindered = (
            2 * ble_pairing.STATUS_TIMEOUT
            + ble_pairing.CANCEL_SETTLE_SECONDS
            + ble_pairing.OVERALL_TIMEOUT
            + ble_pairing.CLEANUP_BUDGET
        )
        assert unhindered <= ble_pairing.REQUEST_BUDGET < 60
        assert ble_pairing.MIN_PAIR_BUDGET < (
            ble_pairing.REQUEST_BUDGET
            - ble_pairing.LIFECYCLE_WAIT
            - 2 * ble_pairing.STATUS_TIMEOUT
            - ble_pairing.CLEANUP_BUDGET
        )

    async def test_automatic_attempt_does_not_wait_for_a_cancelled_bonding(
        self, session, monkeypatch
    ):
        import time

        monkeypatch.setattr(ble_pairing, "CANCEL_SETTLE_SECONDS", 5.0)
        ble_pairing._last_cancelled_at = ble_pairing._monotonic()
        started = time.monotonic()
        await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert time.monotonic() - started < 1.0

    async def test_mark_unreported_lets_the_same_failure_toast_again(self, session, clock):
        session.script.append(BlePairingError("ble_pair_device_not_found"))
        with pytest.raises(BlePairingError):
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        ble_pairing.mark_unreported("ble_pair_device_not_found")
        ble_pairing.mark_unreported("ble_pair_wrong_pin")  # another code: no effect
        clock.now += 60
        session.script.append(BlePairingError("ble_pair_unreachable"))
        with pytest.raises(BlePairingError) as again:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert again.value.already_reported is False

    async def test_backoff_windows(self, session, clock):
        windows = []
        for _ in range(6):
            session.script.append(BlePairingError("ble_pair_unreachable"))
            with pytest.raises(BlePairingError):
                await ble_pairing.ensure_paired(ADDRESS, PIN)
            failure = ble_pairing._last_failure
            assert failure is not None
            windows.append(failure.retry_at - clock.now)
            # Inside the window: re-raised from memory, nothing sent.
            calls = len(session.calls)
            with pytest.raises(BlePairingError) as blocked:
                await ble_pairing.ensure_paired(ADDRESS, PIN)
            assert blocked.value.already_reported is True
            assert len(session.calls) == calls
            clock.now += windows[-1]
        assert windows == [5, 10, 20, 40, 60, 60]

    async def test_repeated_failure_is_reported_once(self, session, clock):
        session.script.extend(
            [BlePairingError("ble_pair_unreachable"), BlePairingError("ble_pair_unreachable")]
        )
        with pytest.raises(BlePairingError) as first:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        clock.now += 5
        with pytest.raises(BlePairingError) as second:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert first.value.already_reported is False
        assert second.value.already_reported is True

    async def test_backoff_class_is_reported_once_across_codes(self, session, clock):
        # A radio coming and going alternates these codes: one toast, not one per switch.
        codes = [
            "ble_pair_device_not_found",
            "ble_pair_unreachable",
            "ble_pair_timeout",
            "ble_pair_device_not_found",
        ]
        reported = []
        for code in codes:
            session.script.append(BlePairingError(code))
            with pytest.raises(BlePairingError) as excinfo:
                await ble_pairing.ensure_paired(ADDRESS, PIN)
            assert excinfo.value.code == code
            reported.append(excinfo.value.already_reported)
            clock.now += 61
        assert reported == [False, True, True, True]

    async def test_sticky_after_backoff_is_reported(self, session, clock):
        session.script.extend(
            [BlePairingError("ble_pair_unreachable"), BlePairingError("ble_pair_wrong_pin")]
        )
        with pytest.raises(BlePairingError):
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        clock.now += 5
        with pytest.raises(BlePairingError) as sticky:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert sticky.value.code == "ble_pair_wrong_pin"
        assert sticky.value.already_reported is False

    async def test_pairing_in_progress_elsewhere_backs_off_quietly(self, session, clock):
        # Another BlueZ client pairing the radio: no attempt (and no toast) every 5 s.
        session.script.extend(
            [BlePairingError("ble_pair_in_progress"), BlePairingError("ble_pair_in_progress")]
        )
        with pytest.raises(BlePairingError) as first:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        with pytest.raises(BlePairingError) as blocked:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        clock.now += 5
        with pytest.raises(BlePairingError) as second:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert first.value.already_reported is False
        assert blocked.value.already_reported is True
        assert second.value.already_reported is True
        assert len(session.calls) == 2

    async def test_success_resets_backoff(self, session, clock):
        session.script.append(BlePairingError("ble_pair_unreachable"))
        with pytest.raises(BlePairingError):
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        clock.now += 5
        await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert ble_pairing._last_failure is None

    async def test_environmental_errors_are_not_remembered(self, session):
        session.script.append(BlePairingError("ble_pair_dbus_unavailable"))
        with pytest.raises(BlePairingError) as excinfo:
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert excinfo.value.fallback is True
        assert ble_pairing._last_failure is None

    async def test_failure_repr_has_no_pin(self, session):
        session.script.append(BlePairingError("ble_pair_wrong_pin"))
        with pytest.raises(BlePairingError):
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert PIN not in repr(ble_pairing._last_failure)

    async def test_in_progress_flag(self, monkeypatch):
        seen = []

        async def _fake(address, pin, timeout=None):
            seen.append(ble_pairing.in_progress())
            return PairOutcome("paired", address, "/a", "/a/d", {})

        monkeypatch.setattr(ble_pairing, "_pair_session", _fake)
        await ble_pairing.ensure_paired(ADDRESS, PIN)
        assert seen == [True]
        assert ble_pairing.in_progress() is False


def _refused() -> Exception:
    from bleak.exc import BleakDBusError

    return BleakDBusError("org.bluez.Error.AuthenticationFailed", [])


class TestStaleBond:
    def test_three_failures_after_already_paired_broadcast_once(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "app.websocket.broadcast_error", lambda *a, **k: sent.append(k.get("code"))
        )
        for _ in range(4):
            ble_pairing.note_connect_result(
                ADDRESS, ok=False, after_already_paired=True, error=_refused()
            )
        assert sent == ["ble_pair_stale_bond"]

    def test_failures_after_a_fresh_pairing_do_not_count(self, monkeypatch):
        sent = []
        monkeypatch.setattr("app.websocket.broadcast_error", lambda *a, **k: sent.append(k))
        for _ in range(5):
            ble_pairing.note_connect_result(
                ADDRESS, ok=False, after_already_paired=False, error=_refused()
            )
        assert sent == []

    @staticmethod
    def _absent_radio_errors() -> list[BaseException]:
        from bleak.exc import BleakDBusError, BleakDeviceNotFoundError

        return [
            TimeoutError(),
            # meshcore turns bleak's TimeoutError / BleakDeviceNotFoundError into this
            ConnectionError("Failed to connect to device"),
            BleakDeviceNotFoundError(ADDRESS),
            BleakDBusError("org.bluez.Error.Failed", ["le-connection-abort-by-local"]),
            BleakDBusError("org.bluez.Error.Failed", ["Page Timeout"]),
            RuntimeError(f"Failed to open BLE connection to {ADDRESS}"),
        ]

    def test_radio_off_or_out_of_range_never_counts(self, monkeypatch):
        sent = []
        monkeypatch.setattr("app.websocket.broadcast_error", lambda *a, **k: sent.append(k))
        for error in self._absent_radio_errors() * 3:
            ble_pairing.note_connect_result(
                ADDRESS, ok=False, after_already_paired=True, error=error
            )
        ble_pairing.note_connect_result(ADDRESS, ok=False, after_already_paired=True)
        assert sent == []
        assert ble_pairing._already_paired_failures == {}

    def test_refusal_signals(self):
        from bleak.exc import BleakDBusError, BleakError

        refused = [
            _refused(),
            BleakDBusError("org.bluez.Error.NotPermitted", ["Write not permitted"]),
            BleakDBusError("org.bluez.Error.Failed", ["ATT error: 0x05"]),
            BleakDBusError("org.bluez.Error.Failed", ["ATT error: 0x0f"]),
            BleakDBusError("org.bluez.Error.Failed", ["le-connection-abort-by-remote"]),
        ]
        for error in refused:
            assert ble_pairing.connect_error_suggests_stale_bond(error), error
        wrapped = RuntimeError("connect failed")
        wrapped.__cause__ = _refused()
        assert ble_pairing.connect_error_suggests_stale_bond(wrapped)
        for error in self._absent_radio_errors():
            assert not ble_pairing.connect_error_suggests_stale_bond(error), error
        # A link that drops right after it came up: also a radio at the edge of range.
        for error in (
            BleakError("failed to discover services, device disconnected"),
            BleakError("Not connected"),
        ):
            assert not ble_pairing.connect_error_suggests_stale_bond(error), error
        assert not ble_pairing.connect_error_suggests_stale_bond(None)

    def test_success_resets(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "app.websocket.broadcast_error", lambda *a, **k: sent.append(k.get("code"))
        )
        for _ in range(2):
            ble_pairing.note_connect_result(
                ADDRESS, ok=False, after_already_paired=True, error=_refused()
            )
        ble_pairing.note_connect_result(ADDRESS, ok=True, after_already_paired=True)
        for _ in range(2):
            ble_pairing.note_connect_result(
                ADDRESS, ok=False, after_already_paired=True, error=_refused()
            )
        assert sent == []

    async def test_status_hint(self, monkeypatch):
        monkeypatch.setattr("app.websocket.broadcast_error", lambda *a, **k: None)
        monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
        for _ in range(3):
            ble_pairing.note_connect_result(
                ADDRESS, ok=False, after_already_paired=True, error=_refused()
            )
        status = await ble_pairing.pairing_status(ADDRESS, PIN, True)
        assert status.hint == "stale_bond_suspected"


class TestFallbackLog:
    def test_one_warning_per_code(self, caplog):
        caplog.set_level(logging.DEBUG, logger="app.services.ble_pairing")

        def warnings():
            return [r for r in caplog.records if r.levelno == logging.WARNING]

        for _ in range(3):
            ble_pairing.log_fallback(BlePairingError("ble_pair_dbus_unavailable"))
        assert len(warnings()) == 1
        ble_pairing.log_fallback(BlePairingError("ble_pair_permission_denied"))
        assert len(warnings()) == 2
        ble_pairing.reset_for_tests()
        ble_pairing.log_fallback(BlePairingError("ble_pair_permission_denied"))
        assert len(warnings()) == 3


class TestPickAdapter:
    @staticmethod
    def _objects(**adapters):
        return {
            f"/org/bluez/{name}": {"org.bluez.Adapter1": props} for name, props in adapters.items()
        }

    def test_first_powered_central_by_path(self, caplog):
        objects = self._objects(
            hci1={"Powered": True, "Roles": ["central"]},
            hci0={"Powered": True, "Roles": ["central", "peripheral"]},
            hci2={"Powered": False, "Roles": ["central"]},
        )
        with caplog.at_level(logging.WARNING, logger="app.services.ble_pairing"):
            assert ble_pairing.pick_adapter(objects) == "/org/bluez/hci0"
            assert ble_pairing.pick_adapter(objects) == "/org/bluez/hci0"
        assert len([r for r in caplog.records if "Several Bluetooth adapters" in r.message]) == 1

    def test_peripheral_only_is_skipped(self):
        objects = self._objects(
            hci0={"Powered": True, "Roles": ["peripheral"]},
            hci1={"Powered": True, "Roles": ["central"]},
        )
        assert ble_pairing.pick_adapter(objects) == "/org/bluez/hci1"

    def test_roles_missing_means_powered_is_enough(self):
        objects = self._objects(hci0={"Powered": True})
        assert ble_pairing.pick_adapter(objects) == "/org/bluez/hci0"

    def test_rfkill_blocked(self):
        objects = self._objects(hci0={"Powered": False, "PowerState": "off-blocked"})
        with pytest.raises(BlePairingError) as excinfo:
            ble_pairing.pick_adapter(objects)
        assert excinfo.value.code == "ble_pair_adapter_blocked"

    def test_off(self):
        objects = self._objects(hci0={"Powered": False, "PowerState": "off"})
        with pytest.raises(BlePairingError) as excinfo:
            ble_pairing.pick_adapter(objects)
        assert excinfo.value.code == "ble_pair_adapter_off"

    def test_no_adapter(self):
        with pytest.raises(BlePairingError) as excinfo:
            ble_pairing.pick_adapter({"/org/bluez": {"org.bluez.AgentManager1": {}}})
        assert excinfo.value.code == "ble_pair_no_adapter"
        assert excinfo.value.fallback is True


class TestStatusWithoutBus:
    async def test_not_configured(self):
        status = await ble_pairing.pairing_status(ADDRESS, "", True)
        assert status.state == "not_configured"

    async def test_non_mac_is_unsupported(self, monkeypatch):
        monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
        status = await ble_pairing.pairing_status("MeshCore-1", PIN, True)
        assert status.state == "unsupported"
        assert status.supported is False

    async def test_failed_state_carries_the_code(self, session, clock, monkeypatch):
        monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
        session.script.append(BlePairingError("ble_pair_unreachable"))
        with pytest.raises(BlePairingError):
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        clock.now += 2
        status = await ble_pairing.pairing_status(ADDRESS, PIN, True)
        assert status.state == "failed"
        assert status.error_code == "ble_pair_unreachable"
        assert status.retry_in == 3
        assert status.auto_pair_blocked is False

    async def test_failed_state_carries_the_error_params(self, session, monkeypatch):
        monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
        session.script.append(ble_pairing._map_dbus_error("org.bluez.Error.Weird", context="pair"))
        with pytest.raises(BlePairingError):
            await ble_pairing.ensure_paired(ADDRESS, PIN)
        status = await ble_pairing.pairing_status(ADDRESS, PIN, True)
        assert status.error_code == "ble_pair_failed"
        assert status.error_params == {"error": "org.bluez.Error.Weird"}
        assert PIN not in status.model_dump_json()

    async def test_unavailable_when_bus_is_missing(self, monkeypatch):
        monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
        status = await ble_pairing.pairing_status(ADDRESS, PIN, True)
        assert status.state == "unavailable"
        assert status.error_code == "ble_pair_dbus_unavailable"


class _HangingBus:
    """A MessageBus whose handshake never finishes; records the disconnect."""

    instances: list[_HangingBus] = []

    def __init__(self, **_kwargs) -> None:
        self.disconnected = False
        _HangingBus.instances.append(self)

    async def connect(self):
        await asyncio.Event().wait()

    def disconnect(self) -> None:
        self.disconnected = True


class TestBusCancellation:
    """A cancelled bus handshake must not leave a system-bus socket open."""

    @pytest.fixture
    def hanging_bus(self, monkeypatch):
        pytest.importorskip("dbus_fast")
        import dbus_fast.aio

        _HangingBus.instances = []
        monkeypatch.setattr(dbus_fast.aio, "MessageBus", _HangingBus)
        return _HangingBus.instances

    async def test_outer_timeout_closes_the_half_open_bus(self, hanging_bus):
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await ble_pairing._connect_bus()
        assert [bus.disconnected for bus in hanging_bus] == [True]

    async def test_task_cancel_closes_the_half_open_bus(self, hanging_bus):
        task = asyncio.create_task(ble_pairing._connect_bus())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [bus.disconnected for bus in hanging_bus] == [True]

    async def test_status_read_timeout_closes_the_half_open_bus(self, hanging_bus, monkeypatch):
        # The real _open_bus (conftest stubs it): the panel polls this path.
        monkeypatch.setattr(ble_pairing, "_open_bus", ble_pairing._connect_bus)
        monkeypatch.setattr(ble_pairing, "STATUS_TIMEOUT", 0.05)
        monkeypatch.setattr(ble_pairing, "pairing_supported", lambda: (True, None))
        status = await ble_pairing.pairing_status(ADDRESS, PIN, True)
        assert status.state == "unavailable"
        assert status.error_code == "ble_pair_bluez_unavailable"
        assert [bus.disconnected for bus in hanging_bus] == [True]

    async def test_bus_connect_counts_in_the_overall_budget(self, monkeypatch):
        async def _never(*_args):
            await asyncio.Event().wait()

        monkeypatch.setattr(ble_pairing, "_open_bus", _never)
        monkeypatch.setattr(ble_pairing, "OVERALL_TIMEOUT", 0.05)
        with pytest.raises(BlePairingError) as exc:
            await ble_pairing._pair_session(ADDRESS, PIN)
        assert exc.value.code == "ble_pair_timeout"
