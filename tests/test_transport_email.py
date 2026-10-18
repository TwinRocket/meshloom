"""The native e-mail transport: recipients in one Apprise URL, language, neutral subject."""

from __future__ import annotations

import email
import smtplib
from email import policy

import pytest

from app.notifications.channels import Channel, email_is_cloud
from app.notifications.events import event_from_payload
from app.notifications.transports import email as email_transport
from app.notifications.transports.base import Delivery
from app.notifications.transports.email import (
    EmailTransport,
    build_email_apprise_url,
    parse_recipients,
    render_email,
)

CONFIG = {
    "host": "93.184.215.14",
    "port": 25,
    "mode": "none",
    "from": "meshloom@example.org",
    "to": "a@example.org\nb@example.org, c@example.org",
}
DM = {
    "type": "PRIV",
    "conversation_key": "aa" * 32,
    "sender_name": "Léa",
    "text": "secret plans",
}
PUBLIC = {
    "type": "CHAN",
    "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
    "channel_name": "Public",
    "sender_name": "Bob",
    "text": "hello mesh",
}


def _channel(**kwargs) -> Channel:
    config = kwargs.pop("config", CONFIG)
    return Channel(id="email-1", type="email", name="Mail", config=config, **kwargs)


def _delivery(payload: dict) -> Delivery:
    return Delivery(
        name=str(payload.get("event")), payload=payload, event=event_from_payload(payload)
    )


@pytest.fixture(autouse=True)
def _instance_language(monkeypatch):
    async def french():
        return "fr"

    monkeypatch.setattr("app.email_template.resolve_instance_language", french)


class _SMTP:
    sent: list[tuple[str, list[str], str]] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, *a, **k):
        return (220, b"")

    def login(self, *a, **k):
        return (235, b"")

    def sendmail(self, from_addr, to_addrs, msg):
        type(self).sent.append((from_addr, list(to_addrs), msg))
        return {}

    def send_message(self, msg, from_addr=None, to_addrs=None, **kw):
        type(self).sent.append((from_addr, list(to_addrs or []), msg.as_string()))
        return {}

    def quit(self):
        return (221, b"")

    def close(self):
        return None


@pytest.fixture
def smtp(monkeypatch):
    _SMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    return _SMTP.sent


def test_recipients_are_split_deduplicated_and_validated():
    assert parse_recipients("a@x.org\nb@x.org, c@x.org;a@x.org\n\nnonsense") == [
        "a@x.org",
        "b@x.org",
        "c@x.org",
    ]
    assert parse_recipients("Léa <lea@x.org>") == ["Léa <lea@x.org>"]
    assert parse_recipients(None) == []


def test_three_recipients_make_one_apprise_url_with_three_targets():
    import apprise

    url = build_email_apprise_url(CONFIG)
    assert url.count("mailto://") == 1
    assert "to=a%40example.org%2Cb%40example.org%2Cc%40example.org" in url
    server = apprise.Apprise.instantiate(url)
    assert len(server) == 3  # the mailto plugin splits the comma list itself


def test_cc_and_tls_options_reach_the_url():
    import apprise

    url = build_email_apprise_url(
        {**CONFIG, "mode": "starttls", "cc": "boss@example.org", "password": "pw"},
        verify_tls=False,
    )
    assert url.startswith("mailtos://")
    assert "verify=no" in url
    server = apprise.Apprise.instantiate(url)
    assert server.cc == {"boss@example.org"}
    assert server.verify_certificate is False
    assert "verify=no" not in build_email_apprise_url(CONFIG)


@pytest.mark.parametrize(
    ("lang", "needle", "absent"),
    [("fr", "Tension de la batterie", "Battery"), ("en", "Battery", "Tension")],
)
def test_template_follows_the_language(lang, needle, absent):
    payload = {
        "event": "telemetry_alert",
        "name": "Hill",
        "rule_id": "battery",
        "value": 3.46,
        "threshold": 3.5,
        "op": "lt",
    }
    _, html = render_email(payload, lang, "always")
    assert needle in html and absent not in html
    assert f'<html lang="{lang}">' in html


@pytest.mark.asyncio
async def test_channel_language_wins_over_the_instance(smtp):
    for language, expected in (
        ("en", "New message"),
        ("fr", "Nouveau message"),
        ("auto", "Nouveau message"),
    ):
        result = await EmailTransport().send(
            _channel(language=language, include_message_text="never"), None, _delivery(DM), {}
        )
        assert result.ok
        assert expected in smtp[-1][2]


@pytest.mark.parametrize(
    ("policy_", "payload", "withheld"),
    [
        ("never", DM, True),
        ("never", PUBLIC, True),
        ("public_only", DM, True),
        ("public_only", PUBLIC, False),
        ("always", DM, False),
    ],
)
def test_neutral_subject_and_no_text_when_the_policy_strips_it(policy_, payload, withheld):
    subject, html = render_email(payload, "en", policy_)
    assert (payload["text"] in html) is not withheld
    assert (subject == "New message") is withheld
    if withheld:
        assert "Léa" not in subject and "Bob" not in subject


@pytest.mark.asyncio
async def test_real_send_reaches_every_recipient_with_the_inline_logo(smtp):
    result = await EmailTransport().send(
        _channel(allow_private_network=False, language="en"),
        None,
        _delivery({"event": "channel_found", "name": "#meshcore"}),
        {"password": "smtp-pass-1"},
    )
    assert result.ok
    # Apprise's mailto plugin sends one message per recipient inside the one URL.
    assert sorted(r for _, to, _ in smtp for r in to) == [
        "a@example.org",
        "b@example.org",
        "c@example.org",
    ]
    raw = smtp[0][2]
    message = email.message_from_string(raw, policy=policy.default)
    assert message.get_content_type() == "multipart/related"
    assert "smtp-pass-1" not in raw


@pytest.mark.asyncio
async def test_failure_is_final_and_coded(monkeypatch):
    monkeypatch.setattr(email_transport, "send_email_sync", lambda *a: False)
    result = await EmailTransport().send(_channel(), None, _delivery(DM), {})
    assert (result.status, result.error_code) == ("failed", "send_failed")

    def boom(*a):
        raise RuntimeError("smtp://user:smtp-pass-1@host exploded")

    monkeypatch.setattr(email_transport, "send_email_sync", boom)
    result = await EmailTransport().send(
        _channel(), None, _delivery(DM), {"password": "smtp-pass-1"}
    )
    assert (result.status, result.error_code) == ("failed", "send_failed")


@pytest.mark.asyncio
async def test_not_configured_refused_host_and_tls_policy(smtp):
    send = EmailTransport().send
    assert (await send(_channel(config={}), None, _delivery(DM), {})).error_code == "not_configured"
    assert (
        await send(_channel(config={**CONFIG, "to": "nonsense"}), None, _delivery(DM), {})
    ).error_code == "not_configured"
    private = await send(
        _channel(config={**CONFIG, "host": "192.168.1.5"}), None, _delivery(DM), {}
    )
    assert private.error_code == "egress_private_address"
    insecure = await send(_channel(verify_tls=False), None, _delivery(DM), {})
    assert insecure.error_code == "verify_tls_requires_private_network"
    lan = await send(
        _channel(config={**CONFIG, "host": "192.168.1.5"}, allow_private_network=True),
        None,
        _delivery(DM),
        {},
    )
    assert lan.ok
    assert len(smtp) == 3


@pytest.mark.parametrize(
    ("host", "cloud"),
    [
        ("smtp.gmail.com", True),
        ("93.184.216.34", True),
        ("192.168.1.5", False),
        ("127.0.0.1", False),
        ("mail", False),
        ("mail.home.arpa", False),
        ("nas.local", False),
        ("", False),
    ],
)
def test_email_is_cloud_when_the_smtp_host_is_not_private(host, cloud):
    assert email_is_cloud({"host": host}) is cloud
