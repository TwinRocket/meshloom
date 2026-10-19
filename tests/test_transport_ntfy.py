"""The native ntfy transport: JSON body, auth, priorities, error mapping, guard, secrets."""

from __future__ import annotations

import base64
import json
import re
import ssl

import httpx
import pytest

from app.notifications.channels import (
    REGISTRY,
    Channel,
    NtfyConfig,
    generate_ntfy_topic,
    ntfy_is_hosted,
)
from app.notifications.events import event_from_payload
from app.notifications.transports.base import Delivery
from app.notifications.transports.ntfy import NtfyTransport

TOPIC = "s3cret-topic_AbC123"
SERVER = "https://93.184.215.14"
DM = {
    "event": "new_message",
    "type": "PRIV",
    "sender": "Léa",
    "sender_name": "Léa",
    "text": "Bonjour, ça va ?",
    "conversation_key": "ab" * 32,
}
OFFLINE = {"event": "radio_offline"}


class _Seen(list):
    answers: list[httpx.Response]
    kwargs: list[dict]


@pytest.fixture
def requests_seen(monkeypatch) -> _Seen:
    seen = _Seen()
    answers: list[httpx.Response] = []
    seen_kwargs: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.pop(0) if answers else httpx.Response(200, json={"id": "x"})

    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, **kwargs):
            seen_kwargs.append(kwargs)
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    seen.answers = answers
    seen.kwargs = seen_kwargs
    return seen


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    async def en():
        return "en"

    monkeypatch.setattr("app.email_template.resolve_instance_language", en)


def _channel(config: dict | None = None, **kwargs) -> Channel:
    return Channel(
        id="ntfy-1",
        type="ntfy",
        name="Phone",
        config={"server": SERVER, **(config or {})},
        **kwargs,
    )


def _delivery(payload: dict) -> Delivery:
    return Delivery(
        name=str(payload.get("event")), payload=payload, event=event_from_payload(payload)
    )


async def _send(channel=None, payload=DM, secrets=None):
    return await NtfyTransport().send(
        channel or _channel(),
        None,
        _delivery(payload),
        {"topic": TOPIC} if secrets is None else secrets,
    )


@pytest.mark.asyncio
async def test_json_body_keeps_the_topic_out_of_the_url_and_utf8_titles(requests_seen):
    result = await _send(_channel({"tags": "mesh, phone", "markdown": True}))
    assert result.ok
    (request,) = requests_seen
    assert request.method == "POST"
    assert TOPIC not in str(request.url)
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert body["topic"] == TOPIC
    assert body["priority"] == 3
    assert body["tags"] == ["mesh", "phone"]
    assert body["markdown"] is True
    assert "Léa" in body["title"] or "Léa" in body["message"]
    # No ntfy header carries the text: a French title would not encode in ASCII.
    assert not {"title", "priority", "tags"} & {k.lower() for k in request.headers}


@pytest.mark.asyncio
async def test_urgent_events_use_the_urgent_priority(requests_seen):
    await _send(_channel({"priority_default": 2, "priority_urgent": 4}), OFFLINE)
    await _send(_channel({"priority_default": 2, "priority_urgent": 4}), DM)
    urgent, normal = (json.loads(r.content)["priority"] for r in requests_seen)
    assert (urgent, normal) == (4, 2)


@pytest.mark.asyncio
async def test_radio_offline_defaults_to_priority_5(requests_seen):
    await _send(payload=OFFLINE)
    assert json.loads(requests_seen[0].content)["priority"] == 5


@pytest.mark.asyncio
async def test_click_is_the_base_url_plus_the_url_hash(requests_seen):
    await _send(_channel({"click_url": "https://mesh.example.net/"}))
    click = json.loads(requests_seen[0].content)["click"]
    assert click == f"https://mesh.example.net/#contact/{'ab' * 32}"
    await _send()
    assert "click" not in json.loads(requests_seen[1].content)


@pytest.mark.asyncio
async def test_bearer_token_wins_over_basic(requests_seen):
    await _send(
        _channel({"username": "u"}), secrets={"topic": TOPIC, "token": "tk_abc", "password": "pw"}
    )
    assert requests_seen[0].headers["Authorization"] == "Bearer tk_abc"


@pytest.mark.asyncio
async def test_basic_auth_from_username_and_password(requests_seen):
    await _send(_channel({"username": "léa"}), secrets={"topic": TOPIC, "password": "pä:ss"})
    header = requests_seen[0].headers["Authorization"]
    assert header.startswith("Basic ")
    assert base64.b64decode(header[6:]).decode() == "léa:pä:ss"


@pytest.mark.asyncio
async def test_no_auth_header_without_credentials(requests_seen):
    await _send()
    assert "Authorization" not in requests_seen[0].headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "status", "code", "retry_after"),
    [
        (httpx.Response(401, json={"error": "unauthorized"}), "failed", "auth_failed", None),
        (httpx.Response(403), "failed", "auth_failed", None),
        (httpx.Response(413), "failed", "service_error", None),
        (httpx.Response(400), "failed", "http_400", None),
        (httpx.Response(502), "retry", "service_error", None),
        (httpx.Response(429, headers={"Retry-After": "17"}), "retry", "rate_limited", 17.0),
        (
            httpx.Response(429, json={"code": 42901, "error": "limit reached: too many requests"}),
            "retry",
            "rate_limited",
            None,
        ),
        (
            httpx.Response(
                429, json={"code": 42908, "error": "limit reached: daily message quota reached"}
            ),
            "failed",
            "quota_exceeded",
            None,
        ),
    ],
)
async def test_error_mapping(requests_seen, response, status, code, retry_after):
    requests_seen.answers.append(response)
    result = await _send()
    assert (result.status, result.error_code, result.retry_after) == (status, code, retry_after)


@pytest.mark.asyncio
async def test_network_errors_and_tls_failures(monkeypatch):
    real = httpx.AsyncClient

    def boom(exc):
        class Failing(real):
            def __init__(self, **kwargs):
                def handler(request):
                    raise exc

                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Failing)

    boom(httpx.ReadTimeout("slow"))
    timeout = await _send()
    assert (timeout.status, timeout.error_code) == ("retry", "timeout")
    tls_failure = httpx.ConnectError("certificate")
    tls_failure.__cause__ = ssl.SSLCertVerificationError("self signed certificate")
    boom(tls_failure)
    tls = await _send()
    assert (tls.status, tls.error_code) == ("failed", "tls_error")


@pytest.mark.asyncio
async def test_not_configured_without_a_topic(requests_seen):
    result = await _send(secrets={})
    assert (result.status, result.error_code) == ("failed", "not_configured")
    assert not requests_seen


@pytest.mark.asyncio
async def test_private_server_needs_the_channel_flag(requests_seen):
    private = {"server": "http://192.168.1.20:8080"}
    refused = await _send(_channel(private))
    assert (refused.status, refused.error_code) == ("failed", "egress_private_address")
    assert not requests_seen
    ok = await _send(_channel(private, allow_private_network=True))
    assert ok.ok
    assert requests_seen[0].url.host == "192.168.1.20"


@pytest.mark.asyncio
async def test_verify_tls_off_only_for_a_private_destination(requests_seen):
    for flag in (False, True):
        refused = await _send(_channel(verify_tls=False, allow_private_network=flag))
        assert refused.error_code == "verify_tls_requires_private_destination"
    assert not requests_seen
    lan = _channel({"server": "https://192.168.1.20"}, verify_tls=False, allow_private_network=True)
    assert (await _send(lan)).ok
    assert requests_seen.kwargs[-1]["verify"] is False
    await _send()
    assert requests_seen.kwargs[-1]["verify"] is True


@pytest.mark.asyncio
async def test_secrets_are_never_logged(requests_seen, caplog):
    requests_seen.answers.append(httpx.Response(500))
    caplog.set_level("DEBUG")
    await _send(
        _channel({"username": "u"}),
        secrets={"topic": TOPIC, "token": "tk_zzz", "password": "pw-zzz"},
    )
    for secret in (TOPIC, "tk_zzz", "pw-zzz"):
        assert secret not in caplog.text


def test_generated_topics_are_24_url_safe_characters_and_distinct():
    topics = {generate_ntfy_topic() for _ in range(50)}
    assert len(topics) == 50
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{24}", t) for t in topics)


def test_config_validation_hides_the_topic_and_refuses_bad_urls():
    with pytest.raises(ValueError) as err:
        NtfyConfig(topic="not a valid/topic")  # type: ignore[arg-type]
    assert "not a valid" not in str(err.value)
    with pytest.raises(ValueError):
        NtfyConfig(server="ftp://ntfy.example")
    with pytest.raises(ValueError):
        NtfyConfig(click_url="javascript:alert(1)")
    schema = REGISTRY["ntfy"].json_schema()
    props = schema["properties"]
    assert {n for n, p in props.items() if p.get("writeOnly")} == {"topic", "token", "password"}
    assert props["priority_default"]["default"] == 3
    assert props["priority_urgent"]["default"] == 5
    assert props["server"]["default"] == "https://ntfy.sh"


def test_ntfy_sh_is_cloud_with_a_daily_limit_and_a_self_hosted_server_is_not_capped():
    entry = REGISTRY["ntfy"]
    assert entry.cloud
    assert ntfy_is_hosted({}) and ntfy_is_hosted({"server": "https://ntfy.sh/"})
    assert not ntfy_is_hosted({"server": "https://ntfy.sh.evil.example"})
    assert entry.limits_for({}).per_day == 200
    assert entry.limits_for({"server": "http://192.168.1.20"}).per_day is None
    assert entry.include_message_text_for({}) == "public_only"
    assert entry.include_message_text_for({"server": "http://192.168.1.20"}) == "always"


def test_quota_is_detected_by_the_json_code_not_the_wording():
    from app.notifications.transports.ntfy import is_quota_response

    assert is_quota_response(httpx.Response(429, json={"code": 42908, "error": "anything"}))
    assert not is_quota_response(
        httpx.Response(429, json={"code": 42901, "error": "daily quota too many requests"})
    )
    assert not is_quota_response(httpx.Response(429, text="daily quota"))
    assert not is_quota_response(httpx.Response(429, json=["code", 42908]))


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://m.example", "https://m.example/#contact/ab"),
        ("https://m.example/", "https://m.example/#contact/ab"),
        ("https://m.example/app/?x=1", "https://m.example/app/?x=1#contact/ab"),
    ],
)
def test_click_url_is_joined_with_urlsplit(base, expected):
    from app.notifications.transports.ntfy import click_url

    assert click_url(base, "#contact/ab") == expected
    assert click_url("https://m.example/#old", "#contact/ab") == "https://m.example/#contact/ab"


def test_server_and_click_url_refuse_userinfo_and_a_click_fragment():
    for bad in ("https://u:pw@ntfy.example/x", "https://tok@ntfy.example"):
        with pytest.raises(ValueError):
            NtfyConfig(server=bad)
        with pytest.raises(ValueError):
            NtfyConfig(click_url=bad)
    with pytest.raises(ValueError):
        NtfyConfig(click_url="https://m.example/#a")
    assert NtfyConfig(server="https://ntfy.example/base").server == "https://ntfy.example/base"
