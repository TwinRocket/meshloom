"""The native webhook transport: v1 body and signature, new headers, pinning, retries."""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from pathlib import Path

import httpx
import pytest

from app.notifications.channels import Channel
from app.notifications.events import event_from_payload
from app.notifications.transports.base import Delivery
from app.notifications.transports.webhook import (
    WebhookTransport,
    body_bytes,
    request_headers,
    sign,
    verify_signature,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "notifications_golden.json").read_text(encoding="utf-8")
)
SECRET_URL = "https://93.184.215.14/hook?token=token-xyz"
HMAC = "hmac-secret-1"


class _Seen(list):
    answers: list[int]
    kwargs: list[dict]


@pytest.fixture
def requests_seen(monkeypatch) -> _Seen:
    seen = _Seen()
    answers: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(answers.pop(0) if answers else 204)

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, **kwargs):
            seen_kwargs.append(kwargs)
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    seen_kwargs: list[dict] = []
    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    seen.answers = answers
    seen.kwargs = seen_kwargs
    return seen


def _channel(**kwargs) -> Channel:
    return Channel(id="webhook-1", type="webhook", name="Hook", **kwargs)


def _delivery(payload: dict) -> Delivery:
    return Delivery(
        name=str(payload.get("event")), payload=payload, event=event_from_payload(payload)
    )


PAYLOAD = {"event": "first_seen", "name": "Hill", "contact_type": 2}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_v1_body_is_identical_to_the_golden_fixture(name):
    from scripts.dev.gen_notifications_golden import EVENTS

    assert body_bytes(EVENTS[name]).decode() == GOLDEN[name]["fr"]["webhook_body"]


def test_signature_is_the_v1_hmac_of_the_body_only():
    body = body_bytes(PAYLOAD)
    headers = request_headers(PAYLOAD, body, secret=HMAC, dedup_key="abc", now=1_700_000_000)
    assert headers["X-Webhook-Signature"] == sign(HMAC, body)
    assert headers["X-Webhook-Signature"].startswith("sha256=")
    # The timestamp is not part of the signed bytes: v1 stays as receivers verify it.
    later = request_headers(PAYLOAD, body, secret=HMAC, dedup_key="abc", now=1_800_000_000)
    assert later["X-Webhook-Signature"] == headers["X-Webhook-Signature"]
    assert headers["X-Webhook-Timestamp"] == "1700000000"
    assert headers["X-Webhook-Id"] == "abc"
    assert headers["X-Webhook-Event"] == "first_seen"


def test_unsigned_without_a_secret_and_id_falls_back_to_a_body_hash():
    body = body_bytes(PAYLOAD)
    headers = request_headers(PAYLOAD, body)
    assert "X-Webhook-Signature" not in headers
    assert len(headers["X-Webhook-Id"]) == 16
    assert headers["X-Webhook-Id"] == request_headers(PAYLOAD, body)["X-Webhook-Id"]


def test_verify_signature_is_constant_time_and_strict(monkeypatch):
    import hmac as hmac_module

    body = body_bytes(PAYLOAD)
    good = sign(HMAC, body)
    calls = []
    real = hmac_module.compare_digest
    monkeypatch.setattr(
        "app.notifications.transports.webhook.hmac.compare_digest",
        lambda a, b: calls.append(1) or real(a, b),
    )
    assert verify_signature(HMAC, body, good)
    assert calls
    assert not verify_signature(HMAC, body, good[:-1] + "0")
    assert not verify_signature("other", body, good)
    assert not verify_signature(HMAC, body, None)
    assert not verify_signature("", body, good)


@pytest.mark.asyncio
async def test_send_posts_signed_body_with_id_and_timestamp(requests_seen):
    delivery = _delivery(PAYLOAD)
    before = int(time.time())
    result = await WebhookTransport().send(
        _channel(), None, delivery, {"url": SECRET_URL, "hmac_secret": HMAC}
    )
    assert result.ok
    (request,) = requests_seen
    assert request.content == body_bytes(PAYLOAD)
    assert request.headers["X-Webhook-Id"] == delivery.event.dedup_key
    assert before <= int(request.headers["X-Webhook-Timestamp"]) <= int(time.time())
    assert verify_signature(HMAC, request.content, request.headers["X-Webhook-Signature"])
    assert request.headers["Host"] == "93.184.215.14"


@pytest.mark.asyncio
async def test_a_retry_keeps_the_id_and_refreshes_the_timestamp(requests_seen, monkeypatch):
    delivery = _delivery(PAYLOAD)
    clock = iter([1000.0, 1005.0])
    monkeypatch.setattr(
        "app.notifications.transports.webhook.time",
        type("Clock", (), {"time": staticmethod(lambda: next(clock))}),
    )
    for _ in range(2):
        await WebhookTransport().send(_channel(), None, delivery, {"url": SECRET_URL})
    first, second = requests_seen
    assert first.headers["X-Webhook-Id"] == second.headers["X-Webhook-Id"]
    assert first.headers["X-Webhook-Timestamp"] == "1000"
    assert second.headers["X-Webhook-Timestamp"] == "1005"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "status", "code"),
    [
        (503, "retry", "service_error"),
        (429, "retry", "rate_limited"),
        (401, "failed", "http_401"),
        (404, "failed", "http_404"),
        (302, "failed", "http_302"),
    ],
)
async def test_http_answers_follow_the_dispatcher_policy(requests_seen, answer, status, code):
    requests_seen.answers.append(answer)
    result = await WebhookTransport().send(
        _channel(), None, _delivery(PAYLOAD), {"url": SECRET_URL}
    )
    assert (result.status, result.error_code) == (status, code)
    assert len(requests_seen) == 1  # the transport never retries or follows a redirect


@pytest.mark.asyncio
async def test_not_configured_and_refused_destinations_are_coded(requests_seen):
    send = WebhookTransport().send
    assert (await send(_channel(), None, _delivery(PAYLOAD), {})).error_code == "not_configured"
    private = await send(
        _channel(), None, _delivery(PAYLOAD), {"url": "http://192.168.1.10:1880/hook"}
    )
    assert (private.status, private.error_code) == ("failed", "egress_private_address")
    scheme = await send(_channel(), None, _delivery(PAYLOAD), {"url": "ftp://93.184.215.14/x"})
    assert scheme.error_code == "egress_scheme"
    assert not requests_seen


@pytest.mark.asyncio
async def test_private_network_needs_the_channel_flag(requests_seen):
    channel = _channel(allow_private_network=True)
    result = await WebhookTransport().send(
        channel, None, _delivery(PAYLOAD), {"url": "http://192.168.1.10:1880/hook"}
    )
    assert result.ok
    assert requests_seen[0].url.host == "192.168.1.10"


@pytest.mark.asyncio
async def test_verify_tls_off_only_with_private_network(requests_seen):
    refused = await WebhookTransport().send(
        _channel(verify_tls=False), None, _delivery(PAYLOAD), {"url": SECRET_URL}
    )
    assert refused.error_code == "verify_tls_requires_private_network"
    assert not requests_seen
    ok = await WebhookTransport().send(
        _channel(verify_tls=False, allow_private_network=True),
        None,
        _delivery(PAYLOAD),
        {"url": "https://192.168.1.10/hook"},
    )
    assert ok.ok
    assert requests_seen.kwargs[-1]["verify"] is False
    await WebhookTransport().send(_channel(), None, _delivery(PAYLOAD), {"url": SECRET_URL})
    assert requests_seen.kwargs[-1]["verify"] is True


@pytest.mark.asyncio
async def test_pinned_to_the_resolved_address(requests_seen, monkeypatch):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    await WebhookTransport().send(
        _channel(), None, _delivery(PAYLOAD), {"url": "https://hooks.example.org/x"}
    )
    (request,) = requests_seen
    assert request.url.host == str(ipaddress.ip_address("93.184.216.34"))
    assert request.headers["Host"] == "hooks.example.org"
    assert request.extensions["sni_hostname"] == "hooks.example.org"


@pytest.mark.asyncio
async def test_secrets_are_never_logged(requests_seen, caplog):
    requests_seen.answers.append(500)
    caplog.set_level("DEBUG")
    await WebhookTransport().send(
        _channel(), None, _delivery(PAYLOAD), {"url": SECRET_URL, "hmac_secret": HMAC}
    )
    assert "token-xyz" not in caplog.text
    assert HMAC not in caplog.text
