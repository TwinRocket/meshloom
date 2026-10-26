"""``POST /api/share/{id}/report`` (MSHL-246): forwarded with the instance key, coded errors."""

from __future__ import annotations

import base64
import hashlib
import json
import logging

import httpx
import pytest
from nacl.signing import VerifyKey

from app.repository import AppSettingsRepository
from app.services.instance_identity import instance_public_key
from tests.share_fakes import Dns, Recorder, json_response

# The recorder replaces httpx.AsyncClient: keep the real class for the app client.
_RealAsyncClient = httpx.AsyncClient

REPORTS: list[dict] = []


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _service(request: httpx.Request) -> httpx.Response:
    """A mocked service that stores each report row with the reporter's key."""
    if request.url.path != "/v1/reports":
        return json_response(404, {"code": "not_found"})
    body = json.loads(request.content)
    if body["id"] == "QuotaXX":
        return json_response(429, {"code": "quota_exceeded", "scope": "key"})
    if body["id"] == "BannedX":
        return json_response(403, {"code": "banned"})
    token = request.headers["authorization"].split(" ", 1)[1]
    header, payload, signature = token.split(".")
    claims = json.loads(_b64(payload))
    # The Community wire format: header_b64.payload_b64.signature_hex.
    VerifyKey(bytes.fromhex(claims["publicKey"])).verify(
        f"{header}.{payload}".encode(), bytes.fromhex(signature)
    )
    binding = f"{body['id']}\n{body['reason']}".encode()
    assert claims["bh"] == hashlib.sha256(binding).hexdigest()
    REPORTS.append({**body, "reporter": claims["publicKey"].lower()})
    return json_response(202, {"received_at": 1760100000})


@pytest.fixture
async def client():
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def on(test_db, monkeypatch):
    REPORTS.clear()
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


@pytest.fixture
async def off(test_db, monkeypatch):
    REPORTS.clear()
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


class TestReport:
    async def test_stores_a_row_with_the_instance_key(self, on, client):
        response = await client.post(
            "/api/share/AbCdEfG/report", json={"reason": "phishing", "details": "Fake login"}
        )
        assert response.status_code == 202
        assert response.json() == {"received": True}
        public_key = await instance_public_key("share")
        assert public_key is not None
        assert [
            {
                "id": "AbCdEfG",
                "reason": "phishing",
                "details": "Fake login",
                "reporter": public_key.hex(),
            }
        ] == REPORTS
        assert on.hosts() == ["meshl.ink"]

    async def test_details_are_optional(self, on, client):
        response = await client.post("/api/share/AbCdEfG/report", json={"reason": "spam"})
        assert response.status_code == 202
        assert REPORTS[0]["details"] == ""

    async def test_off_is_share_disabled_with_no_egress(self, off, client):
        response = await client.post("/api/share/AbCdEfG/report", json={"reason": "spam"})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_disabled"
        assert off.requests == []

    @pytest.mark.parametrize(
        "body",
        [
            {"reason": "boring"},
            {},
            {"reason": "spam", "details": "x" * 2001},
            {"reason": "spam", "email": "me@example.org"},
        ],
    )
    async def test_bad_body_is_refused_locally(self, on, client, body):
        response = await client.post("/api/share/AbCdEfG/report", json=body)
        assert response.status_code == 422
        assert on.requests == []

    async def test_bad_id_is_refused_locally(self, on, client):
        response = await client.post("/api/share/not-an-id/report", json={"reason": "spam"})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_id"
        assert on.requests == []

    @pytest.mark.parametrize(
        ("share_id", "status", "code"),
        [("QuotaXX", 429, "share_quota_key"), ("BannedX", 403, "share_banned")],
    )
    async def test_service_errors_are_coded(self, on, client, share_id, status, code):
        response = await client.post(f"/api/share/{share_id}/report", json={"reason": "spam"})
        assert response.status_code == status
        assert response.json()["detail"]["code"] == code

    async def test_cross_site_is_refused(self, on, client, sign_in_as):
        sign_in_as("admin", credential="token")
        response = await client.post(
            "/api/share/AbCdEfG/report",
            json={"reason": "spam"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_cross_site"
        assert on.requests == []

    async def test_roles(self, on, client, sign_in_as):
        for role, status in (("kiosk", 403), ("viewer", 403), ("member", 202), ("admin", 202)):
            sign_in_as(role)
            response = await client.post("/api/share/AbCdEfG/report", json={"reason": "spam"})
            assert response.status_code == status, role

    async def test_logs_carry_no_identifier(self, on, client, caplog):
        caplog.set_level(logging.DEBUG)
        await client.post(
            "/api/share/AbCdEfG/report", json={"reason": "spam", "details": "secret words"}
        )
        await client.post("/api/share/QuotaXX/report", json={"reason": "spam"})
        public_key = await instance_public_key("share")
        assert public_key is not None
        # The app's own loggers (httpx logs the test client's own requests).
        for record in (r for r in caplog.records if r.name.startswith("app")):
            text = record.getMessage()
            for needle in ("AbCdEfG", "QuotaXX", "secret words", public_key.hex()):
                assert needle.lower() not in text.lower()
