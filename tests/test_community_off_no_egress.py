"""Opted out of Community means no connection to Community or to the airport search.

Recorders replace ``httpx.AsyncClient``, ``websockets.connect`` and
``aiomqtt.Client`` for the whole test. With Community off, every route under
``/api/community``, ``/api/directory`` and ``/api/tools`` is called (found
dynamically from ``app.routes``), plus the hashtag catalogue and the update
check with GitHub answering 503. Any request to ``*.meshloom.app``,
``api.fx-port.com`` or the effective Community API base fails the test.

The transition tests cover the opt-out itself (on → off) and an IATA change:
the retained MQTT status is cleared before the DISCONNECT, the Live socket is
closed, requests in flight are cancelled, and nothing leaves afterwards.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.decoder import encrypt_group_text
from app.fanout.manager import SYSTEM_MESHLOOM_STATS_ID, fanout_manager
from app.fanout.meshloom_stats import MeshloomStatsPublisher
from app.packet_processor import process_raw_packet
from app.services import meshloom_community
from app.services.community_live import (
    get_live_relay,
    reset_community_live_for_tests,
    subscribe_live,
)
from app.services.hashtag_catalogue import run_catalogue_pass
from app.services.meshloom_community import (
    AIRPORT_SEARCH_URL,
    community_breaker,
    fetch_meshloom_latest,
    get_community_effective,
    note_sample_quota,
    sample_quota_blocked,
    schedule_hashtag_names_publish,
    search_community_airports,
    stats_json,
    update_community,
    upload_hashtag_sample,
)
from app.services.oss_updates import fetch_github_latest as _real_fetch_github_latest
from app.services.oss_updates import refresh_oss_update_cache

_RealAsyncClient = httpx.AsyncClient
_real_fetch_meshloom_latest = fetch_meshloom_latest

# An API base that is neither the default nor a placeholder, so "the effective
# api_base" is checked on its own and not only through *.meshloom.app.
EGRESS_API_BASE = "https://stats.egress-check.net"
PUBKEY = bytes(range(32))
PUBKEY_HEX = PUBKEY.hex().upper()
FX_PORT_HOST = urlsplit(AIRPORT_SEARCH_URL).hostname


def _forbidden(host: str | None) -> bool:
    host = (host or "").lower()
    return (
        host == "meshloom.app"
        or host.endswith(".meshloom.app")
        or host == FX_PORT_HOST
        or host == urlsplit(EGRESS_API_BASE).hostname
    )


class EgressRecorder:
    """Records every outbound connection attempt, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []
        self.http_gate: asyncio.Event | None = None
        self.mqtt_connects = True
        self.ws_connects = True
        self.sockets: list[FakeLiveSocket] = []

    def hosts(self, since: int = 0) -> list[str]:
        out: list[str] = []
        for event in self.events[since:]:
            if event[0] in {"http", "ws_connect", "mqtt_connect"}:
                out.append(event[1])
        return out

    def forbidden(self, since: int = 0) -> list[str]:
        return [h for h in self.hosts(since) if _forbidden(h)]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.events.append(("http", request.url.host, request.url.path))
        if request.url.host == "github.com":
            return httpx.Response(503)
        if self.http_gate is not None:
            await self.http_gate.wait()
        return httpx.Response(503)


class FakeLiveSocket:
    def __init__(self, recorder: EgressRecorder) -> None:
        self.recorder = recorder
        self.closed = asyncio.Event()

    async def __aenter__(self) -> FakeLiveSocket:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    def __aiter__(self) -> FakeLiveSocket:
        return self

    async def __anext__(self) -> object:
        await self.closed.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        if not self.closed.is_set():
            self.recorder.events.append(("ws_close",))
        self.closed.set()


class FakeMqttClient:
    def __init__(self, recorder: EgressRecorder, **kwargs: Any) -> None:
        self.recorder = recorder
        self.kwargs = kwargs

    async def __aenter__(self) -> FakeMqttClient:
        self.recorder.events.append(("mqtt_connect", self.kwargs.get("hostname")))
        if not self.recorder.mqtt_connects:
            raise RuntimeError("MQTT is not allowed here")
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self.recorder.events.append(("mqtt_disconnect",))
        return False

    async def publish(self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False):
        self.recorder.events.append(("mqtt_publish", topic, payload, retain, qos))


@pytest.fixture
def egress(monkeypatch):
    recorder = EgressRecorder()

    class RecordingAsyncClient(_RealAsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(recorder.handle)
            super().__init__(*args, **kwargs)

    def ws_connect(url: str, **_kwargs: Any) -> FakeLiveSocket:
        recorder.events.append(("ws_connect", urlsplit(url).hostname))
        if not recorder.ws_connects:
            raise RuntimeError("Live is not allowed here")
        socket = FakeLiveSocket(recorder)
        recorder.sockets.append(socket)
        return socket

    def mqtt_client(**kwargs: Any) -> FakeMqttClient:
        return FakeMqttClient(recorder, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", RecordingAsyncClient)
    monkeypatch.setattr("websockets.connect", ws_connect)
    monkeypatch.setattr("app.fanout.mqtt_base.aiomqtt.Client", mqtt_client)
    # conftest stubs both release lookups; this file wants the real ones.
    monkeypatch.setattr("app.services.oss_updates.fetch_github_latest", _real_fetch_github_latest)
    monkeypatch.setattr(
        "app.services.oss_updates.fetch_meshloom_latest", _real_fetch_meshloom_latest
    )
    monkeypatch.setenv("MESHLOOM_COMMUNITY_API_BASE", EGRESS_API_BASE)
    with (
        patch("app.keystore.get_public_key", return_value=PUBKEY),
        patch("app.keystore.get_private_key", return_value=b"\x02" * 64),
        patch("app.keystore.has_private_key", return_value=True),
        patch.object(meshloom_community, "mint_stats_jwt", return_value="jwt"),
        patch("app.fanout.meshloom_stats._generate_jwt_token", return_value="jwt"),
        patch("app.fanout.community_mqtt._generate_jwt_token", return_value="jwt"),
        patch.object(
            MeshloomStatsPublisher,
            "_fetch_device_info",
            AsyncMock(return_value={"model": "t", "firmware_version": "v1"}),
        ),
        patch.object(MeshloomStatsPublisher, "_fetch_stats", AsyncMock(return_value=None)),
        patch("app.fanout.mqtt_base._broadcast_health"),
        patch("app.websocket.broadcast_success"),
        patch("app.websocket.broadcast_error"),
        patch("app.websocket.broadcast_health"),
        patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock),
    ):
        reset_community_live_for_tests()
        yield recorder
        if recorder.http_gate is not None:
            recorder.http_gate.set()


@pytest.fixture
async def cleanup_community():
    yield
    await fanout_manager.remove_config(SYSTEM_MESHLOOM_STATS_ID)
    await get_live_relay().shutdown()
    reset_community_live_for_tests()


async def _until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def _status_topic(iata: str) -> str:
    return f"meshcore/{iata}/{PUBKEY_HEX}/status"


def _clear(iata: str) -> tuple[Any, ...]:
    return ("mqtt_publish", _status_topic(iata), b"", True, 1)


def _online_published(recorder: EgressRecorder, iata: str) -> bool:
    return any(
        e[0] == "mqtt_publish" and e[1] == _status_topic(iata) and e[2] != b""
        for e in recorder.events
    )


async def _store_unknown_group_text() -> None:
    key = bytes(16)
    payload = encrypt_group_text(key, int(time.time()), "Bob: unknown channel", 0)
    with (
        patch("app.packet_processor.broadcast_event"),
        patch("app.services.hashtag_catalogue.schedule_unknown_group_text_resolve"),
    ):
        await process_raw_packet(bytes([0x15, 0x00]) + payload, timestamp=int(time.time()))


_PATH_PARAMS = {
    "code": "CDG",
    "session_id": "session-1",
    "pubkey": "ab" * 32,
    "packet_hash": "AB" * 8,
}
_QUERY = {
    "/api/community/airports": {"q": "Lyon", "locale": "fr"},
    "/api/directory/nodes/search": {"q": "hill"},
}
_BODIES: dict[tuple[str, str], Any] = {
    ("PATCH", "/api/community"): {"iata": "LYS"},
    ("PUT", "/api/community/me/iata"): {"iata": "LYS"},
    ("PUT", "/api/community/me/hashtags"): {"names": ["meshcore"]},
    ("POST", "/api/community/live/subscribe"): {},
    ("POST", "/api/directory/resolve-hops"): {"hops": ["abcd", "abcdef"]},
    ("POST", "/api/directory/packets/reach-counts"): {"hashes": ["AB" * 8]},
    ("POST", "/api/tools/mesh-test"): {"flood_scope": "#test"},
}
_SWEPT_PREFIXES = ("/api/community", "/api/directory", "/api/tools")


def _walk_routes(routes: list[Any], prefix: str = "") -> list[tuple[str, APIRoute]]:
    """Every APIRoute with its full path, through included routers."""
    out: list[tuple[str, APIRoute]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            out.append((prefix + route.path, route))
        context = getattr(route, "include_context", None)  # FastAPI's lazy include
        if context is not None:
            out.extend(_walk_routes(context.included_router.routes, prefix + context.prefix))
    return out


def _swept_routes() -> list[tuple[str, str]]:
    from app.main import app

    out: list[tuple[str, str]] = []
    for path, route in _walk_routes(app.routes):
        if not path.startswith(_SWEPT_PREFIXES):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, path))
    return out


class TestCommunityOffNoEgress:
    def test_sweep_finds_the_community_routes(self):
        routes = _swept_routes()
        paths = {path for _method, path in routes}
        assert "/api/community/airports" in paths
        assert "/api/directory/packets/{packet_hash}/reach" in paths
        assert "/api/tools/mesh-test" in paths
        assert len(routes) >= 20

    @pytest.mark.asyncio
    async def test_no_community_or_airport_egress_when_off(
        self, test_db, egress, cleanup_community
    ):
        from app.main import app

        # The state an opt-out leaves behind: off, IATA still stored.
        await update_community(enabled=False, iata="CDG")
        assert (await get_community_effective()).enabled is False

        async with _RealAsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for method, template in _swept_routes():
                path = re.sub(r"\{(\w+)\}", lambda m: _PATH_PARAMS[m.group(1)], template)
                response = await client.request(
                    method,
                    path,
                    params=_QUERY.get(template),
                    json=_BODIES.get((method, template)),
                )
                # 422 would mean the handler never ran: add a body to _BODIES.
                assert response.status_code != 422, (method, template, response.text)
                assert not egress.forbidden(), (method, template, egress.events)

        # Background Community work.
        await _store_unknown_group_text()
        await run_catalogue_pass()
        await schedule_hashtag_names_publish(["meshcore"])
        assert await upload_hashtag_sample("ab", "ab" * 40) is False
        await fanout_manager.sync_system_modules()
        await subscribe_live()
        await asyncio.sleep(0.05)

        # The update check, with GitHub down: no fallback to the Community mirror.
        assert await refresh_oss_update_cache() is None
        assert await fetch_meshloom_latest() is None
        assert await search_community_airports("Lyon") == []

        assert egress.forbidden() == [], egress.events
        assert set(egress.hosts()) <= {"github.com"}, egress.events
        assert ("http", "github.com", "/TwinRocket/meshloom/releases/latest") in egress.events


class TestOptOutTransition:
    @pytest.mark.asyncio
    async def test_on_to_off_tears_everything_down(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))

        await subscribe_live()
        await _until(lambda: get_live_relay().connected)
        socket = egress.sockets[-1]

        egress.http_gate = asyncio.Event()
        in_flight = asyncio.create_task(stats_json("GET", "/v1/community/stats", auth=False))
        await _until(
            lambda: any(
                e[:3] == ("http", "stats.egress-check.net", "/v1/community/stats")
                for e in egress.events
            )
        )

        breaker = community_breaker()
        for _ in range(breaker.threshold):
            breaker.record_failure()
        note_sample_quota()

        await update_community(enabled=False)
        mark = len(egress.events)

        # Retained status cleared, and before the DISCONNECT.
        events = egress.events
        assert _clear("CDG") in events
        assert events.index(_clear("CDG")) < events.index(("mqtt_disconnect",))
        # Live closed, request in flight cancelled into the opt-out 403.
        assert socket.closed.is_set()
        assert get_live_relay().connected is False
        with pytest.raises(HTTPException) as exc:
            await in_flight
        assert exc.value.status_code == 403
        # Client state reset.
        assert breaker.failures == 0
        assert sample_quota_blocked() is False
        assert get_live_relay()._queue.empty()

        # Nothing leaves afterwards.
        with pytest.raises(HTTPException):
            await stats_json("GET", "/v1/community/stats", auth=False)
        assert await fetch_meshloom_latest() is None
        assert await search_community_airports("Lyon") == []
        await subscribe_live()
        await fanout_manager.sync_system_modules()
        await asyncio.sleep(0.05)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_iata_change_clears_the_old_status_topic(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))

        await update_community(iata="LYS")
        await _until(lambda: _online_published(egress, "LYS"))

        events = egress.events
        clear_at = events.index(_clear("CDG"))
        first_disconnect = events.index(("mqtt_disconnect",))
        lys_online = next(
            i
            for i, e in enumerate(events)
            if e[0] == "mqtt_publish" and e[1] == _status_topic("LYS") and e[2] != b""
        )
        assert clear_at < first_disconnect < lys_online
        assert _clear("LYS") not in events

    @pytest.mark.asyncio
    async def test_reload_without_iata_change_keeps_the_status(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await update_community(iata="CDG")
        await _until(lambda: egress.events.count(("mqtt_disconnect",)) >= 1)
        assert _clear("CDG") not in egress.events
