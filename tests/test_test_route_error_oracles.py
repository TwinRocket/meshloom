"""The push and notification test routes answer coded details, never the exception text.

Both routes send to a destination the client chose, so a network error in the
answer (connection refused, unknown host, HTTP status) would let a page probe
the LAN. The cause goes to the server log only.
"""

import asyncio
import base64
import contextlib
import json
import logging
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pywebpush import WebPushException

from app.push.vapid import ensure_vapid_keys
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

DNS_MARKER = "nxdomain-oracle-marker"
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


def _assert_coded(response, status: int, code: str) -> None:
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    text = json.dumps(response.json()).lower()
    for word in LEAKY_WORDS:
        assert word not in text, f"{word!r} leaked into {text}"


async def _subscription(endpoint: str) -> str:
    p256dh, auth = _client_keys()
    sub = await PushSubscriptionRepository.create(endpoint=endpoint, p256dh=p256dh, auth=auth)
    return sub["id"]


class TestPushTestRoute:
    @pytest.mark.asyncio
    async def test_refused_connection_is_coded(self, test_db, client, caplog):
        await ensure_vapid_keys()
        sub_id = await _subscription(f"http://127.0.0.1:{_closed_port()}/push")
        with caplog.at_level(logging.WARNING, logger="app.routers.push"):
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "push_delivery_failed")
        assert "Test push failed" in caplog.text

    @pytest.mark.asyncio
    async def test_dns_failure_is_coded(self, test_db, client, caplog):
        await ensure_vapid_keys()
        sub_id = await _subscription("https://push.lan.invalid/push")
        with caplog.at_level(logging.WARNING, logger="app.routers.push"), _dns_failure():
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "push_delivery_failed")
        # The cause is still in the server log.
        assert DNS_MARKER in caplog.text

    @pytest.mark.asyncio
    async def test_push_server_error_is_coded(self, test_db, client):
        await ensure_vapid_keys()
        async with _failing_http_server(500) as port:
            sub_id = await _subscription(f"http://127.0.0.1:{port}/push")
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 422, "push_delivery_failed")
        assert await PushSubscriptionRepository.get(sub_id) is not None

    @pytest.mark.asyncio
    async def test_stale_subscription_is_coded_and_removed(self, test_db, client):
        await ensure_vapid_keys()
        sub_id = await _subscription("https://push.example.test/push")
        exc = WebPushException("Push failed: 410 Gone marker", response=MagicMock(status_code=410))
        with patch("app.routers.push.send_push", new=AsyncMock(side_effect=exc)):
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 410, "push_stale")
        assert await PushSubscriptionRepository.get(sub_id) is None

    @pytest.mark.asyncio
    async def test_timeout_is_coded(self, test_db, client):
        await ensure_vapid_keys()
        sub_id = await _subscription("https://push.example.test/push")
        with patch("app.routers.push.send_push", new=AsyncMock(side_effect=TimeoutError)):
            response = await client.post(f"/api/push/subscriptions/{sub_id}/test")
        _assert_coded(response, 408, "push_timeout")


async def _set_webhook(url: str) -> None:
    dest = (await AppSettingsRepository.get()).notification_destinations
    dest.webhook.url = url
    await AppSettingsRepository.update(notification_destinations=dest)


async def _test_destination(client, channel: str):
    return await client.post(
        "/api/settings/notification-destinations/test", json={"channel": channel}
    )


class TestNotificationDestinationTestRoute:
    @pytest.mark.asyncio
    async def test_refused_connection_is_coded(self, test_db, client, caplog):
        await _set_webhook(f"http://127.0.0.1:{_closed_port()}/hook")
        with caplog.at_level(logging.WARNING, logger="app.routers.settings"):
            response = await _test_destination(client, "webhook")
        _assert_coded(response, 502, "notification_test_failed")
        assert "Notification test (webhook) failed" in caplog.text

    @pytest.mark.asyncio
    async def test_dns_failure_is_coded(self, test_db, client, caplog):
        await _set_webhook("https://hook.lan.invalid/hook")
        with caplog.at_level(logging.WARNING, logger="app.routers.settings"), _dns_failure():
            response = await _test_destination(client, "webhook")
        _assert_coded(response, 502, "notification_test_failed")
        assert DNS_MARKER in caplog.text

    @pytest.mark.asyncio
    async def test_http_error_status_is_coded(self, test_db, client):
        async with _failing_http_server(500) as port:
            await _set_webhook(f"http://127.0.0.1:{port}/hook")
            response = await _test_destination(client, "webhook")
        _assert_coded(response, 502, "notification_test_failed")

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
        _assert_coded(response, 502, "notification_test_failed")

    @pytest.mark.asyncio
    async def test_unconfigured_destination_is_coded(self, test_db, client):
        response = await _test_destination(client, "webhook")
        _assert_coded(response, 400, "notification_destination_invalid")
