"""With Community opted out, the Wi-Fi routes never reach a Community host.

``/api/radio/wifi`` is outside the prefixes swept by
``test_community_off_no_egress.py``, so this file calls every route under it,
with the same egress recorder, on the loopback radio.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import radio_wifi
from app.services.meshloom_community import get_community_effective, update_community
from tests.test_community_off_no_egress import (  # noqa: F401  (pytest fixtures)
    _RealAsyncClient,
    _walk_routes,
    cleanup_community,
    egress,
)
from tests.test_radio_wifi_router import PK, _make_radio

_BODIES: dict[tuple[str, str], dict] = {
    ("PUT", "/api/radio/wifi/credentials"): {"ssid": "Home", "password": "S3cr3t-Pw!"},
    ("PUT", "/api/radio/wifi/enabled"): {"enabled": False},
    ("POST", "/api/radio/wifi/prompt/dismiss"): {"radio_public_key": PK},
}


def _wifi_routes() -> list[tuple[str, str]]:
    from app.main import app

    routes = []
    for path, route in _walk_routes(app.routes):
        if path.startswith("/api/radio/wifi"):
            for method in sorted(route.methods or ()):
                routes.append((method, path))
    # The reboot last: it ends the session's setup.
    return sorted(routes, key=lambda item: item[1].endswith("/reboot"))


def test_every_wifi_route_is_covered():
    paths = {path for _method, path in _wifi_routes()}
    assert paths == {
        "/api/radio/wifi",
        "/api/radio/wifi/credentials",
        "/api/radio/wifi/enabled",
        "/api/radio/wifi/reboot",
        "/api/radio/wifi/prompt/dismiss",
    }


@pytest.mark.asyncio
async def test_wifi_routes_send_nothing_to_community_when_off(
    test_db,
    egress,  # noqa: F811
    cleanup_community,  # noqa: F811
    monkeypatch,
):
    from app.main import app

    await update_community(enabled=False, iata="CDG")
    assert (await get_community_effective()).enabled is False
    env = await _make_radio(monkeypatch)
    try:
        routes = _wifi_routes()
        assert len(routes) == 5
        async with _RealAsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for method, path in routes:
                response = await client.request(method, path, json=_BODIES.get((method, path)))
                assert response.status_code == 200, (method, path, response.text)
                assert not egress.forbidden(), (method, path, egress.events)
            response = await client.get("/api/radio/wifi", params={"refresh": "true"})
            assert not egress.forbidden(), egress.events
        assert egress.forbidden() == [], egress.events
    finally:
        radio_wifi.clear_cache()
        await env.loop.close()
