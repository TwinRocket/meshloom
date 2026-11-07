"""The push and notification test routes answer coded details, never the exception text.

Both routes send to a destination the client chose, so a network error in the
answer (connection refused, unknown host, TLS failure, HTTP status, timeout)
would let a page probe the LAN. The egress guard refuses a private destination
before any connection (no status-code or timing oracle); past the guard, the
answer carries a failure class only. The server log gets the exception type
(plus the webhook's host and HTTP status), never its text: an HTTPStatusError
carries the full webhook URL and its token, a WebPushException the push
service's body.
"""

import asyncio
import base64
import contextlib
import json
import logging
import socket
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pywebpush import WebPushException

from app.push.vapid import ensure_vapid_keys
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

DNS_MARKER = "nxdomain-oracle-marker"
# A webhook token in the userinfo, the path and the query: never in a log.
TOKEN = "tok3n-s3cret"
# Words a network error carries; none may reach a response body.
LEAKY_WORDS = ("errno", "refused", "connect", "resolve", "127.0.0.1", "lan.invalid", "marker")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _client_keys() -> tuple[str, str]:
    """A real P-256 public key and auth secret, so pywebpush encrypts and sends."""
    pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    return _b64(pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)), _b64(
        b"0123456789abcdef"
    )


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _dns_failure():
    """Every name lookup fails, with a marker in the error text."""
    with patch(
        "socket.getaddrinfo",
        side_effect=socket.gaierror(socket.EAI_NONAME, f"Name not known {DNS_MARKER}"),
    ):
        yield


@contextlib.asynccontextmanager
async def _failing_http_server(status: int = 500):
    """A local HTTP server answering every request with ``status`` and a marker body."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await reader.readuntil(b"\r\n\r\n")
        body = b"internal marker body"
        writer.write(
            f"HTTP/1.1 {status} Oops\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


@contextlib.asynccontextmanager
async def _silent_server(*, plain_reply: bool = False):
    """A local server that never answers, or answers a TLS ClientHello in plain HTTP
    (``plain_reply``), which fails the client's TLS handshake."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await reader.read(65536)
            if plain_reply:
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            else:
                await asyncio.sleep(5)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()


def _webhook_url(scheme: str, host: str, port: int | None = None) -> str:
    netloc = f"{host}:{port}" if port else host
    return f"{scheme}://user:{TOKEN}@{netloc}/hook/{TOKEN}?token={TOKEN}"


def _assert_log_clean(caplog) -> None:
    for word in (TOKEN, "marker"):
        assert word not in caplog.text, f"{word!r} leaked into the log"


def _assert_coded(response, status: int, code: str) -> None:
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    text = json.dumps(response.json()).lower()
    for word in LEAKY_WORDS:
        assert word not in text, f"{word!r} leaked into {text}"


async def _allow_private_push() -> None:
    await AppSettingsRepository.update(push_allow_private_endpoints=True)


async def _subscription(endpoint: str) -> str:
    p256dh, auth = _client_keys()
    sub = await PushSubscriptionRepository.create(endpoint=endpoint, p256dh=p256dh, auth=auth)
    return sub["id"]


class TestPushTestRoute:
    @pytest.mark.asyncio
    async def test_private_endpoint_is_refused_before_any_connection(self, push_owner, client):
        await ensure_vapid_keys()
        async with _silent_server() as port:
            sub_id = await _subscription(f"http://127.0.0.1:{port}/push")
            with patch("app.push.send.webpush") as webpush:
                started = time.monotonic()
                response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
                elapsed = time.monotonic() - started
        _assert_coded(response, 422, "egress_private_address")
        webpush.assert_not_called()
        assert elapsed < 1.0
        assert await PushSubscriptionRepository.get(sub_id) is not None

    @pytest.mark.asyncio
    async def test_refused_connection_is_coded(self, push_owner, client, caplog):
        await ensure_vapid_keys()
        await _allow_private_push()
        sub_id = await _subscription(f"http://127.0.0.1:{_closed_port()}/push")
        with caplog.at_level(logging.WARNING, logger="app.routers.push"):
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "push_delivery_failed")
        assert "Test push failed" in caplog.text

    @pytest.mark.asyncio
    async def test_dns_failure_is_coded(self, push_owner, client, caplog):
        await ensure_vapid_keys()
        sub_id = await _subscription("https://push.lan.invalid/push")
        with caplog.at_level(logging.INFO, logger="app.routers.push"), _dns_failure():
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "egress_unresolvable")
        assert "egress guard: egress_unresolvable" in caplog.text
        assert DNS_MARKER not in caplog.text

    @pytest.mark.asyncio
    async def test_push_server_error_is_coded(self, push_owner, client, caplog):
        await ensure_vapid_keys()
        await _allow_private_push()
        async with _failing_http_server(500) as port:
            sub_id = await _subscription(f"http://127.0.0.1:{port}/push")
            with caplog.at_level(logging.WARNING, logger="app.routers.push"):
                response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "push_delivery_failed")
        assert "Test push failed: WebPushException" in caplog.text
        _assert_log_clean(caplog)
        assert await PushSubscriptionRepository.get(sub_id) is not None

    @pytest.mark.asyncio
    async def test_tls_error_is_coded(self, push_owner, client, caplog):
        await ensure_vapid_keys()
        await _allow_private_push()
        async with _silent_server(plain_reply=True) as port:
            # https:// to a plain-HTTP peer: the TLS handshake fails.
            sub_id = await _subscription(f"https://127.0.0.1:{port}/push")
            with caplog.at_level(logging.WARNING, logger="app.routers.push"):
                response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "push_delivery_failed")
        assert "Test push failed: SSLError" in caplog.text

    @pytest.mark.asyncio
    async def test_stale_subscription_is_coded_and_removed(self, push_owner, client):
        await ensure_vapid_keys()
        sub_id = await _subscription("https://93.184.215.14/push")
        exc = WebPushException("Push failed: 410 Gone marker", response=MagicMock(status_code=410))
        with patch("app.routers.push.send_push", new=AsyncMock(side_effect=exc)):
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 410, "push_stale")
        assert await PushSubscriptionRepository.get(sub_id) is None

    @pytest.mark.asyncio
    async def test_timeout_is_a_422_like_every_delivery_failure(self, push_owner, client):
        await ensure_vapid_keys()
        sub_id = await _subscription("https://93.184.215.14/push")
        with patch("app.routers.push.send_push", new=AsyncMock(side_effect=TimeoutError)):
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "push_timeout")


async def _set_webhook(url: str, *, allow_private_network: bool = True) -> None:
    dest = (await AppSettingsRepository.get()).notification_destinations
    dest.webhook.url = url
    dest.webhook.allow_private_network = allow_private_network
    await AppSettingsRepository.update(notification_destinations=dest)


async def _test_destination(client, channel: str):
    return await client.post(
        "/api/settings/notification-destinations/test", json={"channel": channel}
    )


class TestNotificationDestinationTestRoute:
    @pytest.mark.asyncio
    async def test_private_webhook_is_refused_before_any_connection(self, test_db, client):
        async with _silent_server() as port:
            await _set_webhook(_webhook_url("http", "127.0.0.1", port), allow_private_network=False)
            started = time.monotonic()
            response = await _test_destination(client, "webhook")
            elapsed = time.monotonic() - started
        _assert_coded(response, 400, "egress_private_address")
        # The 10 s webhook timeout never started: no connection was made.
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_refused_connection_is_coded(self, test_db, client, caplog):
        await _set_webhook(_webhook_url("http", "127.0.0.1", _closed_port()))
        with caplog.at_level(logging.WARNING, logger="app"):
            response = await _test_destination(client, "webhook")
        _assert_coded(response, 502, "service_error")
        _assert_log_clean(caplog)

    @pytest.mark.asyncio
    async def test_dns_failure_is_coded(self, test_db, client, caplog):
        await _set_webhook(_webhook_url("https", "hook.lan.invalid"))
        with caplog.at_level(logging.INFO, logger="app"), _dns_failure():
            response = await _test_destination(client, "webhook")
        _assert_coded(response, 400, "egress_unresolvable")
        assert DNS_MARKER not in caplog.text
        _assert_log_clean(caplog)

    @pytest.mark.asyncio
    async def test_http_error_status_is_coded(self, test_db, client, caplog):
        async with _failing_http_server(500) as port:
            await _set_webhook(_webhook_url("http", "127.0.0.1", port))
            with caplog.at_level(logging.WARNING, logger="app"):
                response = await _test_destination(client, "webhook")
        _assert_coded(response, 502, "service_error")
        _assert_log_clean(caplog)

    @pytest.mark.asyncio
    async def test_tls_error_is_coded(self, test_db, client, caplog, monkeypatch):
        monkeypatch.setattr("app.notify.WEBHOOK_TIMEOUT_SECONDS", 0.3)
        async with _silent_server(plain_reply=True) as port:
            # https:// to a plain-HTTP peer: the TLS handshake fails.
            await _set_webhook(_webhook_url("https", "127.0.0.1", port))
            with caplog.at_level(logging.WARNING, logger="app"):
                response = await _test_destination(client, "webhook")
        _assert_coded(response, 502, "tls_error")
        _assert_log_clean(caplog)

    @pytest.mark.asyncio
    async def test_timeout_is_coded(self, test_db, client, caplog, monkeypatch):
        monkeypatch.setattr("app.notify.WEBHOOK_TIMEOUT_SECONDS", 0.3)
        async with _silent_server() as port:
            await _set_webhook(_webhook_url("http", "127.0.0.1", port))
            with caplog.at_level(logging.WARNING, logger="app"):
                response = await _test_destination(client, "webhook")
        _assert_coded(response, 504, "timeout")
        _assert_log_clean(caplog)

    @pytest.mark.asyncio
    async def test_email_failure_is_coded(self, test_db, client):
        dest = (await AppSettingsRepository.get()).notification_destinations
        dest.email.host = "smtp.lan.invalid"
        dest.email.to = "ops@example.test"
        await AppSettingsRepository.update(notification_destinations=dest)
        with patch(
            "app.notify._send_email_sync",
            side_effect=OSError(111, "Connection refused to smtp.lan.invalid marker"),
        ):
            response = await _test_destination(client, "email")
        _assert_coded(response, 502, "service_error")

    @pytest.mark.asyncio
    async def test_unconfigured_destination_is_coded(self, test_db, client):
        response = await _test_destination(client, "webhook")
        _assert_coded(response, 400, "invalid_config")
