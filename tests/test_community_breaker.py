"""Community unreachable: circuit breaker and 503 (not 500) from the local API."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.services import meshloom_community
from app.services.meshloom_community import (
    BREAKER_FAILURE_THRESHOLD,
    COMMUNITY_UNREACHABLE_DETAIL,
    CommunityBreaker,
    CommunityEffective,
    community_breaker,
    stats_json,
)

_ON = CommunityEffective(
    enabled=True,
    iata="LYS",
    broker_host="mqtt.meshloom.app",
    api_base="https://api.meshloom.app",
    api_audience="api.meshloom.app",
    mqtt_audience="mqtt.meshloom.app",
)


def _client_factory(handler: Callable[[httpx.Request], httpx.Response]):
    real = httpx.AsyncClient

    def make(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    return make


class _Calls:
    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.count = 0
        self._respond = respond

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        return self._respond(request)


def _patched(calls: _Calls):
    return (
        patch.object(meshloom_community, "get_community_effective", AsyncMock(return_value=_ON)),
        patch.object(meshloom_community, "mint_stats_jwt", MagicMock(return_value="tok")),
        patch.object(meshloom_community.httpx, "AsyncClient", _client_factory(calls)),
    )


async def _get(path: str = "/v1/community/stats") -> object:
    return await stats_json("GET", path, auth=False)


class TestBreakerUnit:
    def test_opens_after_threshold_then_half_opens_once(self):
        breaker = CommunityBreaker(threshold=2, open_seconds=10)
        assert breaker.allow(now=0)
        breaker.record_failure(now=0)
        assert breaker.allow(now=0)
        breaker.record_failure(now=0)
        assert not breaker.allow(now=5)
        assert breaker.allow(now=11)  # one trial
        assert not breaker.allow(now=11)  # others still fail fast
        breaker.record_failure(now=11)
        assert not breaker.allow(now=15)
        assert breaker.allow(now=22)
        breaker.record_success()
        assert breaker.allow(now=22) and breaker.allow(now=22)


@pytest.mark.asyncio
class TestCommunityUnreachable:
    async def test_timeout_is_503_and_opens_breaker(self):
        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("slow", request=request)

        calls = _Calls(timeout)
        p1, p2, p3 = _patched(calls)
        with p1, p2, p3:
            for _ in range(BREAKER_FAILURE_THRESHOLD):
                with pytest.raises(HTTPException) as exc:
                    await _get()
                assert exc.value.status_code == 503
                assert exc.value.detail == COMMUNITY_UNREACHABLE_DETAIL
            assert calls.count == BREAKER_FAILURE_THRESHOLD
            # Open: answers 503 at once, no network.
            for _ in range(5):
                with pytest.raises(HTTPException) as exc:
                    await _get()
                assert exc.value.status_code == 503
            assert calls.count == BREAKER_FAILURE_THRESHOLD
        assert community_breaker().is_open

    async def test_upstream_5xx_is_503_and_counts(self):
        calls = _Calls(lambda _r: httpx.Response(502, json={"detail": "bad gateway"}))
        p1, p2, p3 = _patched(calls)
        with p1, p2, p3:
            with pytest.raises(HTTPException) as exc:
                await _get()
        assert exc.value.status_code == 503
        assert "HTTP 502" in exc.value.detail
        assert community_breaker().failures == 1

    async def test_4xx_is_not_a_breaker_failure(self):
        calls = _Calls(lambda _r: httpx.Response(404, json={"detail": "nope"}))
        p1, p2, p3 = _patched(calls)
        with p1, p2, p3:
            for _ in range(BREAKER_FAILURE_THRESHOLD + 1):
                with pytest.raises(HTTPException) as exc:
                    await _get()
                assert exc.value.status_code == 502
        assert calls.count == BREAKER_FAILURE_THRESHOLD + 1
        assert not community_breaker().is_open

    async def test_half_open_success_closes(self):
        state = {"fail": True}

        def respond(request: httpx.Request) -> httpx.Response:
            if state["fail"]:
                raise httpx.ConnectError("down", request=request)
            return httpx.Response(200, json={"ok": True})

        calls = _Calls(respond)
        p1, p2, p3 = _patched(calls)
        with p1, p2, p3:
            for _ in range(BREAKER_FAILURE_THRESHOLD):
                with pytest.raises(HTTPException):
                    await _get()
            community_breaker().open_until = 0.0  # cooldown elapsed
            state["fail"] = False
            assert await _get() == {"ok": True}
            assert await _get() == {"ok": True}
        assert not community_breaker().is_open

    async def test_401_clock_skew_detail_is_explained(self):
        calls = _Calls(
            lambda _r: httpx.Response(
                401, json={"detail": "iat in future", "code": "clock_skew", "server_time": 1}
            )
        )
        p1, p2, p3 = _patched(calls)
        with p1, p2, p3:
            with pytest.raises(HTTPException) as exc:
                await _get()
        assert exc.value.status_code == 502
        assert "clock" in exc.value.detail

    async def test_router_answers_503_not_500(self, test_db):
        from fastapi.testclient import TestClient

        from app.main import app
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        calls = _Calls(down)
        with (
            patch.object(meshloom_community, "mint_stats_jwt", MagicMock(return_value="tok")),
            patch.object(meshloom_community.httpx, "AsyncClient", _client_factory(calls)),
        ):
            client = TestClient(app)
            response = client.get("/api/community/stats")
        assert response.status_code == 503
        assert response.json()["detail"] == COMMUNITY_UNREACHABLE_DETAIL
