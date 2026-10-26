"""Encrypted share files (MSHL-255): upload, download, meta, and no secret anywhere.

A fake share service stores the envelopes it receives and serves them back, so
every test is a real round trip through ``POST /api/share/files`` and
``GET /api/share/{id}``. The secrets (channel key, DM shared secret, radio
private key) must never appear in a request, a log record or the cache.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
from pathlib import Path

import httpx
import pytest

from app import keystore
from app.channel_constants import PUBLIC_CHANNEL_KEY
from app.config import settings
from app.decoder import derive_public_key, derive_shared_secret
from app.models import CONTACT_TYPE_ROOM
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)
from app.services import share, share_crypto, share_upload
from tests.share_fakes import Dns, Recorder, json_response

_RealAsyncClient = httpx.AsyncClient

# A valid 1x1 PNG (checked again after decryption).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a5f6b1bd0000000049454e44ae426082"
)
NAME = "holiday photo é.png"
CHANNEL_KEY = "5F1CA2B7D93E4086A1C2E3F405162738"
OTHER_CHANNEL_KEY = "0123456789ABCDEF0123456789ABCDEF"
RADIO_PRIVATE_KEY = bytes(range(64))
CONTACT_KEY = derive_public_key(bytes(range(64, 128))).hex()
DM_SECRET = derive_shared_secret(RADIO_PRIVATE_KEY, bytes.fromhex(CONTACT_KEY))
SECRETS = (bytes.fromhex(CHANNEL_KEY), DM_SECRET, RADIO_PRIVATE_KEY[:32])


def _secret_texts() -> set[str]:
    out = set()
    for secret in SECRETS:
        out |= {secret.hex(), secret.hex().upper()}
    return out


class Service:
    """The share service: stores uploaded envelopes and serves them back."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.refuse_with: list[httpx.Response] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/files":
            if self.refuse_with:
                return self.refuse_with.pop(0)
            # A public upload proposes no id: the service picks one.
            share_id = request.headers.get("x-share-id", "AbCdEfG")
            self.blobs[share_id] = request.content
            return json_response(
                201,
                {
                    "id": share_id,
                    "url": f"https://meshl.ink/{share_id}",
                    "expires_at": 1791636000,
                    "size": len(request.content),
                },
            )
        if path.startswith("/v1/meta/"):
            share_id = path.rsplit("/", 1)[1]
            blob = self.blobs.get(share_id)
            if blob is None:
                return json_response(404, {"code": "not_found"})
            return json_response(
                200,
                {
                    "id": share_id,
                    "kind": "file",
                    "mode": "encrypted",
                    "size_kib": -(-len(blob) // 1024),
                    "created_at": 1791000000,
                    "expires_at": 1791636000,
                },
            )
        blob = self.blobs.get(path.lstrip("/"))
        if blob is None:
            return json_response(404, {"code": "not_found"})
        return httpx.Response(200, content=blob, headers={"Content-Type": share.OCTET_STREAM})


@pytest.fixture
async def client():
    from app.main import app

    sent: list[httpx.Request] = []

    async def record(request: httpx.Request) -> None:
        sent.append(request)

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        event_hooks={"request": [record]},
    ) as c:
        c.sent = sent  # type: ignore[attr-defined]
        yield c


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "meshloom.db"))
    share_upload.reset_upload_state_for_tests()
    yield tmp_path
    share_upload.reset_upload_state_for_tests()


@pytest.fixture
async def service(test_db, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    await ChannelRepository.upsert(CHANNEL_KEY, "Family")
    await ChannelRepository.upsert(OTHER_CHANNEL_KEY, "Work")
    await ContactRepository.upsert({"public_key": CONTACT_KEY, "name": "Alice"})
    keystore.set_private_key(RADIO_PRIVATE_KEY)
    Dns(monkeypatch)
    fake = Service()
    fake.recorder = Recorder(monkeypatch, fake)
    try:
        yield fake
    finally:
        keystore.clear_keys()


def _headers(conversation_type: str | None, conversation_key: str | None, **extra) -> dict:
    headers = {
        "Content-Type": "image/png",
        "Content-Length": str(len(PNG)),
        "X-Share-Mode": "encrypted",
        "X-Share-Name": urllib.parse.quote(NAME),
    }
    if conversation_type is not None:
        headers["X-Share-Conversation-Type"] = conversation_type
    if conversation_key is not None:
        headers["X-Share-Conversation-Key"] = conversation_key
    headers.update(extra)
    return headers


async def _upload(client, conversation_type="channel", conversation_key=CHANNEL_KEY, **extra):
    return await client.post(
        "/api/share/files",
        content=PNG,
        headers=_headers(conversation_type, conversation_key, **extra),
    )


async def _message(msg_type: str, conversation_key: str, text: str) -> int:
    message_id = await MessageRepository.create(
        msg_type=msg_type, text=text, received_at=1791000000, conversation_key=conversation_key
    )
    assert message_id is not None
    return message_id


def _code(response: httpx.Response) -> str:
    return response.json()["detail"]["code"]


def _assert_no_secret_in_requests(service: Service) -> None:
    texts = _secret_texts()
    for request in service.recorder.requests:
        wire = bytes(request.url.raw_path) + b"".join(k + v for k, v in request.headers.raw)
        for secret in SECRETS:
            assert secret not in wire
            assert secret not in request.content
        decoded = wire.decode("latin-1") + request.content.decode("latin-1")
        for text in texts:
            assert text not in decoded


def _assert_no_secret_in_access_log(client) -> None:
    """The request lines the access log writes (method, path, query) carry no secret.

    The conversation is named in headers, which the access log never writes; the
    read side names only a message id.
    """
    texts = _secret_texts()
    for request in client.sent:
        line = f"{request.method} {request.url.raw_path.decode()} HTTP/1.1"
        for text in texts:
            assert text not in line
            assert text not in urllib.parse.unquote(line)


def _assert_no_secret_in_logs(caplog) -> None:
    texts = _secret_texts()
    for record in caplog.records:
        line = record.getMessage() + repr(record.args) + str(record.exc_info)
        for text in texts:
            assert text not in line
        for secret in SECRETS:
            assert repr(secret) not in line


class TestRoundTrip:
    @pytest.mark.parametrize(
        ("msg_type", "conversation_type", "conversation_key"),
        [("CHAN", "channel", CHANNEL_KEY), ("PRIV", "contact", CONTACT_KEY)],
    )
    async def test_upload_then_download_and_meta(
        self, service, client, caplog, msg_type, conversation_type, conversation_key
    ):
        caplog.set_level(logging.DEBUG)
        response = await _upload(client, conversation_type, conversation_key)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["mode"] == "encrypted"
        assert body["text"] == f"\N{LOCK} {body['url']}"
        share_id = body["id"]
        assert share.is_share_id(share_id)
        blob = service.blobs[share_id]
        assert body["size"] == len(blob)
        # The service sees an envelope: no image, no name, no type.
        assert blob.startswith(b"MLF1\x01")
        assert PNG not in blob and b"holiday" not in blob and b"image/png" not in blob
        (request,) = service.recorder.requests
        assert request.headers["x-share-mode"] == "encrypted"
        assert request.headers["x-share-id"] == share_id
        assert request.headers["content-type"] == "application/octet-stream"
        assert request.headers["x-share-sha256"] == hashlib.sha256(blob).hexdigest()
        for header in ("x-share-name", "x-share-conversation-type", "x-share-conversation-key"):
            assert header not in request.headers

        message_id = await _message(msg_type, conversation_key, body["text"])
        image = await client.get(f"/api/share/{share_id}?message_id={message_id}")
        assert image.status_code == 200, image.text
        assert image.content == PNG
        assert image.headers["content-type"] == "image/png"
        assert image.headers["content-disposition"] == (
            f"inline; filename*=UTF-8''{urllib.parse.quote(NAME, safe='')}"
        )
        assert image.headers["x-content-type-options"] == "nosniff"
        assert image.headers["content-security-policy"] == "default-src 'none'; sandbox"
        meta = await client.get(f"/api/share/{share_id}/meta?message_id={message_id}")
        assert meta.status_code == 200
        assert meta.json() == {
            "kind": "file",
            "target_host": None,
            "expires_at": 1791636000,
            "created_at": 1791000000,
            "mode": "encrypted",
            "content_type": "image/png",
            "size": len(PNG),
            "name": NAME,
            "decryptable": True,
        }
        _assert_no_secret_in_requests(service)
        _assert_no_secret_in_logs(caplog)
        _assert_no_secret_in_access_log(client)
        # The answers to the browser carry no secret either.
        for answer in (response.text, meta.text):
            for text in _secret_texts():
                assert text not in answer

    async def test_the_cache_holds_ciphertext_only(self, service, client, data_dir):
        body = (await _upload(client)).json()
        message_id = await _message("CHAN", CHANNEL_KEY, body["text"])
        for _ in range(2):
            image = await client.get(f"/api/share/{body['id']}?message_id={message_id}")
            assert image.status_code == 200
        # One fetch: the second read decrypts the cached envelope again.
        assert [r.method for r in service.recorder.requests] == ["POST", "GET"]
        files = [p for p in Path(data_dir).rglob("*") if p.is_file()]
        assert files
        for path in files:
            content = path.read_bytes()
            assert PNG not in content
            assert NAME.encode() not in content
            for secret in SECRETS:
                assert secret not in content
                assert secret.hex().encode() not in content
                assert secret.hex().upper().encode() not in content


class TestWhoCanDecrypt:
    async def test_another_conversation_cannot(self, service, client):
        body = (await _upload(client)).json()
        forwarded = await _message("CHAN", OTHER_CHANNEL_KEY, f"fwd {body['url']}")
        response = await client.get(f"/api/share/{body['id']}?message_id={forwarded}")
        assert response.status_code == 422
        assert _code(response) == "share_undecryptable"
        meta = (await client.get(f"/api/share/{body['id']}/meta?message_id={forwarded}")).json()
        assert meta["decryptable"] is False
        assert (meta["name"], meta["content_type"], meta["size"]) == (None, None, None)
        direct = await _message("PRIV", CONTACT_KEY, f"fwd {body['url']}")
        response = await client.get(f"/api/share/{body['id']}?message_id={direct}")
        assert _code(response) == "share_undecryptable"

    async def test_no_message_no_decryption(self, service, client):
        body = (await _upload(client)).json()
        response = await client.get(f"/api/share/{body['id']}")
        assert response.status_code == 422
        assert _code(response) == "share_undecryptable"
        meta = (await client.get(f"/api/share/{body['id']}/meta")).json()
        assert (meta["mode"], meta["decryptable"], meta["name"]) == ("encrypted", False, None)

    async def test_a_dm_needs_the_radio_key(self, service, client):
        body = (await _upload(client, "contact", CONTACT_KEY)).json()
        message_id = await _message("PRIV", CONTACT_KEY, body["text"])
        keystore.clear_keys()
        response = await client.get(f"/api/share/{body['id']}?message_id={message_id}")
        assert _code(response) == "share_undecryptable"

    async def test_a_sealed_non_image_is_refused(self, service, client):
        share_id = "HtMLenc"
        service.blobs[share_id] = share_crypto.encrypt_envelope(
            bytes.fromhex(CHANNEL_KEY), share_id, "x.html", "text/html", b"<script>"
        )
        message_id = await _message("CHAN", CHANNEL_KEY, f"https://meshl.ink/{share_id}")
        response = await client.get(f"/api/share/{share_id}?message_id={message_id}")
        assert response.status_code == 415
        assert _code(response) == "share_type_refused"
        lying = "LiEsPng"
        service.blobs[lying] = share_crypto.encrypt_envelope(
            bytes.fromhex(CHANNEL_KEY), lying, "x.png", "image/png", b"<html>"
        )
        message_id = await _message("CHAN", CHANNEL_KEY, f"https://meshl.ink/{lying}")
        response = await client.get(f"/api/share/{lying}?message_id={message_id}")
        assert response.status_code == 415
        meta = (await client.get(f"/api/share/{lying}/meta?message_id={message_id}")).json()
        assert meta["decryptable"] is False


class TestRefusedConversations:
    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("Public", PUBLIC_CHANNEL_KEY),
            ("#test", hashlib.sha256(b"#test").digest()[:16].hex().upper()),
        ],
    )
    async def test_public_channels_are_public_only(self, service, client, name, key):
        await ChannelRepository.upsert(key, name)
        response = await _upload(client, "channel", key)
        assert response.status_code == 422
        assert _code(response) == "share_mode_not_allowed"
        assert service.recorder.requests == []

    async def test_a_room_is_refused(self, service, client):
        room = derive_public_key(bytes(range(128, 192))).hex()
        await ContactRepository.upsert(
            {"public_key": room, "name": "Room", "type": CONTACT_TYPE_ROOM}
        )
        response = await _upload(client, "contact", room)
        assert response.status_code == 422
        assert _code(response) == "share_mode_not_allowed"
        assert service.recorder.requests == []

    async def test_a_low_order_contact_key_is_refused(self, service, client):
        low_order = "01" + "00" * 31
        await ContactRepository.upsert({"public_key": low_order, "name": "Bad"})
        response = await _upload(client, "contact", low_order)
        assert response.status_code == 422
        assert _code(response) == "share_mode_not_allowed"
        assert service.recorder.requests == []

    @pytest.mark.parametrize(
        ("conversation_type", "conversation_key"),
        [
            ("channel", "FF" * 16),
            ("channel", "not-hex"),
            ("contact", "ab" * 32),
            ("contact", CHANNEL_KEY),
            ("room", CONTACT_KEY),
        ],
    )
    async def test_unknown_or_malformed_is_invalid(
        self, service, client, conversation_type, conversation_key
    ):
        response = await _upload(client, conversation_type, conversation_key)
        assert response.status_code == 400
        assert _code(response) == "share_invalid_request"
        assert service.recorder.requests == []

    async def test_no_radio_key_refuses_dm_only(self, service, client):
        keystore.clear_keys()
        response = await _upload(client, "contact", CONTACT_KEY)
        assert response.status_code == 503
        assert _code(response) == "share_radio_key_unavailable"
        assert service.recorder.requests == []
        # A private channel does not need the radio key; public mode still works.
        assert (await _upload(client)).status_code == 201
        public = await client.post(
            "/api/share/files",
            content=PNG,
            headers={
                "Content-Type": "image/png",
                "Content-Length": str(len(PNG)),
                "X-Share-Mode": "public",
            },
        )
        assert public.status_code == 201


class TestServiceAnswers:
    async def test_id_taken_is_retried_with_a_new_id_and_envelope(self, service, client):
        service.refuse_with = [json_response(409, {"code": "id_taken"})]
        response = await _upload(client)
        assert response.status_code == 201, response.text
        first, second = service.recorder.requests
        assert first.headers["x-share-id"] != second.headers["x-share-id"]
        assert first.content != second.content
        share_id = response.json()["id"]
        assert share_id == second.headers["x-share-id"]
        content = share_crypto.decrypt_envelope(
            bytes.fromhex(CHANNEL_KEY), share_id, service.blobs[share_id]
        )
        assert (content.data, content.name, content.content_type) == (PNG, NAME, "image/png")
        assert share.share_breaker().failures == 0

    async def test_id_taken_three_times_gives_up(self, service, client):
        service.refuse_with = [json_response(409, {"code": "id_taken"}) for _ in range(5)]
        response = await _upload(client)
        assert response.status_code == share.SHARE_ERRORS["share_unavailable"][0]
        assert _code(response) == "share_unavailable"
        assert len(service.recorder.requests) == share_upload.ENCRYPTED_ATTEMPTS

    async def test_a_service_without_encrypted_mode(self, service, client):
        service.refuse_with = [json_response(415, {"code": "type_refused"})]
        response = await _upload(client)
        assert response.status_code == 422
        assert _code(response) == "share_mode_unavailable"
        assert len(service.recorder.requests) == 1

    async def test_an_answer_with_another_id_is_refused(self, service, client):
        service.refuse_with = [
            json_response(
                201,
                {
                    "id": "AbCdEfG",
                    "url": "https://meshl.ink/AbCdEfG",
                    "expires_at": 1791636000,
                    "size": 1,
                },
            )
        ]
        response = await _upload(client)
        assert response.status_code == share.SHARE_ERRORS["share_unavailable"][0]
        assert _code(response) == "share_unavailable"


def test_new_share_ids_follow_the_grammar_and_skip_reserved_paths(monkeypatch):
    ids = {share_upload.new_share_id() for _ in range(200)}
    assert all(share.is_share_id(i) for i in ids)
    assert len(ids) == 200
    picks = iter("privacyAbCdEfG")
    monkeypatch.setattr(share_upload.secrets, "choice", lambda _alphabet: next(picks))
    assert share_upload.new_share_id() == "AbCdEfG"
