"""Store egress (MSHL-194): nothing leaves for the Store while ``store_online`` is off.

A DNS table and an ``httpx.AsyncClient`` recorder (``tests/share_fakes.py``) see every
lookup and request. With ``store_online`` false (the default), Community on or off,
``store_fetch`` to every Store host, every ``/api/store`` route, the settings writes
and the background refresh make no lookup and no request. With it on, requests go only
to the hosts of ``STORE_ALLOWLIST`` (the hosts ``GET /api/store/status`` names), over
HTTPS, with the Meshloom ``User-Agent``. Turning it on starts the refresh loop; turning
it off stops the loop before its next tick and cancels the requests in flight.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models import AppSettings, BackupExport
from app.net.egress_guard import EgressBlocked
from app.plugins import store_egress
from app.plugins.store_egress import (
    STORE_ALLOWLIST,
    STORE_HOSTS,
    StoreOffline,
    note_store_online_changed,
    register_store_refresher,
    start_store_refresh,
    store_fetch,
)
from app.repository import AppSettingsRepository
from app.repository.plugins import PluginRepository
from tests.share_fakes import PUBLIC_IP, Dns, Recorder

_RealAsyncClient = httpx.AsyncClient
MAX = 64 * 1024


@pytest.fixture(autouse=True)
async def _reset_store():
    await store_egress.reset_store_egress_for_tests()
    yield
    await store_egress.reset_store_egress_for_tests()


@pytest.fixture
def dns(monkeypatch):
    return Dns(monkeypatch, {host: [PUBLIC_IP] for host in (*STORE_HOSTS, "evil.example")})


@pytest.fixture
def recorder(monkeypatch):
    return Recorder(monkeypatch, lambda request: httpx.Response(200, content=b"{}"))


@pytest.fixture(params=["community_off", "community_on"])
def community(request, monkeypatch):
    """The Store gate ignores Community: run each check with Community both ways."""
    if request.param == "community_on":
        state = SimpleNamespace(enabled=True, iata="CDG")
        monkeypatch.setattr(
            "app.services.meshloom_community.community_egress_state",
            AsyncMock(return_value=state),
        )
        monkeypatch.setattr(
            "app.services.meshloom_community.community_active", AsyncMock(return_value=True)
        )
    return request.param


async def _online_instance() -> None:
    await PluginRepository.create(
        plugin_id="com.example.weather",
        label="Weather",
        kind="custom",
        version="1.0.0",
        manifest={},
        repository="example/weather",
    )


def _hosts(recorder: Recorder) -> list[str]:
    return [r.headers["host"] for r in recorder.requests]


def _store_routes() -> list[tuple[str, str]]:
    from app.main import app
    from tests.test_community_off_no_egress import _walk_routes

    out = []
    for path, route in _walk_routes(app.routes):
        if path.startswith("/api/store"):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                out.append((method, re.sub(r"\{[^}]+\}", "x", path)))
    return out


async def _client():
    from app.main import app

    return _RealAsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestDefaultOff:
    def test_model_default_is_off(self):
        assert AppSettings().store_online is False

    async def test_fresh_database_is_off(self, test_db):
        assert (await AppSettingsRepository.get()).store_online is False
        assert await store_egress.store_egress_allowed() is False

    async def test_settings_api_reads_and_writes_it(self, test_db, dns, recorder):
        async with await _client() as client:
            assert (await client.get("/api/settings")).json()["store_online"] is False
            response = await client.patch("/api/settings", json={"store_online": True})
            assert response.status_code == 200, response.text
            assert response.json()["store_online"] is True
            assert store_egress.store_refresh_running()
            response = await client.patch("/api/settings", json={"store_online": False})
            assert response.json()["store_online"] is False
        assert not store_egress.store_refresh_running()
        assert recorder.requests == []

    def test_never_written_to_a_backup(self):
        export = BackupExport(
            format="meshloom-backup-v1",
            exported_at=0,
            settings=AppSettings(store_online=True),
        )
        assert "store_online" not in export.model_dump()["settings"]


class TestOffNothingLeaves:
    @pytest.mark.parametrize("host", STORE_HOSTS)
    async def test_store_fetch_refuses_before_any_lookup(
        self, test_db, dns, recorder, community, host
    ):
        with pytest.raises(StoreOffline) as info:
            await store_fetch("GET", f"https://{host}/index.json", max_response_bytes=MAX)
        assert info.value.code == "store_offline"
        assert recorder.requests == []
        assert dns.lookups == []

    async def test_store_routes_and_settings_writes_make_no_request(
        self, test_db, dns, recorder, community
    ):
        await _online_instance()
        routes = _store_routes()
        assert ("GET", "/api/store/status") in routes
        async with await _client() as client:
            for method, path in routes:
                assert (await client.request(method, path)).status_code < 500
            assert (await client.patch("/api/settings", json={})).status_code == 200
            assert (
                await client.patch("/api/settings", json={"store_online": False})
            ).status_code == 200
        await asyncio.sleep(0.05)
        assert recorder.requests == []
        assert dns.lookups == []

    async def test_background_loop_never_starts_and_never_runs(
        self, test_db, dns, recorder, community
    ):
        await _online_instance()
        calls: list[int] = []

        async def refresher() -> None:
            calls.append(1)
            await store_fetch("GET", "https://github.com/example/weather", max_response_bytes=MAX)

        register_store_refresher(refresher)
        await start_store_refresh()
        assert not store_egress.store_refresh_running()
        assert await store_egress.run_store_refresh() is False
        await asyncio.sleep(0.05)
        assert calls == []
        assert recorder.requests == []
        assert dns.lookups == []


class TestOn:
    async def test_only_allowlisted_hosts_over_https_with_the_user_agent(
        self, test_db, dns, recorder, community
    ):
        await AppSettingsRepository.update(store_online=True)
        for host in STORE_HOSTS:
            response = await store_fetch(
                "GET",
                f"https://{host}/x",
                max_response_bytes=MAX,
                headers={"User-Agent": "spoofed", "Accept": "application/json"},
            )
            assert response.status_code == 200
        assert _hosts(recorder) == list(STORE_HOSTS)
        assert {r.headers["user-agent"] for r in recorder.requests} == {
            store_egress.store_user_agent()
        }
        assert store_egress.store_user_agent().startswith("Meshloom/")
        assert all(r.url.scheme == "https" for r in recorder.requests)

    async def test_other_hosts_and_plain_http_are_refused_before_dns(self, test_db, dns, recorder):
        await AppSettingsRepository.update(store_online=True)
        for url, code in (
            ("https://evil.example/x", "egress_host_not_allowed"),
            ("https://api.meshloom.app/x", "egress_host_not_allowed"),
            ("http://github.com/x", "egress_scheme"),
        ):
            with pytest.raises(EgressBlocked) as info:
                await store_fetch("GET", url, max_response_bytes=MAX)
            assert info.value.code == code
        assert recorder.requests == []
        assert dns.lookups == []

    async def test_a_redirect_off_the_allowlist_is_refused(self, test_db, dns, monkeypatch):
        def answer(request: httpx.Request) -> httpx.Response:
            if request.headers["host"] == "github.com":
                return httpx.Response(302, headers={"Location": "https://evil.example/a.zip"})
            return httpx.Response(200, content=b"zip")

        recorder = Recorder(monkeypatch, answer)
        await AppSettingsRepository.update(store_online=True)
        with pytest.raises(EgressBlocked) as info:
            await store_fetch("GET", "https://github.com/o/r/a.zip", max_response_bytes=MAX)
        assert info.value.code == "egress_host_not_allowed"
        assert _hosts(recorder) == ["github.com"]

    async def test_status_names_exactly_the_guard_allowlist(self, test_db):
        async with await _client() as client:
            off = (await client.get("/api/store/status")).json()
            await AppSettingsRepository.update(store_online=True)
            on = (await client.get("/api/store/status")).json()
        assert off["enabled"] is False and on["enabled"] is True
        allowlisted = sorted(host for host, _port in STORE_ALLOWLIST.entries)
        assert on["hosts"] == allowlisted == sorted(STORE_HOSTS)
        assert all(port is None for _host, port in STORE_ALLOWLIST.entries)
        assert on["user_agent"] == store_egress.store_user_agent()
        assert on["refresh_interval_s"] == 6 * 3600
        assert on["sends"] == ["ip_address", "user_agent"]
        assert on["missed_when_disabled"] == ["updates", "revocation_alerts"]
        assert on["last_refresh_at"] is None


class TestRefreshLoop:
    async def test_enabling_starts_it_and_disabling_stops_it(self, test_db, dns, recorder):
        await _online_instance()
        ran = asyncio.Event()
        calls: list[int] = []

        async def refresher() -> None:
            calls.append(1)
            await store_fetch("GET", "https://github.com/example/weather", max_response_bytes=MAX)
            ran.set()

        register_store_refresher(refresher)
        await AppSettingsRepository.update(store_online=True)
        assert store_egress.store_refresh_running()
        await asyncio.wait_for(ran.wait(), 3)
        assert _hosts(recorder) == ["github.com"]
        assert (await store_egress.store_status()).last_refresh_at is not None

        await AppSettingsRepository.update(store_online=False)
        assert not store_egress.store_refresh_running()
        # Nothing wakes it while off: not a new write, not a direct nudge.
        await note_store_online_changed(False)
        assert await store_egress.run_store_refresh() is False
        await asyncio.sleep(0.05)
        assert calls == [1]
        assert len(recorder.requests) == 1

    async def test_no_online_instance_means_no_refresh(self, test_db, dns, recorder):
        await PluginRepository.create(
            plugin_id="com.example.local", label="Local", kind="local", version="1", manifest={}
        )
        calls: list[int] = []

        async def refresher() -> None:
            calls.append(1)

        register_store_refresher(refresher)
        await AppSettingsRepository.update(store_online=True)
        assert store_egress.store_refresh_running()
        await asyncio.sleep(0.05)
        assert calls == []
        assert recorder.requests == []

    async def test_starts_at_boot_only_when_on(self, test_db):
        await start_store_refresh()
        assert not store_egress.store_refresh_running()
        await AppSettingsRepository.update(store_online=True)
        await store_egress.stop_store_refresh()
        assert not store_egress.store_refresh_running()
        await start_store_refresh()
        assert store_egress.store_refresh_running()

    async def test_disabling_cancels_a_request_in_flight(self, test_db, monkeypatch):
        started = asyncio.Event()

        async def hanging(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(store_egress, "follow_checked_redirects", hanging)
        await AppSettingsRepository.update(store_online=True)
        pending = asyncio.create_task(
            store_fetch("GET", "https://github.com/o/r", max_response_bytes=MAX)
        )
        await asyncio.wait_for(started.wait(), 3)
        await AppSettingsRepository.update(store_online=False)
        with pytest.raises(StoreOffline):
            await asyncio.wait_for(pending, 3)


class TestReviewFollowUps:
    """MSHL-366: user info refused, refresh spacing, restore hook, narrowing only."""

    async def test_a_url_with_user_info_is_refused_before_any_lookup(self, test_db, dns, recorder):
        await AppSettingsRepository.update(store_online=True)
        for url in (
            "https://user:pw@github.com/o/r",
            "https://user@github.com/o/r",
            "https://:pw@raw.githubusercontent.com/o/r",
        ):
            with pytest.raises(EgressBlocked) as info:
                await store_fetch("GET", url, max_response_bytes=MAX)
            assert info.value.code == "egress_userinfo"
            assert info.value.permanent is True
        assert recorder.requests == []
        assert dns.lookups == []

    async def test_user_info_is_refused_even_before_the_consent_check_passes(
        self, test_db, dns, recorder
    ):
        with pytest.raises(StoreOffline):
            await store_fetch("GET", "https://user:pw@github.com/o/r", max_response_bytes=MAX)
        assert recorder.requests == []

    async def test_allowed_hosts_only_narrow_the_store_allowlist(self, test_db, dns, recorder):
        from app.net.egress_guard import HostAllowlist

        await AppSettingsRepository.update(store_online=True)
        wider = HostAllowlist.of("evil.example", "github.com")
        with pytest.raises(EgressBlocked) as info:
            await store_fetch(
                "GET", "https://evil.example/x", max_response_bytes=MAX, allowed_hosts=wider
            )
        assert info.value.code == "egress_host_not_allowed"
        narrow = HostAllowlist.of("github.com")
        with pytest.raises(EgressBlocked):
            await store_fetch(
                "GET",
                "https://raw.githubusercontent.com/x",
                max_response_bytes=MAX,
                allowed_hosts=narrow,
            )
        ok = await store_fetch(
            "GET", "https://github.com/x", max_response_bytes=MAX, allowed_hosts=wider
        )
        assert ok.status_code == 200
        assert _hosts(recorder) == ["github.com"]

    async def test_ticks_are_never_closer_than_the_minimum_gap(self, test_db, monkeypatch):
        await _online_instance()
        await AppSettingsRepository.update(store_online=True)
        await store_egress.stop_store_refresh()
        now = [1_000_000.0]
        monkeypatch.setattr(store_egress._state, "clock", lambda: now[0])
        calls: list[int] = []

        async def refresher() -> None:
            calls.append(1)

        register_store_refresher(refresher)
        assert await store_egress.run_store_refresh() is True
        now[0] += store_egress.MIN_REFRESH_GAP_S - 1
        assert await store_egress.run_store_refresh() is False
        now[0] += 1
        assert await store_egress.run_store_refresh() is True
        assert calls == [1, 1]

    async def test_writing_true_again_does_not_trigger_a_refresh(self, test_db, dns, recorder):
        await _online_instance()
        calls: list[int] = []

        async def refresher() -> None:
            calls.append(1)

        register_store_refresher(refresher)
        await AppSettingsRepository.update(store_online=True)
        task = store_egress._state.task
        for _ in range(20):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls == [1]
        for _ in range(3):
            await AppSettingsRepository.update(store_online=True)
            await note_store_online_changed(True)
        await asyncio.sleep(0.05)
        assert calls == [1]
        assert store_egress._state.task is task

    async def test_a_restore_reapplies_the_consent_and_cancels_in_flight(
        self, test_db, monkeypatch
    ):
        from app.services.backup_sections import run_on_restore_hooks

        started = asyncio.Event()

        async def hanging(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(store_egress, "follow_checked_redirects", hanging)
        await AppSettingsRepository.update(store_online=True)
        pending = asyncio.create_task(
            store_fetch("GET", "https://github.com/o/r", max_response_bytes=MAX)
        )
        await asyncio.wait_for(started.wait(), 3)
        # What a SQLite snapshot does: the column comes back off without the settings route.
        async with test_db.tx() as conn:
            await conn.execute("UPDATE app_settings SET store_online = 0 WHERE id = 1")
        await run_on_restore_hooks("sqlite")
        with pytest.raises(StoreOffline):
            await asyncio.wait_for(pending, 3)
        assert not store_egress.store_refresh_running()
