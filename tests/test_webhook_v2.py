"""The webhook body v2 (MSHL-137): v1 unchanged byte for byte, v2 models, schema, signature,
text policy, the read-time default of ``body_type`` and the doc pages' examples."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
from pathlib import Path

import httpx
import pytest

from app.notifications import webhook_v2
from app.notifications.channels import REGISTRY, Channel, ChannelCreate, ChannelUpdate
from app.notifications.events import event_from_payload
from app.notifications.transports.base import Delivery
from app.notifications.transports.webhook import (
    WebhookTransport,
    body_bytes,
    request_headers,
    request_headers_v2,
    sign_v2,
    verify_signature_v2,
)
from app.repository.notification_channels import NotificationChannelRepository as Channels
from scripts.dev.gen_notifications_golden import EVENTS
from scripts.dev.gen_webhook_schema import SAMPLES, example_bodies

ROOT = Path(__file__).resolve().parents[1]
V1_GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "webhook_v1_golden.json").read_text())
DOCS = {
    lang: ROOT / "docs" / "user" / lang / "deep" / "notifications-webhook.md"
    for lang in ("en", "fr")
}
SCHEMA_FILE = ROOT / "docs" / "user" / "en" / "deep" / "notifications-webhook.schema.json"
SECRET_URL = "https://93.184.215.14/hook?token=token-xyz"
HMAC = "golden-hmac-secret"
INSTANCE = webhook_v2.Instance(name="Base", radio_public_key="cd" * 32)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = "93.184.216.34"
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def seen(monkeypatch) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real = httpx.AsyncClient

    class Recording(real):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda r: requests.append(r) or httpx.Response(204)
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Recording)
    return requests


def _delivery(payload: dict) -> Delivery:
    return Delivery(
        name=str(payload.get("event")), payload=payload, event=event_from_payload(payload)
    )


# -- v1 stays byte for byte ---------------------------------------------------------


@pytest.mark.parametrize("name", sorted(V1_GOLDEN))
def test_v1_body_and_headers_match_the_golden_fixture(name):
    body = body_bytes(EVENTS[name])
    assert body.decode() == V1_GOLDEN[name]["body"]
    headers = request_headers(
        EVENTS[name], body, secret=HMAC, dedup_key="golden-id", now=1700000000
    )
    assert headers == V1_GOLDEN[name]["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{}, {"body_type": "v1"}])
async def test_a_v1_channel_sends_the_golden_bytes(seen, monkeypatch, config):
    monkeypatch.setattr("app.notifications.transports.webhook.time.time", lambda: 1700000000)
    payload = EVENTS["first_seen_companion"]
    delivery = _delivery(payload)
    channel = Channel(id="webhook-1", type="webhook", name="Hook", config=config)
    result = await WebhookTransport().send(
        channel, None, delivery, {"url": SECRET_URL, "hmac_secret": HMAC}
    )
    assert result.ok
    expected = V1_GOLDEN["first_seen_companion"]
    assert seen[0].content.decode() == expected["body"]
    assert seen[0].headers["X-Webhook-Signature"] == expected["headers"]["X-Webhook-Signature"]
    assert "X-Webhook-Version" not in seen[0].headers


# -- v2 bodies -----------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_every_v2_event_builds_a_valid_body(lang):
    bodies = example_bodies(lang)
    assert set(bodies) == set(webhook_v2.BODIES)
    for name, body in bodies.items():
        parsed = webhook_v2.WebhookBodyV2.model_validate(body).root
        assert parsed.event == name and parsed.version == 2
        assert body["rendered"]["title"]


def test_a_payload_without_a_v2_model_builds_nothing():
    assert (
        webhook_v2.build_body(
            {"event": "mystery"}, None, lang="en", text_policy="always", instance=INSTANCE
        )
        is None
    )


def test_the_schema_file_matches_the_models():
    assert SCHEMA_FILE.read_text(encoding="utf-8") == webhook_v2.schema_text(), (
        "regenerate with: uv run python scripts/dev/gen_webhook_schema.py"
    )
    schema = json.loads(SCHEMA_FILE.read_text())
    assert schema["$schema"].endswith("2020-12/schema")
    assert set(schema["discriminator"]["mapping"]) == set(webhook_v2.BODIES)


def test_a_summary_keeps_its_id_across_retries():
    payload = SAMPLES["summary.grouped"]
    first = webhook_v2.build_body(
        payload, None, lang="en", text_policy="always", instance=INSTANCE, now=1
    )
    again = webhook_v2.build_body(
        payload, None, lang="en", text_policy="always", instance=INSTANCE, now=99
    )
    assert first is not None and again is not None
    assert first.dedup_key == again.dedup_key == first.event_id


# -- no more than v1 -----------------------------------------------------------------


PRIVATE = {
    "type": "CHAN",
    "conversation_key": "0123456789ABCDEF0123456789ABCDEF",
    "channel_name": "Secret club",
    "sender_name": "Bob",
    "text": "the private words",
}


@pytest.mark.parametrize(
    ("policy", "shown"), [("always", True), ("public_only", False), ("never", False)]
)
def test_message_text_follows_the_text_policy_and_no_key_is_sent(policy, shown):
    body = webhook_v2.build_body(
        PRIVATE, event_from_payload(PRIVATE), lang="en", text_policy=policy, instance=INSTANCE
    )
    assert body is not None
    wire = webhook_v2.body_bytes(body).decode()
    assert ("the private words" in wire) is shown
    assert body.data.text == (PRIVATE["text"] if shown else None)  # type: ignore[attr-defined]
    assert PRIVATE["conversation_key"] not in wire
    assert PRIVATE["conversation_key"].lower() not in wire


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_v2_data_holds_no_value_the_v1_body_lacks(name):
    """Every string in ``data`` is already in the v1 body of the same payload (or is the
    enriched metric/unit, which v1 carries too)."""
    payload = SAMPLES[name]
    event = event_from_payload(payload)
    body = webhook_v2.build_body(payload, event, lang="en", text_policy="always", instance=INSTANCE)
    assert body is not None
    v1 = body_bytes(payload).decode()

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list | tuple):
            for v in value:
                yield from strings(v)

    data = json.loads(webhook_v2.body_bytes(body))["data"]
    for value in strings(data):
        if value in ("PRIV", "CHAN", "") or value in v1:
            continue
        assert json.dumps(value)[1:-1] in v1, (name, value)


# -- v2 signature ----------------------------------------------------------------------


def test_v2_signature_covers_timestamp_and_body():
    body = b'{"version":2}'
    expected = (
        "sha256=" + hmac.new(HMAC.encode(), b"1700000000." + body, hashlib.sha256).hexdigest()
    )
    assert sign_v2(HMAC, "1700000000", body) == expected
    assert verify_signature_v2(HMAC, "1700000000", body, expected)
    assert not verify_signature_v2(HMAC, "1700000001", body, expected)
    assert not verify_signature_v2(HMAC, "1700000000", body + b" ", expected)
    assert not verify_signature_v2("other", "1700000000", body, expected)
    assert not verify_signature_v2(HMAC, None, body, expected)
    assert not verify_signature_v2(HMAC, "1700000000", body, None)


def test_v2_headers_sign_the_timestamp_they_send():
    model = webhook_v2.build_body(
        SAMPLES["app.update"],
        event_from_payload(SAMPLES["app.update"]),
        lang="en",
        text_policy="always",
        instance=INSTANCE,
    )
    assert model is not None
    body = webhook_v2.body_bytes(model)
    headers = request_headers_v2(model, body, secret=HMAC, now=1700000000)
    assert headers["X-Webhook-Timestamp"] == "1700000000"
    assert headers["X-Webhook-Event"] == "app.update"
    assert headers["X-Webhook-Id"] == model.dedup_key
    assert headers["X-Webhook-Version"] == "2"
    assert verify_signature_v2(HMAC, "1700000000", body, headers["X-Webhook-Signature"])
    assert "X-Webhook-Signature" not in request_headers_v2(model, body)


@pytest.mark.asyncio
async def test_a_v2_channel_sends_a_signed_v2_body(seen):
    channel = Channel(
        id="webhook-2", type="webhook", name="Hook", language="en", config={"body_type": "v2"}
    )
    payload = SAMPLES["contact.first_seen"]
    result = await WebhookTransport().send(
        channel, None, _delivery(payload), {"url": SECRET_URL, "hmac_secret": HMAC}
    )
    assert result.ok
    request = seen[0]
    body = webhook_v2.WebhookBodyV2.model_validate_json(request.content).root
    assert body.event == "contact.first_seen"
    assert request.headers["X-Webhook-Id"] == body.dedup_key
    assert verify_signature_v2(
        HMAC,
        request.headers["X-Webhook-Timestamp"],
        request.content,
        request.headers["X-Webhook-Signature"],
    )


@pytest.mark.asyncio
async def test_a_v2_channel_refuses_a_payload_it_has_no_model_for(seen):
    channel = Channel(
        id="webhook-2", type="webhook", name="Hook", language="en", config={"body_type": "v2"}
    )
    result = await WebhookTransport().send(
        channel, None, Delivery(name="x", payload={"event": "mystery"}), {"url": SECRET_URL}
    )
    assert (result.status, result.error_code) == ("failed", "v2_unsupported_event")
    assert seen == []


# -- body_type default -----------------------------------------------------------------


def test_a_stored_config_without_body_type_reads_as_v1():
    entry = REGISTRY["webhook"]
    assert entry.read_config({}) == {"body_type": "v1"}
    assert entry.read_config({"body_type": "v2"}) == {"body_type": "v2"}
    assert REGISTRY["email"].read_config({}) == {}


@pytest.mark.asyncio
async def test_new_channels_are_v2_and_old_rows_read_as_v1(test_db):
    created = await Channels.create(
        ChannelCreate(type="webhook", name="Hook", config={"url": SECRET_URL})
    )
    assert created.config["body_type"] == "v2"

    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE notification_channels SET config = ? WHERE id = ?", ("{}", created.id)
        )
    old = await Channels.get(created.id)
    assert old is not None and old.config["body_type"] == "v1"

    # Saving the old channel writes the key it read; switching to v2 sticks.
    renamed = await Channels.update(created.id, ChannelUpdate(name="Renamed"))
    assert renamed.config["body_type"] == "v1"
    switched = await Channels.update(created.id, ChannelUpdate(config={"body_type": "v2"}))
    assert switched.config["body_type"] == "v2"


def test_an_old_backup_restores_as_v1():
    from app.services.backup_notifications import _public_config, _same_config

    entry = REGISTRY["webhook"]
    assert _public_config(entry, {"url": SECRET_URL}) == {"body_type": "v1"}
    assert _public_config(entry, {"body_type": "v2"}) == {"body_type": "v2"}
    assert _same_config(entry, {}, {"body_type": "v1"})
    assert not _same_config(entry, {"body_type": "v2"}, {})


# -- the doc pages ---------------------------------------------------------------------


def _blocks(lang: str, kind: str) -> list[str]:
    text = DOCS[lang].read_text(encoding="utf-8")
    return re.findall(rf"```{kind}\n(.*?)```", text, flags=re.S)


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_every_doc_example_validates_and_every_event_has_one(lang):
    events = set()
    for block in _blocks(lang, "json"):
        doc = json.loads(block)
        if doc.get("version") != 2:
            continue
        events.add(webhook_v2.WebhookBodyV2.model_validate(doc).root.event)
    assert events == set(webhook_v2.BODIES)


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_the_doc_python_snippet_verifies_a_real_request(lang):
    (snippet,) = [b for b in _blocks(lang, "python") if "def verify" in b]
    scope: dict = {}
    exec(compile(snippet, str(DOCS[lang]), "exec"), scope)  # noqa: S102 - our own doc
    verify = scope["verify"]

    model = webhook_v2.build_body(
        SAMPLES["app.update"],
        event_from_payload(SAMPLES["app.update"]),
        lang=lang,
        text_policy="always",
        instance=INSTANCE,
    )
    assert model is not None
    body = webhook_v2.body_bytes(model)
    headers = request_headers_v2(model, body, secret=HMAC)
    ts, sig = headers["X-Webhook-Timestamp"], headers["X-Webhook-Signature"]
    assert verify(HMAC, ts, body, sig)
    assert not verify(HMAC, ts, body + b" ", sig)
    assert not verify("wrong", ts, body, sig)
    stale = str(int(time.time()) - 3600)
    assert not verify(HMAC, stale, body, sign_v2(HMAC, stale, body))
