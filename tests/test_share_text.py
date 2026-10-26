"""Text-only share summary for push and e-mail (MSHL-250)."""

import json
import socket

import httpx
import pytest

from app.notifications.render import (
    build_push_payload,
    event_notification_text,
    render_html_email,
    render_plain_email,
)
from app.services.share import SHARE_HOSTS
from app.share_text import DEFAULT_SHARE_HOSTS, share_text_summary

LINK = "https://meshl.ink/AbCdEfG"
KEY = "0123456789abcdef0123456789abcdef"


def test_default_hosts_match_the_share_service():
    assert DEFAULT_SHARE_HOSTS == SHARE_HOSTS


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        (f"📷 {LINK}", "en", "[image]"),
        (f"📷 {LINK}", "fr", "[image]"),
        (f"🔒 {LINK}#{KEY}", "en", "[encrypted file]"),
        (f"🔒 {LINK}#{KEY}", "fr", "[fichier chiffré]"),
        (f"📷 {LINK} sunset at the lake", "en", "[image] sunset at the lake"),
        (f"@[Léa] 📷 {LINK}", "en", "[image]"),
        (LINK, "en", "[link]"),
        (LINK, "fr", "[lien]"),
        (f"{LINK}/ ", "en", "[link]"),
        ("https://MESHL.INK/l/AbCdEfG read this", "en", "[link] read this"),
        (f"{LINK}#frag read this", "en", "[link] read this"),
        (f"{LINK} see https://evil.example/x?a=1#k now", "en", "[link] see now"),
        (f"📷   {LINK}", "de", "[image]"),
    ],
)
def test_summaries(text, lang, expected):
    assert share_text_summary(text, lang) == expected


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "",
        "   ",
        "see https://meshl.ink/AbCdEfG here",  # not the composer form: left as typed
        "https://example.org/AbCdEfG",  # not a service host
        "http://meshl.ink/AbCdEfG",
        "https://meshl.ink:8443/AbCdEfG",
        "https://user@meshl.ink/AbCdEfG",
        "https://meshl.ink/AbCdEfG?x=1",
        "https://meshl.ink/AbCdEf",  # 6 characters
        "https://meshl.ink/AbCdEfG/decrypt",
        "https://meshl.ink.evil.example/AbCdEfG",
        "📷 https://example.org/AbCdEfG",
        "📷",
        "g:APqEbxBsVIkWSuFpth",
    ],
)
def test_other_texts_are_not_summarised(text):
    assert share_text_summary(text, "en") is None


def test_never_a_url_or_a_fragment():
    for text in (f"🔒 {LINK}#{KEY}", f"📷 {LINK}#{KEY} cap https://x.example/#{KEY}", LINK):
        out = share_text_summary(text, "en")
        assert out is not None
        assert "http" not in out and "#" not in out and KEY not in out and "meshl" not in out


def _message(text, **extra):
    return {
        "type": "CHAN",
        "conversation_key": "AA" * 16,
        "channel_name": "#paris",
        "sender_name": "Léa",
        "text": text,
        **extra,
    }


@pytest.fixture
def no_egress(monkeypatch):
    calls = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("network call on the notification path")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    yield calls
    assert calls == []


@pytest.mark.parametrize("lang", ["en", "fr"])
@pytest.mark.parametrize("text", [f"📷 {LINK}", f"🔒 {LINK}#{KEY}", f"{LINK} caption"])
def test_push_and_email_carry_the_summary_and_no_egress(no_egress, text, lang):
    data = _message(text)
    summary = share_text_summary(text, lang)
    assert summary

    pushed = json.loads(build_push_payload(data, lang))
    assert pushed["body"] == summary

    _, body = event_notification_text(data, lang)
    assert body == summary

    plain = render_plain_email(data, lang)
    html = render_html_email(data, lang)
    for rendered in (json.dumps(pushed), plain, html):
        assert "meshl.ink" not in rendered
        assert KEY not in rendered
        assert "http" not in rendered.replace("https://www.w3.org", "")
    assert summary in plain


def test_a_plain_message_is_unchanged():
    pushed = json.loads(build_push_payload(_message("hello https://example.org/x"), "en"))
    assert pushed["body"] == "hello https://example.org/x"
