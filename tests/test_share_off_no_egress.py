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
