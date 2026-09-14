"""Live relay race, reconnect, and reload behaviour."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.community_live import (
    CLOSE_INACTIVE,
    CLOSE_SUPERSEDED,
    CommunityLiveRelay,
    reset_community_live_for_tests,
)
from app.services.meshloom_community import CommunityEffective
from tests.test_community_live import FakeStatsSocket, _Close, _wait_until


def _enabled_state() -> CommunityEffective:
    return CommunityEffective(
        enabled=True,
        locked=False,
        iata="LYS",
        broker_host="mqtt.meshloom.app",
        api_base="https://api.meshloom.app",
        api_audience="api.meshloom.app",
        mqtt_audience="mqtt.meshloom.app",
    )


@pytest.fixture(autouse=True)
def _reset_relay():
    reset_community_live_for_tests()
    yield
    reset_community_live_for_tests()


def _enter_enabled(stack: ExitStack, relay: CommunityLiveRelay, connect: object) -> None:
    stack.enter_context(
        patch(
            "app.services.meshloom_community.community_enabled",
            new_callable=AsyncMock,
            return_value=True,
        )
    )
    stack.enter_context(
        patch(
            "app.services.meshloom_community.get_community_effective",
            new_callable=AsyncMock,
            return_value=_enabled_state(),
        )
    )
    stack.enter_context(
        patch("app.services.meshloom_community.mint_stats_jwt", MagicMock(return_value="tok"))
    )
    stack.enter_context(patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock))
    stack.enter_context(patch.object(relay, "_connect", side_effect=connect))


@pytest.mark.asyncio
class TestCommunityLiveResilience:
    async def test_concurrent_subscribe_opens_one_upstream_socket(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []
        gate = asyncio.Event()

        async def connect(_url: str, _token: str):
            connects.append(1)
            await gate.wait()
            return FakeStatsSocket([])

        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            stack.enter_context(patch("app.services.community_live.RECONNECT_INITIAL_S", 0.01))
            first, second = await asyncio.gather(relay.subscribe(), relay.subscribe())
            assert first["session_id"] != second["session_id"]
            await _wait_until(lambda: len(connects) >= 1)
            await asyncio.sleep(0.05)
            assert len(connects) == 1
            assert relay._reader_task is not None
            assert not relay._reader_task.done()
            gate.set()
            await relay.close_stats()
        assert len(connects) == 1

    async def test_page_reload_without_unsubscribe_does_not_reopen(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            return FakeStatsSocket([])

        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            first = await relay.subscribe()
            await _wait_until(lambda: relay.connected or connects)
            reloaded = await relay.subscribe()
            assert reloaded["session_id"] != first["session_id"]
            assert first["session_id"] in relay._sessions
            assert reloaded["session_id"] in relay._sessions
            assert relay.consumer_count == 2
            await asyncio.sleep(0.03)
            await relay.close_stats()
        assert len(connects) == 1

    async def test_reconnects_with_backoff_after_4005(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []
        delays: list[float] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                return FakeStatsSocket([], close_exc=_Close(4005))
            return FakeStatsSocket([])

        original = relay._sleep_backoff

        async def track_sleep(generation: int, seconds: float) -> None:
            delays.append(seconds)
            await original(generation, 0)

        relay._sleep_backoff = track_sleep  # type: ignore[method-assign]
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            stack.enter_context(patch("app.services.community_live.RECONNECT_INITIAL_S", 0.5))
            stack.enter_context(patch("app.services.community_live.RECONNECT_MAX_S", 8.0))
            stack.enter_context(patch("app.services.community_live.RECONNECT_FACTOR", 2.0))
            status = await relay.subscribe()
            await _wait_until(lambda: len(connects) >= 2)
            assert relay.close_code not in {4003, CLOSE_SUPERSEDED}
            assert status["close_code"] not in {4003, CLOSE_SUPERSEDED}
            await _wait_until(lambda: relay.connected or len(connects) >= 2)
            await relay.close_stats()
        assert delays[0] == 0.5
        assert relay.close_code != CLOSE_SUPERSEDED

    async def test_4002_does_not_retry(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            return FakeStatsSocket([], close_exc=_Close(4002))

        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            stack.enter_context(patch("app.services.community_live.RECONNECT_INITIAL_S", 0.01))
            await relay.subscribe()
            await _wait_until(lambda: relay.close_code == CLOSE_INACTIVE)
            await asyncio.sleep(0.08)
            heartbeat = await relay.subscribe()
            await asyncio.sleep(0.05)
            await relay.close_stats()
        assert len(connects) == 1
        assert heartbeat["state"] == "gate"
        assert heartbeat["close_code"] == CLOSE_INACTIVE
        assert heartbeat["connected"] is False

    async def test_relancer_clears_gate_and_reconnects(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                return FakeStatsSocket([], close_exc=_Close(4002))
            return FakeStatsSocket([])

        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay.close_code == CLOSE_INACTIVE)
            await relay.relancer()
            await _wait_until(lambda: len(connects) >= 2)
            await relay.close_stats()
        assert len(connects) >= 2
        assert relay._gate_blocked is False

    async def test_status_distinguishes_connected_reconnect_gate_and_opt_out(self):
        relay = CommunityLiveRelay()
        phase = {"n": 0}

        async def connect(_url: str, _token: str):
            phase["n"] += 1
            if phase["n"] == 1:
                return FakeStatsSocket([], close_exc=_Close(4005))
            return FakeStatsSocket([])

        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            stack.enter_context(patch("app.services.community_live.RECONNECT_INITIAL_S", 0.01))
            opening = await relay.subscribe()
            assert opening["state"] in {"reconnecting", "connected"}
            await _wait_until(lambda: relay.connected)
            assert relay.snapshot(opted_out=False)["state"] == "connected"
            relay._connected = False
            mid = relay.snapshot(opted_out=False)
            assert mid["state"] == "reconnecting"
            assert mid["close_code"] not in {4003, 4005}
            relay._gate_blocked = True
            relay._close_code = CLOSE_INACTIVE
            gated = relay.snapshot(opted_out=False)
            assert gated["state"] == "gate"
            opted = relay.snapshot(opted_out=True)
            assert opted["state"] == "opted_out"
            await relay.close_stats()
