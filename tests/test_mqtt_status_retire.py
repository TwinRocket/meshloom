"""Retained MQTT status on the way out: cleared before DISCONNECT, time-bounded stop.

A clean DISCONNECT makes the broker drop the Will, so a publisher that only
cancels its loop leaves the last retained ``online`` on its status topic for
good. ``retire_status()`` + ``stop()`` must publish an empty retained payload
on that topic before the DISCONNECT.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.fanout import mqtt_base
from app.fanout.meshloom_stats import MeshloomStatsPublisher, _state_to_settings

PUBKEY = bytes(range(32))
PUBKEY_HEX = PUBKEY.hex().upper()


class RecordingClient:
    """Stands in for ``aiomqtt.Client``; records everything in call order."""

    def __init__(self, events: list[tuple], *, hang_empty_publish=False, hang_exit=False, **kw):
        self.events = events
        self.kwargs = kw
        self.hang_empty_publish = hang_empty_publish
        self.hang_exit = hang_exit

    async def __aenter__(self) -> RecordingClient:
        self.events.append(("connect", self.kwargs.get("hostname")))
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.events.append(("disconnect",))
        if self.hang_exit:
            await asyncio.sleep(3600)
        return False

    async def publish(self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False):
        if payload == b"" and self.hang_empty_publish:
            await asyncio.sleep(3600)
        self.events.append(("publish", topic, payload, retain, qos))


def _settings(iata: str = "CDG") -> SimpleNamespace:
    state = SimpleNamespace(
        enabled=True,
        iata=iata,
        broker_host="mqtt.meshloom.app",
        mqtt_audience="mqtt.meshloom.app",
    )
    return _state_to_settings(state)


async def _start(events: list[tuple], **client_kw) -> MeshloomStatsPublisher:
    pub = MeshloomStatsPublisher()

    async def _device_info() -> dict[str, str]:
        return {"model": "test", "firmware_version": "v1"}

    async def _stats() -> None:
        return None

    pub._fetch_device_info = _device_info  # type: ignore[method-assign]
    pub._fetch_stats = _stats  # type: ignore[method-assign]

    def factory(**kwargs: Any) -> RecordingClient:
        return RecordingClient(events, **client_kw, **kwargs)

    patch("app.fanout.mqtt_base.aiomqtt.Client", side_effect=factory).start()
    await pub.start(_settings())
    for _ in range(200):
        if pub.connected and any(e[0] == "publish" for e in events):
            break
        await asyncio.sleep(0.01)
    assert pub.connected
    return pub


@pytest.fixture(autouse=True)
def _env():
    patches = [
        patch("app.keystore.get_public_key", return_value=PUBKEY),
        patch("app.keystore.get_private_key", return_value=b"\x02" * 64),
        patch("app.keystore.has_private_key", return_value=True),
        patch("app.fanout.meshloom_stats._generate_jwt_token", return_value="jwt"),
        patch("app.fanout.community_mqtt._generate_jwt_token", return_value="jwt"),
        patch("app.fanout.mqtt_base._broadcast_health"),
        patch("app.websocket.broadcast_success"),
        patch("app.websocket.broadcast_error"),
        patch("app.websocket.broadcast_health"),
    ]
    for p in patches:
        p.start()
    yield
    patch.stopall()


class TestRetireStatus:
    @pytest.mark.asyncio
    async def test_retire_clears_retained_status_before_disconnect(self):
        events: list[tuple] = []
        pub = await _start(events)
        topic = f"meshcore/CDG/{PUBKEY_HEX}/status"
        assert ("publish", topic) == events[1][:2]
        assert events[1][3] is True  # the online status is retained

        pub.retire_status()
        await pub.stop()

        clear = ("publish", topic, b"", True, 1)
        assert clear in events
        assert events.index(clear) < events.index(("disconnect",))
        assert events[-1] == ("disconnect",)

    @pytest.mark.asyncio
    async def test_plain_stop_keeps_retained_status(self):
        """A reload or a shutdown is not an opt-out: no empty payload."""
        events: list[tuple] = []
        pub = await _start(events)
        await pub.stop()
        assert not [e for e in events if e[0] == "publish" and e[2] == b""]
        assert events[-1] == ("disconnect",)

    @pytest.mark.asyncio
    async def test_hung_clear_is_bounded_and_still_disconnects(self, monkeypatch):
        monkeypatch.setattr(mqtt_base, "BEFORE_DISCONNECT_TIMEOUT_S", 0.05)
        events: list[tuple] = []
        pub = await _start(events, hang_empty_publish=True)
        pub.retire_status()
        started = time.monotonic()
        await pub.stop()
        assert time.monotonic() - started < 2
        assert events[-1] == ("disconnect",)
        assert pub.connected is False

    @pytest.mark.asyncio
    async def test_hung_disconnect_is_bounded(self, monkeypatch):
        monkeypatch.setattr(mqtt_base, "STOP_TIMEOUT_S", 0.05)
        events: list[tuple] = []
        pub = await _start(events, hang_exit=True)
        task = pub._task
        started = time.monotonic()
        await pub.stop()
        assert time.monotonic() - started < 2
        assert pub._task is None
        assert pub.connected is False
        assert task is not None
        task.cancel()
        await asyncio.wait({task}, timeout=1)
