"""Tests for concurrent radio connections, failure isolation, and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import RadioTransportSnapshot
from app.services.radio_instance import (
    REMOTE_BACKOFF_BASE_SECONDS,
    REMOTE_BACKOFF_JITTER_RATIO,
    REMOTE_BACKOFF_MAX_SECONDS,
    RadioInstance,
)
from app.services.radio_registry import RadioRegistry


class TestRadioConnectionBackoff:
    """Validate exponential backoff and jitter calculations for remote transports."""

    def test_serial_transport_has_no_backoff(self):
        snapshot = RadioTransportSnapshot(transport="serial", serial_port="COM3")
        radio = RadioInstance(radio_id="serial_radio", transport_snapshot=snapshot)

        assert radio.is_remote is False
        assert radio.compute_reconnect_delay() == 0.0
        assert radio.can_attempt_reconnect() is True

    def test_remote_transports_identified(self):
        tcp_radio = RadioInstance(
            radio_id="tcp_radio",
            transport_snapshot=RadioTransportSnapshot(
                transport="tcp", tcp_host="10.0.0.1", tcp_port=4403
            ),
        )
        ble_radio = RadioInstance(
            radio_id="ble_radio",
            transport_snapshot=RadioTransportSnapshot(
                transport="ble", ble_address="AA:BB:CC:DD:EE:FF"
            ),
        )
        serial_radio = RadioInstance(
            radio_id="serial_radio",
            transport_snapshot=RadioTransportSnapshot(
                transport="serial", serial_port="/dev/ttyUSB0"
            ),
        )

        assert tcp_radio.is_remote is True
        assert ble_radio.is_remote is True
        assert serial_radio.is_remote is False

    def test_exponential_backoff_progression_with_jitter(self):
        radio = RadioInstance(
            radio_id="tcp_radio",
            transport_snapshot=RadioTransportSnapshot(
                transport="tcp", tcp_host="10.0.0.1", tcp_port=4403
            ),
        )

        # Failure 0: base = 2.0s (+/- 20% -> 1.6s to 2.4s)
        radio._reconnect_failures = 0
        for _ in range(20):
            delay = radio.compute_reconnect_delay()
            assert (
                REMOTE_BACKOFF_BASE_SECONDS * (1.0 - REMOTE_BACKOFF_JITTER_RATIO) - 0.01
                <= delay
                <= REMOTE_BACKOFF_BASE_SECONDS * (1.0 + REMOTE_BACKOFF_JITTER_RATIO) + 0.01
            )

        # Failure 1: base = 4.0s (+/- 20% -> 3.2s to 4.8s)
        radio._reconnect_failures = 1
        delay1 = radio.compute_reconnect_delay()
        assert 3.19 <= delay1 <= 4.81

        # Failure 2: base = 8.0s (+/- 20% -> 6.4s to 9.6s)
        radio._reconnect_failures = 2
        delay2 = radio.compute_reconnect_delay()
        assert 6.39 <= delay2 <= 9.61

        # Failure 3: base = 16.0s (+/- 20% -> 12.8s to 19.2s)
        radio._reconnect_failures = 3
        delay3 = radio.compute_reconnect_delay()
        assert 12.79 <= delay3 <= 19.21

        # High failures: capped at 60.0s (+/- 20% -> 48.0s to 72.0s)
        radio._reconnect_failures = 10
        delay_capped = radio.compute_reconnect_delay()
        assert (
            REMOTE_BACKOFF_MAX_SECONDS * (1.0 - REMOTE_BACKOFF_JITTER_RATIO) - 0.01
            <= delay_capped
            <= REMOTE_BACKOFF_MAX_SECONDS * (1.0 + REMOTE_BACKOFF_JITTER_RATIO) + 0.01
        )

    def test_record_failure_and_cooldown_window(self):
        radio = RadioInstance(
            radio_id="tcp_radio",
            transport_snapshot=RadioTransportSnapshot(
                transport="tcp", tcp_host="10.0.0.1", tcp_port=4403
            ),
        )
        assert radio.can_attempt_reconnect() is True
        assert radio._reconnect_failures == 0

        # Record failure sets next_reconnect_time in future
        delay = radio._record_reconnect_failure()
        assert delay >= 1.0
        assert radio._reconnect_failures == 1
        assert radio.can_attempt_reconnect() is False

        # Reset clears backoff
        radio._reset_reconnect_backoff()
        assert radio._reconnect_failures == 0
        assert radio.can_attempt_reconnect() is True

    def test_resume_connection_resets_backoff(self):
        radio = RadioInstance(
            radio_id="tcp_radio",
            transport_snapshot=RadioTransportSnapshot(
                transport="tcp", tcp_host="10.0.0.1", tcp_port=4403
            ),
        )
        radio._record_reconnect_failure()
        radio._record_reconnect_failure()
        assert radio._reconnect_failures == 2
        assert radio.can_attempt_reconnect() is False

        radio.resume_connection()
        assert radio.connection_desired is True
        assert radio._reconnect_failures == 0
        assert radio.can_attempt_reconnect() is True


class TestConcurrentConnectionLifecycle:
    """Validate concurrent connection and failure isolation across multiple radios."""

    @pytest.mark.asyncio
    async def test_start_all_and_stop_all(self):
        registry = RadioRegistry()
        r1 = RadioInstance(radio_id="radio1")
        r2 = RadioInstance(radio_id="radio2")
        r3 = RadioInstance(radio_id="radio3")

        registry.register(r1)
        registry.register(r2)
        registry.register(r3)

        assert r1._reconnect_task is None
        assert r2._reconnect_task is None
        assert r3._reconnect_task is None

        # start_all starts monitors for all radios concurrently
        await registry.start_all()

        assert r1._reconnect_task is not None
        assert r2._reconnect_task is not None
        assert r3._reconnect_task is not None

        # stop_all stops monitors and disconnects all radios concurrently
        await registry.stop_all()

        assert r1._reconnect_task is None
        assert r2._reconnect_task is None
        assert r3._reconnect_task is None

    @pytest.mark.asyncio
    async def test_failing_radio_does_not_block_healthy_radio(self):
        """A failing radio (e.g. unreachable TCP host) does not prevent healthy radios from connecting."""
        registry = RadioRegistry()
        r_healthy = RadioInstance(
            radio_id="healthy",
            transport_snapshot=RadioTransportSnapshot(
                transport="tcp", tcp_host="127.0.0.1", tcp_port=4403
            ),
        )
        r_failing = RadioInstance(
            radio_id="failing",
            transport_snapshot=RadioTransportSnapshot(
                transport="tcp", tcp_host="192.0.2.1", tcp_port=9999
            ),
        )

        mock_mc = MagicMock()
        mock_mc.is_connected = True
        mock_mc.disconnect = AsyncMock()

        async def healthy_connect():
            r_healthy._meshcore = mock_mc
            r_healthy._last_connected = True
            r_healthy._connection_info = "TCP: 127.0.0.1:4403"

        async def failing_connect():
            # Simulate a network timeout or connection refused error
            await asyncio.sleep(0.05)
            raise ConnectionRefusedError("Connection refused to 192.0.2.1:9999")

        r_healthy.connect = healthy_connect
        r_failing.connect = failing_connect

        registry.register(r_healthy)
        registry.register(r_failing)

        # Run reconnections concurrently
        res_healthy, res_failing = await asyncio.gather(
            r_healthy.reconnect(),
            r_failing.reconnect(),
            return_exceptions=True,
        )

        # Healthy radio successfully connects
        assert res_healthy is True
        assert r_healthy.is_connected is True

        # Failing radio gracefully returns False, records backoff, no uncaught exception
        assert res_failing is False
        assert r_failing.is_connected is False
        assert r_failing._reconnect_failures == 1
        assert r_failing.can_attempt_reconnect() is False

        await registry.stop_all()

    @pytest.mark.asyncio
    async def test_concurrent_reconnections_without_lock_contention(self):
        """Reconnection attempts on separate radios do not block each other."""
        r1 = RadioInstance(radio_id="radio1")
        r2 = RadioInstance(radio_id="radio2")

        mc1 = MagicMock()
        mc1.is_connected = True
        mc1.disconnect = AsyncMock()
        mc2 = MagicMock()
        mc2.is_connected = True
        mc2.disconnect = AsyncMock()

        timeline: list[str] = []

        async def connect_r1():
            timeline.append("r1_connect_start")
            await asyncio.sleep(0.05)
            r1._meshcore = mc1
            r1._last_connected = True
            r1._connection_info = "TCP: r1"
            timeline.append("r1_connect_end")

        async def connect_r2():
            timeline.append("r2_connect_start")
            await asyncio.sleep(0.05)
            r2._meshcore = mc2
            r2._last_connected = True
            r2._connection_info = "TCP: r2"
            timeline.append("r2_connect_end")

        r1.connect = connect_r1
        r2.connect = connect_r2

        # Reconnect both simultaneously
        res1, res2 = await asyncio.gather(r1.reconnect(), r2.reconnect())

        assert res1 is True
        assert res2 is True
        # Both started before either finished -> concurrent execution without serial lock contention
        assert timeline.index("r1_connect_start") < timeline.index("r1_connect_end")
        assert timeline.index("r2_connect_start") < timeline.index("r2_connect_end")
        assert "r1_connect_start" in timeline and "r2_connect_start" in timeline

        await r1.disconnect()
        await r2.disconnect()

    @pytest.mark.asyncio
    async def test_start_all_and_stop_all_isolate_exceptions(self):
        """Exceptions in one radio during start_all/stop_all do not break other radios."""
        registry = RadioRegistry()
        r_good = RadioInstance(radio_id="good")
        r_bad = RadioInstance(radio_id="bad")

        async def failing_start():
            raise RuntimeError("Hardware failure during monitor start")

        async def failing_stop():
            raise RuntimeError("Hardware failure during disconnect")

        r_bad.start_connection_monitor = failing_start
        r_bad.disconnect = failing_stop

        registry.register(r_good)
        registry.register(r_bad)

        # start_all should complete without raising
        await registry.start_all()
        assert r_good._reconnect_task is not None

        # stop_all should complete without raising
        await registry.stop_all()
        assert r_good._reconnect_task is None

    @pytest.mark.asyncio
    async def test_log_traceability_tagging(self, caplog):
        """Radio log messages are tagged with [radio:<radio_id>] for multi-radio traceability."""
        caplog.set_level(logging.DEBUG)
        radio = RadioInstance(
            radio_id="sensor_node",
            transport_snapshot=RadioTransportSnapshot(
                transport="tcp", tcp_host="10.0.0.1", tcp_port=4403
            ),
        )

        async def fail_connect():
            raise ConnectionError("Host unreachable")

        radio.connect = fail_connect

        await radio.reconnect()

        # Check that logs contain [radio:sensor_node]
        radio_logs = [rec.message for rec in caplog.records if "[radio:sensor_node]" in rec.message]
        assert len(radio_logs) > 0
        assert any("Attempting to reconnect to radio" in msg for msg in radio_logs)
        assert any("Connection backoff" in msg for msg in radio_logs)
