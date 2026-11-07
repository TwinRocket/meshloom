"""Update badge: Community-only release check, auto-update hold, SemVer, GET /api/updates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import meshloom_community
from app.services.meshloom_community import (
    DEFAULT_API_BASE,
    CommunityEffective,
    fetch_meshloom_latest,
)
from app.services.oss_updates import (
    UPDATE_POLL_INTERVAL_SECONDS,
    fetch_latest_release,
    get_update_status,
    is_newer_release,
    is_unknown_local_version,
    refresh_oss_update_cache,
    reset_oss_update_cache,
)
from app.version_info import AppBuildInfo

_STATS_PAYLOAD = {
    "version": "9.9.9",
    "tag": "9.9.9",
    "released_at": "2026-01-01T00:00:00Z",
    "html_url": "https://github.com/TwinRocket/meshloom/releases/tag/9.9.9",
}

_OPTED_OUT = CommunityEffective(
    enabled=False,
    iata="",
    broker_host="mqtt.meshloom.app",
    api_base=DEFAULT_API_BASE,
    api_audience="api.meshloom.app",
    mqtt_audience="mqtt.meshloom.app",
)


_OPTED_IN = CommunityEffective(
    enabled=True,
    iata="CDG",
    broker_host="mqtt.meshloom.app",
    api_base=DEFAULT_API_BASE,
    api_audience="api.meshloom.app",
    mqtt_audience="mqtt.meshloom.app",
)


def _build(version: str) -> AppBuildInfo:
    return AppBuildInfo(
        version=version, version_source="test", commit_hash=None, commit_source=None
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_oss_update_cache()
    yield
    reset_oss_update_cache()


def _community(handler):
    """Patch Community's HTTP client with a transport answering through ``handler``."""
    real = httpx.AsyncClient

    def make(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch("app.services.meshloom_community.httpx.AsyncClient", make)


def _answer(body=_STATS_PAYLOAD, *, status_code=200):
    return lambda _request: httpx.Response(status_code, json=body)


class TestFetchMeshloomLatest:
    @pytest.mark.parametrize(
        "effective",
        [_OPTED_OUT, _OPTED_IN],
        ids=["opted-out", "opted-in"],
    )
    @pytest.mark.asyncio
    async def test_plain_get_without_jwt_whatever_the_community_state(self, effective):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_STATS_PAYLOAD)

        read_state = AsyncMock(return_value=effective)
        mint = MagicMock()
        with (
            patch("app.services.meshloom_community.get_community_effective", new=read_state),
            _community(handler),
            patch("app.services.meshloom_community.mint_stats_jwt", new=mint),
            patch("app.services.meshloom_community.stats_request", new=AsyncMock()) as stats_req,
        ):
            payload = await fetch_meshloom_latest()

        assert payload == _STATS_PAYLOAD
        assert len(seen) == 1
        request = seen[0]
        assert request.method == "GET"
        assert str(request.url) == f"{DEFAULT_API_BASE}/v1/meshloom/latest"
        assert request.content == b""
        # Only transport headers, and nothing with a version in it.
        assert set(request.headers.keys()) == {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
        }
        assert request.headers["user-agent"] == "Meshloom"
        assert request.headers["accept-encoding"] == "identity"
        mint.assert_not_called()
        stats_req.assert_not_called()
        read_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_open_breaker_does_not_stop_it_and_it_does_not_feed_the_breaker(self):
        from app.services.meshloom_community import community_breaker

        breaker = community_breaker()
        for _ in range(breaker.threshold):
            breaker.record_failure()
        with _community(_answer()):
            assert await fetch_meshloom_latest() == _STATS_PAYLOAD
        assert breaker.failures == breaker.threshold

        breaker.record_success()
        with _community(_answer(status_code=503)):
            assert await fetch_meshloom_latest() is None
        assert breaker.failures == 0
        assert breaker.is_open is False

    @pytest.mark.parametrize(
        "handler",
        [
            _answer(status_code=503),
            lambda _r: httpx.Response(302, headers={"location": "https://evil.example/"}),
            lambda _r: httpx.Response(200, text="<html>"),
            lambda _r: httpx.Response(200, content=b"\xff\xfe"),
            lambda _r: httpx.Response(200, content=b"[" * 60_000),
            _answer(["not", "an", "object"]),
        ],
        ids=["5xx", "redirect", "not-json", "not-utf8", "deep-nesting", "not-an-object"],
    )
    @pytest.mark.asyncio
    async def test_unusable_answer_is_none(self, handler):
        with _community(handler):
            assert await fetch_meshloom_latest() is None

    @pytest.mark.asyncio
    async def test_transport_failure_is_none(self):
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        with _community(down):
            assert await fetch_meshloom_latest() is None

    @pytest.mark.asyncio
    async def test_teardown_cancels_it_into_none(self):
        import asyncio

        started = asyncio.Event()

        async def hang(_request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError

        with _community(hang):
            fetch = asyncio.create_task(fetch_meshloom_latest())
            await asyncio.wait_for(started.wait(), 2)
            await meshloom_community._cancel_inflight_requests()
            assert await asyncio.wait_for(fetch, 2) is None


class TestReleaseCheckBoundsOnARealSocket:
    """A hostile or broken server on a real socket: the check always ends, and small."""

    @pytest.fixture
    def serve(self, monkeypatch: pytest.MonkeyPatch):
        import asyncio

        servers: list[asyncio.Server] = []
        tasks: list[asyncio.Task] = []

        async def start(respond) -> None:
            async def on_client(reader, writer):
                tasks.append(asyncio.current_task())
                try:
                    await reader.readuntil(b"\r\n\r\n")
                    await respond(writer)
                except (ConnectionError, asyncio.CancelledError):
                    pass
                finally:
                    writer.close()

            server = await asyncio.start_server(on_client, "127.0.0.1", 0)
            servers.append(server)
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(meshloom_community, "DEFAULT_API_BASE", f"http://127.0.0.1:{port}")

        yield start
        for task in tasks:
            task.cancel()
        for server in servers:
            server.close()

    @pytest.mark.asyncio
    async def test_slow_body_stops_at_the_total_limit(self, serve, monkeypatch):
        import asyncio
        import time

        monkeypatch.setattr(meshloom_community, "RELEASE_CHECK_TOTAL_SECONDS", 0.4)

        async def respond(writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
            while True:  # Each read stays well inside the 8 s read timeout.
                writer.write(b"x")
                await writer.drain()
                await asyncio.sleep(0.02)

        await serve(respond)
        started = time.monotonic()
        assert await fetch_meshloom_latest() is None
        assert time.monotonic() - started < 2

    @pytest.mark.asyncio
    async def test_oversized_body_stops_at_the_cap(self, serve):
        sent = 0

        async def respond(writer):
            nonlocal sent
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 400000000\r\n\r\n")
            while sent < 400_000_000:
                writer.write(b"x" * 65536)
                await writer.drain()
                sent += 65536

        await serve(respond)
        assert await fetch_meshloom_latest() is None
        assert sent < 50_000_000  # far from the 400 MB on offer

    @pytest.mark.parametrize("encoding", [b"gzip", b"deflate", b"br", b"GZIP, identity"])
    @pytest.mark.asyncio
    async def test_compressed_answer_is_refused_without_being_decoded(self, serve, encoding):
        import gzip
        import json

        # Valid JSON once inflated, past any cap: a refusal cannot be a parsing accident.
        bomb = gzip.compress(json.dumps({"version": "9.9.9", "pad": "x" * 50_000_000}).encode())

        async def respond(writer):
            headers = b"HTTP/1.1 200 OK\r\nContent-Encoding: %s\r\nContent-Length: %d\r\n\r\n"
            writer.write(headers % (encoding, len(bomb)))
            writer.write(bomb)
            await writer.drain()

        await serve(respond)
        assert await fetch_meshloom_latest() is None

    @pytest.mark.asyncio
    async def test_a_normal_answer_passes(self, serve):
        import json

        body = json.dumps(_STATS_PAYLOAD).encode()

        async def respond(writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body))
            writer.write(body)
            await writer.drain()

        await serve(respond)
        assert await fetch_meshloom_latest() == _STATS_PAYLOAD


class TestSemVerAndCache:
    def test_null_latest_is_not_available(self):
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.2.3"),
        ):
            status = get_update_status()
        assert status["latest"] is None
        assert status["update_available"] is False
        assert status["current"] == "1.2.3"
        assert status["html_url"] is None

    def test_local_0_0_0_never_badges(self):
        import app.services.oss_updates as oss_updates

        assert is_unknown_local_version("0.0.0") is True
        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("0.0.0"),
        ):
            status = get_update_status()
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is False

    def test_mocked_stats_payload_marks_update(self):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.0.0"),
        ):
            status = get_update_status()
        assert status["current"] == "1.0.0"
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is True
        assert status["html_url"] == _STATS_PAYLOAD["html_url"]

    def test_env_latest_override_wins_over_catalogue(self, monkeypatch: pytest.MonkeyPatch):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        monkeypatch.setenv("MESHLOOM_UPDATE_LATEST", "9.9.9")
        monkeypatch.setenv("MESHLOOM_UPDATE_HTML_URL", "https://example.invalid/private")
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.0.0"),
        ):
            status = get_update_status()
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is True
        assert status["html_url"] == "https://example.invalid/private"

    def test_env_latest_override_keeps_catalogue_html(self, monkeypatch: pytest.MonkeyPatch):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        monkeypatch.setenv("MESHLOOM_UPDATE_LATEST", "8.8.8")
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.0.0"),
        ):
            status = get_update_status()
        assert status["latest"] == "8.8.8"
        assert status["html_url"] == _STATS_PAYLOAD["html_url"]

    def test_equal_or_older_latest_is_not_available(self):
        assert is_newer_release("1.2.3", "1.2.3") is False
        assert is_newer_release("1.2.2", "1.2.3") is False
        assert is_newer_release("1.3.0", "1.2.9") is True
        assert is_newer_release("v2.0.0", "1.9.9") is True

    def test_html_url_must_be_a_release_page_of_the_public_repository(self):
        import app.services.oss_updates as oss_updates

        for bad in (
            "javascript:alert(1)",
            "https://evil.example/TwinRocket/meshloom/releases/tag/9.9.9",
            "https://github.com/TwinRocket/meshloom.evil/releases/tag/9.9.9",
            "https://github.com/TwinRocket/meshloom/releases/../../../evil/x",
            "https://github.com/TwinRocket/meshloom/releases/tag/9.9.9/../../../../evil",
            "https://github.com/TwinRocket/meshloom/releases/tag/1.0.0",
            "https://github.com/TwinRocket/meshloom/releases",
            12,
        ):
            oss_updates._latest_payload = {"version": "9.9.9", "html_url": bad}
            with patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=_build("1.0.0"),
            ):
                assert get_update_status()["html_url"] is None, bad

    @pytest.mark.asyncio
    async def test_answer_without_version_counts_as_no_answer(self):
        null_payload = {
            "version": None,
            "tag": None,
            "released_at": None,
            "html_url": None,
        }
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value=null_payload),
        ):
            assert await refresh_oss_update_cache() is None
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.2.3"),
        ):
            status = get_update_status()
        assert status["latest"] is None
        assert status["update_available"] is False
        assert status["checked_at"] is None


class TestUpdatesEndpoint:
    def test_get_updates_returns_cached_status(self, tmp_path, monkeypatch):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        monkeypatch.setenv("MESHLOOM_UPDATE_JOB_PATH", str(tmp_path / "update-job.json"))
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": _STATS_PAYLOAD["html_url"],
                },
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(
                    return_value=type(
                        "S",
                        (),
                        {
                            "auto_update": False,
                            "auto_update_window_start": "00:00",
                            "auto_update_window_end": "00:00",
                            "auto_update_weekdays": [0, 1, 2, 3, 4, 5, 6],
                        },
                    )()
                ),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/api/updates")
                health = client.get("/api/health").json()

        assert response.status_code == 200
        data = response.json()
        assert data["current"] == "1.0.0"
        assert data["latest"] == "9.9.9"
        assert data["update_available"] is True
        assert data["html_url"] == _STATS_PAYLOAD["html_url"]
        assert data["install_kind"] in {"package", "compose", "addon", "container", "source"}
        assert data["apply_supported"] is False or data["install_kind"] in {"package", "compose"}
        assert data["auto_update"] is False
        assert data["auto_update_window_start"] == "00:00"
        assert data["auto_update_window_end"] == "00:00"
        assert data["auto_update_weekdays"] == [0, 1, 2, 3, 4, 5, 6]
        assert data["checked_at"] is None
        assert isinstance(data["tz_name"], str) and data["tz_name"]
        assert data["next_auto_apply_at"] is None
        assert data["job"] == {
            "state": "idle",
            "phase": None,
            "percent": None,
            "error": None,
            "started_at": None,
        }
        assert "update_available" not in health

    def test_refresh_fetches_without_auto_apply(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHLOOM_UPDATE_JOB_PATH", str(tmp_path / "update-job.json"))
        from app.main import app

        with (
            patch(
                "app.routers.updates.refresh_oss_update_cache",
                new=AsyncMock(return_value=_STATS_PAYLOAD),
            ) as refresh,
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": _STATS_PAYLOAD["html_url"],
                    "checked_at": 1_700_000_000,
                },
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(
                    return_value=type(
                        "S",
                        (),
                        {
                            "auto_update": True,
                            "auto_update_window_start": "00:00",
                            "auto_update_window_end": "00:00",
                            "auto_update_weekdays": [0, 1, 2, 3, 4, 5, 6],
                        },
                    )()
                ),
            ),
            patch("app.routers.updates.start_apply", new=AsyncMock()) as start,
        ):
            with TestClient(app) as client:
                response = client.post("/api/updates/refresh")

        assert response.status_code == 200
        refresh.assert_awaited_once()
        start.assert_not_called()
        data = response.json()
        assert data["checked_at"] == 1_700_000_000
        assert data["auto_update"] is True
        assert data["latest"] == "9.9.9"


class TestCommunityOnlySource:
    @pytest.mark.parametrize(
        "version",
        [
            "banana",
            "x" * 100_000,
            "9" * 100_000 + ".0.0",
            "4.18.5.post1",
            "1!0.0.1",
            "4.18.5+x",
            "4.18.5.dev0",
            "4.18.5rc1",
            "4.18.5-rc1",
            "4.18.5-beta.1",
            "v4.18.5",
            " 4.18.5",
            "4.18",
            "04.18.5",
            "4.18.5\n",
            4,
            None,
        ],
    )
    @pytest.mark.asyncio
    async def test_a_nonsense_version_is_no_answer_and_keeps_what_is_known(self, version):
        with (
            patch(
                "app.services.oss_updates.fetch_meshloom_latest",
                new=AsyncMock(return_value={"version": "4.18.4"}),
            ),
            patch("app.services.oss_updates.get_app_build_info", return_value=_build("4.18.3")),
        ):
            await refresh_oss_update_cache()
            with patch(
                "app.services.oss_updates.fetch_meshloom_latest",
                new=AsyncMock(return_value={"version": version}),
            ):
                assert await refresh_oss_update_cache() is None
            status = get_update_status()
        assert status["latest"] == "4.18.4"
        assert status["check_stale"] is True

    @pytest.mark.parametrize("version", ["5.0.0", "0.0.1", "10.20.30", "5.0.0-rc.3"])
    @pytest.mark.asyncio
    async def test_a_release_version_is_an_answer(self, version):
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value={"version": version}),
        ):
            assert (await fetch_latest_release() or {}).get("version") == version

    @pytest.mark.asyncio
    async def test_community_answer_is_cached_as_its_source(self):
        with (
            patch(
                "app.services.oss_updates.fetch_meshloom_latest",
                new=AsyncMock(return_value=dict(_STATS_PAYLOAD)),
            ),
            patch("app.services.oss_updates.get_app_build_info", return_value=_build("4.18.0")),
        ):
            payload = await fetch_latest_release()
            await refresh_oss_update_cache()
            status = get_update_status()
        assert payload == {**_STATS_PAYLOAD, "source": "community"}
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is True
        assert status["source"] == "community"
        assert status["check_stale"] is False

    def test_no_other_release_source_is_left(self):
        from app.services import oss_updates

        assert not hasattr(oss_updates, "httpx")
        assert not hasattr(oss_updates, "fetch_github_latest")
        assert not hasattr(oss_updates, "release_from_redirect")

    def test_pin_keeps_working_without_community(self, monkeypatch: pytest.MonkeyPatch):
        import app.services.oss_updates as oss_updates

        oss_updates._last_check_failed = True
        monkeypatch.setenv("MESHLOOM_UPDATE_LATEST", "9.9.9")
        with patch("app.services.oss_updates.get_app_build_info", return_value=_build("1.0.0")):
            status = get_update_status()
        assert status["source"] == "env"
        assert status["update_available"] is True
        assert status["check_stale"] is False

    @pytest.mark.parametrize(
        "lookup",
        [AsyncMock(return_value=None), AsyncMock(side_effect=RuntimeError("boom"))],
        ids=["no-answer", "unexpected-error"],
    )
    @pytest.mark.asyncio
    async def test_community_down_keeps_the_last_status_and_marks_it_stale(self, lookup):
        with (
            patch(
                "app.services.oss_updates.fetch_meshloom_latest",
                new=AsyncMock(return_value=dict(_STATS_PAYLOAD)),
            ),
            patch("app.services.oss_updates.get_app_build_info", return_value=_build("4.18.0")),
        ):
            await refresh_oss_update_cache()
            checked_at = get_update_status()["checked_at"]
            with patch("app.services.oss_updates.fetch_meshloom_latest", new=lookup):
                assert await refresh_oss_update_cache() is None
            status = get_update_status()
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is True
        assert status["checked_at"] == checked_at
        assert status["check_stale"] is True


_REASON = {"fr": "Passez à la 5.0 à la main.", "en": "Move to 5.0 by hand."}


def _hold(below="5.0.0", reason=_REASON):
    return {"below": below, "reason": reason}


def _status(current: str, latest: str, hold):
    import app.services.oss_updates as oss_updates

    oss_updates._latest_payload = {"version": latest, "auto_update_hold": hold, "html_url": None}
    with patch("app.services.oss_updates.get_app_build_info", return_value=_build(current)):
        return get_update_status()


class TestHoldRule:
    @pytest.mark.parametrize(
        ("current", "latest", "held"),
        [
            ("4.18.3", "5.0.0", True),  # crosses it
            ("4.18.3", "5.2.1", True),
            ("4.18.3", "4.18.4", False),  # latest is below it
            ("5.0.0", "5.0.1", False),  # already past it
            ("5.0.0-rc.3", "5.0.0", False),  # a pre-release of it is not below it
            ("5.0.0-rc.3", "5.0.1", False),
            ("4.18.3", "5.0.0-rc.1", True),  # a pre-release of it does reach it
            ("4.18.3-rc.1", "5.0.0", True),
        ],
    )
    def test_applies_only_when_current_below_latest(self, current, latest, held):
        status = _status(current, latest, _hold())
        assert status["update_available"] is True
        assert status["auto_update_held"] is held
        assert status["auto_update_hold_reason"] == (_REASON if held else None)

    def test_below_is_compared_as_a_release(self):
        below = "5.0.0-rc.1"
        assert _status("4.18.3", "5.0.0", _hold(below=below))["auto_update_held"] is True
        assert _status("5.0.0-rc.3", "5.0.0", _hold(below=below))["auto_update_held"] is False

    def test_field_absent_is_no_hold(self):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "5.0.0"}
        with patch("app.services.oss_updates.get_app_build_info", return_value=_build("4.18.3")):
            status = get_update_status()
        assert status["auto_update_held"] is False
        assert status["auto_update_hold_reason"] is None

    def test_null_is_no_hold(self):
        assert _status("4.18.3", "5.0.0", None)["auto_update_held"] is False

    def test_no_update_available_is_never_held(self):
        assert _status("5.0.0", "5.0.0", _hold())["auto_update_held"] is False

    @pytest.mark.parametrize(
        "reason",
        [
            None,
            "not an object",
            ["fr", "en"],
            {"fr": "seul"},
            {"fr": 1, "en": "x"},
            {"fr": "x", "en": None},
            {"fr": "   ", "en": "x"},
            {"fr": "x" * 301, "en": "x"},
        ],
    )
    def test_malformed_reason_is_no_reason_but_the_hold_stays(self, reason):
        status = _status("4.18.3", "5.0.0", _hold(reason=reason))
        assert status["auto_update_held"] is True
        assert status["auto_update_hold_reason"] is None

    def test_reason_keeps_only_its_two_locales_and_the_limit_is_inclusive(self):
        reason = {"fr": "x" * 300, "en": " y ", "de": "z", "html": "<b>"}
        status = _status("4.18.3", "5.0.0", _hold(reason=reason))
        assert status["auto_update_hold_reason"] == {"fr": "x" * 300, "en": "y"}

    @pytest.mark.parametrize(
        "hold",
        [
            _hold(below="not a version"),
            _hold(below="5.0"),
            _hold(below="v5.0.0"),
            _hold(below="5.0.0.post1"),
            _hold(below="1!5.0.0"),
            _hold(below="5.0.0+x"),
            _hold(below="5" * 100),
            _hold(below=5),
            {"reason": _REASON},
            {},
            "hold",
            ["5.0.0"],
            True,
        ],
    )
    def test_unparsable_hold_fails_closed_on_a_major_crossing_only(self, hold):
        assert _status("4.18.3", "5.0.0", hold)["auto_update_held"] is True
        assert _status("4.18.3", "4.19.0", hold)["auto_update_held"] is False
        assert _status("5.0.0", "5.0.1", hold)["auto_update_held"] is False

    def test_unparsable_hold_warns_once(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level("WARNING", logger="app.services.oss_updates"):
            for _ in range(3):
                _status("4.18.3", "5.0.0", _hold(below="nope"))
        assert len([r for r in caplog.records if "auto_update_hold" in r.getMessage()]) == 1


def _auto_settings(auto_update: bool = True):
    return type(
        "S",
        (),
        {
            "auto_update": auto_update,
            "auto_update_window_start": "00:00",
            "auto_update_window_end": "00:00",
            "auto_update_weekdays": [0, 1, 2, 3, 4, 5, 6],
        },
    )()


class TestAutoApplyHold:
    @pytest.fixture
    def apply_env(self):
        start = AsyncMock()
        with (
            patch("app.services.oss_updates.get_app_build_info", return_value=_build("4.18.3")),
            patch("app.services.install_kind.detect_install_kind", return_value=("package", True)),
            patch(
                "app.repository.AppSettingsRepository.get",
                new=AsyncMock(return_value=_auto_settings()),
            ),
            patch("app.services.update_apply.start_apply", new=start),
            patch("app.services.update_apply.expire_stale_applying_job", return_value={}),
            patch("app.services.update_apply.job_is_applying", return_value=False),
        ):
            yield start

    @pytest.mark.asyncio
    async def test_held_does_not_apply_and_cancels_the_timer(self, apply_env):
        import asyncio

        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "5.0.0", "auto_update_hold": _hold()}
        timer = asyncio.create_task(asyncio.sleep(3600))
        oss_updates._window_timer_task = timer
        with patch("app.services.oss_updates._schedule_window_apply") as schedule:
            await oss_updates._maybe_auto_apply()
        await asyncio.gather(timer, return_exceptions=True)

        apply_env.assert_not_called()
        schedule.assert_not_called()
        assert timer.cancelled()
        assert oss_updates._window_timer_task is None

    @pytest.mark.asyncio
    async def test_held_outside_the_window_arms_no_timer(self, apply_env):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "5.0.0", "auto_update_hold": _hold()}
        with (
            patch("app.services.oss_updates.in_window", return_value=False),
            patch("app.services.oss_updates._schedule_window_apply") as schedule,
        ):
            await oss_updates._maybe_auto_apply()
        schedule.assert_not_called()
        apply_env.assert_not_called()

    @pytest.mark.asyncio
    async def test_hold_below_latest_does_not_stop_a_minor_update(self, apply_env):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "4.18.4", "auto_update_hold": _hold()}
        await oss_updates._maybe_auto_apply()
        apply_env.assert_awaited_once_with("package", target="4.18.4")

    @pytest.mark.asyncio
    async def test_community_down_never_auto_applies_on_the_last_status(self, apply_env):
        import app.services.oss_updates as oss_updates

        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value={"version": "4.18.4"}),
        ):
            await oss_updates.refresh_oss_update_cache()
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest", new=AsyncMock(return_value=None)
        ):
            await oss_updates.refresh_oss_update_cache()
        await oss_updates._maybe_auto_apply()
        apply_env.assert_not_called()

        # Community answers again: back to normal.
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value={"version": "4.18.4"}),
        ):
            await oss_updates.refresh_oss_update_cache()
        await oss_updates._maybe_auto_apply()
        apply_env.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_one_failed_poll_leaves_the_armed_timer_alone(self, apply_env):
        import asyncio

        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "4.18.4"}
        oss_updates._last_check_failed = True
        timer = asyncio.create_task(asyncio.sleep(3600))
        oss_updates._window_timer_task = timer
        await oss_updates._maybe_auto_apply()
        assert oss_updates._window_timer_task is timer
        assert not timer.cancelled()
        apply_env.assert_not_called()
        timer.cancel()
        await asyncio.gather(timer, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_window_timer_stays_referenced_while_applying(self, apply_env):
        import asyncio
        from datetime import datetime

        import app.services.oss_updates as oss_updates
        from app.background_tasks import pending_background_tasks

        release = asyncio.Event()
        started = asyncio.Event()

        async def slow_start(*_args, **_kwargs):
            started.set()
            await release.wait()

        apply_env.side_effect = slow_start
        before = pending_background_tasks()
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value={"version": "4.18.4"}),
        ):
            oss_updates._schedule_window_apply(datetime.now().astimezone())
            task = oss_updates._window_timer_task
            await asyncio.wait_for(started.wait(), 2)
            assert oss_updates._window_timer_task is None
            assert pending_background_tasks() == before + 1
            release.set()
            await asyncio.wait_for(task, 2)

    @pytest.mark.asyncio
    async def test_window_timer_reads_a_hold_published_since_the_last_check(self, apply_env):
        import asyncio
        from datetime import datetime

        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "5.0.0"}
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value={"version": "5.0.0", "auto_update_hold": _hold()}),
        ):
            oss_updates._schedule_window_apply(datetime.now().astimezone())
            await asyncio.wait_for(oss_updates._window_timer_task, 2)
        apply_env.assert_not_called()

    @pytest.mark.asyncio
    async def test_window_timer_survives_its_own_cancel_to_start_the_apply(self, apply_env):
        """The timer lands on _cancel_window_timer in the window: it must not kill itself."""
        import asyncio
        from datetime import datetime

        import app.services.oss_updates as oss_updates

        reached_past_await = asyncio.Event()

        async def slow_start(*_args, **_kwargs):
            await asyncio.sleep(0)
            reached_past_await.set()

        apply_env.side_effect = slow_start
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value={"version": "4.18.4"}),
        ):
            oss_updates._schedule_window_apply(datetime.now().astimezone())
            task = oss_updates._window_timer_task
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
        assert reached_past_await.is_set()
        assert not task.cancelled()


class TestUpdatesEndpointHold:
    def _client_status(self, monkeypatch, tmp_path, latest="5.0.0", auto_update=True, stale=False):
        import app.services.oss_updates as oss_updates

        monkeypatch.setenv("MESHLOOM_UPDATE_JOB_PATH", str(tmp_path / "update-job.json"))
        oss_updates._latest_payload = {"version": latest, "auto_update_hold": _hold()}
        oss_updates._last_check_failed = stale
        return (
            patch("app.services.oss_updates.get_app_build_info", return_value=_build("4.18.3")),
            patch("app.routers.updates.detect_install_kind", return_value=("package", True)),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_auto_settings(auto_update)),
            ),
        )

    @pytest.mark.asyncio
    async def test_held_announces_no_auto_apply(self, monkeypatch, tmp_path):
        from app.routers.updates import build_update_status

        version, kind, settings = self._client_status(monkeypatch, tmp_path)
        with version, kind, settings:
            status = await build_update_status()
        assert status.update_available is True
        assert status.auto_update_held is True
        assert status.auto_update_hold_reason is not None
        assert status.auto_update_hold_reason.en == _REASON["en"]
        assert status.next_auto_apply_at is None

    @pytest.mark.asyncio
    async def test_not_held_still_announces_the_window(self, monkeypatch, tmp_path):
        from app.routers.updates import build_update_status

        version, kind, settings = self._client_status(monkeypatch, tmp_path, latest="4.18.4")
        with version, kind, settings:
            status = await build_update_status()
        assert status.auto_update_held is False
        assert status.auto_update_hold_reason is None
        assert status.next_auto_apply_at is not None

    @pytest.mark.asyncio
    async def test_stale_check_announces_no_auto_apply(self, monkeypatch, tmp_path):
        from app.routers.updates import build_update_status

        version, kind, settings = self._client_status(
            monkeypatch, tmp_path, latest="4.18.4", stale=True
        )
        with version, kind, settings:
            status = await build_update_status()
        assert status.update_available is True
        assert status.check_stale is True
        assert status.next_auto_apply_at is None

    @pytest.mark.asyncio
    async def test_manual_apply_stays_allowed_while_held(self, monkeypatch, tmp_path):
        from app.routers.updates import apply_updates

        version, kind, settings = self._client_status(monkeypatch, tmp_path)
        start = AsyncMock()
        with version, kind, settings, patch("app.routers.updates.start_apply", new=start):
            status = await apply_updates()
        start.assert_awaited_once_with("package", target="5.0.0")
        assert status.auto_update_held is True


class TestSettingsRecheck:
    @pytest.mark.asyncio
    async def test_patch_rechecks_in_the_background_and_cannot_fail(self, test_db):
        import asyncio

        from app.routers.updates import UpdateSettingsPatch, patch_update_settings

        ran = asyncio.Event()

        async def boom() -> None:
            ran.set()
            raise RuntimeError("boom")

        with patch("app.services.oss_updates._maybe_auto_apply", new=boom):
            status = await patch_update_settings(UpdateSettingsPatch(auto_update=True))
            await asyncio.wait_for(ran.wait(), 2)
        assert status.auto_update is True

    @pytest.mark.asyncio
    async def test_empty_patch_does_not_recheck(self, test_db):
        from app.routers.updates import UpdateSettingsPatch, patch_update_settings

        recheck = AsyncMock()
        with patch("app.routers.updates.recheck_auto_apply", new=recheck):
            await patch_update_settings(UpdateSettingsPatch())
        recheck.assert_not_called()

    @pytest.mark.asyncio
    async def test_old_status_is_read_again_before_applying_recent_is_not(self):
        import time

        import app.services.oss_updates as oss_updates

        apply = AsyncMock()
        lookup = AsyncMock(return_value={"version": "5.0.0", "auto_update_hold": _hold()})
        oss_updates._latest_payload = {"version": "5.0.0"}
        with (
            patch("app.services.oss_updates.get_app_build_info", return_value=_build("4.18.3")),
            patch("app.services.oss_updates._maybe_auto_apply", new=apply),
            patch("app.services.oss_updates.fetch_meshloom_latest", new=lookup),
        ):
            oss_updates._checked_at = int(time.time())
            await oss_updates.recheck_auto_apply()
            lookup.assert_not_called()
            oss_updates._checked_at = int(time.time()) - 3600
            await oss_updates.recheck_auto_apply()
            lookup.assert_awaited_once()
        assert apply.await_count == 2


class TestPollCadence:
    def test_one_cadence_for_everybody(self):
        from app.services import oss_updates

        assert UPDATE_POLL_INTERVAL_SECONDS == 6 * 3600
        assert not hasattr(oss_updates, "poll_interval_seconds")
        assert not hasattr(oss_updates, "nudge_oss_update_poll")

    @pytest.mark.asyncio
    async def test_loop_survives_community_down_and_waits_6h(self):
        import asyncio

        from app.services import oss_updates

        lookup = AsyncMock(return_value=None)
        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)
            if len(waits) == 2:
                raise asyncio.CancelledError

        with (
            patch("app.services.oss_updates.fetch_meshloom_latest", new=lookup),
            patch("app.services.oss_updates.asyncio.sleep", new=fake_sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await oss_updates._oss_update_loop()

        assert lookup.await_count == 2
        assert waits == [6 * 3600, 6 * 3600]

    @pytest.mark.asyncio
    async def test_loop_survives_a_failure_in_auto_apply(self):
        import asyncio

        from app.services import oss_updates

        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)
            raise asyncio.CancelledError

        with (
            patch(
                "app.services.oss_updates._maybe_auto_apply",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch("app.services.oss_updates.asyncio.sleep", new=fake_sleep),
            pytest.raises(asyncio.CancelledError),
        ):
            await oss_updates._oss_update_loop()
        assert waits == [6 * 3600]
