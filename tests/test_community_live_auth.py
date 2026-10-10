"""Live relay: a handshake 401 backs off and gives up; it is never a 4001 remint loop."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.models import CommunityLiveStatus
from app.services.community_live import (
    AUTH_REJECT_MAX_ATTEMPTS,
    CLOSE_JWT_EXPIRED,
    CommunityLiveRelay,
    map_stats_close,
    reset_community_live_for_tests,
)
from app.services.meshloom_community import classify_auth_rejection
from tests.test_community_live import FakeStatsSocket, _Close, _wait_until
from tests.test_community_live_resilience import _enter_enabled


def _refused(status: int = 401, body: dict | None = None) -> InvalidStatus:
    raw = json.dumps(body).encode() if body is not None else b""
    return InvalidStatus(Response(status, "Unauthorized", Headers(), raw))


@pytest.fixture(autouse=True)
def _reset_relay():
    reset_community_live_for_tests()
    yield
    reset_community_live_for_tests()


def _track_sleeps(relay: CommunityLiveRelay) -> list[float]:
    delays: list[float] = []
    original = relay._sleep_backoff

    async def track(generation: int, seconds: float) -> None:
        delays.append(seconds)
        await original(generation, 0)

    relay._sleep_backoff = track  # type: ignore[method-assign]
    return delays


class TestClassifyAuthRejection:
    def test_clock_skew_code(self):
        reason, skew = classify_auth_rejection(
            {"detail": "x", "code": "clock_skew", "server_time": 1000}, now=1300
        )
        assert reason == "clock_skew"
        assert skew == -300

    def test_expired_with_large_skew_is_clock(self):
        reason, skew = classify_auth_rejection(
            {"detail": "x", "code": "token_expired", "server_time": 2000}, now=1000
        )
        assert (reason, skew) == ("clock_skew", 1000)

    def test_expired_within_tolerance_stays_expired(self):
        reason, _ = classify_auth_rejection(
            {"detail": "x", "code": "token_expired", "server_time": 1030}, now=1000
        )
        assert reason == "token_expired"

    def test_other_code_is_passed_through(self):
        assert classify_auth_rejection({"code": "token_signature", "server_time": 5}, now=5)[0] == (
            "token_signature"
        )

    def test_old_server_without_code(self):
        assert classify_auth_rejection({"detail": "token expired"}) == ("token_expired", None)
        assert classify_auth_rejection({"detail": "invalid token"}) == ("unknown", None)
        assert classify_auth_rejection(None) == ("unknown", None)

    def test_handshake_401_is_not_a_jwt_expiry_close(self):
        assert map_stats_close(http_status=401) is None
        assert map_stats_close(code=4001) == CLOSE_JWT_EXPIRED


@pytest.mark.asyncio
class TestHandshake401:
    async def test_backs_off_then_gives_up_with_visible_state(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            raise _refused(401, {"detail": "invalid token"})

        delays = _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay._auth_error is not None)
            await asyncio.sleep(0.05)
            snap = relay.snapshot(opted_out=False)
            await relay.close_stats()
        assert len(connects) == AUTH_REJECT_MAX_ATTEMPTS
        # Never an immediate remint: every retry waits, and the wait grows.
        assert delays == [2.0, 4.0, 8.0, 16.0]
        assert snap["state"] == "auth_rejected"
        assert snap["auth_error"] == "token_rejected"
        assert snap["close_code"] is None
        CommunityLiveStatus(**snap)

    async def test_clock_skew_is_reported_with_drift(self):
        relay = CommunityLiveRelay()

        async def connect(_url: str, _token: str):
            raise _refused(
                401,
                {
                    "detail": "iat in future",
                    "code": "clock_skew",
                    "server_time": int(time.time()) - 600,
                },
            )

        _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay._auth_error is not None)
            snap = relay.snapshot(opted_out=False)
            await relay.close_stats()
        assert snap["auth_error"] == "clock_skew"
        assert snap["auth_code"] == "clock_skew"
        assert snap["clock_skew_s"] is not None and snap["clock_skew_s"] <= -590

    async def test_fatal_code_gives_up_at_once(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            raise _refused(
                401,
                {"detail": "bad sig", "code": "token_signature", "server_time": int(time.time())},
            )

        _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay._auth_error is not None)
            await asyncio.sleep(0.05)
            await relay.close_stats()
        assert len(connects) == 1
        assert relay._auth_error == "token_rejected"
        assert relay._auth_code == "token_signature"

    async def test_new_tab_does_not_restart_but_relancer_does(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                raise _refused(401, {"code": "token_audience", "server_time": int(time.time())})
            return FakeStatsSocket([])

        _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay._auth_error is not None)
            status = await relay.subscribe()
            await asyncio.sleep(0.05)
            assert len(connects) == 1
            assert status["state"] == "auth_rejected"
            after = await relay.relancer()
            assert after["auth_error"] is None
            await _wait_until(lambda: relay.connected)
            await relay.close_stats()
        assert len(connects) == 2

    async def test_recovers_when_token_is_accepted_again(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) <= 2:
                raise _refused(401, {"code": "token_expired", "server_time": int(time.time())})
            return FakeStatsSocket([])

        _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay.connected)
            snap = relay.snapshot(opted_out=False)
            await relay.close_stats()
        assert snap["state"] == "connected"
        assert snap["auth_error"] is None


@pytest.mark.asyncio
class TestCloseJwtExpired:
    async def test_4001_right_after_open_backs_off(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) <= 2:
                return FakeStatsSocket([], close_exc=_Close(4001))
            return FakeStatsSocket([])

        delays = _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            stack.enter_context(patch("app.services.community_live.RECONNECT_INITIAL_S", 0.5))
            await relay.subscribe()
            await _wait_until(lambda: len(connects) >= 3)
            await relay.close_stats()
        assert delays[:2] == [0.5, 1.0]

    async def test_4001_after_uptime_remints_at_once(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                return FakeStatsSocket([], close_exc=_Close(4001))
            return FakeStatsSocket([])

        delays = _track_sleeps(relay)
        with ExitStack() as stack:
            _enter_enabled(stack, relay, connect)
            stack.enter_context(patch("app.services.community_live.JWT_EXPIRY_MIN_UPTIME_S", 0.0))
            await relay.subscribe()
            await _wait_until(lambda: len(connects) >= 2)
            await relay.close_stats()
        assert delays[0] == 0.0
