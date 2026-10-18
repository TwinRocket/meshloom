"""Sharing off means no connection to the share service; on, only ``SHARE_HOST`` and no secret.

A recorder replaces ``httpx.AsyncClient`` for the whole test (the egress guard's
pinned clients are ``httpx.AsyncClient`` too). With ``share_enabled`` false, every
network function of ``app.services.share`` (found from the module, so a new one
must be added to ``SERVICE_CALLS``) and every ``/api/share/*`` route (found from
``app.routes``; the routes land with later tickets) answers without any request.
With it on, requests go only to ``SHARE_HOST``, and none of them carries the
instance seed, a channel key or the radio private key (the DM secret).
"""

from __future__ import annotations

import asyncio
import inspect
import re

import httpx
import pytest
from fastapi.routing import APIRoute

from app import keystore
from app.repository import AppSettingsRepository, ChannelRepository
from app.services import share
from app.services.instance_identity import ensure_instance_key
from app.services.share import ShareError
from tests.share_fakes import Dns, Recorder, json_response

_RealAsyncClient = httpx.AsyncClient

CHANNEL_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
RADIO_PRIVATE_KEY = bytes(range(64))

# Every network function of the share client, with arguments.
SERVICE_CALLS = {
    "create_link": lambda: share.create_link("https://example.com/a"),
    "get_meta": lambda: share.get_meta("AbCdEfG"),
    "fetch_object": lambda: share.fetch_object("AbCdEfG"),
    "report_object": lambda: share.report_object("AbCdEfG", "spam", "details"),
    "share_request": lambda: share.share_request("GET", "/v1/meta/AbCdEfG", binding=None),
}
# Coroutine functions of the module that never connect.
_LOCAL = {"share_egress_state"}


def _answer(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/links":
        return json_response(
            201, {"id": "AbCdEfG", "url": "https://meshl.ink/AbCdEfG", "expires_at": 1}
        )
    if path.startswith("/v1/meta/"):
        return json_response(200, {"id": "AbCdEfG", "kind": "link", "target_host": "example.com"})
    if path == "/v1/reports":
        return httpx.Response(202)
    return httpx.Response(200, content=b"\x89PNG", headers={"Content-Type": "image/png"})


def _share_routes() -> list[tuple[str, str]]:
    from app.main import app

    out = []
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/share"):
            for method in sorted(route.methods - {"HEAD"}):
                out.append((method, re.sub(r"\{[^}]+\}", "AbCdEfG", route.path)))
    return out


def test_every_network_function_is_swept():
    coroutines = {
        name
        for name, fn in inspect.getmembers(share, inspect.iscoroutinefunction)
        if fn.__module__ == share.__name__
    }
    assert coroutines - _LOCAL == set(SERVICE_CALLS)


class TestShareOff:
    @pytest.mark.parametrize("name", sorted(SERVICE_CALLS))
    async def test_service_function_makes_no_request(self, test_db, monkeypatch, name):
        dns = Dns(monkeypatch)
        recorder = Recorder(monkeypatch, _answer)
        with pytest.raises(ShareError) as info:
            await SERVICE_CALLS[name]()
        assert (info.value.status_code, info.value.code) == (403, "share_disabled")
        assert recorder.requests == []
        assert dns.lookups == []

    async def test_share_routes_make_no_request(self, test_db, monkeypatch):
        routes = _share_routes()
        dns = Dns(monkeypatch)
        recorder = Recorder(monkeypatch, _answer)
        from app.main import app

        async with _RealAsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for method, path in routes:
                await client.request(method, path, json={"url": "https://example.com/a"})
        assert recorder.requests == []
        assert dns.lookups == []

    async def test_turning_it_off_stops_requests(self, test_db, monkeypatch):
        Dns(monkeypatch)
        recorder = Recorder(monkeypatch, _answer)
        await AppSettingsRepository.update(share_enabled=True)
        await share.get_meta("AbCdEfG")
        await AppSettingsRepository.update(share_enabled=False)
        for call in SERVICE_CALLS.values():
            with pytest.raises(ShareError):
                await call()
        assert len(recorder.requests) == 1


class TestShareOn:
    async def test_only_share_host_and_no_secret(self, test_db, monkeypatch):
        await AppSettingsRepository.update(share_enabled=True)
        await ensure_instance_key("share")
        seed = await AppSettingsRepository.get_instance_seed("share")
        assert seed
        await ChannelRepository.upsert(CHANNEL_KEY, "#secret-room")
        keystore.set_private_key(RADIO_PRIVATE_KEY)
        try:
            dns = Dns(monkeypatch)
            recorder = Recorder(monkeypatch, _answer)
            for call in SERVICE_CALLS.values():
                await call()
            assert recorder.hosts() == [share.SHARE_HOST] * len(SERVICE_CALLS)
            assert set(dns.lookups) == {share.SHARE_HOST}
            assert all(r.url.scheme == "https" for r in recorder.requests)
            secrets = {
                seed.lower(),
                seed.upper(),
                CHANNEL_KEY.lower(),
                CHANNEL_KEY.upper(),
                RADIO_PRIVATE_KEY.hex(),
                RADIO_PRIVATE_KEY.hex().upper(),
            }
            for text in recorder.dumps():
                for secret in secrets:
                    assert secret not in text
        finally:
            keystore.clear_keys()


class SlowServer:
    """An ``httpx.AsyncClient`` whose server takes the request, then never answers."""

    def __init__(self, monkeypatch) -> None:
        self.requests: list[httpx.Request] = []
        self.started = asyncio.Event()
        outer = self

        async def hang(request: httpx.Request) -> httpx.Response:
            outer.requests.append(request)
            outer.started.set()
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        class Slow(_RealAsyncClient):
            def __init__(self, **kwargs):
                kwargs["transport"] = httpx.MockTransport(hang)
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Slow)


class TestTurningOffCutsInFlight:
    @pytest.mark.parametrize("name", ["create_link", "get_meta", "fetch_object"])
    async def test_request_in_flight_is_cut(self, test_db, monkeypatch, name):
        await AppSettingsRepository.update(share_enabled=True)
        Dns(monkeypatch)
        server = SlowServer(monkeypatch)
        call = asyncio.ensure_future(SERVICE_CALLS[name]())
        await asyncio.wait_for(server.started.wait(), 5)
        await AppSettingsRepository.update(share_enabled=False)
        with pytest.raises(ShareError) as info:
            await asyncio.wait_for(call, 2)
        assert info.value.code == "share_disabled"
        assert share._state.inflight == set()
        # The cut is not a service failure.
        assert share.share_breaker().failures == 0
        for other in SERVICE_CALLS.values():
            with pytest.raises(ShareError):
                await other()
        assert len(server.requests) == 1

    async def test_other_settings_do_not_cut(self, test_db, monkeypatch):
        await AppSettingsRepository.update(share_enabled=True)
        Dns(monkeypatch)
        server = SlowServer(monkeypatch)
        call = asyncio.ensure_future(share.get_meta("AbCdEfG"))
        await asyncio.wait_for(server.started.wait(), 5)
        await AppSettingsRepository.update(share_preview_policy="never")
        await asyncio.sleep(0.05)
        assert not call.done()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

    async def test_caller_cancellation_stays_a_cancellation(self, test_db, monkeypatch):
        await AppSettingsRepository.update(share_enabled=True)
        Dns(monkeypatch)
        server = SlowServer(monkeypatch)
        call = asyncio.ensure_future(share.get_meta("AbCdEfG"))
        await asyncio.wait_for(server.started.wait(), 5)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        assert share._state.inflight == set()

    async def test_off_and_on_during_minting_sends_nothing(self, test_db, monkeypatch):
        """Turned off (then on again) while the token is minted: the old request never leaves."""
        await AppSettingsRepository.update(share_enabled=True)
        Dns(monkeypatch)
        recorder = Recorder(monkeypatch, _answer)
        real_mint = share.mint_share_jwt

        async def mint_then_toggle(binding: str) -> str:
            token = await real_mint(binding)
            await AppSettingsRepository.update(share_enabled=False)
            await AppSettingsRepository.update(share_enabled=True)
            return token

        monkeypatch.setattr(share, "mint_share_jwt", mint_then_toggle)
        with pytest.raises(ShareError) as info:
            await share.create_link("https://example.com/a")
        assert info.value.code == "share_disabled"
        assert recorder.requests == []

    async def test_answer_from_before_the_cut_is_never_used(self, test_db, monkeypatch):
        """The answer arrives, but sharing was turned off while it was on its way."""
        await AppSettingsRepository.update(share_enabled=True)
        Dns(monkeypatch)
        real_follow = share.follow_checked_redirects

        async def answer_after_cut(*args, **kwargs):
            response = await real_follow(*args, **kwargs)
            # Off without the cancel reaching this task (it is already done).
            share._state.generation += 1
            return response

        Recorder(monkeypatch, _answer)
        monkeypatch.setattr(share, "follow_checked_redirects", answer_after_cut)
        with pytest.raises(ShareError) as info:
            await share.get_meta("AbCdEfG")
        assert info.value.code == "share_disabled"
