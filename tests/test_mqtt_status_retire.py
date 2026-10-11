"""Retained MQTT status on the way out: cleared before DISCONNECT, time-bounded stop.

A clean DISCONNECT makes the broker drop the Will, so a publisher that only
cancels its loop leaves the last retained ``online`` on its status topic for
good. Every clean ``stop()`` publishes the Will's retained ``offline`` itself;
``retire_status()`` + ``stop()`` then also clears the topic with an empty
retained payload, all before the DISCONNECT.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

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
        # The publisher re-checks the egress guard before each connect.
        patch(
            "app.services.meshloom_community.community_egress_state",
            AsyncMock(return_value=SimpleNamespace(enabled=True)),
        ),
        patch("app.websocket.broadcast_success"),
        patch("app.websocket.broadcast_error"),
        patch("app.websocket.broadcast_health"),
    ]
    for p in patches:
        p.start()
    yield
    patch.stopall()


def _offline_events(events: list[tuple], topic: str) -> list[tuple]:
    out = []
    for e in events:
        if e[0] != "publish" or e[1] != topic or not isinstance(e[2], str):
            continue
        if json.loads(e[2]).get("status") == "offline":
            out.append(e)
    return out


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

        offline = _offline_events(events, topic)
        clear = ("publish", topic, b"", True, 1)
        assert len(offline) == 1
        assert clear in events
        # offline first: if the clear fails, "offline" stays, never "online".
        assert events.index(offline[0]) < events.index(clear)
        assert events.index(clear) < events.index(("disconnect",))
        assert events[-1] == ("disconnect",)

    @pytest.mark.asyncio
    async def test_plain_stop_publishes_offline_and_keeps_topic(self):
        """A reload or a shutdown: the Will's "offline", retained; no clear."""
        events: list[tuple] = []
        pub = await _start(events)
        topic = f"meshcore/CDG/{PUBKEY_HEX}/status"
        await pub.stop()
        assert not [e for e in events if e[0] == "publish" and e[2] == b""]
        offline = _offline_events(events, topic)
        assert len(offline) == 1
        assert offline[0][3] is True and offline[0][4] == 1
        body = json.loads(offline[0][2])
        assert body["origin_id"] == PUBKEY_HEX
        assert events.index(offline[0]) < events.index(("disconnect",))
        assert events[-1] == ("disconnect",)

    @pytest.mark.asyncio
    async def test_stop_while_disconnected_publishes_nothing(self):
        """Broker unreachable at opt-out: nothing to send, the stop still ends."""
        events: list[tuple] = []
        pub = await _start(events)
        pub.connected = False
        pub.retire_status()
        await pub.stop()
        assert _offline_events(events, f"meshcore/CDG/{PUBKEY_HEX}/status") == []
        assert not [e for e in events if e[0] == "publish" and e[2] == b""]
        assert pub._task is None

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


@pytest.mark.asyncio
async def test_retired_status_topic_is_set_only_once_the_clear_is_acknowledged():
    """The opt-out reads it to decide whether a later clear is still needed."""
    events: list[tuple] = []
    pub = await _start(events)
    pub.retire_status()
    await pub.stop()
    assert pub.retired_status_topic == f"meshcore/CDG/{PUBKEY_HEX}/status"

    hung: list[tuple] = []
    pub = await _start(hung, hang_empty_publish=True)
    pub.retire_status()
    with patch.object(mqtt_base, "BEFORE_DISCONNECT_TIMEOUT_S", 0.05):
        await pub.stop()
    assert pub.retired_status_topic is None

    plain: list[tuple] = []
    pub = await _start(plain)
    await pub.stop()  # no retire: offline only
    assert pub.retired_status_topic is None
