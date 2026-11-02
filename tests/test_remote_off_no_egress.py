"""Remote off means no connection to the relay or its registry (MSHL-79).

A recorder replaces ``httpx.AsyncClient`` for the whole test (the egress guard's
pinned clients are ``httpx.AsyncClient`` too). With ``remote_state.enabled`` false,
every ``/api/remote/*`` route but ``POST /api/remote/enable`` (found from
``app.routes``) and every network function of ``app.services.remote`` answers without
any request. Turned on, requests go only to ``RELAY_HOST`` and carry neither the
Remote seed nor the share seed, in any form. Disabled again, nothing leaves.

The relay socket (``app/services/remote_relay.py``, MSHL-87) never opens while Remote
is off or unregistered, whatever the Community state: with Community reported on, a
recorder in place of ``websockets.connect`` sees nothing. The other direction (Remote
on, Community off: only the relay host) is in ``tests/test_community_off_no_egress.py``.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import httpx
import pytest
from fastapi.routing import APIRoute

from app.auth.audit import SYSTEM_ACTOR
from app.repository import AppSettingsRepository
from app.repository.auth import UserRepository
from app.services import remote
from app.services.instance_identity import REMOTE_SERVICE, SHARE_SERVICE, ensure_instance_key
from tests.share_fakes import PUBLIC_IP, Dns, Recorder, json_response

_RealAsyncClient = httpx.AsyncClient

PATH_PARAMS = {"device_id": "A" * 22, "request_id": "B" * 22}
BODIES = {
    ("POST", "/api/remote/devices/client"): {"label": "sweep"},
    ("POST", "/api/remote/access-requests/approve"): {"code": "7K3QM-X9TZ2"},
    ("POST", "/api/remote/domain/test"): {"domain": "chat.example.org"},
}
# Network functions of the service (found from the module: a new one must be listed).
_NETWORK = {"_registry", "_register_locked", "register", "enable", "unregister", "rotate"}
# Also network, but towards the user's own domain, never the relay (tests/test_remote_domain.py).
_NETWORK |= {"_probe_domain", "check_custom_domain"}
# Coroutine functions that never connect.
_LOCAL = {
    "_notify",
    "load",
    "send_control",
    "disable",
    "_reset_after_restore",
    "gate_state",
    "push_gate",
    "pass_state",
    "_sign",
    "verify_device_pass",
    "_require_registered",
    "_new_device",
    "issue_enroll_token",
    "issue_client_pass",
    "_answer_enroll",
    "answer_enroll",
    "approve_access",
    "deny_access",
    "revoke_device",
    "revoke_all_devices",
    "handle_control",
    "key_fingerprint",
}


def _registry(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/instances":
        return json_response(
            201,
            {
                "slug": "k3xwq2m7ta",
                "hostname": "k3xwq2m7ta.meshloom.link",
                "relay_url": "wss://relay.meshloom.link/v1/connect",
            },
        )
    return httpx.Response(204)


@pytest.fixture
async def recorder(test_db, monkeypatch):
    await UserRepository.create("test-admin", "admin")
    Dns(monkeypatch, {remote.RELAY_HOST: [PUBLIC_IP]})
    return Recorder(monkeypatch, _registry)


def _walk(routes: list[Any], prefix: str = "") -> list[tuple[str, APIRoute]]:
    """Every APIRoute with its full path, through FastAPI's lazy includes."""
    out: list[tuple[str, APIRoute]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            out.append((prefix + route.path, route))
        context = getattr(route, "include_context", None)
        if context is not None:
            out.extend(_walk(context.included_router.routes, prefix + context.prefix))
    return out


def _routes() -> list[tuple[str, str]]:
    from app.main import app

    out = []
    for path, route in _walk(app.routes):
        if path.startswith("/api/remote"):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                if (method, path) != ("POST", "/api/remote/enable"):
                    out.append((method, path))
    return out


def test_every_coroutine_of_the_service_is_classified():
    found = {
        name
        for name, value in vars(remote).items()
        if inspect.iscoroutinefunction(value) and value.__module__ == remote.__name__
    }
    assert found == _NETWORK | _LOCAL, found ^ (_NETWORK | _LOCAL)


async def test_off_nothing_leaves(recorder):
    from app.main import app

    routes = _routes()
    assert ("POST", "/api/remote/rotate") in routes and len(routes) >= 12
    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, template in routes:
            path = re.sub(r"\{(\w+)\}", lambda m: PATH_PARAMS[m.group(1)], template)
            response = await client.request(method, path, json=BODIES.get((method, template)))
            assert response.status_code != 422, (method, template, response.text)
    for call in (
        remote.register(SYSTEM_ACTOR),
        remote.unregister(SYSTEM_ACTOR),
        remote._registry("GET", "/v1/instances/self"),  # noqa: SLF001
    ):
        with pytest.raises(remote.RemoteError) as exc:
            await call
        assert exc.value.code == "remote_disabled"
    await remote.rotate(SYSTEM_ACTOR)
    assert recorder.requests == []


async def test_on_only_the_relay_host_and_no_seed(recorder):
    await ensure_instance_key(SHARE_SERVICE)
    await remote.enable(SYSTEM_ACTOR)
    await remote.register(SYSTEM_ACTOR)
    await remote.rotate(SYSTEM_ACTOR)
    await remote.unregister(SYSTEM_ACTOR)
    assert recorder.requests
    assert set(recorder.hosts()) == {remote.RELAY_HOST}
    seeds = [
        await AppSettingsRepository.get_instance_seed(REMOTE_SERVICE),
        await AppSettingsRepository.get_instance_seed(SHARE_SERVICE),
    ]
    for dump in recorder.dumps():
        for seed in seeds:
            assert seed and seed.lower() not in dump.lower()
    # Off again (unregister turned it off): nothing more leaves.
    count = len(recorder.requests)
    with pytest.raises(remote.RemoteError):
        await remote.register(SYSTEM_ACTOR)
    assert len(recorder.requests) == count


# ---------------------------------------------------------------- the relay socket (MSHL-87)


class _WsRecorder:
    """Replaces ``websockets.connect``: records the URL, then refuses."""

    def __init__(self, monkeypatch) -> None:
        self.urls: list[str] = []
        monkeypatch.setattr("websockets.connect", self)

    def __call__(self, url: str, **_kwargs: Any) -> Any:
        self.urls.append(url)
        raise OSError("refused by the test")


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"slug": "k3xwq2m7ta", "hostname": "k3xwq2m7ta.meshloom.link"},
        {"enabled": True},
    ],
    ids=["off", "off-but-registered", "on-but-unregistered"],
)
async def test_the_relay_socket_never_opens_while_off_or_unregistered(test_db, monkeypatch, state):
    """Community on (as far as any guard can tell) changes nothing for Remote."""
    from app.repository.remote import RemoteRepository
    from app.services import meshloom_community, remote_relay

    async def community_on() -> object:
        return object()

    monkeypatch.setattr(meshloom_community, "community_egress_state", community_on)
    monkeypatch.setattr(meshloom_community, "community_enrolled_state", community_on)
    sockets = _WsRecorder(monkeypatch)
    if state:
        await RemoteRepository.update_state(**state)
    client = remote_relay.RelayClient()
    from app.main import app

    await client.start(app)
    await client.restart()
    assert not client.running()
    assert sockets.urls == []
    await client.stop()
