"""Opted out of Community means no connection to Community or to the airport search.

Recorders replace ``httpx.AsyncClient``, ``websockets.connect`` and
``aiomqtt.Client`` for the whole test. With Community off, every route under
``/api/community``, ``/api/directory``, ``/api/tools``, ``/api/channels``,
``/api/settings`` and ``/api/updates`` is called (found dynamically from
``app.routes``), plus the hashtag catalogue and the update check with GitHub
answering 503. Any request to ``*.meshloom.app``,
``api.fx-port.com`` or the effective Community API base fails the test.

The transition tests cover the opt-out itself (on → off) and an IATA change:
the retained MQTT status is cleared before the DISCONNECT, the Live socket is
closed, requests in flight are cancelled, and nothing leaves afterwards. They
also run when the update fails or is cancelled halfway, and an IATA bind
clears the old status before ``PUT /v1/me/iata`` and before the reconnect.

The one exception, ``TestRetainedStatusClear``: when the broker was unreachable
at the opt-out, one bounded reconnect clears the retained status, and that
connection carries only the empty retained payload. Nothing else connects,
and nothing at all once it succeeded or gave up. ``TestOrphanedStatusAtStartup``
uses the same clear for a topic left behind while the app was stopped.

``TestPositionRebindOff``: the automatic re-bind on a radio position or policy
change sends nothing while Community is off.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
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
from app.services import community_status_clear, meshloom_community
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
        self.mqtt_clients: list[FakeMqttClient] = []
        # Path -> canned answer from Community (everything else gets a 503).
        self.responses: dict[str, httpx.Response] = {}

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
        canned = self.responses.get(request.url.path)
        if canned is not None:
            return httpx.Response(canned.status_code, content=canned.content)
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
        recorder.mqtt_clients.append(self)

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
    monkeypatch.setattr(meshloom_community, "DEFAULT_API_BASE", EGRESS_API_BASE)
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


def _status_of(event: tuple[Any, ...]) -> str | None:
    if len(event) < 3 or event[0] != "mqtt_publish" or not isinstance(event[2], str):
        return None
    return json.loads(event[2]).get("status")


def _online_published(recorder: EgressRecorder, iata: str) -> bool:
    return any(_status_of(e) == "online" and e[1] == _status_topic(iata) for e in recorder.events)


def _online_since(recorder: EgressRecorder, iata: str, start: int) -> bool:
    return any(
        _status_of(e) == "online" and e[1] == _status_topic(iata) for e in recorder.events[start:]
    )


async def _last_on_fixed_broker() -> bool:
    last = await community_status_clear.last_published()
    return last is not None and last.broker_host == "mqtt.meshloom.app"


def _offline_at(events: list[tuple[Any, ...]], iata: str) -> int:
    return next(
        i
        for i, e in enumerate(events)
        if e[0] == "mqtt_publish" and e[1] == _status_topic(iata) and _status_of(e) == "offline"
    )


def _open_mqtt_connections(recorder: EgressRecorder) -> int:
    connects = sum(1 for e in recorder.events if e[0] == "mqtt_connect")
    return connects - recorder.events.count(("mqtt_disconnect",))


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
    "key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
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
    ("POST", "/api/channels"): {"name": "#meshcore"},
    ("POST", "/api/channels/bulk-hashtag"): {"channel_names": ["#meshcore"]},
    ("POST", "/api/channels/{key}/flood-scope-override"): {"flood_scope_override": ""},
    ("POST", "/api/channels/{key}/path-hash-mode-override"): {"path_hash_mode_override": None},
    ("PATCH", "/api/settings"): {},
    ("POST", "/api/settings/favorites/toggle"): {"type": "contact", "id": "ab" * 32},
    ("POST", "/api/settings/pins/toggle"): {"type": "contact", "id": "ab" * 32},
    ("POST", "/api/settings/muted-channels/toggle"): {"key": _PATH_PARAMS["key"]},
    ("POST", "/api/settings/blocked-keys/toggle"): {"key": "ab" * 32},
    ("POST", "/api/settings/blocked-names/toggle"): {"name": "Bob"},
    ("POST", "/api/settings/tracked-telemetry/toggle"): {"public_key": "ab" * 32},
    ("POST", "/api/settings/tracked-telemetry-contacts/toggle"): {"public_key": "ab" * 32},
    ("POST", "/api/settings/notification-destinations/test"): {"channel": "webhook"},
    ("POST", "/api/settings/backup/restore"): {"confirm": True},
    ("PATCH", "/api/updates/settings"): {},
}
_SWEPT_PREFIXES = (
    "/api/community",
    "/api/directory",
    "/api/tools",
    "/api/channels",
    "/api/settings",
    "/api/updates",
)


# Local only, and needs the on-disk database the test fixture does not open.
_NOT_SWEPT = {("GET", "/api/settings/backup/database")}


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
            if (method, path) not in _NOT_SWEPT:
                out.append((method, path))
    return out


class TestCommunityOffNoEgress:
    def test_sweep_finds_the_community_routes(self):
        routes = _swept_routes()
        paths = {path for _method, path in routes}
        assert "/api/community/airports" in paths
        assert "/api/directory/packets/{packet_hash}/reach" in paths
        assert "/api/tools/mesh-test" in paths
        assert "/api/channels/{key}/adopt" in paths
        assert "/api/settings" in paths
        assert "/api/updates/refresh" in paths
        assert len(routes) >= 50

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

            # The update check from the UI: GitHub only.
            response = await client.post("/api/updates/refresh")
            assert response.status_code == 200, response.text
            assert not egress.forbidden(), egress.events

        # A restart while opted out: the fanout manager starts no publisher.
        await fanout_manager.load_from_db()
        assert SYSTEM_MESHLOOM_STATS_ID not in fanout_manager._modules

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
        # "offline" first, so a failed clear never leaves "online" behind.
        assert _offline_at(events, "CDG") < events.index(_clear("CDG"))
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
            if _status_of(e) == "online" and e[1] == _status_topic("LYS")
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

    @pytest.mark.asyncio
    async def test_opt_out_and_opt_in_racing_leave_one_publisher(
        self, test_db, egress, cleanup_community
    ):
        """Two tabs: off then on at once. The teardown must not undo the opt-in."""
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await subscribe_live()
        await _until(lambda: get_live_relay().connected)

        # Slow the teardown's first step so the opt-in lands in the middle of it.
        real_cancel = meshloom_community._cancel_inflight_requests

        async def slow_cancel() -> None:
            await asyncio.sleep(0.2)
            await real_cancel()

        with patch.object(meshloom_community, "_cancel_inflight_requests", slow_cancel):
            opt_out = asyncio.create_task(update_community(enabled=False))
            await asyncio.sleep(0.05)
            await update_community(enabled=True)
            await opt_out

        assert (await get_community_effective()).enabled is True
        await _until(lambda: _open_mqtt_connections(egress) == 1)
        assert SYSTEM_MESHLOOM_STATS_ID in fanout_manager._modules
        # Live reopened for the sessions that are still there.
        await _until(lambda: get_live_relay().connected)
        await asyncio.sleep(0.05)
        assert _open_mqtt_connections(egress) == 1

    @pytest.mark.asyncio
    async def test_overlapping_syncs_start_one_publisher(self, test_db, egress, cleanup_community):
        """A publisher overwritten in the module table would never be stopped."""
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))

        await asyncio.gather(*(fanout_manager.sync_system_modules() for _ in range(3)))
        await _until(lambda: _open_mqtt_connections(egress) == 1)

        await update_community(enabled=False)
        await asyncio.sleep(0.05)
        assert _open_mqtt_connections(egress) == 0

    @pytest.mark.asyncio
    async def test_iata_change_reopens_live(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await subscribe_live()
        await _until(lambda: get_live_relay().connected)
        first = egress.sockets[-1]

        await update_community(iata="LYS")
        await _until(lambda: len(egress.sockets) == 2 and get_live_relay().connected)
        assert first.closed.is_set()

    @pytest.mark.asyncio
    async def test_publisher_never_reconnects_once_opted_out(
        self, test_db, egress, cleanup_community
    ):
        """Safety net for a stop that abandoned its loop: no reconnect after opt-out."""
        await update_community(enabled=False, iata="CDG")
        publisher = MeshloomStatsPublisher()
        settings = SimpleNamespace(community_mqtt_enabled=True, community_mqtt_iata="CDG")
        publisher._settings = settings
        assert await publisher._pre_connect(settings) is False
        assert settings.community_mqtt_enabled is False
        assert publisher._is_configured() is False
        assert egress.events == []

    @pytest.mark.asyncio
    async def test_live_close_is_bounded(self, test_db, egress, cleanup_community, monkeypatch):
        from app.services import community_live

        monkeypatch.setattr(community_live, "CLOSE_TIMEOUT_S", 0.05)
        await update_community(enabled=True, iata="CDG")
        await subscribe_live()
        await _until(lambda: get_live_relay().connected)
        socket = egress.sockets[-1]

        async def hang() -> None:
            await asyncio.sleep(3600)

        socket.close = hang  # type: ignore[method-assign]
        started = time.monotonic()
        await update_community(enabled=False)
        assert time.monotonic() - started < 2
        assert get_live_relay()._reader_task is None


class TestTransitionsSurviveFailures:
    """The teardown (and the status move) run even when the update itself fails."""

    @pytest.mark.asyncio
    async def test_opt_out_tears_down_when_a_reset_raises(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await subscribe_live()
        await _until(lambda: get_live_relay().connected)
        socket = egress.sockets[-1]

        with (
            patch(
                "app.services.directory.reset_directory_nodes_cache",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            await update_community(enabled=False)

        assert (await get_community_effective()).enabled is False
        assert _clear("CDG") in egress.events
        assert socket.closed.is_set()
        assert SYSTEM_MESHLOOM_STATS_ID not in fanout_manager._modules
        assert _open_mqtt_connections(egress) == 0

    @pytest.mark.asyncio
    async def test_opt_out_tears_down_when_the_caller_is_cancelled(
        self, test_db, egress, cleanup_community
    ):
        from app.repository.settings import AppSettingsRepository

        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await subscribe_live()
        await _until(lambda: get_live_relay().connected)
        socket = egress.sockets[-1]

        real_write = AppSettingsRepository.update_community
        written = asyncio.Event()

        async def write_then_hang(**kwargs: Any) -> None:
            await real_write(**kwargs)
            written.set()
            await asyncio.sleep(3600)

        with patch.object(AppSettingsRepository, "update_community", write_then_hang):
            task = asyncio.create_task(update_community(enabled=False))
            await asyncio.wait_for(written.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert _clear("CDG") in egress.events
        assert socket.closed.is_set()
        assert SYSTEM_MESHLOOM_STATS_ID not in fanout_manager._modules
        mark = len(egress.events)
        await fanout_manager.sync_system_modules()
        await subscribe_live()
        await asyncio.sleep(0.05)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_teardown_finishes_when_cancelled_midway(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        real_cancel = meshloom_community._cancel_inflight_requests
        started = asyncio.Event()

        async def slow_cancel() -> None:
            started.set()
            await asyncio.sleep(0.2)
            await real_cancel()

        with patch.object(meshloom_community, "_cancel_inflight_requests", slow_cancel):
            task = asyncio.create_task(update_community(enabled=False))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        # The cancellation waited for the whole teardown.
        assert _clear("CDG") in egress.events
        assert SYSTEM_MESHLOOM_STATS_ID not in fanout_manager._modules

    @pytest.mark.asyncio
    async def test_iata_change_moves_the_status_when_a_reset_raises(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))

        with (
            patch(
                "app.services.directory.reset_directory_nodes_cache",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError),
        ):
            await update_community(iata="LYS")

        await _until(lambda: _online_published(egress, "LYS"))
        assert _clear("CDG") in egress.events
        assert _open_mqtt_connections(egress) == 1


def _bind_ok(iata: str) -> httpx.Response:
    body = {"iata": iata, "concordance": "ok", "honored_for_buckets": True}
    return httpx.Response(200, content=json.dumps(body).encode())


async def _put_me_iata(iata: str, *, enable: bool | None = None) -> httpx.Response:
    from app.main import app

    body: dict[str, Any] = {"iata": iata}
    if enable is not None:
        body["enable"] = enable
    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.put("/api/community/me/iata", json=body)


def _bind_at(events: list[tuple[Any, ...]]) -> int:
    return next(i for i, e in enumerate(events) if e[0] == "http" and e[2] == "/v1/me/iata")


class TestIataBindOrder:
    """identity.md, Changing IATA: clear the old retained status, PUT, then reconnect."""

    @pytest.mark.asyncio
    async def test_old_status_cleared_before_the_put_and_the_reconnect(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        egress.responses["/v1/me/iata"] = _bind_ok("LYS")

        response = await _put_me_iata("LYS")
        assert response.status_code == 200, response.text
        await _until(lambda: _online_published(egress, "LYS"))

        events = egress.events
        clear_at = events.index(_clear("CDG"))
        disconnect_at = events.index(("mqtt_disconnect",))
        lys_online = next(
            i
            for i, e in enumerate(events)
            if _status_of(e) == "online" and e[1] == _status_topic("LYS")
        )
        assert clear_at < disconnect_at < _bind_at(events) < lys_online
        assert (await get_community_effective()).iata == "LYS"
        assert await _pending() == []
        assert _open_mqtt_connections(egress) == 1

    @pytest.mark.asyncio
    async def test_failed_bind_brings_the_old_topic_back(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        # No canned answer: Community answers 503.

        response = await _put_me_iata("LYS")
        assert response.status_code == 503, response.text
        await _until(lambda: _open_mqtt_connections(egress) == 1)

        events = egress.events
        clear_at = events.index(_clear("CDG"))
        back_online = max(
            i
            for i, e in enumerate(events)
            if _status_of(e) == "online" and e[1] == _status_topic("CDG")
        )
        assert clear_at < _bind_at(events) < back_online
        assert not _online_published(egress, "LYS")
        assert (await get_community_effective()).iata == "CDG"
        assert await _pending() == []

    @pytest.mark.asyncio
    async def test_cancelled_during_the_retire_brings_the_old_topic_back(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        real_retire = meshloom_community._retire_status_and_reload
        started = asyncio.Event()

        async def slow_retire(*args: Any, **kwargs: Any) -> None:
            started.set()
            await asyncio.sleep(0.2)
            await real_retire(*args, **kwargs)

        bind = AsyncMock()
        with patch.object(meshloom_community, "_retire_status_and_reload", slow_retire):
            task = asyncio.create_task(meshloom_community.bind_community_iata("LYS", bind))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        bind.assert_not_awaited()
        # The retire ran to its end, then the publisher came back on the old topic.
        assert _clear("CDG") in egress.events
        assert SYSTEM_MESHLOOM_STATS_ID in fanout_manager._modules
        await _until(lambda: _open_mqtt_connections(egress) == 1)
        back_online = max(
            i
            for i, e in enumerate(egress.events)
            if _status_of(e) == "online" and e[1] == _status_topic("CDG")
        )
        assert egress.events.index(_clear("CDG")) < back_online
        state = await get_community_effective()
        assert (state.enabled, state.iata) == (True, "CDG")
        assert await _pending() == []

    @pytest.mark.asyncio
    async def test_same_iata_bind_keeps_the_status(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        egress.responses["/v1/me/iata"] = _bind_ok("CDG")

        response = await _put_me_iata("CDG")
        assert response.status_code == 200, response.text
        assert _clear("CDG") not in egress.events


class TestJoinBindsFirst:
    """Join (Community off): ``PUT /v1/me/iata`` first, then on + publisher."""

    @pytest.mark.asyncio
    async def test_join_binds_before_turning_on_and_publishing(
        self, test_db, egress, cleanup_community
    ):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=False, iata="CDG")
        egress.responses["/v1/me/iata"] = _bind_ok("LYS")
        mint = patch.object(
            meshloom_community, "mint_stats_jwt", wraps=meshloom_community.mint_stats_jwt
        )

        with mint as minted:
            response = await _put_me_iata("LYS", enable=True)
        assert response.status_code == 200, response.text
        await _until(lambda: _online_published(egress, "LYS"))

        # The bind token carries the new IATA.
        assert minted.call_args_list[0].kwargs["iata"] == "LYS"
        events = egress.events
        first_mqtt = next(i for i, e in enumerate(events) if e[0] == "mqtt_connect")
        assert _bind_at(events) < first_mqtt
        assert not _online_published(egress, "CDG")
        state = await get_community_effective()
        assert (state.enabled, state.iata) == (True, "LYS")
        assert _open_mqtt_connections(egress) == 1

    @pytest.mark.asyncio
    async def test_refused_join_stays_off_with_the_old_iata(
        self, test_db, egress, cleanup_community
    ):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=False, iata="CDG")
        egress.responses["/v1/me/iata"] = httpx.Response(
            429, content=json.dumps({"detail": "IATA change cap exceeded"}).encode()
        )

        response = await _put_me_iata("LYS", enable=True)
        assert response.status_code == 429, response.text
        assert "IATA" in response.json()["detail"]
        await asyncio.sleep(0.05)

        assert [e for e in egress.events if e[0] != "http"] == []
        assert [e[2] for e in egress.events if e[0] == "http"] == ["/v1/me/iata"]
        state = await get_community_effective()
        assert (state.enabled, state.iata) == (False, "CDG")
        assert SYSTEM_MESHLOOM_STATS_ID not in fanout_manager._modules
        # The join egress was scoped to the bind: Community is still off.
        with pytest.raises(HTTPException) as exc:
            await stats_json("GET", "/v1/me/stats", auth=True)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_bind_without_enable_stays_refused_while_off(
        self, test_db, egress, cleanup_community
    ):
        response = await _put_me_iata("LYS")
        assert response.status_code == 403, response.text
        assert egress.events == []

    @pytest.mark.asyncio
    async def test_join_cancelled_between_the_write_and_the_reload_still_publishes(
        self, test_db, egress, cleanup_community
    ):
        from app.models import CommunityIataBindResult
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=False, iata="")
        real_write = AppSettingsRepository.update_community
        written = asyncio.Event()

        async def write_then_wait(**kwargs: Any) -> None:
            await real_write(**kwargs)
            written.set()
            await asyncio.sleep(0.2)

        bind = AsyncMock(
            return_value=CommunityIataBindResult(
                iata="LYS", concordance="ok", honored_for_buckets=True
            )
        )
        with patch.object(AppSettingsRepository, "update_community", write_then_wait):
            task = asyncio.create_task(
                meshloom_community.bind_community_iata("LYS", bind, enable=True)
            )
            await asyncio.wait_for(written.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # The database says on: the publisher was started all the same.
        state = await get_community_effective()
        assert (state.enabled, state.iata) == (True, "LYS")
        assert SYSTEM_MESHLOOM_STATS_ID in fanout_manager._modules
        await _until(lambda: _online_published(egress, "LYS"))


async def _patch_community(body: dict[str, Any]) -> httpx.Response:
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.patch("/api/community", json=body)


class TestPatchNeverChangesTheIata:
    """An IATA change is a bind (PUT /community/me/iata), never a PATCH."""

    @staticmethod
    def _refused(response: httpx.Response) -> None:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "iata_requires_bind"

    @pytest.mark.asyncio
    async def test_off_to_on_with_an_iata_is_refused(self, test_db, egress, cleanup_community):
        self._refused(await _patch_community({"enabled": True, "iata": "LYS"}))
        state = await get_community_effective()
        assert (state.enabled, state.iata) == (False, "")
        assert egress.events == []

    @pytest.mark.asyncio
    async def test_iata_while_off_then_on_is_refused(self, test_db, egress, cleanup_community):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=False, iata="CDG")
        # The two-step way around: store another IATA while off, then turn on.
        self._refused(await _patch_community({"iata": "LYS"}))
        response = await _patch_community({"enabled": True})
        assert response.status_code == 200, response.text
        await _until(lambda: _online_published(egress, "CDG"))
        assert not _online_published(egress, "LYS")
        assert (await get_community_effective()).iata == "CDG"

    @pytest.mark.asyncio
    async def test_iata_while_on_is_refused(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        mark = len(egress.events)

        self._refused(await _patch_community({"iata": "LYS"}))
        self._refused(await _patch_community({"enabled": True, "iata": "LYS"}))
        self._refused(await _patch_community({"iata": ""}))
        await asyncio.sleep(0.05)
        assert egress.events[mark:] == []
        assert (await get_community_effective()).iata == "CDG"

    @pytest.mark.asyncio
    async def test_the_stored_iata_is_accepted(self, test_db, egress, cleanup_community):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=False, iata="CDG")
        response = await _patch_community({"enabled": True, "iata": "cdg"})
        assert response.status_code == 200, response.text
        await _until(lambda: _online_published(egress, "CDG"))
        response = await _patch_community({"iata": "CDG"})
        assert response.status_code == 200, response.text


def _stats_publisher() -> MeshloomStatsPublisher:
    module = fanout_manager._modules[SYSTEM_MESHLOOM_STATS_ID][0]
    return module._publisher  # type: ignore[attr-defined]


async def _pending() -> list[dict[str, Any]]:
    from app.repository.settings import AppSettingsRepository

    return await AppSettingsRepository.get_community_status_clear()


async def _pending_topics() -> list[str]:
    return [f"meshcore/{e['iata']}/{e['pubkey']}/status" for e in await _pending()]


async def _until_async(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not await predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


async def _nothing_pending() -> bool:
    return await _pending() == []


@pytest.fixture
def fast_retries(monkeypatch):
    """Retries a few ms apart instead of minutes to hours."""

    def set_delays(delay: float) -> None:
        monkeypatch.setattr(
            community_status_clear,
            "CLEAR_RETRY_DELAYS_S",
            (delay,) * community_status_clear.CLEAR_MAX_ATTEMPTS,
        )

    set_delays(0.05)
    monkeypatch.setattr(community_status_clear, "WORKER_POLL_S", 0.01)
    return set_delays


async def _opt_out_with_broker_down(egress: EgressRecorder, iata: str = "CDG") -> int:
    """Opt out while the broker is unreachable; return the event index after it."""
    await update_community(enabled=True, iata=iata)
    await _until(lambda: _online_published(egress, iata))
    egress.mqtt_connects = False
    _stats_publisher().connected = False  # the broker went away
    await update_community(enabled=False)
    return len(egress.events)


def _clear_connections(egress: EgressRecorder, since: int) -> list[tuple[Any, ...]]:
    return [e for e in egress.events[since:] if e[0] == "mqtt_connect"]


class TestRetainedStatusClear:
    """The single exception after an opt-out: one bounded clear of the retained status."""

    @pytest.mark.asyncio
    async def test_cleared_at_opt_out_leaves_nothing_pending(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await update_community(enabled=False)
        mark = len(egress.events)

        assert _clear("CDG") in egress.events
        assert await _pending() == []
        await community_status_clear.start_status_clear_worker()
        await asyncio.sleep(0.2)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_unreachable_broker_gets_one_clear_and_nothing_after(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(0.3)
        mark = await _opt_out_with_broker_down(egress)
        assert _clear("CDG") not in egress.events
        # Persisted before anything else, so a restart cannot lose it.
        assert await _pending_topics() == [_status_topic("CDG")]

        egress.mqtt_connects = True  # the broker is back
        await _until_async(_nothing_pending)
        # One connection, the empty retained payload, DISCONNECT. Nothing else.
        assert egress.events[mark:] == [
            ("mqtt_connect", "mqtt.meshloom.app"),
            _clear("CDG"),
            ("mqtt_disconnect",),
        ]
        client = egress.mqtt_clients[-1]
        assert "will" not in client.kwargs
        assert client.kwargs["username"] == f"v1_{PUBKEY_HEX}"

        # Nothing after success, even with the worker started again.
        done = len(egress.events)
        await community_status_clear.start_status_clear_worker()
        await stats_json_forbidden()
        await fanout_manager.sync_system_modules()
        await subscribe_live()
        await asyncio.sleep(0.2)
        assert egress.events[done:] == []

    @pytest.mark.asyncio
    async def test_gives_up_after_the_last_attempt(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(0.0)
        mark = await _opt_out_with_broker_down(egress)
        await _until_async(_nothing_pending)

        attempts = _clear_connections(egress, mark)
        assert len(attempts) == community_status_clear.CLEAR_MAX_ATTEMPTS
        assert set(egress.hosts(mark)) == {"mqtt.meshloom.app"}
        assert not [e for e in egress.events[mark:] if e[0] == "mqtt_publish"]

        # Abandoned: nothing more, even after a restart.
        egress.mqtt_connects = True
        done = len(egress.events)
        await community_status_clear.start_status_clear_worker()
        await asyncio.sleep(0.2)
        assert egress.events[done:] == []

    @pytest.mark.asyncio
    async def test_gives_up_after_24_hours_without_connecting(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        mark = await _opt_out_with_broker_down(egress)
        await community_status_clear.stop_status_clear_worker()
        entries = await _pending()
        entries[0]["since"] -= community_status_clear.CLEAR_GIVE_UP_AFTER_S
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.set_community_status_clear(entries)

        egress.mqtt_connects = True
        await community_status_clear.start_status_clear_worker()
        await _until_async(_nothing_pending)
        await asyncio.sleep(0.2)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_a_pending_clear_survives_a_restart(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(3600.0)
        mark = await _opt_out_with_broker_down(egress)
        await community_status_clear.stop_status_clear_worker()  # shutdown
        assert await _pending_topics() == [_status_topic("CDG")]
        assert egress.events[mark:] == []

        # Restart, delay already elapsed: the lifespan resumes the clear.
        fast_retries(0.0)
        egress.mqtt_connects = True
        await community_status_clear.start_status_clear_worker()
        await _until_async(_nothing_pending)
        assert egress.events[mark:] == [
            ("mqtt_connect", "mqtt.meshloom.app"),
            _clear("CDG"),
            ("mqtt_disconnect",),
        ]

    @pytest.mark.asyncio
    async def test_waits_for_the_radio_key_without_spending_attempts(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(0.0)
        mark = await _opt_out_with_broker_down(egress)
        egress.mqtt_connects = True
        with patch("app.keystore.get_private_key", return_value=None):
            await asyncio.sleep(0.2)
            assert egress.events[mark:] == []
            assert (await _pending())[0]["attempts"] == 0
        await _until_async(_nothing_pending)
        assert _clear("CDG") in egress.events[mark:]

    @pytest.mark.asyncio
    async def test_old_iata_is_cleared_with_a_token_for_that_iata(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        from app.fanout import community_mqtt

        fast_retries(0.3)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        _stats_publisher().connected = False  # CDG could not be cleared
        await update_community(iata="LYS")
        await _until(lambda: _online_published(egress, "LYS"))
        assert _clear("CDG") not in egress.events
        assert await _pending_topics() == [_status_topic("CDG")]

        await _until_async(_nothing_pending)
        assert _clear("CDG") in egress.events
        assert _clear("LYS") not in egress.events
        token_iatas = [
            c.kwargs.get("iata") for c in community_mqtt._generate_jwt_token.call_args_list
        ]
        assert "CDG" in token_iatas
        # The LYS publisher is untouched: still one open connection.
        assert _open_mqtt_connections(egress) == 1

    @pytest.mark.asyncio
    async def test_opting_back_in_drops_the_pending_clear(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(3600.0)
        await _opt_out_with_broker_down(egress)
        assert await _pending_topics() == [_status_topic("CDG")]

        egress.mqtt_connects = True
        await update_community(enabled=True)
        await _until(lambda: sum(_status_of(e) == "online" for e in egress.events) >= 2)
        assert await _pending() == []
        fast_retries(0.0)
        await community_status_clear.start_status_clear_worker()
        await asyncio.sleep(0.2)
        assert _clear("CDG") not in egress.events


async def stats_json_forbidden() -> None:
    with pytest.raises(HTTPException):
        await stats_json("GET", "/v1/community/stats", auth=False)


async def _last_topic() -> str | None:
    last = await community_status_clear.last_published()
    return last.topic if last is not None else None


async def _stop_app() -> None:
    """A clean shutdown: the publisher says offline and disconnects."""
    await fanout_manager.remove_config(SYSTEM_MESHLOOM_STATS_ID)
    await community_status_clear.stop_status_clear_worker()


async def _restart_app() -> None:
    """The lifespan order: adopt or resume the status clear, then start fanout."""
    await community_status_clear.start_status_clear_worker()
    await fanout_manager.load_from_db()


class TestOrphanedStatusAtStartup:
    """A status topic left behind while the app was stopped is cleared at the next start."""

    @pytest.mark.asyncio
    async def test_the_publisher_remembers_its_topic(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))

        await update_community(iata="LYS")
        await _until(lambda: _online_published(egress, "LYS"))
        await _until_async(lambda: _is_last_topic("LYS"))

    @pytest.mark.asyncio
    async def test_restart_with_another_iata_clears_the_old_topic(
        self, test_db, egress, cleanup_community, fast_retries, monkeypatch
    ):
        fast_retries(0.0)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        await _stop_app()
        assert _clear("CDG") not in egress.events  # a plain stop leaves "offline"

        from app.repository.settings import AppSettingsRepository

        # Changed while stopped (database edit, restore, the one-shot env import).
        await AppSettingsRepository.update_community(iata="LYS")
        mark = len(egress.events)
        await _restart_app()
        await _until(lambda: _online_published(egress, "LYS"))
        await _until_async(_nothing_pending)

        assert _clear("CDG") in egress.events[mark:]
        assert _clear("LYS") not in egress.events
        assert await _last_topic() == _status_topic("LYS")
        from app.fanout import community_mqtt

        token_iatas = [
            c.kwargs.get("iata") for c in community_mqtt._generate_jwt_token.call_args_list
        ]
        assert "CDG" in token_iatas

    @pytest.mark.asyncio
    async def test_restart_clears_a_retired_custom_broker_once(
        self, test_db, egress, cleanup_community, fast_retries, monkeypatch
    ):
        """An old custom broker (the retired MESHLOOM_COMMUNITY_BROKER_HOST): the
        topic recorded there is cleared once at the first start on fixed addresses."""
        fast_retries(0.0)
        monkeypatch.setattr(meshloom_community, "DEFAULT_BROKER_HOST", "mqtt.old-custom.test")
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        await _stop_app()

        monkeypatch.setattr(meshloom_community, "DEFAULT_BROKER_HOST", "mqtt.meshloom.app")
        mark = len(egress.events)
        await _restart_app()
        await _until(lambda: _online_since(egress, "CDG", mark))
        await _until_async(_nothing_pending)
        connects = [e for e in egress.events[mark:] if e[0] == "mqtt_connect"]
        assert ("mqtt_connect", "mqtt.old-custom.test") in connects
        assert ("mqtt_connect", "mqtt.meshloom.app") in connects
        assert _clear("CDG") in egress.events[mark:]
        await _until_async(_last_on_fixed_broker)

        # Nothing more on the old broker afterwards.
        done = len(egress.events)
        await _stop_app()
        await _restart_app()
        await _until(lambda: _online_since(egress, "CDG", done))
        assert ("mqtt_connect", "mqtt.old-custom.test") not in egress.events[done:]

    @pytest.mark.asyncio
    async def test_restart_with_community_off_clears_the_old_topic(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        from app.repository.settings import AppSettingsRepository

        fast_retries(0.0)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        await _stop_app()
        # Turned off while stopped (database edit, restore, crash before the teardown).
        await AppSettingsRepository.update_community(enabled=False)

        mark = len(egress.events)
        await _restart_app()
        await _until_async(_nothing_pending)
        assert egress.events[mark:] == [
            ("mqtt_connect", "mqtt.meshloom.app"),
            _clear("CDG"),
            ("mqtt_disconnect",),
        ]
        assert await _last_topic() is None
        # Nothing ever again.
        done = len(egress.events)
        await _stop_app()
        await _restart_app()
        await asyncio.sleep(0.2)
        assert egress.events[done:] == []

    @pytest.mark.asyncio
    async def test_restart_on_the_same_topic_clears_nothing(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(0.0)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        await _stop_app()

        await _restart_app()
        await _until(lambda: sum(_status_of(e) == "online" for e in egress.events) >= 2)
        await asyncio.sleep(0.2)
        assert _clear("CDG") not in egress.events
        assert await _pending() == []

    @pytest.mark.asyncio
    async def test_a_cleared_opt_out_leaves_nothing_for_the_restart(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(0.0)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        await update_community(enabled=False)
        assert _clear("CDG") in egress.events
        assert await _last_topic() is None

        mark = len(egress.events)
        await _stop_app()
        await _restart_app()
        await asyncio.sleep(0.2)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_opt_out_without_the_radio_key_clears_the_remembered_topic(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(3600.0)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        egress.mqtt_connects = False
        _stats_publisher().connected = False
        with patch("app.keystore.get_public_key", return_value=None):
            await update_community(enabled=False)
        # The radio was away: the remembered topic stands in for its key.
        assert await _pending_topics() == [_status_topic("CDG")]
        assert await _last_topic() is None


async def _is_last_topic(iata: str) -> bool:
    return await _last_topic() == _status_topic(iata)


class TestPositionRebindOff:
    """A SELF_INFO with a new position re-binds only while Community is on."""

    @pytest.mark.asyncio
    async def test_self_info_change_while_off_connects_nowhere(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        from app.event_handlers import on_self_info
        from app.repository.settings import AppSettingsRepository
        from app.services import observer_position

        await AppSettingsRepository.update_community(enabled=False, iata="LYS")
        info = {
            "public_key": PUBKEY_HEX,
            "adv_lat": 45.764043,
            "adv_lon": 4.835659,
            "adv_loc_policy": 0,
        }
        monkeypatch.setattr(observer_position, "radio_self_info", lambda: info)
        monkeypatch.setattr(observer_position, "REBIND_DEBOUNCE_S", 0.01)

        await on_self_info(SimpleNamespace(payload=info))
        info = {**info, "adv_loc_policy": 1}
        await on_self_info(SimpleNamespace(payload=info))
        task = observer_position._rebind_task
        assert task is not None
        await asyncio.wait_for(task, 3)

        assert egress.events == []

    @pytest.mark.asyncio
    async def test_self_info_change_while_on_rebinds_the_same_iata(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        from app.services import observer_position

        await update_community(enabled=True, iata="LYS")
        await _until(lambda: _online_published(egress, "LYS"))
        egress.responses["/v1/me/iata"] = _bind_ok("LYS")
        info = {"public_key": PUBKEY_HEX, "adv_lat": 45.76, "adv_lon": 4.83, "adv_loc_policy": 0}
        monkeypatch.setattr(observer_position, "radio_self_info", lambda: info)
        monkeypatch.setattr(observer_position, "REBIND_DEBOUNCE_S", 0.01)
        since = len(egress.events)

        observer_position.note_radio_self_info(info)
        task = observer_position._rebind_task
        assert task is not None
        await asyncio.wait_for(task, 3)

        new = egress.events[since:]
        assert [e for e in new if e[0] == "http"] == [
            ("http", urlsplit(EGRESS_API_BASE).hostname, "/v1/me/iata")
        ]
        # Same IATA: the status topic does not move.
        assert _clear("LYS") not in egress.events
        assert (await get_community_effective()).iata == "LYS"
