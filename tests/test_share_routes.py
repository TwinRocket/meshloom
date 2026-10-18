"""``/api/share``: status, link creation, meta, permissions, the cross-site guard."""

from __future__ import annotations

import httpx
import pytest

from app import keystore
from app.repository import AppSettingsRepository
from app.services import share
from app.services.instance_identity import ensure_instance_key
from tests.share_fakes import Dns, Recorder, json_response

LINK = {"id": "AbCdEfG", "url": "https://meshl.ink/AbCdEfG", "expires_at": 1791636000}
META = {"id": "AbCdEfG", "kind": "link", "target_host": "example.com", "expires_at": 1791636000}
URL = "https://example.com/some/long/path"

# The recorder replaces httpx.AsyncClient: keep the real class for the app client.
_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
async def client():
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _service(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/links":
        return json_response(201, LINK)
    if request.url.path == "/v1/meta/GoneGon":
        return json_response(410, {"code": "gone"})
    if request.url.path == "/v1/meta/HtMeErr":
        return httpx.Response(404, content=b"<html>", headers={"Content-Type": "text/html"})
    return json_response(200, META)


@pytest.fixture
async def on(test_db, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True)
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


@pytest.fixture
async def off(test_db, monkeypatch):
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


class TestStatus:
    async def test_off_makes_no_request(self, off, client):
        response = await client.get("/api/share/status")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "enabled": False,
            "host": share.SHARE_HOST,
            "hosts": list(share.SHARE_HOSTS),
            "identity_ready": False,
            "dm_encryption_available": False,
            "banned": False,
            "shorten_mode": "auto",
            "preview_policy": "private",
        }
        assert off.requests == []

    async def test_identity_ready_and_private_key_follow_the_instance(self, on, client):
        await ensure_instance_key("share")
        keystore.set_private_key(bytes(range(64)))
        try:
            body = (await client.get("/api/share/status")).json()
        finally:
            keystore.clear_keys()
        assert body["enabled"] is True
        assert body["identity_ready"] is True
        assert body["dm_encryption_available"] is True
        assert on.requests == []


class TestLinks:
    async def test_off_is_share_disabled_with_no_egress(self, off, client):
        response = await client.post("/api/share/links", json={"url": URL})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_disabled"
        assert off.requests == []

    async def test_creates_with_one_request(self, on, client):
        response = await client.post("/api/share/links", json={"url": URL})
        assert response.status_code == 201
        assert response.json() == LINK
        assert on.hosts() == [share.SHARE_HOST]

    @pytest.mark.parametrize(
        "url",
        ["", "ftp://example.com/a", "javascript:alert(1)", "https://", "https://a b", "x" * 3000],
    )
    async def test_invalid_url_is_refused_locally(self, on, client, url):
        response = await client.post("/api/share/links", json={"url": url})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_url"
        assert on.requests == []

    async def test_service_error_is_coded(self, test_db, monkeypatch, client):
        await AppSettingsRepository.update(share_enabled=True)
        Dns(monkeypatch)
        Recorder(monkeypatch, lambda _r: json_response(422, {"code": "target_blocked"}))
        response = await client.post("/api/share/links", json={"url": URL})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "share_target_blocked"


class TestCrossSiteGuard:
    """The auth gate already refuses these for a session; a bearer token is exempt from it."""

    @pytest.fixture(autouse=True)
    def _token(self, sign_in_as):
        sign_in_as("admin", credential="token")

    async def test_cross_site_is_refused_without_egress(self, on, client):
        response = await client.post(
            "/api/share/links", json={"url": URL}, headers={"Sec-Fetch-Site": "cross-site"}
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_cross_site"
        assert on.requests == []

    @pytest.mark.parametrize("value", [None, "same-origin", "same-site", "none"])
    async def test_other_values_and_no_header_pass(self, on, client, value):
        headers = {"Sec-Fetch-Site": value} if value else {}
        response = await client.post("/api/share/links", json={"url": URL}, headers=headers)
        assert response.status_code == 201
        assert len(on.requests) == 1

    async def test_cross_site_read_is_not_guarded(self, on, client):
        response = await client.get("/api/share/status", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 200


class TestMeta:
    async def test_off_is_share_disabled(self, off, client):
        response = await client.get("/api/share/AbCdEfG/meta")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_disabled"
        assert off.requests == []

    async def test_meta_is_cached_ten_minutes(self, on, client, monkeypatch):
        now = [1000.0]
        share.reset_share_client_for_tests(lambda: now[0])
        first = await client.get("/api/share/AbCdEfG/meta")
        assert first.status_code == 200
        assert first.json() == {
            "kind": "link",
            "target_host": "example.com",
            "expires_at": 1791636000,
        }
        await client.get("/api/share/AbCdEfG/meta")
        assert len(on.requests) == 1
        now[0] += share.META_CACHE_S + 1
        await client.get("/api/share/AbCdEfG/meta")
        assert len(on.requests) == 2

    async def test_gone_is_cached_and_html_error_is_not(self, on, client):
        for _ in range(2):
            response = await client.get("/api/share/GoneGon/meta")
            assert response.status_code == 410
        assert len(on.requests) == 1
        for _ in range(2):
            response = await client.get("/api/share/HtMeErr/meta")
            assert response.status_code == 502
            assert response.json()["detail"]["code"] == "share_unavailable"
        assert len(on.requests) == 3

    async def test_bad_id_is_a_local_404(self, on, client):
        response = await client.get("/api/share/not-an-id/meta")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "share_not_found"
        assert on.requests == []


class TestPermissions:
    async def test_roles(self, on, client, sign_in_as):
        for role, write in (("kiosk", 403), ("viewer", 403), ("member", 201), ("admin", 201)):
            sign_in_as(role)
            assert (await client.post("/api/share/links", json={"url": URL})).status_code == write
            assert (await client.get("/api/share/status")).status_code == 200
            assert (await client.get("/api/share/AbCdEfG/meta")).status_code == 200
