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
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
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
            "unreachable": False,
            "shorten_mode": "auto",
            "preview_policy": "private",
            "terms_version": share.SHARE_TERMS_VERSION,
            "terms_accepted_version": 0,
            "terms_accepted_at": None,
            "terms_stale": False,
        }
        assert off.requests == []

    async def test_reports_the_recorded_acceptance(self, on, client):
        body = (await client.get("/api/share/status")).json()
        assert body["enabled"] is True
        assert body["terms_accepted_version"] == share.SHARE_TERMS_VERSION
        assert isinstance(body["terms_accepted_at"], int) and body["terms_accepted_at"] > 0
        assert body["terms_stale"] is False

    async def test_a_new_terms_version_turns_it_off_until_accepted_again(
        self, on, client, monkeypatch
    ):
        monkeypatch.setattr(share, "SHARE_TERMS_VERSION", share.SHARE_TERMS_VERSION + 1)
        body = (await client.get("/api/share/status")).json()
        assert body["enabled"] is False
        assert body["terms_stale"] is True
        assert body["terms_version"] == share.SHARE_TERMS_VERSION
        response = await client.post("/api/share/links", json={"url": URL})
        assert response.status_code == 403
        assert on.requests == []
        refused = await client.patch("/api/settings", json={"share_enabled": True})
        assert refused.status_code == 409
        assert refused.json()["detail"] == "share_terms_required"
        accepted = await client.patch(
            "/api/settings", json={"share_enabled": True, "share_terms_accepted": True}
        )
        assert accepted.status_code == 200
        body = (await client.get("/api/share/status")).json()
        assert (body["enabled"], body["terms_stale"]) == (True, False)

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

    async def test_unreachable_follows_the_breaker_without_a_request(self, off, client):
        breaker = share.share_breaker()
        for _ in range(share.BREAKER_FAILURE_THRESHOLD):
            breaker.record_failure()
        try:
            body = (await client.get("/api/share/status")).json()
        finally:
            breaker.record_success()
        assert body["unreachable"] is True
        assert off.requests == []
        assert (await client.get("/api/share/status")).json()["unreachable"] is False


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
        [
            "",
            "ftp://example.com/a",
            "javascript:alert(1)",
            "https://",
            "https://a b",
            "x" * 3000,
            "https://user:pass@example.com/a",
            "https://user@example.com/a",
            "http://192.168.1.10/admin",
            "http://127.0.0.1:8000/",
            "http://[::1]/",
            "http://[fd00::1]/x",
            "http://localhost/a",
            "http://localhost:8000/a",
            "http://printer.local/a",
            "http://nas.lan/a",
            "http://router.internal/a",
            "http://app.localhost/a",
            "http://intranet/a",
            "http://example.com:notaport/a",
        ],
    )
    async def test_invalid_url_is_refused_locally(self, on, client, url):
        response = await client.post("/api/share/links", json={"url": url})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_url"
        assert on.requests == []

    async def test_public_hosts_with_ports_and_paths_pass(self, on, client):
        for url in ("http://example.com:8080/a?b=c#d", "https://sub.example.co.uk/"):
            assert (await client.post("/api/share/links", json={"url": url})).status_code == 201

    async def test_service_error_is_coded(self, test_db, monkeypatch, client):
        await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
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

    @pytest.mark.parametrize("value", ["same-site", "SAME-SITE ", "cross-origin", "bogus"])
    async def test_same_site_and_unknown_values_are_refused(self, on, client, value):
        """Same-origin only: without the Public Suffix List every
        <slug>.meshloom.link instance (or a self-hoster's other apps) is same-site."""
        response = await client.post(
            "/api/share/links", json={"url": URL}, headers={"Sec-Fetch-Site": value}
        )
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_cross_site"
        assert on.requests == []

    @pytest.mark.parametrize("value", [None, "", "same-origin", " Same-Origin", "none"])
    async def test_same_origin_none_and_no_header_pass(self, on, client, value):
        headers = {"Sec-Fetch-Site": value} if value is not None else {}
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
        # MSHL-382: the answer may describe a decrypted file, never cached downstream.
        assert first.headers["cache-control"] == "no-store"
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


# Objects: GET /api/share/{id} ------------------------------------------------

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a5f6b1bd0000000049454e44ae426082"
)
PNG_40MPX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200001f40000013880802000000")
PNG_42MPX = bytes.fromhex("89504e470d0a1a0a0000000d494844520000177000001b580802000000")
HTML_AS_PNG = bytes.fromhex("3c68746d6c3e3c7363726970743e")
FILE_META = {
    "id": "FiLeAbC",
    "kind": "file",
    "mode": "public",
    "content_type": "image/png",
    "size": len(PNG_1X1),
    "created_at": 1791000000,
    "expires_at": 1791636000,
}

OBJECTS: dict[str, tuple[bytes, str]] = {
    "/PnGokAy": (PNG_1X1, "image/png"),
    "/PnGfrty": (PNG_40MPX, "image/png"),
    "/PnGbigX": (PNG_42MPX, "image/png"),
    "/HtMxPnG": (HTML_AS_PNG, "image/png"),
    "/BiNaRyX": (b"\x00\x01binary", "application/octet-stream"),
    "/HtMzPag": (b"<html>", "text/html"),
}


def _objects(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/meta/FiLeAbC":
        return json_response(200, FILE_META)
    if path == "/GoneGon":
        return json_response(410, {"code": "gone"})
    if path == "/MisSiNg":
        return json_response(404, {"code": "not_found"})
    if path == "/DoWnSvc":
        return json_response(503, {"code": "unavailable"})
    if path == "/BiGgone":
        body = b"\x89PNG\r\n\x1a\n" + b"\x00" * (share.MAX_OBJECT_RESPONSE_BYTES + 1)
        return httpx.Response(200, content=body, headers={"Content-Type": "image/png"})
    if path == "/EnVeLop":
        body = b"MLF1" + b"\x00" * (share.MAX_ENVELOPE_BYTES - 4)
        return httpx.Response(200, content=body, headers={"Content-Type": share.OCTET_STREAM})
    if path == "/EnVbigg":
        body = b"MLF1" + b"\x00" * (share.MAX_ENVELOPE_BYTES - 3)
        return httpx.Response(200, content=body, headers={"Content-Type": share.OCTET_STREAM})
    if path == "/PnGhuge":
        body = PNG_1X1 + b"\x00" * (share.MAX_IMAGE_BYTES + 1 - len(PNG_1X1))
        return httpx.Response(200, content=body, headers={"Content-Type": "image/png"})
    if path in OBJECTS:
        content, content_type = OBJECTS[path]
        return httpx.Response(200, content=content, headers={"Content-Type": content_type})
    return json_response(200, META)


@pytest.fixture
async def objects_on(test_db, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    Dns(monkeypatch)
    return Recorder(monkeypatch, _objects)


async def _message(msg_type: str, text: str) -> int:
    from app.repository import MessageRepository

    message_id = await MessageRepository.create(
        msg_type=msg_type,
        text=text,
        received_at=1791000000,
        conversation_key="0" * 64 if msg_type == "PRIV" else "1" * 32,
    )
    assert message_id is not None
    return message_id


class TestObjects:
    async def test_second_request_is_served_from_disk(self, objects_on, client):
        first = await client.get("/api/share/PnGokAy")
        assert first.status_code == 200
        assert first.content == PNG_1X1
        assert first.headers["content-type"] == "image/png"
        second = await client.get("/api/share/PnGokAy")
        assert second.status_code == 200
        assert second.content == PNG_1X1
        assert len(objects_on.requests) == 1
        request = objects_on.requests[0]
        assert request.headers["accept"] == "application/octet-stream"
        assert objects_on.hosts() == [share.SHARE_HOST]

    async def test_safe_headers(self, objects_on, client):
        image = await client.get("/api/share/PnGokAy")
        assert image.headers["x-content-type-options"] == "nosniff"
        assert image.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert image.headers["cache-control"] == "private, max-age=3600"
        assert image.headers["content-disposition"] == "inline; filename*=UTF-8''PnGokAy.png"
        # An octet-stream object is an encrypted envelope: never served raw, and
        # without a message to take the secret from it cannot be decrypted.
        binary = await client.get("/api/share/BiNaRyX")
        assert binary.status_code == 422
        assert binary.json()["detail"]["code"] == "share_undecryptable"

    async def test_off_is_refused_even_when_cached(self, objects_on, client):
        assert (await client.get("/api/share/PnGokAy")).status_code == 200
        await AppSettingsRepository.update(share_enabled=False)
        response = await client.get("/api/share/PnGokAy")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_disabled"
        assert len(objects_on.requests) == 1

    async def test_off_makes_no_request(self, off, client):
        response = await client.get("/api/share/PnGokAy")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_disabled"
        assert off.requests == []

    async def test_gone_is_not_refetched_for_ten_minutes(self, objects_on, client):
        now = [1000.0]
        share.reset_share_client_for_tests(lambda: now[0])
        for _ in range(2):
            response = await client.get("/api/share/GoneGon")
            assert response.status_code == 410
        assert len(objects_on.requests) == 1
        now[0] += share.NEGATIVE_CACHE_S - 1
        assert (await client.get("/api/share/GoneGon")).status_code == 410
        assert len(objects_on.requests) == 1
        now[0] += 2
        assert (await client.get("/api/share/GoneGon")).status_code == 410
        assert len(objects_on.requests) == 2

    async def test_not_found_is_cached_and_unavailable_is_not(self, objects_on, client):
        for _ in range(2):
            assert (await client.get("/api/share/MisSiNg")).status_code == 404
        assert len(objects_on.requests) == 1
        for _ in range(2):
            response = await client.get("/api/share/DoWnSvc")
            assert response.status_code == 502
            assert response.json()["detail"]["code"] == "share_unavailable"
        response = await client.get("/api/share/HtMzPag")
        assert response.json()["detail"]["code"] == "share_unavailable"

    async def test_bad_id_is_a_400_without_request(self, objects_on, client):
        for bad in ("not-an-id", "AbCdEf", "AbCdEfGH", "AbC_EfG"):
            response = await client.get(f"/api/share/{bad}")
            assert response.status_code == 400, bad
            assert response.json()["detail"]["code"] == "share_invalid_id"
        assert objects_on.requests == []

    async def test_only_message_id_is_accepted(self, objects_on, client):
        for query in ("?foo=1", "?message_id=1&message_id=2", "?url=https://evil.example"):
            response = await client.get(f"/api/share/PnGokAy{query}")
            assert response.status_code == 400, query
            assert response.json()["detail"]["code"] == "share_invalid_request"
        assert objects_on.requests == []

    async def test_pixel_cap(self, objects_on, client):
        assert (await client.get("/api/share/PnGfrty")).status_code == 200
        refused = await client.get("/api/share/PnGbigX")
        assert refused.status_code == 415
        assert refused.json()["detail"]["code"] == "share_type_refused"
        # A refusal is remembered: no second download.
        assert (await client.get("/api/share/PnGbigX")).status_code == 415
        assert len(objects_on.requests) == 2

    async def test_html_declared_as_png_is_refused(self, objects_on, client):
        response = await client.get("/api/share/HtMxPnG")
        assert response.status_code == 415
        assert response.json()["detail"]["code"] == "share_type_refused"

    async def test_too_large_is_refused(self, objects_on, client):
        response = await client.get("/api/share/BiGgone")
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "share_too_large"
        assert (await client.get("/api/share/BiGgone")).status_code == 413
        assert len(objects_on.requests) == 1

    async def test_envelope_cap_is_the_contract_maximum(self, objects_on, client):
        assert share.MAX_ENVELOPE_BYTES == 10_489_891
        # The largest envelope is fetched and kept (decrypting it then needs a
        # message), one byte more is refused before any decryption.
        exact = await client.get("/api/share/EnVeLop")
        assert exact.status_code == 422
        assert exact.json()["detail"]["code"] == "share_undecryptable"
        assert len((await share.read_object("EnVeLop")).content) == share.MAX_ENVELOPE_BYTES
        over = await client.get("/api/share/EnVbigg")
        assert over.status_code == 413
        assert over.json()["detail"]["code"] == "share_too_large"
        image = await client.get("/api/share/PnGhuge")
        assert image.status_code == 413
        assert image.json()["detail"]["code"] == "share_too_large"

    async def test_trailing_newline_is_not_an_id(self, objects_on, client, sign_in_as):
        private = await _message("PRIV", "look https://meshl.ink/PnGokAy")
        sign_in_as("kiosk")
        response = await client.get("/api/share/PnGokAy%0A")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_id"
        meta = await client.get(f"/api/share/PnGokAy%0A/meta?message_id={private}")
        assert meta.status_code == 404
        assert objects_on.requests == []

    async def test_kiosk_cannot_reach_a_private_message(self, objects_on, client, sign_in_as):
        private = await _message("PRIV", "look https://meshl.ink/PnGokAy")
        channel = await _message("CHAN", "look https://meshl.ink/PnGokAy")
        unrelated = await _message("CHAN", "nothing here")
        sign_in_as("kiosk")
        refused = await client.get(f"/api/share/PnGokAy?message_id={private}")
        assert refused.status_code == 403
        assert (
            await client.get(f"/api/share/PnGokAy/meta?message_id={private}")
        ).status_code == 403
        assert (await client.get(f"/api/share/PnGokAy?message_id={unrelated}")).status_code == 404
        assert (await client.get("/api/share/PnGokAy?message_id=999999")).status_code == 404
        assert objects_on.requests == []
        allowed = await client.get(f"/api/share/PnGokAy?message_id={channel}")
        assert allowed.status_code == 200
        sign_in_as("viewer")
        assert (await client.get(f"/api/share/PnGokAy?message_id={private}")).status_code == 200

    async def test_cache_clear(self, objects_on, client, sign_in_as):
        assert (await client.get("/api/share/PnGokAy")).status_code == 200
        sign_in_as("member")
        assert (await client.post("/api/share/cache/clear")).status_code == 403
        sign_in_as("admin")
        cleared = await client.post("/api/share/cache/clear")
        assert cleared.status_code == 200
        assert cleared.json()["freed_bytes"] > 0
        assert (await client.get("/api/share/PnGokAy")).status_code == 200
        assert len(objects_on.requests) == 2

    async def test_cache_clear_works_when_off(self, off, client):
        response = await client.post("/api/share/cache/clear")
        assert response.status_code == 200
        assert response.json() == {"freed_bytes": 0}
        assert off.requests == []

    async def test_meta_of_a_public_file(self, objects_on, client):
        response = await client.get("/api/share/FiLeAbC/meta")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "file"
        assert body["mode"] == "public"
        assert body["content_type"] == "image/png"
        assert body["size"] == len(PNG_1X1)
        assert body["name"] is None
        assert body["decryptable"] is True
        assert body["expires_at"] == 1791636000
