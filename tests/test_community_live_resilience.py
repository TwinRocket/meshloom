"""Live relay race, reconnect, and reload behaviour."""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.models import CommunityLiveStatus
from app.services.community_live import (
    BACKOFF_JITTER,
    CLOSE_FEED_PAUSED,
    CLOSE_INACTIVE,
    CLOSE_SUPERSEDED,
    PAUSED_RETRY_MAX_S,
    PAUSED_RETRY_MIN_S,
    RECONNECT_INITIAL_S,
    CommunityLiveRelay,
    is_feed_paused,
    jittered,
    reset_community_live_for_tests,
)
from app.services.meshloom_community import CommunityEffective
from tests.test_community_live import FakeStatsSocket, _Close, _wait_until


def _enabled_state() -> CommunityEffective:
    return CommunityEffective(
        enabled=True,
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
            await relay.close_stats()
        assert len(connects) == 1

    async def test_relancer_reconnects_after_4002(self):
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

    async def test_status_distinguishes_connected_reconnect_and_opt_out(self):
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
            opted = relay.snapshot(opted_out=True)
            assert opted["state"] == "opted_out"
            await relay.close_stats()


def _refused_503(code: str | None) -> InvalidStatus:
    body = {"detail": "x", "code": code, "server_time": 0} if code else None
    raw = json.dumps(body).encode() if body is not None else b""
    return InvalidStatus(Response(503, "Service Unavailable", Headers(), raw))


def _track_sleeps(relay: CommunityLiveRelay) -> list[float]:
    """Record the base delay of every backoff sleep, then only yield."""
    delays: list[float] = []
    original = relay._sleep_backoff

    async def track(generation: int, seconds: float) -> None:
        delays.append(seconds)
        await original(generation, 0)

    relay._sleep_backoff = track  # type: ignore[method-assign]
    return delays


@pytest.mark.asyncio
class TestFeedPaused:
    """KILL_LIVE_PUBLIC: 1013 on open sockets, 503 live_disabled on handshakes."""

    async def test_1013_reports_paused_and_waits_bounded_then_connects(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []
        states: list[str] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                return FakeStatsSocket([], close_exc=_Close(CLOSE_FEED_PAUSED))
            if len(connects) <= 3:
                raise _refused_503("live_disabled")
            return FakeStatsSocket([])

        delays = _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            broadcast = stack.enter_context(
                patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock)
            )
            broadcast.side_effect = lambda kind, data: (
                states.append(data["state"]) if kind == "community_live" else None
            )
            await relay.subscribe()
            await _wait_until(lambda: len(connects) >= 4 and relay.connected)
            snap = relay.snapshot(opted_out=False)
            await relay.close_stats()
        # Three pauses (one 1013, two 503 live_disabled): slow waits, bounded.
        assert delays == [PAUSED_RETRY_MIN_S, PAUSED_RETRY_MAX_S, PAUSED_RETRY_MAX_S]
        assert "paused" in states
        assert states[-1] == "connected"
        assert snap["state"] == "connected"
        assert snap["close_code"] is None
        assert relay._paused is False

    async def test_503_live_disabled_handshake_is_paused(self):
        relay = CommunityLiveRelay()
        gate = asyncio.Event()

        async def connect(_url: str, _token: str):
            raise _refused_503("live_disabled")

        async def hold(generation: int, seconds: float) -> None:
            gate.set()
            await asyncio.Event().wait()

        relay._sleep_backoff = hold  # type: ignore[method-assign]
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await asyncio.wait_for(gate.wait(), timeout=2)
            snap = relay.snapshot(opted_out=False)
            CommunityLiveStatus.model_validate(snap)
            await relay.close_stats()
        assert snap["state"] == "paused"
        assert snap["connected"] is False
        assert snap["close_code"] is None
        assert snap["auth_error"] is None

    async def test_503_viewer_cap_keeps_short_backoff(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                raise _refused_503("live_viewer_cap")
            return FakeStatsSocket([])

        delays = _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay.connected)
            await relay.close_stats()
        assert delays == [RECONNECT_INITIAL_S]
        assert relay._paused is False

    async def test_503_with_an_html_body_is_not_a_pause(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                # A proxy in front of Community, not Community itself.
                raise InvalidStatus(
                    Response(
                        503,
                        "Service Unavailable",
                        Headers({"Content-Type": "text/html"}),
                        b"<html><body>503 Service Temporarily Unavailable</body></html>",
                    )
                )
            return FakeStatsSocket([])

        delays = _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay.connected)
            await relay.close_stats()
        assert delays == [RECONNECT_INITIAL_S]
        assert relay._paused is False

    @pytest.mark.parametrize(
        ("status", "body"),
        [
            (401, {"detail": "x", "code": "token_expired"}),
            (409, {"detail": "x", "code": "live_superseded"}),
            (429, {"detail": "x"}),
            (502, None),
            (503, {"detail": "x", "code": "live_viewer_cap"}),
            (503, None),
        ],
    )
    async def test_any_other_handshake_answer_ends_the_pause(self, status, body):
        relay = CommunityLiveRelay()
        connects: list[int] = []
        held = asyncio.Event()

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                raise _refused_503("live_disabled")
            raw = json.dumps(body).encode() if body is not None else b""
            raise InvalidStatus(Response(status, "Refused", Headers(), raw))

        async def sleep(generation: int, seconds: float) -> None:
            if len(connects) < 2:
                await asyncio.sleep(0)  # the paused wait: retry at once
                return
            held.set()
            await asyncio.Event().wait()

        relay._sleep_backoff = sleep  # type: ignore[method-assign]
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await asyncio.wait_for(held.wait(), timeout=2)
            snap = relay.snapshot(opted_out=False)
            await relay.close_stats()
        assert relay._paused is False
        assert snap["state"] == "reconnecting"

    async def test_closing_the_reader_clears_paused(self):
        relay = CommunityLiveRelay()
        gate = asyncio.Event()

        async def connect(_url: str, _token: str):
            raise _refused_503("live_disabled")

        async def hold(generation: int, seconds: float) -> None:
            gate.set()
            await asyncio.Event().wait()

        relay._sleep_backoff = hold  # type: ignore[method-assign]
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            broadcast = stack.enter_context(
                patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock)
            )
            await relay.subscribe()
            await asyncio.wait_for(gate.wait(), timeout=2)
            assert relay._paused is True
            broadcast.reset_mock()
            await relay.close_stats()
        assert relay._paused is False
        # The browser hears that the pause is over (the chip goes away).
        states = [c.args[1]["state"] for c in broadcast.await_args_list]
        assert states and states[-1] != "paused"

    async def test_opt_out_clears_paused(self):
        relay = CommunityLiveRelay()
        relay._paused = True
        relay._sessions["s"] = (0.0, None)
        with patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock):
            await relay.sync_community(False)
        assert relay._paused is False
        assert relay.snapshot(opted_out=True)["state"] == "opted_out"


class TestFeedPausedHelpers:
    def test_is_feed_paused(self):
        assert is_feed_paused(code=CLOSE_FEED_PAUSED)
        assert is_feed_paused(http_status=503, body={"code": "live_disabled"})
        assert not is_feed_paused(http_status=503, body={"code": "live_viewer_cap"})
        assert not is_feed_paused(http_status=503, body=None)
        assert not is_feed_paused(http_status=429, body={"code": "live_disabled"})
        assert not is_feed_paused(code=4005)

    def test_jitter_stays_within_bounds(self):
        for base in (0.5, 2.0, 30.0, PAUSED_RETRY_MAX_S):
            samples = [jittered(base) for _ in range(500)]
            assert min(samples) >= base * (1 - BACKOFF_JITTER)
            assert max(samples) <= base * (1 + BACKOFF_JITTER)
            # Spread, not a constant: instances do not reconnect in step.
            assert len({round(x, 6) for x in samples}) > 1
        assert jittered(0) == 0.0

    def test_jitter_extremes(self):
        with patch("app.services.community_live.random.uniform", side_effect=lambda a, b: a):
            assert jittered(PAUSED_RETRY_MIN_S) == pytest.approx(PAUSED_RETRY_MIN_S * 0.8)
        with patch("app.services.community_live.random.uniform", side_effect=lambda a, b: b):
            assert jittered(PAUSED_RETRY_MAX_S) == pytest.approx(PAUSED_RETRY_MAX_S * 1.2)

    @pytest.mark.asyncio
    async def test_sleep_backoff_applies_jitter(self):
        relay = CommunityLiveRelay()
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        with patch("app.services.community_live.asyncio.sleep", side_effect=fake_sleep):
            for _ in range(50):
                await relay._sleep_backoff(relay._generation, PAUSED_RETRY_MIN_S)
            await relay._sleep_backoff(relay._generation, 0)
        assert slept[-1] == 0
        jitter = slept[:-1]
        assert all(24.0 <= x <= 36.0 for x in jitter)
        assert len(set(jitter)) > 1
