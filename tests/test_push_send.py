"""Tests for Web Push delivery transport behavior."""

import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from app.push.send import (
    DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS,
    PinnedHTTPAdapter,
    send_push,
)
from app.push.vapid import get_vapid_claims, set_cached_vapid_subject

V6 = "2606:4700:4700::1111"
V4 = "93.184.215.14"


@pytest.fixture
def dual_stack_dns(monkeypatch):
    """push.example.test resolves to one public IPv6 and one public IPv4 address."""
    real = socket.getaddrinfo

    def fake(host, port, family=0, type=0, proto=0, flags=0):
        if host != "push.example.test":
            return real(host, port, family, type, proto, flags)
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (V6, port, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (V4, port)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _pinned(session: requests.Session) -> tuple[str, ...]:
    adapter = session.adapters["https://"]
    assert isinstance(adapter, PinnedHTTPAdapter)
    return adapter.pinned_addresses


@pytest.mark.asyncio
async def test_send_push_uses_every_validated_address_first(dual_stack_dns):
    """Successful sends go through the pinned session on every validated address."""
    captured_kwargs: dict = {}

    def fake_webpush(**kwargs):
        captured_kwargs.update(kwargs)
        return SimpleNamespace(status_code=201)

    with patch("app.push.send.webpush", side_effect=fake_webpush):
        status = await send_push(
            subscription_info={"endpoint": "https://push.example.test", "keys": {}},
            payload='{"message":"hello"}',
            vapid_private_key="private-key",
            vapid_claims={"sub": "mailto:test@example.com"},
        )

    assert status == 201
    session = captured_kwargs["requests_session"]
    assert _pinned(session) == (V6, V4)
    assert session.trust_env is False
    assert session.max_redirects == 0
    assert captured_kwargs["timeout"] == (
        DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
        DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    )


@pytest.mark.asyncio
async def test_send_push_retries_with_ipv4_addresses_after_connect_timeout(dual_stack_dns):
    """Connect failures retry on the validated IPv4 addresses only."""
    calls: list[dict] = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise requests.exceptions.ConnectTimeout("ipv6 connect timed out")
        return SimpleNamespace(status_code=201)

    with patch("app.push.send.webpush", side_effect=fake_webpush):
        status = await send_push(
            subscription_info={"endpoint": "https://push.example.test", "keys": {}},
            payload='{"message":"hello"}',
            vapid_private_key="private-key",
            vapid_claims={"sub": "mailto:test@example.com"},
        )

    assert status == 201
    assert len(calls) == 2
    assert _pinned(calls[0]["requests_session"]) == (V6, V4)
    assert _pinned(calls[1]["requests_session"]) == (V4,)
    assert calls[0]["timeout"] == (
        DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
        DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    )
    assert calls[1]["timeout"] == (
        IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS,
        DEFAULT_PUSH_READ_TIMEOUT_SECONDS,
    )


@pytest.mark.asyncio
async def test_send_push_without_ipv4_does_not_retry():
    calls: list[dict] = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        raise requests.exceptions.ConnectTimeout("timed out")

    with (
        patch("app.push.send.webpush", side_effect=fake_webpush),
        pytest.raises(requests.exceptions.ConnectTimeout),
    ):
        await send_push(
            subscription_info={"endpoint": f"https://[{V6}]/push", "keys": {}},
            payload="{}",
            vapid_private_key="private-key",
            vapid_claims={"sub": "mailto:test@example.com"},
        )
    assert len(calls) == 1


@pytest.fixture(autouse=True)
def _reset_vapid_subject_cache():
    set_cached_vapid_subject("")
    yield
    set_cached_vapid_subject("")


def test_get_vapid_claims_defaults_to_meshcore_local():
    """Default subject is unchanged so existing deployments behave identically."""
    assert get_vapid_claims() == {"sub": "mailto:noreply@meshcore.local"}


def test_get_vapid_claims_ignores_the_retired_env_variable(monkeypatch):
    """MESHCORE_VAPID_SUBJECT is imported once at startup, never read here."""
    monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "mailto:env@example.net")
    assert get_vapid_claims() == {"sub": "mailto:noreply@meshcore.local"}


def test_get_vapid_claims_uses_cached_db_subject_then_default():
    set_cached_vapid_subject("mailto:db@example.com")
    assert get_vapid_claims() == {"sub": "mailto:db@example.com"}
    set_cached_vapid_subject("")
    assert get_vapid_claims() == {"sub": "mailto:noreply@meshcore.local"}


def test_get_vapid_claims_normalizes_https_trailing_slash(monkeypatch):
    """py-vapid rejects https://host/ — strip the path so instance URLs work."""
    from py_vapid.__init__ import _check_sub

    set_cached_vapid_subject("https://radio.meshloom.app/")
    claims = get_vapid_claims()
    assert claims == {"sub": "https://radio.meshloom.app"}
    assert _check_sub(claims["sub"]) is True


def test_get_vapid_claims_https_origin_without_slash_is_unchanged(monkeypatch):
    from py_vapid.__init__ import _check_sub

    set_cached_vapid_subject("https://radio.meshloom.app")
    claims = get_vapid_claims()
    assert claims == {"sub": "https://radio.meshloom.app"}
    assert _check_sub(claims["sub"]) is True


def test_get_vapid_claims_skips_unusable_cache(monkeypatch):
    set_cached_vapid_subject("https://")
    assert get_vapid_claims() == {"sub": "mailto:noreply@meshcore.local"}


@pytest.mark.asyncio
async def test_ensure_vapid_keys_seeds_subject_cache_from_db(test_db, monkeypatch):
    from app.push.vapid import ensure_vapid_keys
    from app.repository.settings import AppSettingsRepository

    await AppSettingsRepository.set_vapid_subject("mailto:db@example.com")
    await ensure_vapid_keys()
    assert get_vapid_claims() == {"sub": "mailto:db@example.com"}


def test_build_payload_defaults_to_french_titles():
    from app.push.manager import _build_payload

    dm = json.loads(
        _build_payload(
            {
                "type": "PRIV",
                "text": "hello",
                "sender_name": "Alice",
                "conversation_key": "aa" * 32,
            }
        )
    )
    assert dm["title"] == "Message de Alice"

    unnamed = json.loads(
        _build_payload({"type": "PRIV", "text": "hello", "conversation_key": "bb"})
    )
    assert unnamed["title"] == "Nouveau message privé"

    channel = json.loads(
        _build_payload({"type": "CHAN", "text": "hi", "conversation_key": "cc", "channel_name": ""})
    )
    assert channel["title"] == "Message de canal"


def test_build_payload_english_titles():
    from app.push.manager import _build_payload

    dm = json.loads(
        _build_payload(
            {
                "type": "PRIV",
                "text": "hello",
                "sender_name": "Alice",
                "conversation_key": "aa" * 32,
            },
            language="en",
        )
    )
    assert dm["title"] == "Message from Alice"

    unnamed = json.loads(
        _build_payload({"type": "PRIV", "text": "hello", "conversation_key": "bb"}, language="en")
    )
    assert unnamed["title"] == "New direct message"

    channel = json.loads(
        _build_payload(
            {"type": "CHAN", "text": "hi", "conversation_key": "cc", "channel_name": ""},
            language="en",
        )
    )
    assert channel["title"] == "Channel message"


@pytest.mark.asyncio
async def test_push_subscription_persists_language(test_db):
    from app.repository.push_subscriptions import PushSubscriptionRepository

    created = await PushSubscriptionRepository.create(
        endpoint="https://push.example.test/en",
        p256dh="p256",
        auth="auth",
        label="Chrome",
        language="en",
    )
    assert created["language"] == "en"

    defaulted = await PushSubscriptionRepository.create(
        endpoint="https://push.example.test/fr",
        p256dh="p256",
        auth="auth",
    )
    assert defaulted["language"] == "fr"

    updated = await PushSubscriptionRepository.update(defaulted["id"], language="en")
    assert updated is not None
    assert updated["language"] == "en"


def test_build_payload_channel_found_french_and_english():
    from app.push.manager import _build_payload

    fr = json.loads(
        _build_payload(
            {
                "event": "channel_found",
                "name": "#fr",
                "channel_key": "aa" * 16,
            }
        )
    )
    assert fr["title"] == "Nouveau canal détecté : #fr"
    assert fr["tag"] == "meshcore-channel-found-#fr"
    assert fr["url_hash"] == "#channel/" + "aa" * 16

    en = json.loads(
        _build_payload(
            {
                "event": "channel_found",
                "name": "#fr",
                "channel_key": "bb" * 16,
            },
            language="en",
        )
    )
    assert en["title"] == "New channel detected: #fr"
    assert en["url_hash"] == "#channel/" + "bb" * 16


def test_build_payload_first_seen_french_and_english():
    from app.push.manager import _build_payload

    fr = json.loads(
        _build_payload(
            {
                "event": "first_seen",
                "public_key": "aa" * 32,
                "name": "Alice",
                "contact_type": 1,
            }
        )
    )
    assert fr["title"] == "Nouveau contact : Alice"
    assert fr["tag"] == "meshcore-first-seen-" + "aa" * 32
    assert fr["url_hash"] == "#contact/" + "aa" * 32

    en = json.loads(
        _build_payload(
            {
                "event": "first_seen",
                "public_key": "bb" * 32,
                "name": "Hill",
                "contact_type": 2,
            },
            language="en",
        )
    )
    assert en["title"] == "New repeater: Hill"
    assert en["tag"] == "meshcore-first-seen-" + "bb" * 32


def test_build_payload_telemetry_alert_french_and_english():
    from app.push.manager import _build_payload

    fr = json.loads(
        _build_payload(
            {
                "event": "telemetry_alert",
                "public_key": "aa" * 32,
                "name": "Hill",
                "rule_id": "battery",
                "value": 3.2,
                "threshold": 3.5,
            }
        )
    )
    assert fr["title"] == "Batterie faible : Hill"
    assert fr["tag"] == "meshcore-telemetry-battery-" + "aa" * 32
    assert fr["url_hash"] == "#contact/" + "aa" * 32

    en = json.loads(
        _build_payload(
            {
                "event": "telemetry_alert",
                "public_key": "bb" * 32,
                "name": "Hill",
                "rule_id": "silence",
                "value": 2,
                "threshold": 2,
            },
            language="en",
        )
    )
    assert en["title"] == "Hill is not responding"
    assert en["tag"] == "meshcore-telemetry-silence-" + "bb" * 32

    unknown = json.loads(
        _build_payload(
            {
                "event": "telemetry_alert",
                "public_key": "cc" * 32,
                "name": "Hill",
                "rule_id": "custom_gauge",
                "value": 9,
                "threshold": 4,
            },
            language="en",
        )
    )
    assert unknown["title"] == "Hill: custom_gauge alert"
    assert "is not responding" not in unknown["title"]
    assert "is not responding" not in unknown["body"]


# ── dispatch_message / first-seen ────────────────────────────────────────


async def _add_push_sub(*, language: str = "fr") -> dict:
    from app.repository.push_subscriptions import PushSubscriptionRepository

    return await PushSubscriptionRepository.create(
        endpoint=f"https://push.example.test/{language}",
        p256dh="p256",
        auth="auth",
        language=language,
    )


def _patch_push_send(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return 201

    monkeypatch.setattr("app.push.manager.get_vapid_private_key", lambda: "test-vapid-key")
    monkeypatch.setattr("app.push.manager.send_push", fake_send)
    return sent


@pytest.mark.asyncio
async def test_dispatch_message_sends_for_default_dm(test_db, monkeypatch):
    from app.notifications.producers import emit_message

    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await emit_message(
        {
            "type": "PRIV",
            "outgoing": False,
            "conversation_key": "aa" * 32,
            "text": "hello",
            "sender_name": "Alice",
        }
    )
    assert len(sent) == 1
    payload = json.loads(sent[0]["payload"])
    assert payload["title"] == "Message de Alice"


@pytest.mark.asyncio
async def test_dispatch_message_override_false_skips_dm(test_db, monkeypatch):
    from app.notifications.producers import emit_message
    from app.repository.settings import AppSettingsRepository

    key = "aa" * 32
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await AppSettingsRepository.set_push_conversation_override(f"contact-{key}", False)
    await emit_message(
        {
            "type": "PRIV",
            "outgoing": False,
            "conversation_key": key,
            "text": "hello",
            "sender_name": "Alice",
        }
    )
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_message_muted_channel_skips_even_with_override(test_db, monkeypatch):
    from app.channel_constants import PUBLIC_CHANNEL_KEY
    from app.notifications.producers import emit_message
    from app.repository import ChannelRepository
    from app.repository.settings import AppSettingsRepository

    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await ChannelRepository.upsert(key=PUBLIC_CHANNEL_KEY, name="Public", is_hashtag=False)
    await ChannelRepository.set_muted(PUBLIC_CHANNEL_KEY, True)
    await AppSettingsRepository.set_push_conversation_override(
        f"channel-{PUBLIC_CHANNEL_KEY}", True
    )
    await emit_message(
        {
            "type": "CHAN",
            "outgoing": False,
            "conversation_key": PUBLIC_CHANNEL_KEY,
            "text": "hi",
            "channel_name": "Public",
        }
    )
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_message_public_without_override_sends(test_db, monkeypatch):
    from app.channel_constants import PUBLIC_CHANNEL_KEY
    from app.notifications.producers import emit_message

    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await emit_message(
        {
            "type": "CHAN",
            "outgoing": False,
            "conversation_key": PUBLIC_CHANNEL_KEY,
            "text": "hi",
            "channel_name": "Public",
        }
    )
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_dispatch_message_hashtag_without_override_sends(test_db, monkeypatch):
    from app.notifications.producers import emit_message
    from app.repository import ChannelRepository

    hashtag_key = "cc" * 16
    await ChannelRepository.upsert(key=hashtag_key, name="#ops", is_hashtag=True)
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await emit_message(
        {
            "type": "CHAN",
            "outgoing": False,
            "conversation_key": hashtag_key,
            "text": "hi",
            "channel_name": "#ops",
        }
    )
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_dispatch_message_private_channel_without_override_skips(test_db, monkeypatch):
    from app.notifications.producers import emit_message
    from app.repository import ChannelRepository

    private_key = "dd" * 16
    await ChannelRepository.upsert(key=private_key, name="secret", is_hashtag=False)
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await emit_message(
        {
            "type": "CHAN",
            "outgoing": False,
            "conversation_key": private_key,
            "text": "hi",
            "channel_name": "secret",
        }
    )
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_message_override_does_not_email_when_matrix_off(test_db, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models import NotificationDestinations, NotificationEmailDest
    from app.notifications.producers import emit_message
    from app.repository.settings import AppSettingsRepository

    key = "aa" * 32
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    email = AsyncMock()
    monkeypatch.setattr("app.notify.send_email_alert", email)
    # Only the message push is counted, not the destination-changed notice.
    monkeypatch.setattr("app.notify.notify_destination_changed", lambda *_: None)
    await AppSettingsRepository.update(
        notification_destinations=NotificationDestinations(
            email=NotificationEmailDest(host="smtp.example", to="ops@example.com")
        )
    )
    await AppSettingsRepository.set_push_conversation_override(f"contact-{key}", True)
    await emit_message(
        {
            "type": "PRIV",
            "outgoing": False,
            "conversation_key": key,
            "text": "hello",
            "sender_name": "Alice",
        }
    )
    assert len(sent) == 1
    email.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_message_emails_dm_only_when_matrix_enables_email(test_db, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models import NotificationDestinations, NotificationEmailDest
    from app.notifications.producers import emit_message
    from app.repository.settings import AppSettingsRepository

    key = "aa" * 32
    email = AsyncMock()
    monkeypatch.setattr("app.notify.send_email_alert", email)
    await AppSettingsRepository.update(
        notification_destinations=NotificationDestinations(
            email=NotificationEmailDest(host="smtp.example", to="ops@example.com")
        )
    )
    await AppSettingsRepository.set_push_defaults({"new_dm": {"email": True}})
    await emit_message(
        {
            "type": "PRIV",
            "outgoing": False,
            "conversation_key": key,
            "text": "hello",
            "sender_name": "Alice",
        }
    )
    email.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_seen_type1_notifies_if_either_toggle(test_db, monkeypatch):
    from app.models import Contact
    from app.notifications.dispatcher import notification_dispatcher
    from app.push.first_seen import maybe_notify_contact_first_seen
    from app.radio import radio_manager
    from app.repository.settings import AppSettingsRepository

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    contact = Contact(public_key="aa" * 32, name="Alice", type=1)

    await AppSettingsRepository.set_push_defaults(
        {
            "new_contact": True,
            "advert_companion": False,
            "advert_repeater": False,
            "advert_sensor": False,
        }
    )
    await maybe_notify_contact_first_seen(contact, origin="rf_advert")
    assert len(sent) == 1

    sent.clear()
    notification_dispatcher()._dedup._seen.clear()  # the same contact again: a new test case
    await AppSettingsRepository.set_push_defaults(
        {
            "new_contact": False,
            "advert_companion": True,
            "advert_repeater": False,
            "advert_sensor": False,
        }
    )
    await maybe_notify_contact_first_seen(contact, origin="rf_advert")
    assert len(sent) == 1

    sent.clear()
    notification_dispatcher()._dedup._seen.clear()  # the same contact again: a new test case
    await AppSettingsRepository.set_push_defaults(
        {
            "new_contact": False,
            "advert_companion": False,
            "advert_repeater": True,
            "advert_sensor": True,
        }
    )
    await maybe_notify_contact_first_seen(contact, origin="rf_advert")
    assert sent == []


@pytest.mark.asyncio
async def test_first_seen_type4_notifies_if_advert_sensor(test_db, monkeypatch):
    from app.models import Contact
    from app.push.first_seen import maybe_notify_contact_first_seen
    from app.radio import radio_manager
    from app.repository.settings import AppSettingsRepository

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    contact = Contact(public_key="aa" * 32, name="Sensor", type=4)

    await AppSettingsRepository.set_push_defaults(
        {
            "new_contact": False,
            "advert_companion": False,
            "advert_repeater": False,
            "advert_sensor": True,
        }
    )
    await maybe_notify_contact_first_seen(contact, origin="rf_advert")
    assert len(sent) == 1

    sent.clear()
    await AppSettingsRepository.set_push_defaults(
        {
            "new_contact": False,
            "advert_companion": False,
            "advert_repeater": False,
            "advert_sensor": False,
        }
    )
    await maybe_notify_contact_first_seen(contact, origin="rf_advert")
    assert sent == []


@pytest.mark.asyncio
async def test_first_seen_type_0_and_3_skip(test_db, monkeypatch):
    from app.models import Contact
    from app.push.first_seen import maybe_notify_contact_first_seen
    from app.radio import radio_manager

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)

    await maybe_notify_contact_first_seen(
        Contact(public_key="aa" * 32, name="Unknown", type=0),
        origin="rf_advert",
    )
    await maybe_notify_contact_first_seen(
        Contact(public_key="bb" * 32, name="Room", type=3),
        origin="radio_event",
    )
    assert sent == []


@pytest.mark.asyncio
async def test_first_seen_existing_prefix_skips(test_db, monkeypatch):
    from app.models import Contact
    from app.push.first_seen import maybe_notify_contact_first_seen
    from app.radio import radio_manager
    from app.repository import ContactRepository

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    prefix = "aabbccddeeff"
    full_key = prefix + "11" * 26
    await ContactRepository.upsert({"public_key": prefix, "name": "Alice", "type": 0})
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)

    await maybe_notify_contact_first_seen(
        Contact(public_key=full_key, name="Alice", type=1),
        origin="rf_advert",
    )
    assert sent == []

    await maybe_notify_contact_first_seen(
        Contact(public_key="cc" * 32, name="Bob", type=1),
        origin="rf_advert",
        promoted_keys=[prefix],
    )
    assert sent == []


@pytest.mark.asyncio
async def test_first_seen_incomplete_setup_skips(test_db, monkeypatch):
    from app.models import Contact
    from app.push.first_seen import maybe_notify_contact_first_seen
    from app.radio import radio_manager

    monkeypatch.setattr(radio_manager, "_setup_complete", False)
    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await maybe_notify_contact_first_seen(
        Contact(public_key="aa" * 32, name="Alice", type=1),
        origin="rf_advert",
    )
    assert sent == []


@pytest.mark.asyncio
async def test_on_new_contact_insert_notifies_radio_event(test_db, monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.event_handlers import on_new_contact
    from app.radio import radio_manager

    monkeypatch.setattr(radio_manager, "_setup_complete", True)

    class MockEvent:
        payload = {
            "public_key": "ee" * 32,
            "adv_name": "Eve",
            "type": 1,
            "flags": 0,
        }

    with (
        patch("app.event_handlers.broadcast_event"),
        patch(
            "app.push.first_seen.maybe_notify_contact_first_seen",
            new_callable=AsyncMock,
        ) as notify,
    ):
        await on_new_contact(MockEvent())

    notify.assert_awaited_once()
    assert notify.await_args.kwargs["origin"] == "radio_event"


@pytest.mark.asyncio
async def test_on_new_contact_existing_does_not_notify(test_db, monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.event_handlers import on_new_contact
    from app.radio import radio_manager
    from app.repository import ContactRepository

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    await ContactRepository.upsert({"public_key": "ee" * 32, "name": "Eve", "type": 1})

    class MockEvent:
        payload = {
            "public_key": "ee" * 32,
            "adv_name": "Eve",
            "type": 1,
            "flags": 0,
        }

    with (
        patch("app.event_handlers.broadcast_event"),
        patch(
            "app.push.first_seen.maybe_notify_contact_first_seen",
            new_callable=AsyncMock,
        ) as notify,
    ):
        await on_new_contact(MockEvent())

    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_contact_insert_notifies_manual(test_db, client, monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.radio import radio_manager

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    with (
        patch("app.websocket.broadcast_event"),
        patch(
            "app.push.first_seen.maybe_notify_contact_first_seen",
            new_callable=AsyncMock,
        ) as notify,
    ):
        response = await client.post(
            "/api/contacts",
            json={"public_key": "ff" * 32, "name": "Fay", "type": 1},
        )

    assert response.status_code == 200
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["origin"] == "manual"


@pytest.mark.asyncio
async def test_process_advertisement_insert_notifies_rf_advert(test_db, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.decoder import ParsedAdvertisement
    from app.packet_processor import _process_advertisement
    from app.radio import radio_manager

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    pubkey = "ab" * 32
    packet_info = MagicMock()
    packet_info.path_length = 0
    packet_info.path = b""
    packet_info.payload = b""

    with (
        patch("app.packet_processor.broadcast_event"),
        patch("app.packet_processor.parse_advertisement") as mock_parse,
        patch("app.packet_processor.verify_advert_signature", return_value=True),
        patch(
            "app.push.first_seen.maybe_notify_contact_first_seen",
            new_callable=AsyncMock,
        ) as notify,
    ):
        mock_parse.return_value = ParsedAdvertisement(
            public_key=pubkey,
            name="Ada",
            timestamp=1000,
            lat=None,
            lon=None,
            device_role=1,
        )
        await _process_advertisement(b"", timestamp=1000, packet_info=packet_info)

    notify.assert_awaited_once()
    assert notify.await_args.kwargs["origin"] == "rf_advert"


def test_build_payload_oss_update_french_and_english():
    from app.push.manager import _build_payload

    fr = json.loads(
        _build_payload(
            {
                "event": "oss_update",
                "current": "1.0.0",
                "latest": "9.9.9",
            }
        )
    )
    assert fr["title"] == "Mise à jour Meshloom disponible"
    assert "9.9.9" in fr["body"]
    assert "1.0.0" in fr["body"]
    assert fr["tag"] == "meshcore-oss-update-9.9.9"
    assert fr["url_hash"] == "#settings/updates"

    en = json.loads(
        _build_payload(
            {
                "event": "oss_update",
                "current": "1.0.0",
                "latest": "9.9.9",
            },
            language="en",
        )
    )
    assert en["title"] == "Meshloom update available"
    assert "9.9.9" in en["body"]
    assert en["url_hash"] == "#settings/updates"


@pytest.mark.asyncio
async def test_dispatch_oss_update_sends_once_per_latest(test_db, monkeypatch):
    from app.repository.settings import AppSettingsRepository
    from app.services.oss_updates import _maybe_notify_oss_update, reset_oss_update_cache
    from app.version_info import AppBuildInfo

    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    reset_oss_update_cache()
    import app.services.oss_updates as oss_updates

    oss_updates._latest_payload = {"version": "9.9.9", "html_url": None}
    with patch(
        "app.services.oss_updates.get_app_build_info",
        return_value=AppBuildInfo(
            version="1.0.0", version_source="test", commit_hash=None, commit_source=None
        ),
    ):
        await _maybe_notify_oss_update()
        await _maybe_notify_oss_update()

    assert len(sent) == 1
    payload = json.loads(sent[0]["payload"])
    assert payload["url_hash"] == "#settings/updates"
    assert await AppSettingsRepository.get_last_notified_update_version() == "9.9.9"


@pytest.mark.asyncio
async def test_dispatch_oss_update_skips_if_already_notified(test_db, monkeypatch):
    from app.repository.settings import AppSettingsRepository
    from app.services.oss_updates import _maybe_notify_oss_update, reset_oss_update_cache
    from app.version_info import AppBuildInfo

    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await AppSettingsRepository.set_last_notified_update_version("9.9.9")
    reset_oss_update_cache()
    import app.services.oss_updates as oss_updates

    oss_updates._latest_payload = {"version": "9.9.9", "html_url": None}
    with patch(
        "app.services.oss_updates.get_app_build_info",
        return_value=AppBuildInfo(
            version="1.0.0", version_source="test", commit_hash=None, commit_source=None
        ),
    ):
        await _maybe_notify_oss_update()
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_oss_update_skips_when_default_off(test_db, monkeypatch):
    from app.notifications.producers import emit_oss_update
    from app.repository.settings import AppSettingsRepository

    await _add_push_sub()
    sent = _patch_push_send(monkeypatch)
    await AppSettingsRepository.set_push_defaults({"oss_update": False})
    sent_flag = await emit_oss_update("1.0.0", "9.9.9")
    assert sent_flag is False
    assert sent == []


@pytest.mark.asyncio
async def test_dispatch_keeps_a_subscription_the_guard_refuses(test_db, monkeypatch):
    from app.push.manager import push_manager
    from app.repository.push_subscriptions import PushSubscriptionRepository
    from app.repository.settings import AppSettingsRepository

    sub = await PushSubscriptionRepository.create(
        endpoint="http://192.168.1.10/up", p256dh="p256", auth="auth"
    )
    monkeypatch.setattr("app.push.manager.get_vapid_private_key", lambda: "test-vapid-key")
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.push.send.webpush", lambda **kw: sent.append(kw) or SimpleNamespace(status_code=201)
    )
    await push_manager.dispatch_event({"event": "oss_update", "title": "t", "body": "b"})
    assert sent == []
    stored = await PushSubscriptionRepository.get(sub["id"])
    assert stored is not None
    assert stored["failure_count"] == 0  # a guard refusal is not a delivery failure

    await AppSettingsRepository.update(push_allow_private_endpoints=True)
    await push_manager.dispatch_event({"event": "oss_update", "title": "t", "body": "b"})
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_guard_refused_sends_never_evict_a_subscription(test_db, monkeypatch):
    """A Tailscale/split-DNS name resolving to a private address is refused while the
    opt-in is off. That is a configuration state: after far more refusals than
    MAX_CONSECUTIVE_FAILURES the subscription is still there, with no failure counted."""
    from app.push.manager import push_manager
    from app.repository.push_subscriptions import (
        MAX_CONSECUTIVE_FAILURES,
        PushSubscriptionRepository,
    )

    real = socket.getaddrinfo

    def fake(host, port, family=0, type=0, proto=0, flags=0):
        if host != "ntfy.tail1234.ts.net":
            return real(host, port, family, type, proto, flags)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.101.102.103", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    sub = await PushSubscriptionRepository.create("https://ntfy.tail1234.ts.net/up", "k", "a")
    row = (await PushSubscriptionRepository.get_all())[0]
    sub_id = row["id"]
    assert sub_id

    with patch("app.push.send.webpush") as sent:
        for _ in range(MAX_CONSECUTIVE_FAILURES + 5):
            result = await push_manager._send_one(row, "{}", "private-key", allow_private=False)
            assert result.refused and not result.success and not result.expired
            await push_manager._record_outcomes([result])
        sent.assert_not_called()

    rows = await PushSubscriptionRepository.get_all()
    assert [r["id"] for r in rows] == [sub_id]
    assert rows[0]["failure_count"] == 0
    del sub

    # Ticking the box makes the same subscription deliver.
    with patch("app.push.send.webpush", return_value=SimpleNamespace(status_code=201)):
        result = await push_manager._send_one(row, "{}", "private-key", allow_private=True)
    assert result.success and not result.refused
