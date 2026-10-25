"""Opted out of Community means no connection to Community, and none to an airport search.

Recorders replace ``httpx.AsyncClient``, ``websockets.connect`` and
``aiomqtt.Client`` for the whole test. With Community off, every route under
``/api/community``, ``/api/directory``, ``/api/tools``, ``/api/channels``,
``/api/settings`` and ``/api/updates`` is called (found dynamically from
``app.routes``), plus the hashtag catalogue and the update check with GitHub
answering 503. Any request to ``*.meshloom.app``,
``api.fx-port.com`` (the retired airport search) or the effective Community API
base fails the test. Placing an IATA code reads the bundled airport list.

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

``TestStoreEgressIsSeparate``: the Store (MSHL-194) is its own egress class. With
Community off and ``store_online`` on, exactly ``store.meshloom.app`` (plus the GitHub
hosts of the Store allowlist) may be reached, through ``store_fetch``; every other
``*.meshloom.app`` host is refused by the Store allowlist before any lookup. With
``store_online`` off, nothing goes there. The recorder reads the ``Host`` header, so a
request sent through a pinned client (connected to the IP) is still seen by name.

``TestPositionRebindOff``: the automatic re-bind on a radio position or policy
change sends nothing while Community is off.

``TestOptOutCall``: the opt-out first sends ``POST /v1/me/opt-out`` (while
nothing may reconnect); once Community confirmed it (any 2xx), or when the
call timed out, no status clear retry is armed. When the call fails, the opt-out is kept as pending and only
the explicit ``POST /api/community/opt-out/retry`` sends it again: nothing
else leaves, nothing retries on its own, not even after a restart.

``TestIataSuggestion``: the one request the settings make while Community is
off or waiting, ``GET /v1/iata/suggest`` when the user clicks "Suggest codes", with the
radio's position rounded to 2 decimals and no JWT; nothing without a position,
the bundled airport list when Community does not answer, and nothing else
leaves. The sweeps leave its route out (``_NOT_SWEPT``).

``TestWaitingForPosition``: Community on, but the radio has no position it may
publish. Nothing leaves but the MQTT status (``state: waiting_for_position``)
and the ``PUT /v1/me/iata`` binds; no packet, no HTTP, no Live. Both
transitions re-publish the status at once; a chosen position is bound and the
packets come back.
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
from app.plugins import store_egress
from app.plugins.store_egress import STORE_HOSTS, STORE_INDEX_HOST, StoreOffline, store_fetch
from app.services import community_status_clear, meshloom_community
from app.services.community_live import (
    get_live_relay,
    reset_community_live_for_tests,
    subscribe_live,
)
from app.services.hashtag_catalogue import run_catalogue_pass
from app.services.meshloom_community import (
    community_breaker,
    fetch_meshloom_latest,
    get_community_effective,
    lookup_airport,
    note_sample_quota,
    sample_quota_blocked,
    schedule_hashtag_names_publish,
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
# The airport search Meshloom once asked (removed): never contacted again.
FX_PORT_HOST = "api.fx-port.com"


def _forbidden(host: str | None, *, store_online: bool = False) -> bool:
    host = (host or "").lower()
    if store_online and host == STORE_INDEX_HOST:
        return False  # The Store's own egress class, opted into separately.
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
        # (path, JSON body) of every request that carried one.
        self.bodies: list[tuple[str, Any]] = []
        # Awaited while a request is out (before its answer).
        self.on_http: Any = None

    def hosts(self, since: int = 0) -> list[str]:
        out: list[str] = []
        for event in self.events[since:]:
            if event[0] in {"http", "ws_connect", "mqtt_connect"}:
                out.append(event[1])
        return out

    def forbidden(self, since: int = 0, *, store_online: bool = False) -> list[str]:
        return [h for h in self.hosts(since) if _forbidden(h, store_online=store_online)]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        # A pinned client connects to the IP and names the host in ``Host``.
        host = urlsplit("//" + request.headers.get("host", "")).hostname or request.url.host
        self.events.append(("http", host, request.url.path))
        if request.url.host == "github.com":
            return httpx.Response(503)
        if request.content:
            self.bodies.append((request.url.path, json.loads(request.content)))
        if self.on_http is not None:
            await self.on_http(request)
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
    "job_id": "unknown-job",
    "instance_id": "00000000-0000-4000-8000-000000000000",
}
_QUERY = {
    "/api/community/airports": {"q": "LYS"},
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
    ("PUT", "/api/plugins/pause"): {"paused": False},
    ("POST", "/api/plugins/install"): {
        "kind": "store",
        "plugin_id": "example.weather",
        "granted_permissions": {},
    },
    ("POST", "/api/plugins"): {"label": "Sweep", "template": "blank"},
    ("PATCH", "/api/plugins/{instance_id}"): {},
    ("PUT", "/api/plugins/{instance_id}/source"): {"source": "x = 1\n"},
    ("POST", "/api/plugins/{instance_id}/test"): {"text": "hello"},
    ("POST", "/api/settings/favorites/toggle"): {"type": "contact", "id": "ab" * 32},
    ("POST", "/api/settings/pins/toggle"): {"type": "contact", "id": "ab" * 32},
    ("POST", "/api/settings/muted-channels/toggle"): {"key": _PATH_PARAMS["key"]},
    ("POST", "/api/settings/blocked-keys/toggle"): {"key": "ab" * 32},
    ("POST", "/api/settings/blocked-names/toggle"): {"name": "Bob"},
    ("POST", "/api/settings/tracked-telemetry/toggle"): {"public_key": "ab" * 32},
    ("POST", "/api/settings/tracked-telemetry-contacts/toggle"): {"public_key": "ab" * 32},
    ("POST", "/api/settings/notification-destinations/test"): {"channel": "webhook"},
    ("POST", "/api/settings/backup/restore"): {"confirm": True},
    ("POST", "/api/settings/backup/export"): {},
    ("POST", "/api/settings/backup/inspect"): {
        "backup": {
            "format": "meshloom-backup-v1",
            "exported_at": 0,
            "contacts": [],
            "channels": [],
            "groups": [],
        }
    },
    ("PATCH", "/api/updates/settings"): {},
}
_SWEPT_PREFIXES = (
    "/api/community",
    "/api/directory",
    "/api/tools",
    "/api/channels",
    "/api/settings",
    "/api/updates",
    "/api/store",
    "/api/plugins",
)


# Local only, and needs the on-disk database the test fixture does not open.
# The IATA suggestion is the settings' explicit request while off or waiting
# (TestIataSuggestion covers it).
_NOT_SWEPT = {
    ("GET", "/api/settings/backup/database"),
    ("GET", "/api/community/iata/suggest"),
}


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
        # Left out on purpose: the IATA suggestion (TestIataSuggestion).
        assert ("GET", "/api/community/iata/suggest") not in routes
        from app.main import app

        assert "/api/community/iata/suggest" in {path for path, _ in _walk_routes(app.routes)}

    @pytest.mark.asyncio
    async def test_no_community_or_airport_egress_when_off(
        self, backup_db, egress, cleanup_community
    ):
        # backup_db: test_db also behind app.database.db (backup inspect and
        # restore read the mesh history through it).
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
        assert lookup_airport("LYS")  # bundled list: nothing leaves

        assert egress.forbidden() == [], egress.events
        assert set(egress.hosts()) <= {"github.com"}, egress.events
        assert ("http", "github.com", "/TwinRocket/meshloom/releases/latest") in egress.events

    async def test_full_backup_restore_job_makes_no_egress(
        self, backup_db, egress, cleanup_community
    ):
        """A v2 restore (meshloom_extra included) run to completion while opted out."""
        from app.main import app

        await update_community(enabled=False, iata="CDG")
        doc = {
            "format": "meshloom-backup-v2",
            "exported_at": 1,
            "contacts": [{"public_key": "ab" * 32, "name": "Alice"}],
            "channels": [{"key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72", "name": "Public"}],
            "meshloom_extra": {"path_hash_one_byte_opt_in": False},
        }
        async with _RealAsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/settings/backup/restore", json={"confirm": True, "backup": doc}
            )
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            state = "running"
            for _ in range(300):
                job = (await client.get(f"/api/settings/backup/restore/jobs/{job_id}")).json()
                state = job["state"]
                if state != "running":
                    break
                await asyncio.sleep(0.01)
            assert state == "done", job
        await asyncio.sleep(0.05)
        assert egress.forbidden() == [], egress.events
        assert egress.events == [], egress.events


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
        assert lookup_airport("LYS")  # bundled list: nothing leaves
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
        # Turning on is a join (bind first), never a PATCH either.
        response = await _patch_community({"enabled": True})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "join_requires_bind"
        await asyncio.sleep(0.05)
        assert egress.events == []
        state = await get_community_effective()
        assert (state.enabled, state.iata) == (False, "CDG")

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
        response = await _patch_community({"iata": "cdg"})
        assert response.status_code == 200, response.text
        assert egress.events == []
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        response = await _patch_community({"enabled": True, "iata": "CDG"})
        assert response.status_code == 200, response.text


class TestPatchNeverJoins:
    """Turning Community on is a join (PUT /community/me/iata, enable: true),
    which binds first. A PATCH would start the publisher before any bind."""

    @staticmethod
    def _refused(response: httpx.Response) -> None:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "join_requires_bind"

    @pytest.mark.asyncio
    async def test_off_to_on_is_refused_and_nothing_leaves(
        self, test_db, egress, cleanup_community
    ):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.update_community(enabled=False, iata="CDG")
        modules_before = dict(fanout_manager._modules)
        self._refused(await _patch_community({"enabled": True}))
        self._refused(await _patch_community({"enabled": True, "iata": "CDG"}))
        await asyncio.sleep(0.05)
        assert egress.events == []
        # No publisher was started (or reloaded) by the refused PATCH.
        assert fanout_manager._modules == modules_before
        state = await get_community_effective()
        assert (state.enabled, state.iata) == (False, "CDG")

    @pytest.mark.asyncio
    async def test_off_to_on_without_an_iata_is_refused(self, test_db, egress, cleanup_community):
        self._refused(await _patch_community({"enabled": True}))
        await asyncio.sleep(0.05)
        assert egress.events == []
        assert not (await get_community_effective()).enabled

    @pytest.mark.asyncio
    async def test_enabled_true_while_on_is_a_no_op(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        response = await _patch_community({"enabled": True})
        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is True


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
    async def test_restart_does_not_clear_the_same_broker_spelled_differently(
        self, test_db, egress, cleanup_community, fast_retries, monkeypatch
    ):
        """The official broker set with another case is the same broker: the live
        status topic must not get an empty retained publish."""
        fast_retries(0.0)
        monkeypatch.setattr(meshloom_community, "DEFAULT_BROKER_HOST", "MQTT.meshloom.app")
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        await _stop_app()

        monkeypatch.setattr(meshloom_community, "DEFAULT_BROKER_HOST", "mqtt.meshloom.app")
        mark = len(egress.events)
        await _restart_app()
        await _until(lambda: _online_since(egress, "CDG", mark))
        await _until_async(_nothing_pending)
        assert _clear("CDG") not in egress.events[mark:]

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


# A flood GRP_TXT (header 0x15, no path): decodable, so it would be published.
RAW_PACKET = {"data": "1500" + "AB" * 24, "snr": 5.0, "rssi": -90, "payload_type": "GROUP_TEXT"}
NO_POSITION = {"public_key": PUBKEY_HEX, "adv_lat": 0.0, "adv_lon": 0.0, "adv_loc_policy": 0}
WITH_POSITION = {"public_key": PUBKEY_HEX, "adv_lat": 45.76, "adv_lon": 4.83, "adv_loc_policy": 0}


def _packets_topic(iata: str) -> str:
    return f"meshcore/{iata}/{PUBKEY_HEX}/packets"


def _packets_published(recorder: EgressRecorder, iata: str, since: int = 0) -> int:
    return sum(
        1
        for e in recorder.events[since:]
        if e[0] == "mqtt_publish" and e[1] == _packets_topic(iata)
    )


def _status_payloads(recorder: EgressRecorder, iata: str, since: int = 0) -> list[dict[str, Any]]:
    return [
        json.loads(e[2])
        for e in recorder.events[since:]
        if e[0] == "mqtt_publish" and e[1] == _status_topic(iata) and isinstance(e[2], str)
    ]


def _waiting_status_since(recorder: EgressRecorder, iata: str, since: int = 0) -> bool:
    return any(
        p.get("status") == "online" and p.get("state") == "waiting_for_position"
        for p in _status_payloads(recorder, iata, since)
    )


def _active_status_since(recorder: EgressRecorder, iata: str, since: int = 0) -> bool:
    return any(
        p.get("status") == "online" and "state" not in p
        for p in _status_payloads(recorder, iata, since)
    )


async def _send_raw() -> None:
    entry = fanout_manager._modules.get(SYSTEM_MESHLOOM_STATS_ID)
    assert entry is not None
    await entry[0].on_raw(dict(RAW_PACKET))


async def _wait_state_applied() -> None:
    """Let the background transition (``_apply_position_state``) run to its end."""
    await asyncio.sleep(0.05)
    async with meshloom_community._update_lock:
        pass


class TestWaitingForPosition:
    """Owner decisions 2026-10-14: Q2 last known state kept, Q3 join without a position."""

    @pytest.mark.asyncio
    async def test_waiting_sends_only_the_status(self, backup_db, egress, cleanup_community):
        from app.main import app

        # Q3: Community turned on while the radio has no position.
        meshloom_community.note_radio_position(NO_POSITION)
        await _wait_state_applied()
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _waiting_status_since(egress, "CDG"))

        payload = _status_payloads(egress, "CDG")[-1]
        # Exactly ``status`` then ``state``, then the usual fields.
        assert list(payload)[:2] == ["status", "state"]
        assert payload["status"] == "online"
        assert payload["state"] == "waiting_for_position"
        assert "origin_id" in payload and "client_version" in payload
        status = await meshloom_community.community_status()
        assert (status.enabled, status.state) == (True, "waiting_for_position")

        mark = len(egress.events)
        async with _RealAsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for method, template in _swept_routes():
                if (method, template) == ("PUT", "/api/community/me/iata"):
                    continue  # a bind: allowed while waiting (test below)
                path = re.sub(r"\{(\w+)\}", lambda m: _PATH_PARAMS[m.group(1)], template)
                response = await client.request(
                    method,
                    path,
                    params=_QUERY.get(template),
                    json=_BODIES.get((method, template)),
                )
                assert response.status_code != 422, (method, template, response.text)
            response = await client.get("/api/settings")
            assert response.json()["directory_available"] is False

        await _store_unknown_group_text()
        await run_catalogue_pass()
        await schedule_hashtag_names_publish(["meshcore"])
        assert await upload_hashtag_sample("ab", "ab" * 40) is False
        await subscribe_live()
        await _send_raw()
        assert await refresh_oss_update_cache() is None
        assert await fetch_meshloom_latest() is None
        assert lookup_airport("LYS")  # bundled list: nothing leaves
        with pytest.raises(HTTPException) as exc:
            await stats_json("GET", "/v1/community/stats", auth=False)
        assert exc.value.status_code == 403
        await asyncio.sleep(0.05)

        new = egress.events[mark:]
        # No HTTP to Community, no Live socket, no packet.
        assert [e for e in new if e[0] == "http" and e[1] != "github.com"] == [], new
        assert [e for e in new if e[0] == "ws_connect"] == []
        assert get_live_relay().connected is False
        assert _packets_published(egress, "CDG") == 0
        # MQTT: the status topic only.
        assert {e[1] for e in egress.events if e[0] == "mqtt_publish"} == {_status_topic("CDG")}
        assert _open_mqtt_connections(egress) == 1

    @pytest.mark.asyncio
    async def test_unknown_radio_keeps_the_last_state(self, test_db, egress, cleanup_community):
        from app.services.radio_ingest_gate import allow_ingest, deny_ingest

        meshloom_community.note_radio_position(NO_POSITION)
        await update_community(enabled=True, iata="CDG")
        assert (await meshloom_community.community_status()).state == "waiting_for_position"

        # Q2: no radio, or an identity not accepted yet: no flicker.
        meshloom_community.note_radio_position(None)
        deny_ingest()
        try:
            meshloom_community.note_radio_position(WITH_POSITION)
        finally:
            allow_ingest()
        assert (await meshloom_community.community_status()).state == "waiting_for_position"

        # A position alone does not end the wait: the bind that carries it does.
        meshloom_community.note_radio_position(WITH_POSITION)
        assert (await meshloom_community.community_status()).state == "waiting_for_position"
        await _wait_state_applied()

    @pytest.mark.asyncio
    async def test_waiting_ends_only_once_the_position_is_bound(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        from app.services import observer_position

        meshloom_community.note_radio_position(NO_POSITION)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _waiting_status_since(egress, "CDG"))
        info = dict(WITH_POSITION)
        monkeypatch.setattr(observer_position, "radio_self_info", lambda: info)

        # Community refuses the bind (503): still waiting, no packet.
        egress.responses["/v1/me/iata"] = httpx.Response(503)
        with pytest.raises(HTTPException):
            await meshloom_community.rebind_current_iata(observer_position.put_iata_bind)
        assert meshloom_community.waiting_for_position() is True
        await _send_raw()
        assert _packets_published(egress, "CDG") == 0

        # Accepted with the position: active, and packets flow.
        meshloom_community.community_breaker().record_success()
        egress.responses["/v1/me/iata"] = _bind_ok("CDG")
        mark = len(egress.events)
        await meshloom_community.rebind_current_iata(observer_position.put_iata_bind)
        assert meshloom_community.waiting_for_position() is False
        await _until(lambda: _active_status_since(egress, "CDG", mark))
        await _send_raw()
        assert _packets_published(egress, "CDG", mark) == 1

    @pytest.mark.asyncio
    async def test_connect_without_position_waits_before_the_key_is_exported(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        """B1: the appstart SELF_INFO goes to the identity gate, not to the handlers."""
        from unittest.mock import MagicMock

        from app.services.radio_lifecycle import run_post_connect_setup

        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _active_status_since(egress, "CDG"))
        mark = len(egress.events)

        async def accept(_mc: Any) -> str:
            from app.services.radio_ingest_gate import allow_ingest, begin_connection_session

            allow_ingest(begin_connection_session())
            return "continue"

        monkeypatch.setattr("app.services.radio_identity.evaluate_connected_identity", accept)
        seen: dict[str, Any] = {}

        async def export(_mc: Any) -> None:
            # The radio key arrives here: from now on Community calls could leave.
            seen["waiting"] = meshloom_community.waiting_for_position()
            await _send_raw()
            with pytest.raises(HTTPException) as exc:
                await stats_json("GET", "/v1/me/stats", auth=True)
            seen["status"] = exc.value.status_code

        mc = MagicMock()
        mc.self_info = dict(NO_POSITION)
        radio_manager = MagicMock()
        radio_manager.meshcore = mc
        radio_manager._setup_lock = None
        radio_manager._setup_in_progress = False
        radio_manager._setup_complete = False
        radio_manager._acquire_operation_lock = AsyncMock()
        radio_manager._release_operation_lock = MagicMock()
        with (
            patch("app.event_handlers.register_event_handlers"),
            patch("app.keystore.export_and_store_private_key", new=export),
            # Stop right after the key, long before the end of the setup.
            patch(
                "app.radio_sync.sync_radio_time_outcome",
                new=AsyncMock(return_value="rebooted"),
            ),
        ):
            assert await run_post_connect_setup(radio_manager) == "reconnect"

        assert seen == {"waiting": True, "status": 403}
        await _until(lambda: _waiting_status_since(egress, "CDG", mark))
        new = egress.events[mark:]
        assert [e for e in new if e[0] in {"http", "ws_connect"}] == []
        assert _packets_published(egress, "CDG", mark) == 0

    @pytest.mark.asyncio
    async def test_the_waiting_state_survives_a_restart(self, test_db, egress, cleanup_community):
        """N1: unauthenticated calls stay cut for a radio known to be waiting."""
        from app.repository.settings import AppSettingsRepository
        from app.services.community_live import active_iata_codes

        await update_community(enabled=True, iata="CDG")
        meshloom_community.note_radio_position(NO_POSITION)
        await _wait_state_applied()
        assert await AppSettingsRepository.get_community_waiting_for_position() is True

        # A restart: the in-memory state is gone until the database is read.
        await fanout_manager.remove_config(SYSTEM_MESHLOOM_STATS_ID)
        meshloom_community.reset_stats_client_for_tests()
        assert meshloom_community.waiting_for_position() is False
        await meshloom_community.load_position_state()
        assert (await meshloom_community.community_status()).state == "waiting_for_position"

        mark = len(egress.events)
        with pytest.raises(HTTPException):
            await stats_json("GET", "/v1/community/stats", auth=False)
        with pytest.raises(HTTPException):
            await stats_json("GET", "/v1/hashtags", auth=False)
        assert await active_iata_codes() == {"available": False, "codes": []}
        assert await fetch_meshloom_latest() is None
        assert lookup_airport("LYS")  # bundled list: nothing leaves
        assert [e for e in egress.events[mark:] if e[0] == "http"] == []

        # Bound with a position later: saved as active.
        meshloom_community.note_position_bound()
        await _wait_state_applied()
        assert await AppSettingsRepository.get_community_waiting_for_position() is False

    @pytest.mark.asyncio
    async def test_bind_state_never_leaks_into_the_retire_or_the_restore(
        self, test_db, egress, cleanup_community
    ):
        """N3: only ``await bind()`` sees ``_bind_state``."""
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        seen: list[tuple[str, Any]] = []
        real_retire = meshloom_community._retire_status_and_reload
        real_restore = meshloom_community._restore_publisher

        async def retire(*args: Any, **kwargs: Any) -> None:
            seen.append(("retire", meshloom_community._bind_state.get()))
            await real_retire(*args, **kwargs)

        async def restore() -> None:
            seen.append(("restore", meshloom_community._bind_state.get()))
            await real_restore()

        async def bind() -> Any:
            seen.append(("bind", meshloom_community._bind_state.get()))
            raise HTTPException(status_code=503, detail="down")

        with (
            patch.object(meshloom_community, "_retire_status_and_reload", retire),
            patch.object(meshloom_community, "_restore_publisher", restore),
            pytest.raises(HTTPException),
        ):
            await meshloom_community.bind_community_iata("LYS", bind)

        assert [name for name, _ in seen] == ["retire", "bind", "restore"]
        assert seen[0][1] is None and seen[2][1] is None
        assert seen[1][1] is not None
        assert meshloom_community._bind_state.get() is None

    @pytest.mark.asyncio
    async def test_position_chosen_rebinds_and_packets_come_back(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        from app.services import observer_position

        meshloom_community.note_radio_position(NO_POSITION)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _waiting_status_since(egress, "CDG"))
        await subscribe_live()
        await _send_raw()
        assert _packets_published(egress, "CDG") == 0
        assert not any(e[0] == "ws_connect" for e in egress.events)

        egress.responses["/v1/me/iata"] = _bind_ok("CDG")
        info = dict(WITH_POSITION)
        monkeypatch.setattr(observer_position, "radio_self_info", lambda: info)
        monkeypatch.setattr(observer_position, "REBIND_DEBOUNCE_S", 0.01)
        mark = len(egress.events)

        # The operator chose a position: the radio's next appstart reports it.
        observer_position.note_radio_self_info(info)
        task = observer_position._rebind_task
        assert task is not None
        await asyncio.wait_for(task, 3)
        await _wait_state_applied()

        new = egress.events[mark:]
        bind = next(e for e in new if e[0] == "http" and e[1] != "github.com")
        assert bind[2] == "/v1/me/iata"
        # Re-published at once, without ``state``.
        assert _active_status_since(egress, "CDG", mark)
        assert (await meshloom_community.community_status()).state == "active"
        # Live reopens for its session, and packets flow again.
        await _until(lambda: get_live_relay().connected)
        await _send_raw()
        assert _packets_published(egress, "CDG", mark) == 1
        # The status topic never moved, and the publisher did not reconnect.
        assert _clear("CDG") not in egress.events
        assert _open_mqtt_connections(egress) == 1

    @pytest.mark.asyncio
    async def test_position_lost_switches_to_waiting(self, test_db, egress, cleanup_community):
        meshloom_community.note_radio_position(WITH_POSITION)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _active_status_since(egress, "CDG"))
        await subscribe_live()
        await _until(lambda: get_live_relay().connected)
        socket = egress.sockets[-1]
        await _send_raw()
        assert _packets_published(egress, "CDG") == 1

        mark = len(egress.events)
        meshloom_community.note_radio_position(NO_POSITION)
        await _until(lambda: _waiting_status_since(egress, "CDG", mark))
        await _wait_state_applied()

        assert socket.closed.is_set()
        assert get_live_relay().connected is False
        assert (await meshloom_community.community_status()).state == "waiting_for_position"
        await _send_raw()
        with pytest.raises(HTTPException):
            await stats_json("GET", "/v1/community/stats", auth=False)
        await subscribe_live()
        await asyncio.sleep(0.05)
        new = egress.events[mark:]
        assert [e for e in new if e[0] in {"http", "ws_connect", "mqtt_connect"}] == []
        assert _packets_published(egress, "CDG", mark) == 0
        assert {e[1] for e in new if e[0] == "mqtt_publish"} == {_status_topic("CDG")}

    @pytest.mark.asyncio
    async def test_iata_change_binds_while_waiting(self, test_db, egress, cleanup_community):
        meshloom_community.note_radio_position(NO_POSITION)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _waiting_status_since(egress, "CDG"))
        egress.responses["/v1/me/iata"] = _bind_ok("LYS")

        response = await _put_me_iata("LYS")
        assert response.status_code == 200, response.text
        await _until(lambda: _waiting_status_since(egress, "LYS"))
        assert [e[2] for e in egress.events if e[0] == "http"] == ["/v1/me/iata"]
        assert _clear("CDG") in egress.events

    @pytest.mark.asyncio
    async def test_opt_out_while_waiting_tears_down(self, test_db, egress, cleanup_community):
        meshloom_community.note_radio_position(NO_POSITION)
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _waiting_status_since(egress, "CDG"))

        await update_community(enabled=False)
        assert _clear("CDG") in egress.events
        assert _open_mqtt_connections(egress) == 0
        assert (await meshloom_community.community_status()).state == "off"

    @pytest.mark.asyncio
    async def test_user_mqtt_community_rows_never_carry_the_state(self, test_db):
        from app.fanout.community_mqtt import CommunityMqttPublisher

        meshloom_community.note_radio_position(NO_POSITION)
        assert CommunityMqttPublisher()._status_extra() == {}
        assert MeshloomStatsPublisher()._status_extra() == {"state": "waiting_for_position"}
        await _wait_state_applied()


OPT_OUT_OK = {
    "opted_out_at": "2026-10-14T10:00:00Z",
    "positions_deleted": True,
    "mqtt": {"kicked": True, "retained_cleared": True},
    "later_field": "ignored",
}


def _opt_out_answer(body: Any = None, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=OPT_OUT_OK if body is None else body)


def _opt_out_calls(recorder: EgressRecorder, since: int = 0) -> int:
    return sum(1 for e in recorder.events[since:] if e[0] == "http" and e[2] == "/v1/me/opt-out")


async def _opt_out_pending() -> dict[str, Any] | None:
    from app.repository.settings import AppSettingsRepository

    return await AppSettingsRepository.get_community_opt_out_pending()


async def _api(method: str, path: str, body: Any = None) -> httpx.Response:
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, json=body)


def _status_clear_running() -> bool:
    task = community_status_clear._worker_task
    return task is not None and not task.done()


class TestOptOutCall:
    """``POST /v1/me/opt-out``: the last call, and its manual retry."""

    @pytest.mark.asyncio
    async def test_confirmed_opt_out_arms_no_status_clear(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        # The broker already refuses the key (Community kicked it): the
        # publisher cannot clear anything itself.
        egress.mqtt_connects = False
        _stats_publisher().connected = False

        response = await _api(
            "PATCH", "/api/community", {"enabled": False, "opt_out_reason": "privacy"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["opt_out_pending"] is None
        mark = len(egress.events)

        assert _opt_out_calls(egress) == 1
        assert ("/v1/me/opt-out", {"reason": "privacy"}) in egress.bodies
        # Community cleared the retained status: nothing pending, no worker,
        # and no topic left for the next start.
        assert await _pending() == []
        assert not _status_clear_running()
        assert await _last_topic() is None
        assert await _opt_out_pending() is None
        await _stop_app()
        await _restart_app()
        await asyncio.sleep(0.2)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_opt_out_without_reason_sends_an_empty_body(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        await update_community(enabled=False)
        assert ("/v1/me/opt-out", {}) in egress.bodies
        assert await _opt_out_pending() is None

    @pytest.mark.asyncio
    async def test_opt_out_still_leaves_while_banned(self, test_db, egress, cleanup_community):
        """#111: a banned key may still opt out; the ban state never holds it back."""
        from app.services.community_ban import note_banned

        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        note_banned()
        await update_community(enabled=False)
        assert _opt_out_calls(egress) == 1
        assert await _opt_out_pending() is None

    @pytest.mark.asyncio
    async def test_any_confirmation_arms_no_status_clear(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(0.0)
        # A 2xx confirms, whatever the body says: Community refuses the key
        # on the broker seconds later and clears the status itself.
        egress.responses["/v1/me/opt-out"] = _opt_out_answer(
            {"mqtt": {"kicked": True, "retained_cleared": False}}
        )
        mark = await _opt_out_with_broker_down(egress)
        assert await _opt_out_pending() is None
        assert await _pending() == []
        assert not _status_clear_running()
        egress.mqtt_connects = True
        await asyncio.sleep(0.2)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_timed_out_call_arms_no_status_clear_and_stays_pending(
        self, test_db, egress, cleanup_community, fast_retries, monkeypatch
    ):
        fast_retries(0.0)
        monkeypatch.setattr(meshloom_community, "OPT_OUT_LAST_CALL_TIMEOUT_S", 0.1)

        async def slow(request: httpx.Request) -> None:
            if request.url.path == "/v1/me/opt-out":
                await asyncio.sleep(1.0)

        egress.on_http = slow
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        mark = await _opt_out_with_broker_down(egress)
        # Community may have recorded it: no clear retry, the manual retry decides.
        assert await _opt_out_pending() is not None
        assert await _pending() == []
        assert not _status_clear_running()
        egress.mqtt_connects = True
        await asyncio.sleep(0.2)
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_connection_that_never_opens_fails_and_arms_the_status_clear(
        self, test_db, egress, cleanup_community, fast_retries, monkeypatch
    ):
        fast_retries(3600.0)
        real = meshloom_community.stats_request
        seen: list[Any] = []

        async def spy(*args: Any, **kwargs: Any) -> httpx.Response:
            seen.append(kwargs["timeout"])
            return await real(*args, **kwargs)

        async def blackholed(request: httpx.Request) -> None:
            if request.url.path == "/v1/me/opt-out":
                raise httpx.ConnectTimeout("connect timed out", request=request)

        monkeypatch.setattr(meshloom_community, "stats_request", spy)
        egress.on_http = blackholed
        await _opt_out_with_broker_down(egress)
        # The connect timeout ends before the 5 s bound: the request surely
        # never left, so this is a plain failure and the clear retry is armed.
        timeout = seen[0]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect is not None
        assert timeout.connect < meshloom_community.OPT_OUT_LAST_CALL_TIMEOUT_S
        assert timeout.read is not None
        assert timeout.read > meshloom_community.OPT_OUT_LAST_CALL_TIMEOUT_S
        assert await _opt_out_pending() is not None
        assert await _pending_topics() == [_status_topic("CDG")]

    @pytest.mark.asyncio
    async def test_refused_call_still_arms_the_status_clear(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(3600.0)
        # 503 (the default): Community surely did not record it.
        await _opt_out_with_broker_down(egress)
        assert await _opt_out_pending() is not None
        assert await _pending_topics() == [_status_topic("CDG")]

    @pytest.mark.asyncio
    async def test_radio_away_records_the_published_key(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await _until_async(lambda: _is_last_topic("CDG"))
        with patch("app.keystore.get_public_key", return_value=None):
            await update_community(enabled=False)
        pending = await _opt_out_pending()
        assert pending is not None and pending["pubkey"] == PUBKEY_HEX

    @pytest.mark.asyncio
    async def test_retry_refuses_an_unknown_key(self, test_db, egress, cleanup_community):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.set_community_opt_out_pending(
            {"pubkey": "", "reason": None, "requested_at": "2026-10-14T10:00:00+00:00"}
        )
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        mark = len(egress.events)
        response = await _api("POST", "/api/community/opt-out/retry")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "opt_out_key_unknown"
        assert egress.events[mark:] == []
        assert await _opt_out_pending() is not None

    @pytest.mark.asyncio
    async def test_retry_waits_longer_than_the_last_call(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.set_community_opt_out_pending(
            {"pubkey": PUBKEY_HEX, "reason": "other", "requested_at": "2026-10-14T10:00:00+00:00"}
        )
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        real = meshloom_community.stats_request
        seen: list[float] = []

        async def spy(*args: Any, **kwargs: Any) -> httpx.Response:
            seen.append(kwargs["timeout"])
            return await real(*args, **kwargs)

        monkeypatch.setattr(meshloom_community, "stats_request", spy)
        response = await _api("POST", "/api/community/opt-out/retry")
        assert response.status_code == 200, response.text
        assert seen == [meshloom_community.OPT_OUT_RETRY_HTTP_TIMEOUT]
        assert seen[0].read >= 20 and seen[0].connect <= 3
        assert ("/v1/me/opt-out", {"reason": "other"}) in egress.bodies

    @pytest.mark.asyncio
    async def test_turning_back_on_is_a_join_that_binds_first(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        from app.services import observer_position

        info = {"public_key": PUBKEY_HEX, "adv_lat": 45.76, "adv_lon": 4.84, "adv_loc_policy": 0}
        monkeypatch.setattr(observer_position, "REBIND_DEBOUNCE_S", 0.01)
        monkeypatch.setattr(observer_position, "radio_self_info", lambda: info)
        monkeypatch.setattr("app.services.radio_ingest_gate.ingest_allowed", lambda: True)
        egress.responses["/v1/me/iata"] = _bind_ok("CDG")
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        await update_community(enabled=True, iata="CDG")
        await observer_position.put_iata_bind("CDG")
        binds = sum(
            1 for e in egress.events if e[:3] == ("http", "stats.egress-check.net", "/v1/me/iata")
        )
        assert observer_position._last_bound is not observer_position._UNKNOWN
        await update_community(enabled=False)
        # The teardown forgot the last bind: nothing takes the key as bound.
        assert observer_position._last_bound is observer_position._UNKNOWN

        # A PATCH cannot turn Community back on: it would connect before the
        # PUT /v1/me/iata that lifts Community's refusal.
        response = await _api("PATCH", "/api/community", {"enabled": True})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "join_requires_bind"
        assert not (await get_community_effective()).enabled

        # The join binds first, even with the position unchanged.
        response = await _put_me_iata("CDG", enable=True)
        assert response.status_code == 200, response.text
        after = sum(
            1 for e in egress.events if e[:3] == ("http", "stats.egress-check.net", "/v1/me/iata")
        )
        assert after == binds + 1
        assert (await get_community_effective()).enabled
        observer_position.reset_position_rebind_for_tests()

    @pytest.mark.asyncio
    async def test_nothing_reconnects_while_leaving(self, test_db, egress, cleanup_community):
        from app.services.meshloom_community import community_enrolled_state

        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        publisher = _stats_publisher()
        seen: dict[str, Any] = {}

        async def while_out(request: httpx.Request) -> None:
            if request.url.path != "/v1/me/opt-out":
                return
            # Community kicks the session while it answers.
            seen["enrolled"] = await community_enrolled_state()
            seen["reconnect"] = await publisher._pre_connect(publisher._settings)
            seen["toast"] = publisher._error_toast_allowed()
            seen["egress"] = await meshloom_community.community_egress_state()

        egress.on_http = while_out
        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        await update_community(enabled=False)

        assert seen == {"enrolled": None, "reconnect": False, "toast": False, "egress": None}
        assert meshloom_community.opt_out_in_progress() is False

    @pytest.mark.asyncio
    async def test_failed_call_leaves_anyway_and_keeps_it_pending(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        # Community answers 503 (the default): the local exit still happens.
        response = await _api(
            "PATCH", "/api/community", {"enabled": False, "opt_out_reason": "other"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is False
        assert body["opt_out_pending"]["reason"] == "other"
        assert body["opt_out_pending"]["requested_at"]
        assert _clear("CDG") in egress.events
        pending = await _opt_out_pending()
        assert pending is not None and pending["pubkey"] == PUBKEY_HEX

        # Nothing retries on its own, not even after a restart.
        mark = len(egress.events)
        await _stop_app()
        await _restart_app()
        await asyncio.sleep(0.2)
        assert egress.events[mark:] == []
        status = (await _api("GET", "/api/community")).json()
        assert status["opt_out_pending"]["reason"] == "other"
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_only_the_explicit_retry_leaves_while_off(
        self, test_db, egress, cleanup_community, fast_retries
    ):
        fast_retries(3600.0)
        await _opt_out_with_broker_down(egress)
        assert await _opt_out_pending() is not None
        assert await _pending_topics() == [_status_topic("CDG")]

        # Everything else stays cut while the opt-out is pending.
        mark = len(egress.events)
        await stats_json_forbidden()
        assert await fetch_meshloom_latest() is None
        assert lookup_airport("LYS")  # bundled list: nothing leaves
        await subscribe_live()
        await fanout_manager.sync_system_modules()
        await asyncio.sleep(0.05)
        assert egress.events[mark:] == []

        # A failed retry keeps it pending.
        response = await _api("POST", "/api/community/opt-out/retry")
        assert response.status_code == 503, response.text
        assert await _opt_out_pending() is not None

        egress.responses["/v1/me/opt-out"] = _opt_out_answer()
        response = await _api("POST", "/api/community/opt-out/retry")
        assert response.status_code == 200, response.text
        assert response.json()["opt_out_pending"] is None
        assert response.json()["enabled"] is False
        # Exactly the two retries, both POST /v1/me/opt-out, nothing else.
        assert egress.events[mark:] == [
            ("http", "stats.egress-check.net", "/v1/me/opt-out"),
            ("http", "stats.egress-check.net", "/v1/me/opt-out"),
        ]
        assert await _opt_out_pending() is None
        # Community cleared the retained status: the bounded clear is dropped.
        assert await _pending() == []

        # Nothing is pending any more: a retry sends nothing.
        done = len(egress.events)
        response = await _api("POST", "/api/community/opt-out/retry")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "no_pending_opt_out"
        assert egress.events[done:] == []

    @pytest.mark.asyncio
    async def test_retry_needs_the_radio_that_left(self, test_db, egress, cleanup_community):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await update_community(enabled=False)
        assert await _opt_out_pending() is not None
        mark = len(egress.events)
        with patch("app.keystore.get_public_key", return_value=b"\x07" * 32):
            response = await _api("POST", "/api/community/opt-out/retry")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "opt_out_other_radio"
        assert egress.events[mark:] == []

    @pytest.mark.asyncio
    async def test_joining_again_drops_the_pending_opt_out(
        self, test_db, egress, cleanup_community
    ):
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _online_published(egress, "CDG"))
        await update_community(enabled=False)
        assert await _opt_out_pending() is not None

        egress.responses["/v1/me/iata"] = _bind_ok("CDG")
        response = await _put_me_iata("CDG", enable=True)
        assert response.status_code == 200, response.text
        assert await _opt_out_pending() is None
        status = (await _api("GET", "/api/community")).json()
        assert status["enabled"] is True and status["opt_out_pending"] is None


# Lyon city centre, more decimals than Community may get.
SUGGEST_POSITION = {
    "public_key": PUBKEY_HEX,
    "adv_lat": 45.764043,
    "adv_lon": 4.835659,
    "adv_loc_policy": 1,  # even a broadcast position goes out rounded
}


def _suggest_ok(neighbors: str | None, nearest: str | None) -> httpx.Response:
    body = {"neighbors": neighbors, "nearest_airport": nearest}
    return httpx.Response(200, content=json.dumps(body).encode())


class TestIataSuggestion:
    """``GET /api/community/iata/suggest``: the one request while off or waiting.

    Owner decision 2026-10-14: nothing is assigned; the IATA form proposes the
    neighbours' code and the nearest airport. Community's contract: identity.md,
    § IATA suggestion (no JWT, position rounded to 2 decimals by the client).
    """

    @staticmethod
    def _capture(egress: EgressRecorder) -> list[httpx.Request]:
        seen: list[httpx.Request] = []

        async def on_http(request: httpx.Request) -> None:
            seen.append(request)

        egress.on_http = on_http
        return seen

    @staticmethod
    async def _suggest(info: Any = SUGGEST_POSITION) -> httpx.Response:
        with patch("app.services.observer_position.radio_self_info", return_value=info):
            return await _api("GET", "/api/community/iata/suggest")

    @pytest.mark.asyncio
    async def test_off_sends_the_rounded_position_once(self, test_db, egress, cleanup_community):
        await update_community(enabled=False, iata="CDG")
        seen = self._capture(egress)
        egress.responses["/v1/iata/suggest"] = _suggest_ok("LYS", "LYN")

        response = await self._suggest()

        assert response.status_code == 200, response.text
        assert response.json() == {
            "neighbors": "LYS",
            "nearest_airport": "LYN",
            "source": "community",
        }
        # One request, to Community's suggestion route, nothing else at all.
        assert egress.events == [("http", "stats.egress-check.net", "/v1/iata/suggest")]
        (request,) = seen
        assert request.method == "GET"
        assert dict(request.url.params) == {"lat": "45.76", "lon": "4.84"}
        assert "authorization" not in request.headers
        assert request.content == b""
        # Still off, and the exception did not leak: everything else stays cut.
        assert (await get_community_effective()).enabled is False
        with pytest.raises(HTTPException) as exc:
            await stats_json("GET", "/v1/community/stats", auth=False)
        assert exc.value.status_code == 403
        assert len(egress.events) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("info", "lat", "lon"),
        [
            ({"adv_lat": -33.868820, "adv_lon": 151.209295}, "-33.87", "151.21"),
            ({"adv_lat": 48.8566, "adv_lon": 2.3522, "adv_loc_policy": 0}, "48.86", "2.35"),
            ({"adv_lat": 64.1, "adv_lon": -21.9}, "64.10", "-21.90"),
        ],
    )
    async def test_position_is_always_two_decimals(
        self, test_db, egress, cleanup_community, info, lat, lon
    ):
        seen = self._capture(egress)
        egress.responses["/v1/iata/suggest"] = _suggest_ok(None, "CDG")
        response = await self._suggest(info)
        assert response.status_code == 200
        assert dict(seen[0].url.params) == {"lat": lat, "lon": lon}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "info",
        [
            None,
            {"public_key": PUBKEY_HEX},
            {"adv_lat": 0.0, "adv_lon": 0.0},
            {"adv_lat": 0.004, "adv_lon": -0.003},  # rounds onto "no GPS"
            {"adv_lat": float("nan"), "adv_lon": 4.8},
            {"adv_lat": 95.0, "adv_lon": 4.8},
            {"adv_lat": True, "adv_lon": 4.8},
        ],
    )
    async def test_no_position_sends_nothing(self, test_db, egress, cleanup_community, info):
        response = await self._suggest(info)
        assert response.status_code == 200
        assert response.json() == {"neighbors": None, "nearest_airport": None, "source": None}
        assert egress.events == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answer",
        [
            httpx.Response(429, content=b'{"detail": "public stats rate limit"}'),
            httpx.Response(503),
            httpx.Response(400),
            httpx.Response(200, content=b'{"neighbors": "lyon", "nearest_airport": "LYS"}'),
            httpx.Response(200, content=b"not json"),
        ],
    )
    async def test_failure_falls_back_to_the_bundled_list(
        self, test_db, egress, cleanup_community, answer
    ):
        egress.responses["/v1/iata/suggest"] = answer
        response = await self._suggest()
        assert response.status_code == 200
        # Lyon-Bron is nearer the city centre than Saint-Exupéry.
        assert response.json() == {"neighbors": None, "nearest_airport": "LYN", "source": "local"}
        assert egress.hosts() == ["stats.egress-check.net"]

    @pytest.mark.asyncio
    async def test_unreachable_falls_back_and_never_feeds_the_breaker(
        self, test_db, egress, cleanup_community
    ):
        async def refuse(_request: httpx.Request) -> None:
            raise httpx.ConnectError("offline")

        breaker = community_breaker()
        egress.on_http = refuse
        for _ in range(breaker.threshold + 1):
            response = await self._suggest()
            assert response.json() == {
                "neighbors": None,
                "nearest_airport": "LYN",
                "source": "local",
            }
        # Failures of the public suggestion never open the breaker...
        assert breaker.failures == 0
        assert not breaker.is_open
        egress.on_http = None
        egress.responses["/v1/iata/suggest"] = httpx.Response(503)
        await self._suggest()
        assert breaker.failures == 0

    @pytest.mark.asyncio
    async def test_never_spends_the_half_open_trial(self, test_db, egress, cleanup_community):
        breaker = community_breaker()
        for _ in range(breaker.threshold):
            breaker.record_failure()
        assert breaker.is_open
        egress.responses["/v1/iata/suggest"] = _suggest_ok("LYS", "LYN")
        response = await self._suggest()
        # ...and an open breaker neither blocks it nor is touched by it.
        assert response.json()["source"] == "community"
        assert breaker.is_open
        assert breaker.trial_in_flight is False
        assert breaker.failures == breaker.threshold

    @pytest.mark.asyncio
    async def test_an_opt_out_started_meanwhile_stops_it(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        """``_leaving`` is read again after the awaits, right before the request."""
        real = meshloom_community.get_community_effective

        async def opt_out_starts_meanwhile():
            state = await real()
            monkeypatch.setattr(meshloom_community, "_leaving", True)
            return state

        monkeypatch.setattr(meshloom_community, "get_community_effective", opt_out_starts_meanwhile)
        egress.responses["/v1/iata/suggest"] = _suggest_ok("LYS", "LYN")
        response = await self._suggest()
        assert response.json() == {"neighbors": None, "nearest_airport": "LYN", "source": "local"}
        assert egress.events == []

    @pytest.mark.asyncio
    async def test_a_null_nearest_airport_is_completed_locally(
        self, test_db, egress, cleanup_community
    ):
        egress.responses["/v1/iata/suggest"] = _suggest_ok("LYS", None)
        response = await self._suggest()
        assert response.json() == {
            "neighbors": "LYS",
            "nearest_airport": "LYN",
            "source": "community",
        }

    @pytest.mark.asyncio
    async def test_waiting_for_a_position_may_ask(self, test_db, egress, cleanup_community):
        meshloom_community.note_radio_position(NO_POSITION)
        await _wait_state_applied()
        await update_community(enabled=True, iata="CDG")
        await _until(lambda: _waiting_status_since(egress, "CDG"))
        mark = len(egress.events)
        egress.responses["/v1/iata/suggest"] = _suggest_ok("LYS", "LYN")

        response = await self._suggest()

        assert response.json()["neighbors"] == "LYS"
        assert egress.events[mark:] == [("http", "stats.egress-check.net", "/v1/iata/suggest")]
        assert (await meshloom_community.community_status()).state == "waiting_for_position"

    @pytest.mark.asyncio
    async def test_nothing_leaves_during_an_opt_out(
        self, test_db, egress, cleanup_community, monkeypatch
    ):
        monkeypatch.setattr(meshloom_community, "_leaving", True)
        response = await self._suggest()
        assert response.json() == {"neighbors": None, "nearest_airport": "LYN", "source": "local"}
        assert egress.events == []

    @pytest.mark.asyncio
    async def test_only_the_route_asks(self, test_db, egress, cleanup_community):
        """Nothing in the backend asks a suggestion on its own: off, a restart,
        the radio's position changing and the background passes send nothing."""
        await update_community(enabled=False, iata="CDG")
        with patch("app.services.observer_position.radio_self_info", return_value=SUGGEST_POSITION):
            meshloom_community.note_radio_position(SUGGEST_POSITION)
            await _wait_state_applied()
            await fanout_manager.load_from_db()
            await run_catalogue_pass()
            await subscribe_live()
            await asyncio.sleep(0.05)
        assert [e for e in egress.events if e[0] == "http" and e[2] == "/v1/iata/suggest"] == []
        assert egress.forbidden() == []


class TestStoreEgressIsSeparate:
    """Community off: ``store.meshloom.app`` is reachable exactly when ``store_online``."""

    @pytest.fixture(autouse=True)
    async def _store_state(self):
        await store_egress.reset_store_egress_for_tests()
        yield
        await store_egress.reset_store_egress_for_tests()

    @pytest.fixture
    def store_dns(self, monkeypatch):
        from tests.share_fakes import PUBLIC_IP, Dns

        hosts = (*STORE_HOSTS, "api.meshloom.app", "get.meshloom.app")
        return Dns(monkeypatch, {host: [PUBLIC_IP] for host in hosts})

    @pytest.mark.asyncio
    async def test_store_host_allowed_only_with_store_online(
        self, test_db, egress, cleanup_community, store_dns
    ):
        from app.net.egress_guard import EgressBlocked
        from app.repository import AppSettingsRepository
        from tests.share_fakes import _Stream

        record = egress.handle

        async def streamed(request: httpx.Request) -> httpx.Response:
            # The egress guard reads the body raw: stream it like a socket would.
            response = await record(request)
            return httpx.Response(response.status_code, stream=_Stream(response.content))

        egress.handle = streamed
        await update_community(enabled=False, iata="CDG")
        url = f"https://{STORE_INDEX_HOST}/v1/index.json"

        # Off (the default): refused before any lookup, nothing recorded.
        with pytest.raises(StoreOffline):
            await store_fetch("GET", url, max_response_bytes=4096)
        assert egress.hosts() == [] and store_dns.lookups == []

        await AppSettingsRepository.update(store_online=True)
        await store_fetch("GET", url, max_response_bytes=4096)
        assert egress.hosts() == [STORE_INDEX_HOST]
        assert egress.forbidden() == [STORE_INDEX_HOST]
        assert egress.forbidden(store_online=True) == []

        # Any other meshloom.app host stays out of the Store allowlist.
        for other in ("api.meshloom.app", "get.meshloom.app"):
            with pytest.raises(EgressBlocked) as info:
                await store_fetch("GET", f"https://{other}/", max_response_bytes=4096)
            assert info.value.code == "egress_host_not_allowed"
        assert egress.hosts() == [STORE_INDEX_HOST]

        # Off again: nothing more leaves.
        await AppSettingsRepository.update(store_online=False)
        with pytest.raises(StoreOffline):
            await store_fetch("GET", url, max_response_bytes=4096)
        assert egress.hosts() == [STORE_INDEX_HOST]
        assert (await get_community_effective()).enabled is False
