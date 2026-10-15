"""Tests for RadioManager multi-transport connect dispatch, serial device
testing, and post-connect setup ordering.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType
from serial.serialutil import SerialException

from app.models import RadioTransportSnapshot
from app.services.flood_scope import FORCE_UNSCOPED_FRAME


def _snapshot(**kwargs) -> RadioTransportSnapshot:
    return RadioTransportSnapshot(**kwargs)


def _patch_transport(snapshot: RadioTransportSnapshot):
    return patch("app.radio.get_transport", new=AsyncMock(return_value=snapshot))


class TestRadioManagerConnect:
    """Test that connect() dispatches to the correct transport."""

    @pytest.mark.asyncio
    async def test_connect_serial_explicit_port(self):
        """Serial connect with explicit port sets connection_info."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            _patch_transport(_snapshot(transport="serial", serial_port="/dev/ttyUSB0")),
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_meshcore.create_serial.assert_awaited_once_with(
                port="/dev/ttyUSB0",
                baudrate=115200,
                auto_reconnect=True,
                max_reconnect_attempts=10,
            )
            assert rm.connection_info == "Serial: /dev/ttyUSB0"
            assert rm.meshcore is mock_mc

    @pytest.mark.asyncio
    async def test_connect_serial_autodetect(self):
        """Serial connect without port auto-detects."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            _patch_transport(_snapshot(transport="serial", serial_port="")),
            patch("app.radio.MeshCore") as mock_meshcore,
            patch("app.radio.find_radio_port", new_callable=AsyncMock) as mock_find,
        ):
            mock_find.return_value = "/dev/ttyACM0"
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_find.assert_awaited_once_with(115200)
            assert rm.connection_info == "Serial: /dev/ttyACM0"

    @pytest.mark.asyncio
    async def test_connect_serial_autodetect_fails(self):
        """Serial auto-detect raises when no radio found."""
        from app.radio import RadioManager

        with (
            _patch_transport(_snapshot(transport="serial", serial_port="")),
            patch("app.radio.find_radio_port", new_callable=AsyncMock) as mock_find,
        ):
            mock_find.return_value = None

            rm = RadioManager()
            with pytest.raises(RuntimeError, match="No MeshCore radio found"):
                await rm.connect()

    @pytest.mark.asyncio
    async def test_connect_unconfigured_raises(self):
        """Unconfigured transport must fail closed instead of auto-detecting."""
        from app.radio import RadioManager

        with _patch_transport(_snapshot()):
            rm = RadioManager()
            with pytest.raises(RuntimeError, match="Radio transport is not configured"):
                await rm.connect()
            assert rm._last_connected is False
            assert rm.meshcore is None

    @pytest.mark.asyncio
    async def test_connect_serial_none_fail_closed(self):
        """create_serial returning None must not mark the radio connected."""
        from app.radio import RadioManager

        with (
            _patch_transport(_snapshot(transport="serial", serial_port="/dev/ttyUSB0")),
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_meshcore.create_serial = AsyncMock(return_value=None)
            rm = RadioManager()
            with pytest.raises(RuntimeError, match="Failed to open serial connection"):
                await rm.connect()
            assert rm._last_connected is False
            assert rm.meshcore is None

    @pytest.mark.asyncio
    async def test_connect_tcp(self):
        """TCP connect sets connection_info with host:port."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            _patch_transport(_snapshot(transport="tcp", tcp_host="192.168.1.100", tcp_port=4000)),
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_meshcore.create_tcp = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_meshcore.create_tcp.assert_awaited_once_with(
                host="192.168.1.100",
                port=4000,
                auto_reconnect=True,
                max_reconnect_attempts=10,
            )
            assert rm.connection_info == "TCP: 192.168.1.100:4000"
            assert rm.meshcore is mock_mc

    @pytest.mark.asyncio
    async def test_connect_ble(self):
        """BLE connect sets connection_info with address."""
        from app.radio import RadioManager

        mock_mc = MagicMock()
        mock_mc.is_connected = True

        with (
            _patch_transport(
                _snapshot(
                    transport="ble",
                    ble_address="AA:BB:CC:DD:EE:FF",
                    ble_pin="123456",
                )
            ),
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_meshcore.create_ble = AsyncMock(return_value=mock_mc)

            rm = RadioManager()
            await rm.connect()

            mock_meshcore.create_ble.assert_awaited_once_with(
                address="AA:BB:CC:DD:EE:FF",
                pin="123456",
                auto_reconnect=True,
                max_reconnect_attempts=15,
            )
            assert rm.connection_info == "BLE: AA:BB:CC:DD:EE:FF"
            assert rm.meshcore is mock_mc

    @pytest.mark.asyncio
    async def test_connect_disconnects_existing_first(self):
        """Calling connect() when already connected disconnects first."""
        from app.radio import RadioManager

        old_mc = MagicMock()
        old_mc.disconnect = AsyncMock()
        new_mc = MagicMock()
        new_mc.is_connected = True

        with (
            _patch_transport(_snapshot(transport="tcp", tcp_host="10.0.0.1", tcp_port=4000)),
            patch("app.radio.MeshCore") as mock_meshcore,
        ):
            mock_meshcore.create_tcp = AsyncMock(return_value=new_mc)

            rm = RadioManager()
            rm._meshcore = old_mc

            await rm.connect()

            old_mc.disconnect.assert_awaited_once()
            assert rm.meshcore is new_mc


class TestConnectionMonitor:
    """Tests for the background connection monitor loop."""

    @pytest.mark.asyncio
    async def test_monitor_does_not_mark_connected_when_setup_fails(self):
        """A reconnect with failing post-connect setup should not broadcast healthy status."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "Serial: /dev/ttyUSB0"
        rm._last_connected = True
        rm._meshcore = MagicMock()
        rm._meshcore.is_connected = False

        reconnect_calls = 0

        async def _reconnect(*args, **kwargs):
            nonlocal reconnect_calls
            reconnect_calls += 1
            if reconnect_calls == 1:
                rm._meshcore = MagicMock()
                rm._meshcore.is_connected = True
                return True
            return False

        sleep_calls = 0

        async def _sleep(_seconds: float):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 3:
                raise asyncio.CancelledError()

        rm.reconnect = AsyncMock(side_effect=_reconnect)
        rm.post_connect_setup = AsyncMock(side_effect=RuntimeError("setup failed"))

        with (
            patch("app.radio.asyncio.sleep", side_effect=_sleep),
            patch("app.websocket.broadcast_health") as mock_broadcast_health,
        ):
            await rm.start_connection_monitor()
            try:
                await rm._reconnect_task
            finally:
                await rm.stop_connection_monitor()

        # Should report connection lost, but not report healthy until setup succeeds.
        mock_broadcast_health.assert_any_call(False, "Serial: /dev/ttyUSB0")
        healthy_calls = [c for c in mock_broadcast_health.call_args_list if c.args[0] is True]
        assert healthy_calls == []
        assert rm._last_connected is False

    @pytest.mark.asyncio
    async def test_monitor_retries_setup_when_connected_but_incomplete(self):
        """Monitor retries setup when transport is connected but setup previously failed."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "TCP: test:4000"

        # Simulate: transport connected, _last_connected=True (set by _connect_*),
        # but setup failed so _setup_complete=False.
        mock_mc = MagicMock()
        mock_mc.is_connected = True
        rm._meshcore = mock_mc
        rm._last_connected = True
        rm._setup_complete = False

        setup_calls = 0

        async def _mock_setup():
            nonlocal setup_calls
            setup_calls += 1
            if setup_calls == 1:
                raise RuntimeError("setup failed")
            # Second call succeeds
            rm._setup_complete = True

        rm.post_connect_setup = AsyncMock(side_effect=_mock_setup)

        sleep_count = 0

        async def _sleep(_seconds: float):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 4:
                raise asyncio.CancelledError()

        with (
            patch("app.radio.asyncio.sleep", side_effect=_sleep),
            patch("app.websocket.broadcast_health") as mock_broadcast,
        ):
            await rm.start_connection_monitor()
            try:
                await rm._reconnect_task
            finally:
                await rm.stop_connection_monitor()

        # Setup should have been retried and eventually succeeded
        assert setup_calls >= 2
        # Should broadcast healthy after setup succeeds
        mock_broadcast.assert_any_call(True, "TCP: test:4000")
        assert rm._setup_complete is True

    @pytest.mark.asyncio
    async def test_monitor_does_not_retry_while_setup_already_running(self):
        """Monitor leaves an in-progress setup alone instead of queueing another one."""
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.is_connected = True
        rm._meshcore = mock_mc
        rm._connection_info = "TCP: test:4000"
        rm._last_connected = True
        rm._setup_complete = False
        rm._setup_in_progress = True
        rm.post_connect_setup = AsyncMock()

        async def _sleep(_seconds: float):
            raise asyncio.CancelledError()

        with patch("app.radio.asyncio.sleep", side_effect=_sleep):
            await rm.start_connection_monitor()
            try:
                await rm._reconnect_task
            finally:
                await rm.stop_connection_monitor()

        rm.post_connect_setup.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_does_not_reconnect_when_connection_is_paused(self):
        """Operator-paused state suppresses reconnect attempts."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_desired = False
        rm.reconnect = AsyncMock()
        rm.post_connect_setup = AsyncMock()

        async def _sleep(_seconds: float):
            raise asyncio.CancelledError()

        with patch("app.radio.asyncio.sleep", side_effect=_sleep):
            await rm.start_connection_monitor()
            try:
                await rm._reconnect_task
            finally:
                await rm.stop_connection_monitor()

        rm.reconnect.assert_not_called()
        rm.post_connect_setup.assert_not_called()


def _connected_mc() -> MagicMock:
    mc = MagicMock()
    mc.is_connected = True
    mc.disconnect = AsyncMock()
    mc.connection_manager = None
    return mc


def _error_codes(mock_broadcast_error: MagicMock) -> list[str | None]:
    return [c.kwargs.get("code") for c in mock_broadcast_error.call_args_list]


class TestExpectedReboot:
    """The clock-fix reboot Meshloom sends is a planned reconnect, within a bound."""

    def test_expected_while_within_grace_period(self):
        from app.radio import RadioManager

        rm = RadioManager()
        rm._setup_complete = True
        rm.note_expected_reboot("clock_sync")

        assert rm.expected_reboot == "clock_sync"
        assert rm.is_setup_complete is False

    def test_no_longer_expected_past_the_grace_period(self):
        from app.radio import RadioManager

        rm = RadioManager()
        rm.note_expected_reboot("clock_sync")
        rm._expected_reboot_deadline = 0.0

        assert rm.expected_reboot is None
        assert rm.expire_expected_reboot() == "clock_sync"
        assert rm.expire_expected_reboot() is None

    @pytest.mark.asyncio
    async def test_pausing_forgets_the_expected_reboot(self):
        from app.radio import RadioManager

        rm = RadioManager()
        rm.note_expected_reboot("clock_sync")
        await rm.pause_connection()

        assert rm.expected_reboot is None
        assert rm.take_expected_reboot_teardown() is False

    @pytest.mark.asyncio
    async def test_transport_or_identity_change_forgets_the_expected_reboot(self):
        from app.radio import RadioManager

        rm = RadioManager()
        rm.note_expected_reboot("clock_sync")
        async with rm.lifecycle_transition("put_transport"):
            assert rm.expected_reboot is None
        await rm.stop_connection_monitor()

    def test_reconnect_errors_stay_quiet_then_surface_past_the_bound(self):
        from app.radio import RadioManager

        rm = RadioManager()
        rm.note_expected_reboot("clock_sync")
        with patch("app.websocket.broadcast_error") as mock_broadcast_error:
            for _ in range(5):
                rm._broadcast_reconnect_error_if_needed("Could not connect")
            assert mock_broadcast_error.call_count == 0

            rm._expected_reboot_deadline = 0.0
            rm._broadcast_reconnect_error_if_needed("Could not connect")

        assert _error_codes(mock_broadcast_error) == ["reconnection_failed"]


class TestConnectionMonitorAfterClockReboot:
    """Drive the real monitor, prepare and reconnect code through a clock reboot."""

    @staticmethod
    def _sleep_until(ticks: int):
        count = 0

        async def _sleep(_seconds: float):
            nonlocal count
            count += 1
            if count > ticks:
                raise asyncio.CancelledError()

        return _sleep

    @staticmethod
    async def _run_monitor(rm, ticks: int):
        with (
            patch(
                "app.radio.asyncio.sleep",
                side_effect=TestConnectionMonitorAfterClockReboot._sleep_until(ticks),
            ),
            patch("app.websocket.broadcast_health") as mock_health,
            patch("app.websocket.broadcast_error") as mock_error,
        ):
            await rm.start_connection_monitor()
            try:
                await rm._reconnect_task
            finally:
                await rm.stop_connection_monitor()
        return mock_health, mock_error

    @pytest.mark.asyncio
    async def test_setup_reconnect_result_reconnects_quietly_and_reruns_setup(self):
        """Setup returns "reconnect": the monitor drops the transport, waits out a
        failed attempt without an error toast, then reconnects and runs setup."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "BLE: AA:BB:CC:DD:EE:FF"
        first_mc = _connected_mc()
        rm._meshcore = first_mc
        rm._last_connected = False  # transport up, setup not run yet

        setup_outcomes: list[str] = []

        async def _setup():
            if not setup_outcomes:
                rm.note_expected_reboot("clock_sync")  # what sync_radio_time_outcome does
                setup_outcomes.append("reconnect")
                return "reconnect"
            rm._setup_complete = True
            setup_outcomes.append("done")
            return "done"

        connect_calls = 0

        async def _connect():
            nonlocal connect_calls
            connect_calls += 1
            if connect_calls == 1:
                raise RuntimeError("Failed to open BLE connection")  # still booting
            rm._meshcore = _connected_mc()

        rm.post_connect_setup = AsyncMock(side_effect=_setup)
        rm.connect = AsyncMock(side_effect=_connect)

        mock_health, mock_error = await self._run_monitor(rm, ticks=3)

        assert setup_outcomes == ["reconnect", "done"]
        first_mc.disconnect.assert_awaited()
        assert connect_calls == 2
        assert _error_codes(mock_error) == []
        assert [c.args[0] for c in mock_health.call_args_list] == [False, True]
        assert rm.expected_reboot is None
        assert rm.is_setup_complete is True

    @pytest.mark.asyncio
    async def test_reboot_outside_setup_with_surviving_transport_reruns_setup(self):
        """Periodic sync or a config update rebooted the radio behind a serial
        bridge that kept the port open: the monitor reconnects and reruns setup."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "Serial: /dev/ttyUSB0"
        old_mc = _connected_mc()
        rm._meshcore = old_mc
        rm._last_connected = True
        rm._setup_complete = True
        rm.note_expected_reboot("clock_sync")

        async def _setup():
            rm._setup_complete = True
            return "done"

        async def _connect():
            rm._meshcore = _connected_mc()

        rm.post_connect_setup = AsyncMock(side_effect=_setup)
        rm.connect = AsyncMock(side_effect=_connect)

        mock_health, mock_error = await self._run_monitor(rm, ticks=2)

        old_mc.disconnect.assert_awaited()
        rm.post_connect_setup.assert_awaited_once()
        assert [c.args[0] for c in mock_health.call_args_list] == [False, True]
        assert _error_codes(mock_error) == []
        assert rm.expected_reboot is None

    @pytest.mark.asyncio
    async def test_bound_passing_during_a_long_setup_is_not_reported(self):
        """The radio came back and setup (offload) outlasts the bound: no timeout error."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "Serial: /dev/ttyACM0"
        rm._meshcore = _connected_mc()
        rm._last_connected = True
        rm.note_expected_reboot("clock_sync")
        rm._expected_reboot_teardown_pending = False  # already reconnected
        rm._expected_reboot_deadline = 0.0
        rm._setup_in_progress = True  # setup still running elsewhere
        rm.post_connect_setup = AsyncMock()

        _mock_health, mock_error = await self._run_monitor(rm, ticks=1)

        assert _error_codes(mock_error) == []
        assert rm.expected_reboot is None

    @pytest.mark.asyncio
    async def test_radio_that_never_comes_back_surfaces_errors_past_the_bound(self):
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_info = "Serial: /dev/ttyACM0"
        rm.note_expected_reboot("clock_sync")
        rm._expected_reboot_deadline = 0.0  # grace period over
        rm.connect = AsyncMock(side_effect=RuntimeError("Failed to open serial connection"))
        rm.post_connect_setup = AsyncMock()

        _mock_health, mock_error = await self._run_monitor(rm, ticks=1)

        # One message on the pass where the bound runs out; no reconnect on it.
        assert _error_codes(mock_error) == ["radio_reboot_timeout"]
        rm.connect.assert_not_awaited()
        rm.post_connect_setup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_setup_that_finds_no_transport_is_not_reported_healthy(self):
        """Startup race: the monitor's setup waited on the setup lock while the
        startup setup rebooted the radio and disconnected. It must not report
        "connected" nor clear the expected reboot."""
        from app.radio import RadioManager
        from app.services.radio_lifecycle import prepare_connected_radio

        rm = RadioManager()
        rm._connection_info = "BLE: AA:BB:CC:DD:EE:FF"
        rm._meshcore = None
        rm.note_expected_reboot("clock_sync")

        with patch("app.websocket.broadcast_health") as mock_health:
            ok = await prepare_connected_radio(rm, broadcast_on_success=True)

        assert ok is False
        assert rm._last_connected is False
        assert rm.expected_reboot == "clock_sync"
        assert [c.args[0] for c in mock_health.call_args_list if c.args[0] is True] == []


class TestReconnectLock:
    """Tests for reconnect() lock serialization — no duplicate reconnections."""

    @pytest.mark.asyncio
    async def test_concurrent_reconnects_only_connect_once(self):
        """Two concurrent reconnect() calls should only call connect() once."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._meshcore = None

        connect_count = 0

        async def mock_connect():
            nonlocal connect_count
            connect_count += 1
            # Simulate connect taking some time
            await asyncio.sleep(0.05)
            mock_mc = MagicMock()
            mock_mc.is_connected = True
            rm._meshcore = mock_mc
            rm._connection_info = "TCP: test:4000"

        rm.connect = AsyncMock(side_effect=mock_connect)

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error"),
        ):
            result_a, result_b = await asyncio.gather(
                rm.reconnect(broadcast_on_success=False),
                rm.reconnect(broadcast_on_success=False),
            )

        # First caller does the real connect, second sees is_connected=True
        assert connect_count == 1
        assert result_a is True
        assert result_b is True

    @pytest.mark.asyncio
    async def test_second_reconnect_skips_when_first_succeeds(self):
        """Second caller returns True without connecting when first already succeeded."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._meshcore = None

        call_order: list[str] = []

        async def mock_connect():
            call_order.append("connect")
            await asyncio.sleep(0.05)
            mock_mc = MagicMock()
            mock_mc.is_connected = True
            rm._meshcore = mock_mc
            rm._connection_info = "TCP: test:4000"

        rm.connect = AsyncMock(side_effect=mock_connect)

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error"),
        ):
            await asyncio.gather(
                rm.reconnect(broadcast_on_success=False),
                rm.reconnect(broadcast_on_success=False),
            )

        # connect should appear exactly once
        assert call_order == ["connect"]

    @pytest.mark.asyncio
    async def test_reconnect_retries_after_first_failure(self):
        """If first reconnect fails, a subsequent call should attempt connect again."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._meshcore = None

        attempt = 0

        async def mock_connect():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                # First attempt fails
                return
            # Second attempt succeeds
            mock_mc = MagicMock()
            mock_mc.is_connected = True
            rm._meshcore = mock_mc
            rm._connection_info = "TCP: test:4000"

        rm.connect = AsyncMock(side_effect=mock_connect)

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error"),
        ):
            result1 = await rm.reconnect(broadcast_on_success=False)
            assert result1 is False
            assert attempt == 1

            result2 = await rm.reconnect(broadcast_on_success=False)
            assert result2 is True
            assert attempt == 2

    @pytest.mark.asyncio
    async def test_reconnect_returns_false_when_connection_is_paused(self):
        """Reconnect should no-op when the operator has paused connection attempts."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._connection_desired = False
        rm.connect = AsyncMock()

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error"),
        ):
            result = await rm.reconnect(broadcast_on_success=False)

        assert result is False
        rm.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnect_broadcasts_only_first_three_failures(self):
        """Frontend only sees the first few reconnect failures before suppression kicks in."""
        from app.radio import MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS, RadioManager

        rm = RadioManager()
        rm.connect = AsyncMock(side_effect=RuntimeError("radio unavailable"))

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error") as mock_broadcast_error,
        ):
            for _ in range(MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS + 2):
                result = await rm.reconnect(broadcast_on_success=False)
                assert result is False

        assert mock_broadcast_error.call_count == MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS
        assert mock_broadcast_error.call_args_list[0].args == (
            "Reconnection failed",
            "radio unavailable",
        )
        assert mock_broadcast_error.call_args_list[-1].args == (
            "Reconnection failed",
            "radio unavailable Further reconnect failures will be logged only until a connection succeeds.",
        )

    @pytest.mark.asyncio
    async def test_reconnect_success_resets_error_broadcast_suppression(self):
        """A successful reconnect re-enables frontend error broadcasts for later failures."""
        from app.radio import RadioManager

        rm = RadioManager()
        attempts = 0

        async def mock_connect():
            nonlocal attempts
            attempts += 1
            if attempts in (1, 2, 4):
                raise RuntimeError("radio unavailable")
            mock_mc = MagicMock()
            mock_mc.is_connected = True
            rm._meshcore = mock_mc
            rm._connection_info = "TCP: test:4000"

        rm.connect = AsyncMock(side_effect=mock_connect)

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error") as mock_broadcast_error,
        ):
            assert await rm.reconnect(broadcast_on_success=False) is False
            assert await rm.reconnect(broadcast_on_success=False) is False
            assert await rm.reconnect(broadcast_on_success=False) is True
            rm._meshcore = None
            assert await rm.reconnect(broadcast_on_success=False) is False

        assert mock_broadcast_error.call_count == 3
        for call in mock_broadcast_error.call_args_list:
            assert call.args == ("Reconnection failed", "radio unavailable")

    @pytest.mark.asyncio
    async def test_reconnect_serial_missing_port_logs_clean_message_without_traceback(self):
        """Missing serial ports should log a concise operator-facing warning."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm.connect = AsyncMock(
            side_effect=SerialException(
                2,
                "could not open port /dev/serial/by-id/test-radio: "
                "[Errno 2] No such file or directory: '/dev/serial/by-id/test-radio'",
            )
        )

        rm._transport_snapshot = _snapshot(
            transport="serial",
            serial_port="/dev/serial/by-id/test-radio",
        )

        with (
            patch("app.websocket.broadcast_health"),
            patch("app.websocket.broadcast_error") as mock_broadcast_error,
        ):
            result = await rm.reconnect(broadcast_on_success=False)

        assert result is False
        assert mock_broadcast_error.call_args.args == (
            "Reconnection failed",
            "Could not connect to serial port /dev/serial/by-id/test-radio. "
            "Did the radio get disconnected or change serial ports?",
        )


class TestManualDisconnectCleanup:
    """Tests for manual disconnect teardown behavior."""

    @pytest.mark.asyncio
    async def test_disconnect_disables_library_auto_reconnect(self):
        """Manual disconnect should suppress meshcore_py reconnect behavior."""
        from app.keystore import get_private_key, set_private_key
        from app.radio import RadioManager

        rm = RadioManager()
        reconnect_task: asyncio.Task | None = None

        connection_manager = MagicMock()
        connection_manager.auto_reconnect = True
        connection_manager._reconnect_task = None

        async def _disconnect():
            nonlocal reconnect_task
            reconnect_task = asyncio.create_task(asyncio.sleep(60))
            connection_manager._reconnect_task = reconnect_task

        mock_mc = MagicMock()
        mock_mc.disconnect = AsyncMock(side_effect=_disconnect)
        mock_mc.connection_manager = connection_manager
        rm._meshcore = mock_mc
        rm._setup_complete = True
        rm.device_info_loaded = True
        rm.max_contacts = 350
        rm.device_model = "T-Echo"
        rm.firmware_build = "2025-02-01"
        rm.firmware_version = "1.2.3"
        rm.max_channels = 8
        rm.path_hash_mode = 2
        rm.path_hash_mode_supported = True
        rm.note_channel_slot_loaded("AA" * 16, 0)
        set_private_key(b"\x01" * 64)

        await rm.disconnect()

        mock_mc.disconnect.assert_awaited_once()
        assert get_private_key() is None
        assert connection_manager.auto_reconnect is False
        assert connection_manager._reconnect_task is None
        assert reconnect_task is not None and reconnect_task.cancelled()
        assert rm.meshcore is None
        assert rm.is_setup_complete is False
        assert rm.device_info_loaded is False
        assert rm.max_contacts is None
        assert rm.device_model is None
        assert rm.firmware_build is None
        assert rm.firmware_version is None
        assert rm.max_channels == 40
        assert rm.path_hash_mode == 0
        assert rm.path_hash_mode_supported is False
        assert rm.get_cached_channel_slot("AA" * 16) is None

    @pytest.mark.asyncio
    async def test_disconnect_waits_for_inflight_radio_operation_cleanup(self):
        """Manual disconnect should wait for the shared radio-operation lock."""
        from app.radio import RadioManager

        rm = RadioManager()
        mc = MagicMock()
        mc.disconnect = AsyncMock()
        rm._meshcore = mc

        holder_entered = asyncio.Event()
        allow_release = asyncio.Event()
        disconnect_started = asyncio.Event()

        async def holder():
            async with rm.radio_operation("holder"):
                holder_entered.set()
                await allow_release.wait()

        async def trigger_disconnect():
            disconnect_started.set()
            await rm.disconnect()

        holder_task = asyncio.create_task(holder())
        await holder_entered.wait()

        disconnect_task = asyncio.create_task(trigger_disconnect())
        await disconnect_started.wait()
        await asyncio.sleep(0.02)

        mc.disconnect.assert_not_awaited()
        assert rm.meshcore is mc

        allow_release.set()
        await holder_task
        await disconnect_task

        mc.disconnect.assert_awaited_once()
        assert rm.meshcore is None

    @pytest.mark.asyncio
    async def test_pause_connection_marks_connection_undesired(self):
        """Pausing should flip connection_desired off and tear down transport."""
        from app.keystore import get_private_key, set_private_key
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.disconnect = AsyncMock()
        rm._meshcore = mock_mc
        rm._connection_desired = True
        rm._last_connected = True
        set_private_key(b"\x02" * 64)

        await rm.pause_connection()

        assert rm.connection_desired is False
        assert rm._last_connected is False
        assert get_private_key() is None
        mock_mc.disconnect.assert_awaited_once()


class TestSerialDeviceProbe:
    """Tests for test_serial_device() — verifies cleanup on all exit paths."""

    @pytest.mark.asyncio
    async def test_success_returns_true_and_disconnects(self):
        """Successful probe returns True and always disconnects."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.self_info = {"name": "MyNode"}
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is True
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_not_connected_returns_false_and_disconnects(self):
        """Device that connects but reports is_connected=False still disconnects."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = False
        mock_mc.self_info = None
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is False
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_self_info_returns_false_and_disconnects(self):
        """Connected but no self_info returns False; still disconnects."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.self_info = None
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is False
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_returns_false_no_disconnect_needed(self):
        """asyncio.TimeoutError before create_serial completes — mc is None, no disconnect."""
        from app.radio import test_serial_device

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(side_effect=asyncio.TimeoutError)

            result = await test_serial_device("/dev/ttyUSB0", 115200, timeout=0.1)

        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false_and_disconnects(self):
        """If create_serial succeeds but subsequent code raises, disconnect still runs."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        # Accessing is_connected raises (simulates corrupted state)
        type(mock_mc).is_connected = property(lambda self: (_ for _ in ()).throw(OSError("oops")))
        mock_mc.disconnect = AsyncMock()

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        assert result is False
        mock_mc.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_exception_is_swallowed(self):
        """If disconnect() itself raises, the exception does not propagate."""
        from app.radio import test_serial_device

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.self_info = {"name": "MyNode"}
        mock_mc.disconnect = AsyncMock(side_effect=OSError("port closed"))

        with patch("app.radio.MeshCore") as mock_meshcore:
            mock_meshcore.create_serial = AsyncMock(return_value=mock_mc)

            result = await test_serial_device("/dev/ttyUSB0", 115200)

        # Should still return True despite disconnect failure
        assert result is True
        mock_mc.disconnect.assert_awaited_once()


class TestPostConnectSetupOrdering:
    """Tests for post_connect_setup() — verifies drain-before-auto-fetch ordering."""

    @pytest.fixture(autouse=True)
    def _allow_identity_gate(self):
        """Identity evaluation is Vague 2+; these tests isolate setup ordering."""
        with patch(
            "app.services.radio_identity.evaluate_connected_identity",
            new=AsyncMock(return_value="continue"),
        ):
            yield

    @pytest.mark.asyncio
    async def test_drain_runs_before_auto_fetch(self):
        """drain_pending_messages must be called BEFORE start_auto_message_fetching."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()
        rm._meshcore = mock_mc

        call_order = []

        async def mock_drain(mc):
            call_order.append("drain")
            return 0

        async def mock_start_auto():
            call_order.append("auto_fetch")

        mock_mc.start_auto_message_fetching = AsyncMock(side_effect=mock_start_auto)

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time_outcome", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=AppSettings(),
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch(
                "app.radio_sync.drain_pending_messages",
                new_callable=AsyncMock,
                side_effect=mock_drain,
            ),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        assert call_order == ["drain", "auto_fetch"], (
            f"Expected drain before auto_fetch, got: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_setup_sets_and_clears_in_progress_flag(self):
        """is_setup_in_progress is True during setup and False after."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()
        rm._meshcore = mock_mc

        observed_during = None

        async def mock_drain(mc):
            nonlocal observed_during
            observed_during = rm.is_setup_in_progress
            return 0

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time_outcome", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=AppSettings(),
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch(
                "app.radio_sync.drain_pending_messages",
                new_callable=AsyncMock,
                side_effect=mock_drain,
            ),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        assert observed_during is True
        assert rm.is_setup_in_progress is False

    @pytest.mark.asyncio
    async def test_setup_clears_in_progress_flag_on_failure(self):
        """is_setup_in_progress is cleared even if setup raises."""
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        rm._meshcore = mock_mc

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch(
                "app.radio_sync.sync_radio_time_outcome",
                new_callable=AsyncMock,
                side_effect=RuntimeError("clock failed"),
            ),
        ):
            with pytest.raises(RuntimeError, match="clock failed"):
                await rm.post_connect_setup()

        assert rm.is_setup_in_progress is False

    @pytest.mark.asyncio
    async def test_setup_noop_when_no_meshcore(self):
        """post_connect_setup does nothing when meshcore is None."""
        from app.radio import RadioManager

        rm = RadioManager()
        rm._meshcore = None

        # Should not raise or call any functions
        await rm.post_connect_setup()
        assert rm.is_setup_in_progress is False

    @pytest.mark.asyncio
    async def test_flood_scope_applied_during_setup(self):
        """Non-empty flood_scope from settings is applied during post_connect_setup."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()
        rm._meshcore = mock_mc

        mock_settings = AppSettings(flood_scope="#TestRegion")

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time_outcome", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=mock_settings,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        mock_mc.commands.set_flood_scope.assert_awaited_once_with("#TestRegion")

    @pytest.mark.asyncio
    async def test_plain_flood_scope_is_normalized_during_setup(self):
        """Legacy/plain stored flood_scope is normalized before applying to radio."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()
        rm._meshcore = mock_mc

        mock_settings = AppSettings(flood_scope="TestRegion")

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time_outcome", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=mock_settings,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        mock_mc.commands.set_flood_scope.assert_awaited_once_with("#TestRegion")

    @pytest.mark.asyncio
    async def test_flood_scope_empty_resets_during_setup(self):
        """Empty flood_scope calls set_flood_scope("") during post_connect_setup."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()
        mock_mc.commands.send = AsyncMock()
        # Flood scope is applied after the device query, so report a protocol
        # version that supports the mode-1 unscoped command (>= 12).
        device_query = MagicMock()
        device_query.payload = {"fw ver": 13}
        mock_mc.commands.send_device_query = AsyncMock(return_value=device_query)
        rm._meshcore = mock_mc

        mock_settings = AppSettings(flood_scope="")

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time_outcome", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=mock_settings,
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling"),
        ):
            await rm.post_connect_setup()

        mock_mc.commands.send.assert_awaited_once_with(
            FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR]
        )
        mock_mc.commands.set_flood_scope.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_message_polling_starts_hourly_audit_by_default(self):
        """Post-connect setup always starts the message audit task by default."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()
        rm._meshcore = mock_mc

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time_outcome", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=AppSettings(),
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling") as mock_start_message_polling,
        ):
            await rm.post_connect_setup()

        mock_start_message_polling.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_polling_starts_when_env_flag_enabled(self):
        """Post-connect setup also starts the same task when aggressive fallback is enabled."""
        from app.models import AppSettings
        from app.radio import RadioManager

        rm = RadioManager()
        mock_mc = MagicMock()
        mock_mc.start_auto_message_fetching = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()
        rm._meshcore = mock_mc

        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new_callable=AsyncMock),
            patch("app.radio_sync.sync_radio_time_outcome", new_callable=AsyncMock),
            patch(
                "app.repository.AppSettingsRepository.get",
                new_callable=AsyncMock,
                return_value=AppSettings(),
            ),
            patch("app.radio_sync.sync_and_offload_all", new_callable=AsyncMock, return_value={}),
            patch("app.radio_sync.start_periodic_sync"),
            patch("app.radio_sync.send_advertisement", new_callable=AsyncMock, return_value=False),
            patch("app.radio_sync.start_periodic_advert"),
            patch("app.radio_sync.drain_pending_messages", new_callable=AsyncMock, return_value=0),
            patch("app.radio_sync.start_message_polling") as mock_start_message_polling,
        ):
            await rm.post_connect_setup()

        mock_start_message_polling.assert_called_once()

    @pytest.mark.asyncio
    async def test_prepare_connected_radio_retries_timeout_once_before_failing(self):
        """Hung post-connect setup gets one retry before surfacing an operator error."""
        from app.radio import RadioManager
        from app.services.radio_lifecycle import prepare_connected_radio

        rm = RadioManager()
        rm._connection_info = "Serial: /dev/ttyUSB0"
        rm.post_connect_setup = AsyncMock(side_effect=[TimeoutError(), TimeoutError()])

        with (
            patch("app.websocket.broadcast_error") as mock_broadcast_error,
            patch("app.websocket.broadcast_health") as mock_broadcast_health,
        ):
            with pytest.raises(RuntimeError, match="Post-connect setup timed out"):
                await prepare_connected_radio(rm, broadcast_on_success=True)

        assert rm.post_connect_setup.await_count == 2
        mock_broadcast_error.assert_called_once_with(
            "Radio startup appears stuck",
            "Initial radio offload took too long. Reboot the radio and restart the server.",
            code="radio_startup_stuck",
        )
        mock_broadcast_health.assert_not_called()

    @pytest.mark.asyncio
    async def test_prepare_connected_radio_succeeds_on_retry_after_timeout(self):
        """A slow first attempt can time out once without failing the reconnect flow."""
        from app.radio import RadioManager
        from app.services.radio_lifecycle import prepare_connected_radio

        rm = RadioManager()
        rm._connection_info = "Serial: /dev/ttyUSB0"
        rm._meshcore = _connected_mc()

        attempts = 0

        async def _setup():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError()
            rm._setup_complete = True
            return "done"

        rm.post_connect_setup = AsyncMock(side_effect=_setup)

        with (
            patch("app.websocket.broadcast_error") as mock_broadcast_error,
            patch("app.websocket.broadcast_health") as mock_broadcast_health,
        ):
            await prepare_connected_radio(rm, broadcast_on_success=True)

        assert rm.post_connect_setup.await_count == 2
        assert rm._last_connected is True
        mock_broadcast_error.assert_not_called()
        mock_broadcast_health.assert_called_once_with(True, "Serial: /dev/ttyUSB0")


class TestDetectSerialDevices:
    """detect_serial_devices must include pyserial COM ports (Windows)."""

    def test_includes_com_port_from_list_ports(self):
        from app.radio import detect_serial_devices

        fake = MagicMock()
        fake.device = "COM3"
        with patch("app.radio.list_ports.comports", return_value=[fake]):
            devices = detect_serial_devices()
        assert "COM3" in devices


class TestMaybeImportLegacyEnv:
    """One-shot leftover MESHCORE_* transport env import into app_settings."""

    @pytest.mark.asyncio
    async def test_new_database_ignores_env(self, test_db, monkeypatch):
        from app.services.radio_transport import maybe_import_legacy_env

        monkeypatch.setenv("MESHCORE_SERIAL_PORT", "/dev/ttyUSB0")
        with patch("app.repository.radio_transport.db", test_db):
            snapshot = await maybe_import_legacy_env(existing_database=False)

        assert snapshot.env_imported is True
        assert snapshot.transport is None
        assert snapshot.configured is False
        assert snapshot.serial_port == ""

    @pytest.mark.asyncio
    async def test_existing_empty_transport_imports_env(self, test_db, monkeypatch):
        from app.repository.radio_transport import RadioTransportRepository
        from app.services.radio_transport import maybe_import_legacy_env

        monkeypatch.setenv("MESHCORE_TCP_HOST", "192.168.1.50")
        monkeypatch.setenv("MESHCORE_TCP_PORT", "4000")
        with patch("app.repository.radio_transport.db", test_db):
            current = await RadioTransportRepository.get()
            assert current.transport is None
            assert current.env_imported is False
            snapshot = await maybe_import_legacy_env(existing_database=True)

        assert snapshot.env_imported is True
        assert snapshot.transport == "tcp"
        assert snapshot.tcp_host == "192.168.1.50"
        assert snapshot.tcp_port == 4000

    @pytest.mark.asyncio
    async def test_cleared_transport_does_not_reimport_leftover_env(self, test_db, monkeypatch):
        from app.repository.radio_transport import RadioTransportRepository
        from app.services.radio_transport import maybe_import_legacy_env

        monkeypatch.setenv("MESHCORE_SERIAL_PORT", "/dev/ttyUSB0")
        with patch("app.repository.radio_transport.db", test_db):
            imported = await maybe_import_legacy_env(existing_database=True)
            assert imported.transport == "serial"
            await RadioTransportRepository.save(RadioTransportSnapshot(env_imported=True))
            snapshot = await maybe_import_legacy_env(existing_database=True)

        assert snapshot.env_imported is True
        assert snapshot.transport is None
        assert snapshot.serial_port == ""
