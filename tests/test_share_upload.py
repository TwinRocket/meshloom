"""``POST /api/share/files`` (public mode): staging, local checks, upload, cleanup.

Every refusal is local: no request and no DNS lookup reach the service.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import os
import stat
import tracemalloc
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.net.egress_guard import StreamedBody
from app.repository import AppSettingsRepository
from app.services import share, share_upload
from tests.share_fakes import Dns, Recorder, json_response

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "links_files_vectors.json").read_text())[
    "files"
]
PNG = bytes.fromhex(VECTORS["fixtures"]["png_1x1"]["body_hex"])
PNG_SHA = VECTORS["fixtures"]["png_1x1"]["sha256"]
CASE = next(c for c in VECTORS["cases"] if c["id"] == "valid_file_public")
FILE = {"id": "AbCdEfG", "url": "https://meshl.ink/AbCdEfG", "expires_at": 1762692000}
MIB = 1024 * 1024

# The recorder replaces httpx.AsyncClient: keep the real class for the app client.
_RealAsyncClient = httpx.AsyncClient


@pytest.fixture
async def client():
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "meshloom.db"))
    share_upload.reset_upload_state_for_tests()
    yield tmp_path
    share_upload.reset_upload_state_for_tests()


def _service(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/files":
        return json_response(201, {**FILE, "size": int(request.headers["content-length"])})
    return httpx.Response(404)


@pytest.fixture
async def on(test_db, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    dns = Dns(monkeypatch)
    recorder = Recorder(monkeypatch, _service)
    recorder.dns = dns
    return recorder


@pytest.fixture
async def off(test_db, monkeypatch):
    dns = Dns(monkeypatch)
    recorder = Recorder(monkeypatch, _service)
    recorder.dns = dns
    return recorder


def _headers(content_type="image/png", length=None, body=PNG, **extra):
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body) if length is None else length),
        "X-Share-Mode": "public",
    }
    headers.update(extra)
    return headers


async def _post(client, body=PNG, **kwargs):
    return await client.post(
        "/api/share/files", content=body, headers=_headers(body=body, **kwargs)
    )


def _staged(data_dir: Path) -> list[str]:
    directory = data_dir / share_upload.TMP_DIRNAME
    return sorted(os.listdir(directory)) if directory.exists() else []


def _no_egress(recorder) -> None:
    assert recorder.requests == []
    assert recorder.dns.lookups == []


def _png(width: int, height: int, total: int) -> bytes:
    head = bytes.fromhex("89504e470d0a1a0a0000000d49484452")
    head += width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes.fromhex("0802000000")
    return head + b"\x00" * (total - len(head))


class TestUpload:
    async def test_streams_the_file_with_the_contract_headers(self, on, client, data_dir):
        response = await _post(client)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body == {
            **FILE,
            "size": len(PNG),
            "mode": "public",
            "text": "\N{CAMERA} https://meshl.ink/AbCdEfG",
        }
        (request,) = on.requests
        assert request.method == "POST"
        assert request.url.path == "/v1/files"
        assert request.headers["host"] == "meshl.ink"
        for name, value in CASE["request"]["headers"].items():
            assert request.headers[name] == value
        assert "transfer-encoding" not in request.headers
        assert request.content == PNG
        token = request.headers["authorization"].split(" ", 1)[1]
        payload_b64 = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        assert claims["bh"] == hashlib.sha256(CASE["binding"].encode()).hexdigest()
        assert claims["bh"] == CASE["claims"]["bh"]
        assert _staged(data_dir) == []

    def test_binding_string_matches_the_vector(self):
        assert share_upload.binding_string("public", PNG_SHA, "", len(PNG)) == CASE["binding"]

    async def test_suggested_text_fits_the_byte_budget(self, on, client):
        text = (await _post(client)).json()["text"]
        # Cadrage 4.2: "📷 https://meshl.ink/AbCdEfG" is 30 bytes.
        assert len(text.encode()) == 30

    async def test_content_type_parameters_are_ignored(self, on, client):
        response = await _post(client, content_type="Image/PNG; charset=binary")
        assert response.status_code == 201
        assert on.requests[0].headers["content-type"] == "image/png"

    async def test_a_service_refusal_still_removes_the_file(self, on, client, data_dir):
        on.handler = lambda request: json_response(415, {"code": "type_refused"})
        response = await _post(client)
        assert response.status_code == 415
        assert response.json()["detail"]["code"] == "share_type_refused"
        assert len(on.requests) == 1
        assert _staged(data_dir) == []

    @pytest.mark.parametrize(
        "answer",
        [
            {**FILE, "size": 1},
            {**FILE, "url": "https://evil.example/AbCdEfG", "size": 69},
            {**FILE, "id": "0OIl000", "url": "https://meshl.ink/0OIl000", "size": 69},
        ],
    )
    async def test_an_answer_outside_the_grammar_is_unavailable(self, on, client, answer):
        on.handler = lambda request: json_response(201, answer)
        response = await _post(client)
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "share_unavailable"

    async def test_a_kill_switch_is_not_a_breaker_failure(self, on, client, data_dir):
        on.handler = lambda request: json_response(503, {"code": "kill_switch"})
        for _ in range(4):
            response = await _post(client)
            assert response.status_code == 502
            assert response.json()["detail"]["code"] == "share_unavailable"
        assert share.share_breaker().failures == 0
        assert len(on.requests) == 4
        assert _staged(data_dir) == []


class TestKillSwitchBeforeTheBody:
    """The service refuses an upload (503 ``kill_switch``) before reading its body.

    The mock transport of the other tests reads the whole body first. This one
    goes through the real httpx/httpcore stack to a socket that answers 503 as
    soon as it has the headers, before the body, and then only discards what
    arrives: the answer must be the one reported, and it is not an outage.
    (A server that hard-closes with unread data resets the connection and, on
    macOS, drops its own answer: that variant depends on the OS, so it is not
    tested.)
    """

    async def test_an_answer_sent_before_the_body_is_read(self, on, client, data_dir, monkeypatch):
        answer = json.dumps({"code": "kill_switch"}).encode()

        async def refuse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(answer)}\r\nConnection: close\r\n\r\n".encode()
                + answer
            )
            await writer.drain()
            while await reader.read(65536):
                pass
            writer.close()

        server = await asyncio.start_server(refuse, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        class ToTheSocket(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                on.requests.append(request)
                async with _RealAsyncClient() as real:
                    response = await real.send(
                        httpx.Request(
                            "POST",
                            f"http://127.0.0.1:{port}/v1/files",
                            headers=request.headers,
                            content=request.stream,
                        )
                    )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    stream=httpx.ByteStream(response.content),
                )

        class Recording(_RealAsyncClient):
            def __init__(self, **kwargs):
                kwargs["transport"] = ToTheSocket()
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Recording)
        # Several MiB: the answer is sent while most of the body is still to go.
        body = _png(8, 8, 4 * MIB)
        try:
            async with asyncio.timeout(20):
                response = await _post(client, body=body)
        finally:
            server.close()
            await server.wait_closed()
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "share_unavailable"
        assert len(on.requests) == 1
        assert share.share_breaker().failures == 0
        assert _staged(data_dir) == []


class TestReceiveDeadline:
    async def test_a_trickled_body_times_out_and_frees_the_slot(
        self, on, client, data_dir, monkeypatch
    ):
        monkeypatch.setattr(share_upload, "RECEIVE_TIMEOUT_S", 0.2)

        async def trickle():
            yield PNG[:10]
            await asyncio.sleep(3600)
            yield PNG[10:]

        response = await asyncio.wait_for(
            client.post("/api/share/files", content=trickle(), headers=_headers()), 5
        )
        assert response.status_code == 408
        assert response.json()["detail"]["code"] == "share_upload_timeout"
        _no_egress(on)
        assert _staged(data_dir) == []
        # Both slots are free again.
        with share_upload.upload_slot():
            assert (await _post(client)).status_code == 201


class TestDiskErrors:
    async def test_a_full_disk_is_coded_and_cleaned(self, on, client, data_dir, monkeypatch):
        real_fdopen = os.fdopen

        class Full:
            def __init__(self, fd, mode):
                self.file = real_fdopen(fd, mode)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.file.close()

            def write(self, data):
                raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(share_upload.os, "fdopen", Full)
        response = await _post(client)
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "share_storage_failed"
        _no_egress(on)
        assert _staged(data_dir) == []

    async def test_a_file_removed_while_it_is_sent_is_coded_and_cleaned(
        self, on, client, data_dir, monkeypatch, caplog
    ):
        opened = 0

        def unlink_on_second_open(path, *args, **kwargs):
            # The first open is the local check; the second one streams to the service.
            nonlocal opened
            opened += 1
            if opened == 2:
                os.unlink(path)
            return open(path, *args, **kwargs)

        monkeypatch.setattr(share_upload, "open", unlink_on_second_open, raising=False)
        with caplog.at_level("WARNING"):
            response = await _post(client)
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "share_storage_failed"
        assert "errno ENOENT" in caplog.text
        assert str(data_dir) not in caplog.text
        assert share.share_breaker().failures == 0
        assert _staged(data_dir) == []

    async def test_an_errno_less_error_is_logged_with_a_reason(self, on, client, data_dir, caplog):
        # A link to a directory passes ``mkdir`` and is refused by our own check.
        (data_dir / "elsewhere").mkdir()
        (data_dir / share_upload.TMP_DIRNAME).symlink_to(data_dir / "elsewhere")
        with caplog.at_level("WARNING"):
            response = await _post(client)
        assert response.status_code == 500
        assert "errno None" not in caplog.text
        assert "share-tmp is not a directory" in caplog.text

    @pytest.mark.parametrize("target", ["directory", "missing"])
    async def test_a_linked_tmp_directory_is_refused(self, on, client, data_dir, target):
        elsewhere = data_dir / "elsewhere"
        if target == "directory":
            elsewhere.mkdir()
        (data_dir / share_upload.TMP_DIRNAME).symlink_to(elsewhere)
        response = await _post(client)
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "share_storage_failed"
        _no_egress(on)
        if target == "directory":
            assert os.listdir(elsewhere) == []
        else:
            assert not elsewhere.exists()

    async def test_a_tmp_path_that_is_a_file_is_refused(self, on, client, data_dir):
        (data_dir / share_upload.TMP_DIRNAME).write_bytes(b"x")
        response = await _post(client)
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "share_storage_failed"
        _no_egress(on)


class TestLocalRefusals:
    async def test_off_makes_no_request_and_no_lookup(self, off, client, data_dir):
        response = await _post(client)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_disabled"
        _no_egress(off)
        assert _staged(data_dir) == []

    async def test_declared_length_over_the_cap_is_refused_before_reading(self, on, client):
        pulled = 0

        async def body():
            nonlocal pulled
            for _ in range(30):
                pulled += 1
                yield b"\x00" * MIB

        response = await client.post(
            "/api/share/files", content=body(), headers=_headers(length=30 * MIB)
        )
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "share_too_large"
        assert pulled <= 1
        _no_egress(on)

    async def test_a_lying_length_stops_the_read_without_buffering(self, on, client, data_dir):
        pulled = 0

        async def body():
            nonlocal pulled
            yield PNG
            for _ in range(30):
                pulled += 1
                yield b"\x00" * MIB

        tracemalloc.start()
        try:
            response = await client.post(
                "/api/share/files", content=body(), headers=_headers(length=len(PNG))
            )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_request"
        assert pulled <= 2
        assert peak < 8 * MIB
        _no_egress(on)
        assert _staged(data_dir) == []

    async def test_a_short_body_is_refused(self, on, client, data_dir):
        response = await _post(client, length=len(PNG) + 10)
        assert response.status_code == 400
        _no_egress(on)
        assert _staged(data_dir) == []

    @pytest.mark.parametrize(
        ("content_type", "body", "status", "code"),
        [
            ("image/png", b"<html><script>alert(1)</script></html>", 415, "share_type_refused"),
            ("image/jpeg", PNG, 415, "share_type_refused"),
            ("text/html", b"<html>", 415, "share_type_refused"),
            ("image/svg+xml", b"<svg/>", 415, "share_type_refused"),
            # A PNG signature with a truncated header: no dimensions.
            ("image/png", bytes.fromhex("89504e470d0a1a0a00000000"), 415, "share_type_refused"),
            ("image/png", _png(10000, 10000, 4096), 413, "share_too_large"),
            ("image/png", _png(6000, 7000, 4096), 413, "share_too_large"),
        ],
    )
    async def test_disguised_or_oversized_images_never_leave(
        self, on, client, data_dir, content_type, body, status, code
    ):
        response = await _post(client, body=body, content_type=content_type)
        assert response.status_code == status
        assert response.json()["detail"]["code"] == code
        _no_egress(on)
        assert _staged(data_dir) == []

    async def test_forty_megapixels_is_accepted(self, on, client):
        response = await _post(client, body=_png(8000, 5000, 4096))
        assert response.status_code == 201
        assert len(on.requests) == 1

    async def test_a_gif_over_its_cap(self, on, client):
        response = await client.post(
            "/api/share/files",
            content=b"GIF89a" + b"\x00" * 10,
            headers=_headers(content_type="image/gif", length=5 * MIB + 1),
        )
        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "share_too_large"
        _no_egress(on)

    async def test_encrypted_mode_is_not_available_yet(self, on, client):
        response = await _post(client, **{"X-Share-Mode": "encrypted"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "share_mode_unavailable"
        _no_egress(on)

    @pytest.mark.parametrize("mode", [None, "", "secret", "PUBLIC "])
    async def test_a_missing_or_unknown_mode(self, on, client, mode):
        headers = _headers()
        if mode is None:
            del headers["X-Share-Mode"]
        else:
            headers["X-Share-Mode"] = mode
        response = await client.post("/api/share/files", content=PNG, headers=headers)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_request"
        _no_egress(on)

    async def test_chunked_uploads_are_refused(self, on, client):
        async def body():
            yield PNG

        headers = _headers()
        del headers["Content-Length"]
        response = await client.post("/api/share/files", content=body(), headers=headers)
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_request"
        _no_egress(on)

    @pytest.mark.parametrize("length", ["0", "-1", "1e3", "69 69", "9" * 13])
    async def test_a_malformed_length(self, length):
        with pytest.raises(share.ShareError) as error:
            share_upload.declared_length(length, "image/png")
        assert error.value.code == "share_invalid_request"

    @pytest.mark.parametrize(
        "name", ["../../etc/passwd", "a/b.png", "a\\b.png", "..", ".", "x\n.png", "x" * 256]
    )
    async def test_a_name_with_a_path_is_refused(self, on, client, data_dir, name):
        response = await _post(client, **{"X-Share-Name": name})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "share_invalid_request"
        _no_egress(on)
        assert not (data_dir / "etc").exists()

    async def test_a_plain_name_is_accepted_and_never_sent(self, on, client, data_dir):
        response = await _post(client, **{"X-Share-Name": "holiday photo.png"})
        assert response.status_code == 201
        (request,) = on.requests
        assert "x-share-name" not in request.headers
        assert "holiday" not in "".join(on.dumps())
        assert _staged(data_dir) == []

    async def test_banned_is_refused_without_egress(self, on, client, monkeypatch):
        monkeypatch.setattr(share, "share_banned", lambda: True)
        response = await _post(client)
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "share_banned"
        _no_egress(on)

    async def test_cross_site_is_refused_without_egress(self, on, client):
        response = await _post(client, **{"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403
        _no_egress(on)


class TestPermissions:
    async def test_roles_match_link_creation(self, on, client, sign_in_as):
        for role, status in (("kiosk", 403), ("viewer", 403), ("member", 201), ("admin", 201)):
            sign_in_as(role)
            assert (await _post(client)).status_code == status


class TestConcurrency:
    async def test_one_upload_too_many_is_refused_locally(self, on, client):
        with share_upload.upload_slot(), share_upload.upload_slot():
            response = await _post(client)
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert detail["code"] == "share_rate_limited"
        assert detail["params"] == {"retry_after": 5, "scope": "local"}
        _no_egress(on)
        assert (await _post(client)).status_code == 201


class TestTempDirectory:
    def test_lives_next_to_the_database(self, data_dir):
        assert share_upload.tmp_directory() == data_dir.resolve() / "share-tmp"

    async def test_permissions(self, data_dir):
        async def chunks():
            yield PNG

        async with share_upload.staged_upload(
            chunks(), content_type="image/png", length=len(PNG)
        ) as staged:
            assert staged.path.parent == data_dir.resolve() / "share-tmp"
            assert stat.S_IMODE(staged.path.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(staged.path.stat().st_mode) == 0o600
            assert staged.sha256 == PNG_SHA
            assert staged.path.read_bytes() == PNG
        assert not staged.path.exists()

    async def test_a_loose_directory_is_tightened(self, data_dir):
        directory = data_dir / "share-tmp"
        directory.mkdir(mode=0o755)
        os.chmod(directory, 0o755)

        async def chunks():
            yield PNG

        async with share_upload.staged_upload(chunks(), content_type="image/png", length=len(PNG)):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    def test_startup_cleanup_never_follows_links(self, data_dir):
        directory = data_dir / "share-tmp"
        directory.mkdir()
        outside = data_dir / "keep.txt"
        outside.write_text("keep")
        (directory / "upload-leftover").write_bytes(b"x")
        (directory / "link").symlink_to(outside)
        (directory / "subdir").mkdir()
        assert share_upload.clean_tmp_directory() == 2
        assert outside.read_text() == "keep"
        assert sorted(os.listdir(directory)) == ["subdir"]

    def test_startup_cleanup_without_a_directory(self, data_dir):
        assert share_upload.clean_tmp_directory() == 0


class TestStreamedBody:
    async def test_a_fresh_iterator_and_an_exact_length_per_attempt(self):
        opened = 0

        async def chunks():
            yield b"abc"
            yield b"de"

        def open_():
            nonlocal opened
            opened += 1
            return chunks()

        from app.net import egress_guard

        body = StreamedBody(length=5, open=open_)
        content, headers = egress_guard._body_and_headers(body, {"X": "1"})
        assert headers == {"X": "1", "Content-Length": "5"}
        assert [c async for c in content] == [b"abc", b"de"]
        egress_guard._body_and_headers(body, {})
        assert opened == 2
        assert egress_guard._content_length(body) == 5
        assert egress_guard._content_length(b"abc") == 3
        assert egress_guard._content_length(None) == 0
