"""Meshloom Remote and Links & Files need Community on (MSHL-445).

Both run on TwinRocket infrastructure (``relay.meshloom.link``, ``meshl.ink``). With
Community off:

- Remote cannot be turned on (409 ``remote_community_off``, nothing written, no
  request); an enabled Remote makes no registry request and its relay socket stays
  closed (link status ``community_off``), while its opt-in, registration and devices
  are kept, so turning Community on again resumes it;
- sharing cannot be turned on (409 ``share_community_off``); with ``share_enabled``
  and the terms kept, it is effectively off (``share_egress_state()`` None, status
  ``enabled`` false), and a request in flight is cut the moment Community goes off;
- a restore cannot leave either active: the effective state follows the Community
  opt-in, whatever the restored rows say.

The opt-out itself (``update_community``) pausing the relay socket is in
``tests/test_community_off_no_egress.py`` (``TestRemoteNeedsCommunity``), next to the
other Community transitions.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from app.auth.audit import SYSTEM_ACTOR
from app.repository import AppSettingsRepository
from app.repository.auth import UserRepository
from app.repository.remote import RemoteRepository
from app.services import meshloom_community, remote, remote_relay, share
from app.services.backup_sections import run_on_restore_hooks
from app.services.share import ShareError
from tests.share_fakes import PUBLIC_IP, Dns, Recorder, json_response

_RealAsyncClient = httpx.AsyncClient

SLUG = "k3xwq2m7ta"
REGISTERED = {
    "enabled": True,
    "slug": SLUG,
    "hostname": f"{SLUG}.meshloom.link",
    "relay_url": "wss://relay.meshloom.link/v1/connect",
}


def _answer(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/instances":
        return json_response(201, {k: v for k, v in REGISTERED.items() if k != "enabled"})
    if request.url.path.startswith("/v1/meta/"):
        return json_response(200, {"id": "AbCdEfG", "kind": "link", "target_host": "example.com"})
    return httpx.Response(204)


@pytest.fixture
async def recorder(test_db, monkeypatch):
    await UserRepository.create("test-admin", "admin")
    Dns(monkeypatch, {remote.RELAY_HOST: [PUBLIC_IP], share.SHARE_HOST: [PUBLIC_IP]})
    return Recorder(monkeypatch, _answer)


@pytest.fixture
async def client():
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _set_community(enabled: bool) -> None:
    """Flip the stored opt-in and run the listeners, as ``update_community`` does."""
    await AppSettingsRepository.update_community(enabled=enabled)
    await meshloom_community._notify_enabled_change()  # noqa: SLF001


class _WsRecorder:
    """Replaces ``websockets.connect``: records the URL, then refuses."""

    def __init__(self, monkeypatch) -> None:
        self.urls: list[str] = []
        monkeypatch.setattr("websockets.connect", self)

    def __call__(self, url: str, **_kwargs: Any) -> Any:
        self.urls.append(url)
        raise OSError("refused by the test")


async def _until(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


class TestRemote:
    async def test_enable_is_refused_and_nothing_is_written(self, recorder):
        with pytest.raises(remote.RemoteError) as exc:
            await remote.enable(SYSTEM_ACTOR)
        assert (exc.value.status_code, exc.value.code) == (409, "remote_community_off")
        assert (await RemoteRepository.get_state()).enabled is False
        assert recorder.requests == []

    async def test_enable_route_answers_the_code(self, recorder, client):
        response = await client.post("/api/remote/enable")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "remote_community_off"
        status = (await client.get("/api/remote")).json()
        assert (status["enabled"], status["community_enabled"]) == (False, False)
        assert recorder.requests == []

    async def test_an_enabled_remote_makes_no_registry_request(self, recorder):
        await RemoteRepository.update_state(**REGISTERED)
        for call in (
            remote.register(SYSTEM_ACTOR),
            remote.unregister(SYSTEM_ACTOR),
            remote._registry("GET", "/v1/instances/self"),  # noqa: SLF001
        ):
            with pytest.raises(remote.RemoteError) as exc:
                await call
            assert exc.value.code == "remote_community_off"
        assert recorder.requests == []
        # Kept: Community on again resumes it.
        state = await RemoteRepository.get_state()
        assert state.enabled and state.slug == SLUG

    async def test_enable_works_once_community_is_on(self, recorder):
        await AppSettingsRepository.update_community(enabled=True)
        state = await remote.enable(SYSTEM_ACTOR)
        assert state.enabled and state.slug == SLUG
        assert recorder.hosts() == [remote.RELAY_HOST]

    async def test_the_relay_link_follows_the_community_opt_in(self, test_db, monkeypatch):
        from app.main import app

        sockets = _WsRecorder(monkeypatch)
        monkeypatch.setattr(remote_relay, "BACKOFF_INITIAL_S", 0.01)
        await RemoteRepository.update_state(**REGISTERED)
        relay = remote_relay.get_relay_client()
        try:
            await relay.start(app)
            assert not relay.running()
            assert remote.link_status() == remote_relay.COMMUNITY_OFF
            assert sockets.urls == []

            await _set_community(True)
            await _until(lambda: len(sockets.urls) >= 2)

            assert remote.receive_access_request(
                {
                    "request_id": "B" * 22,
                    "code": "7K3QM-X9TZ2",
                    "user_agent": "phone",
                    "created_at": int(time.time()),
                }
            )
            assert remote.pending_access_requests()
            await _set_community(False)
            assert not relay.running()
            assert remote.link_status() == remote_relay.COMMUNITY_OFF
            assert remote.pending_access_requests() == []
            count = len(sockets.urls)
            await asyncio.sleep(0.05)
            assert len(sockets.urls) == count
        finally:
            await relay.stop()
            relay.app = None
        state = await RemoteRepository.get_state()
        assert state.enabled and state.slug == SLUG


class TestShare:
    async def test_turning_sharing_on_is_refused(self, recorder, client):
        response = await client.patch(
            "/api/settings", json={"share_enabled": True, "share_terms_accepted": True}
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "share_community_off"
        settings = await AppSettingsRepository.get()
        assert settings.share_enabled is False and settings.share_terms_version == 0
        # Turning it off stays possible.
        assert (await client.patch("/api/settings", json={"share_enabled": False})).is_success
        assert recorder.requests == []

    async def test_kept_consent_is_effectively_off(self, recorder, client):
        await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
        assert await share.share_egress_state() is None
        status = (await client.get("/api/share/status")).json()
        assert (status["enabled"], status["community_enabled"]) == (False, False)
        assert (await client.get("/api/settings")).json()["share_effective"] is False
        for call in (
            share.create_link("https://example.com/a"),
            share.get_meta("AbCdEfG"),
            share.list_mine(50, None),
        ):
            with pytest.raises(ShareError) as exc:
                await call
            assert exc.value.code == "share_disabled"
        response = await client.post("/api/share/links", json={"url": "https://example.com/a"})
        assert response.status_code == 403
        assert recorder.requests == []
        # The choice and the terms are kept: Community on resumes sharing.
        await AppSettingsRepository.update_community(enabled=True)
        assert await share.share_egress_state() is not None
        status = (await client.get("/api/share/status")).json()
        assert (status["enabled"], status["community_enabled"]) == (True, True)
        await share.get_meta("AbCdEfG")
        assert recorder.hosts() == [share.SHARE_HOST]

    async def test_community_off_cuts_a_request_in_flight(self, test_db, monkeypatch):
        await AppSettingsRepository.update_community(enabled=True)
        await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
        Dns(monkeypatch)
        started = asyncio.Event()

        async def hang(request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

        class Slow(_RealAsyncClient):
            def __init__(self, **kwargs):
                kwargs["transport"] = httpx.MockTransport(hang)
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Slow)
        call = asyncio.ensure_future(share.get_meta("AbCdEfG"))
        await asyncio.wait_for(started.wait(), 5)
        await _set_community(False)
        with pytest.raises(ShareError) as exc:
            await asyncio.wait_for(call, 2)
        assert exc.value.code == "share_disabled"
        assert share._state.inflight == set()  # noqa: SLF001
        # Not a share settings change: the stored choice is untouched.
        assert (await AppSettingsRepository.get()).share_enabled is True


class TestRestore:
    async def test_a_restore_with_community_off_leaves_nothing_active(self, recorder, monkeypatch):
        """Whatever the restored rows say, Community off means Remote and sharing off."""
        from app.main import app

        sockets = _WsRecorder(monkeypatch)
        await RemoteRepository.update_state(**REGISTERED)
        await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
        await run_on_restore_hooks("json")
        assert (await RemoteRepository.get_state()).enabled is False
        assert await share.share_egress_state() is None
        # Even rows that claim Remote on (a database copy) never open the relay socket.
        await RemoteRepository.update_state(**REGISTERED)
        relay = remote_relay.get_relay_client()
        try:
            await relay.start(app)
            assert not relay.running()
        finally:
            await relay.stop()
            relay.app = None
        assert sockets.urls == []
        assert recorder.requests == []
