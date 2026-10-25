"""The native Discord transport: allowed_mentions, escaping, host rule, rate limits."""

from __future__ import annotations

import ipaddress
import json
import socket

import httpx
import pytest

from app.notifications.channels import Channel, DiscordConfig, parse_discord_webhook
from app.notifications.events import event_from_payload
from app.notifications.transports import discord as discord_module
from app.notifications.transports.base import Delivery
from app.notifications.transports.discord import DiscordTransport

WEBHOOK_ID = "123456789012345678"
TOKEN = "tok_en-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
URL = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{TOKEN}"
CHAN_MESSAGE = {
    "event": "message",
    "type": "CHAN",
    "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
    "channel_name": "Public",
    "sender_name": "Eve",
    "text": "venez @everyone [ici](https://evil)",
}


@pytest.fixture(autouse=True)
def _dns_and_buckets(monkeypatch):
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = "162.159.135.232"
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    discord_module._blocked_until.clear()


class _Seen(list):
    answers: list[httpx.Response]


@pytest.fixture
def requests_seen(monkeypatch):
    seen = _Seen()
    answers = seen.answers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.pop(0) if answers else httpx.Response(204)

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return seen


def _channel(**kwargs) -> Channel:
    kwargs.setdefault("include_message_text", "always")
    return Channel(id="discord-1", type="discord", name="D", **kwargs)


def _delivery(payload: dict) -> Delivery:
    return Delivery(
        name=str(payload.get("event")), payload=payload, event=event_from_payload(payload)
    )


async def _send(requests_seen, payload=CHAN_MESSAGE, channel=None, url=URL):
    return await DiscordTransport().send(
        channel or _channel(), None, _delivery(payload), {"webhook_url": url}
    )


@pytest.mark.asyncio
async def test_channel_message_is_escaped_and_never_pings(requests_seen):
    result = await _send(requests_seen)
    assert result.ok
    (request,) = requests_seen
    body = json.loads(request.content)
    assert body["allowed_mentions"] == {"parse": []}
    assert "@everyone" not in body["content"]
    assert "\\[ici\\]\\(https://evil\\)" in body["content"]
    assert body["flags"] == 4
    assert "username" not in body and "avatar_url" not in body
    assert request.url.path == f"/api/webhooks/{WEBHOOK_ID}/{TOKEN}"


@pytest.mark.asyncio
async def test_username_is_sent_when_set_and_content_is_capped(requests_seen):
    payload = {**CHAN_MESSAGE, "text": "x" * 5000}
    await _send(requests_seen, payload, _channel(config={"username": "Meshloom"}))
    body = json.loads(requests_seen[0].content)
    assert body["username"] == "Meshloom"
    assert len(body["content"]) <= 2000


@pytest.mark.asyncio
async def test_a_dm_is_title_only_under_the_default_cloud_policy(requests_seen):
    dm = {**CHAN_MESSAGE, "type": "PRIV", "text": "secret words"}
    await _send(requests_seen, dm, _channel(include_message_text="public_only"))
    assert "secret words" not in json.loads(requests_seen[0].content)["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "state", "code"),
    [
        (401, "failed", "auth_failed"),
        (403, "failed", "auth_failed"),
        (404, "failed", "auth_failed"),
        (400, "failed", "invalid_config"),
        (500, "retry", "service_error"),
        (418, "failed", "http_418"),
    ],
)
async def test_status_mapping(requests_seen, status, state, code):
    requests_seen.answers.append(httpx.Response(status))
    result = await _send(requests_seen)
    assert (result.status, result.error_code) == (state, code)
    assert len(requests_seen) == 1


@pytest.mark.asyncio
async def test_429_carries_retry_after_from_the_body_and_headers(requests_seen):
    requests_seen.answers.append(
        httpx.Response(
            429, headers={"Retry-After": "2"}, content=b'{"retry_after": 3.5, "global": false}'
        )
    )
    result = await _send(requests_seen)
    assert (result.status, result.error_code, result.retry_after) == (
        "retry",
        "rate_limited",
        3.5,
    )


@pytest.mark.asyncio
async def test_429_without_a_json_body_uses_the_header(requests_seen):
    requests_seen.answers.append(httpx.Response(429, headers={"Retry-After": "7"}, content=b"no"))
    assert (await _send(requests_seen)).retry_after == 7


@pytest.mark.asyncio
async def test_an_empty_bucket_makes_the_next_send_wait_without_a_request(requests_seen):
    requests_seen.answers.append(
        httpx.Response(204, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "30"})
    )
    assert (await _send(requests_seen)).ok
    second = await _send(requests_seen)
    assert (second.status, second.error_code) == ("retry", "rate_limited")
    assert 0 < (second.retry_after or 0) <= 30
    assert len(requests_seen) == 1


@pytest.mark.asyncio
async def test_a_bucket_with_room_does_not_block(requests_seen):
    requests_seen.answers.append(
        httpx.Response(204, headers={"X-RateLimit-Remaining": "3", "X-RateLimit-Reset-After": "30"})
    )
    assert (await _send(requests_seen)).ok
    assert (await _send(requests_seen)).ok
    assert len(requests_seen) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.example/api/webhooks/{WEBHOOK_ID}/{TOKEN}",
        f"http://discord.com/api/webhooks/{WEBHOOK_ID}/{TOKEN}",
        f"https://discord.com.evil.example/api/webhooks/{WEBHOOK_ID}/{TOKEN}",
        f"https://discord.com@evil.example/api/webhooks/{WEBHOOK_ID}/{TOKEN}",
        f"https://discord.com:8443/api/webhooks/{WEBHOOK_ID}/{TOKEN}",
        "https://discord.com/api/webhooks/abc/def",
    ],
)
async def test_a_wrong_host_is_refused_before_any_request(requests_seen, url):
    result = await _send(requests_seen, url=url)
    assert (result.status, result.error_code) == ("failed", "invalid_config")
    assert not requests_seen


@pytest.mark.asyncio
async def test_not_configured_and_verify_tls_off_on_a_public_host(requests_seen):
    result = await DiscordTransport().send(_channel(), None, _delivery(CHAN_MESSAGE), {})
    assert result.error_code == "not_configured"
    refused = await _send(requests_seen, channel=_channel(verify_tls=False))
    assert refused.error_code == "verify_tls_requires_private_destination"
    assert not requests_seen


@pytest.mark.asyncio
async def test_the_token_never_reaches_a_log(requests_seen, caplog):
    caplog.set_level("DEBUG")
    requests_seen.answers.append(httpx.Response(401))
    await _send(requests_seen)

    class Boom(httpx.ConnectError):
        pass

    async def raising(*_a, **_k):
        raise Boom(f"cannot connect to {URL}")

    import app.net.egress_guard as guard

    requests_seen.answers.append(httpx.Response(204))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(guard.PinnedClient, "request", raising)
        result = await _send(requests_seen)
    assert result.status == "retry"
    assert TOKEN not in caplog.text


def test_config_accepts_only_discord_webhooks_and_hides_the_error_input():
    assert parse_discord_webhook(URL) == (WEBHOOK_ID, TOKEN)
    assert parse_discord_webhook(URL.replace("discord.com", "discordapp.com"))
    assert parse_discord_webhook(f"https://discord.com/api/v10/webhooks/{WEBHOOK_ID}/{TOKEN}")
    with pytest.raises(ValueError) as exc:
        DiscordConfig(webhook_url="https://evil.example/api/webhooks/1/" + TOKEN)  # type: ignore[arg-type]
    assert TOKEN not in str(exc.value)
    schema = DiscordConfig.model_json_schema()
    assert schema["properties"]["webhook_url"]["writeOnly"] is True
