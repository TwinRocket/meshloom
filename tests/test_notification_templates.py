"""Per-channel message templates (MSHL-127): the renderer, the validation on save, the
preview route, the Apprise transport and the backup round trip."""

import ipaddress
import socket
from types import SimpleNamespace

import pytest

from app.notifications.render import event_notification_text
from app.notifications.templates import MAX_TEMPLATE_LENGTH, SAMPLE_CHANNEL, SAMPLE_DM
from app.notifications.transports.apprise import AppriseTransport

URL = "tgram://111111:AAAA-token-a/1001"


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


def dm(**extra):
    return {**SAMPLE_DM, **extra}


def chan(**extra):
    return {**SAMPLE_CHANNEL, "conversation_key": "cd" * 16, **extra}


def body_of(payload, **kwargs):
    return event_notification_text(payload, "en", **kwargs)[1]


# ── Renderer ────────────────────────────────────────────────────────────


def test_empty_templates_keep_the_default_body():
    default = event_notification_text(dm(), "en")
    assert event_notification_text(dm(), "en", template_dm="", template_channel="x") == default
    assert event_notification_text(dm(), "en", template_dm="  \n ") == default


def test_custom_template_uses_the_fanout_variables_and_title():
    template = (
        "{sender_name}|{text}|{hops}|{hops_backticked}|{hop_count}|{rssi}|{snr}|{type}|{title}"
    )
    body = body_of(dm(), template_dm=template)
    title = event_notification_text(dm(), "en")[0]
    assert body == f"Alice|See you at the summit|a1, b2, c3|`a1`, `b2`, `c3`|3|-87|6.5|PRIV|{title}"


def test_the_dm_and_the_channel_template_apply_to_their_own_kind():
    kwargs = {"template_dm": "DM {sender_name}", "template_channel": "{channel_name} {text}"}
    assert body_of(dm(), **kwargs) == "DM Alice"
    # The "Bob: " prefix of a channel text is the sender label, not part of {text}.
    assert body_of(chan(), **kwargs) == "#hiking Anyone on the ridge?"


def test_an_unknown_variable_is_left_as_written():
    assert (
        body_of(dm(), template_dm="{nope} {text} {Text}") == "{nope} See you at the summit {Text}"
    )


def test_a_substituted_value_is_not_expanded_again():
    payload = dm(text="{sender_name} {text}", sender_name="{text}")
    assert body_of(payload, template_dm="{text}/{sender_name}") == "{sender_name} {text}/{text}"


def test_the_text_policy_empties_text_but_keeps_the_rest():
    template = "{sender_name}: [{text}]"
    assert body_of(dm(), template_dm=template, include_message_text="never") == "Alice: []"
    assert body_of(dm(), template_dm=template, include_message_text="public_only") == "Alice: []"
    assert (
        body_of(
            chan(), template_dm="", template_channel="[{text}]", include_message_text="public_only"
        )
        == "[]"
    )


def test_a_template_that_renders_empty_falls_back_to_the_default_body():
    body = body_of(dm(), template_dm="{text}", include_message_text="never")
    assert body == "Message from Alice"


def test_mentions_are_neutralised_in_values_and_in_the_template():
    body = body_of(dm(text="@everyone <@123>"), template_dm="@channel {text}")
    assert "@channel" not in body and "@everyone" not in body and "<@123>" not in body


def test_markdown_escapes_mesh_text_and_keeps_the_template_markup():
    payload = dm(text="*bold* [x](http://e)", sender_name="a_b")
    body = body_of(
        payload, markdown=True, template_dm="**{sender_name}**: {text} via {hops_backticked}"
    )
    assert body == "**a\\_b**: \\*bold\\* \\[x\\]\\(http://e\\) via `a1`, `b2`, `c3`"


def test_a_template_never_applies_outside_message_events():
    payload = {"event": "first_seen", "name": "Node", "public_key": "ab" * 32, "contact_type": 1}
    assert event_notification_text(payload, "en", template_dm="X", template_channel="X") == (
        event_notification_text(payload, "en")
    )


def test_a_share_link_still_reads_as_its_summary():
    body = body_of(dm(text="📷 https://meshl.ink/AbCdEfG"), template_dm="{sender_name}: {text}")
    assert body == "Alice: [image]"


# ── API ─────────────────────────────────────────────────────────────────


def apprise(**extra):
    return {"type": "apprise", "name": "Apprise", "config": {"urls": URL}, **extra}


async def post(client, payload):
    return await client.post("/api/notifications/channels", json=payload)


async def test_templates_are_stored_returned_updated_and_cleared(test_db, client):
    created = await post(client, apprise(template_dm="{text}", template_channel="{channel_name}"))
    assert created.status_code == 201, created.text
    channel = created.json()
    assert (channel["template_dm"], channel["template_channel"]) == ("{text}", "{channel_name}")
    path = f"/api/notifications/channels/{channel['id']}"
    patched = await client.patch(path, json={"template_dm": "{sender_name}"})
    assert patched.json()["template_dm"] == "{sender_name}"
    assert patched.json()["template_channel"] == "{channel_name}"
    cleared = await client.patch(path, json={"template_channel": ""})
    assert (
        cleared.json()["template_channel"] == ""
        and cleared.json()["template_dm"] == "{sender_name}"
    )
    assert (await client.get(path)).json()["template_dm"] == "{sender_name}"


async def test_a_template_is_not_a_secret(test_db, client):
    channel = (await post(client, apprise(template_dm="{text}"))).json()
    from app.repository.notification_channels import NotificationChannelRepository

    found = await NotificationChannelRepository.get_for_send(channel["id"])
    assert found is not None
    assert "template_dm" not in found[1]
    assert found[0].template_dm == "{text}"
    assert "template_dm" not in found[0].secret_fields_set


async def test_too_long_or_invalid_templates_are_refused(test_db, client):
    too_long = await post(client, apprise(template_dm="x" * (MAX_TEMPLATE_LENGTH + 1)))
    assert too_long.status_code == 400
    assert too_long.json()["detail"]["code"] == "template_too_long"
    assert too_long.json()["detail"]["params"]["field"] == "template_dm"
    assert (
        await post(client, apprise(template_channel="x" * MAX_TEMPLATE_LENGTH))
    ).status_code == 201
    control = await post(client, apprise(template_channel="a\x00b"))
    assert control.json()["detail"]["code"] == "template_invalid"
    channel = (await post(client, apprise())).json()
    path = f"/api/notifications/channels/{channel['id']}"
    refused = await client.patch(path, json={"template_dm": "y" * 600})
    assert refused.json()["detail"]["code"] == "template_too_long"
    assert (await client.get(path)).json()["template_dm"] == ""


async def test_a_type_without_a_text_body_refuses_a_template(test_db, client):
    response = await post(
        client,
        {
            "type": "webhook",
            "name": "Hook",
            "config": {"url": "https://hooks.example.org/x/y"},
            "template_dm": "{text}",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "template_unsupported"


async def test_the_types_say_which_ones_take_templates(test_db, client):
    types = {t["type"]: t for t in (await client.get("/api/notifications/types")).json()}
    assert (
        types["apprise"]["templates"]
        and types["ntfy"]["templates"]
        and types["discord"]["templates"]
    )
    assert types["telegram"]["templates"]
    assert not types["email"]["templates"] and not types["webhook"]["templates"]


async def test_preview_renders_the_samples_with_the_renderer(test_db, client):
    response = await client.post(
        "/api/notifications/channels/preview-template",
        json={
            "template_dm": "{sender_name}: {text} [{hops}] {unknown}",
            "template_channel": "{channel_name} {rssi}",
            "language": "en",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dm"] == "Alice: See you at the summit [a1, b2, c3] {unknown}"
    assert body["channel"] == "#hiking -87"
    assert "title" in body["variables"] and "hops_backticked" in body["variables"]


async def test_preview_without_a_template_shows_the_default_and_respects_policy_and_markdown(
    test_db, client
):
    url = "/api/notifications/channels/preview-template"
    default = (await client.post(url, json={"language": "en"})).json()
    assert default["dm"] == body_of(dm())
    stripped = await client.post(
        url, json={"template_dm": "[{text}]", "include_message_text": "never", "language": "en"}
    )
    assert stripped.json()["dm"] == "[]"
    md = await client.post(
        url,
        json={
            "template_channel": "{sender_name} {channel_name}",
            "markdown": True,
            "language": "en",
        },
    )
    assert md.json()["channel"] == "Bob \\#hiking"


async def test_preview_refuses_a_bad_template(test_db, client):
    url = "/api/notifications/channels/preview-template"
    response = await client.post(url, json={"template_dm": "x" * 501})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "template_too_long"
    assert (await client.post(url, json={"bogus": 1})).status_code == 422


# ── Transport and backup ────────────────────────────────────────────────


async def test_the_apprise_transport_sends_the_templated_body(monkeypatch):
    sent = {}

    async def fake_send(urls, **kwargs):
        sent.update(kwargs)
        return []

    monkeypatch.setattr("app.notifications.transports.apprise.send_to_urls", fake_send)
    channel = SimpleNamespace(
        preset=None,
        config={"format": "text"},
        include_message_text="always",
        language="en",
        allow_private_network=False,
        verify_tls=True,
        template_dm="{sender_name} said {text} ({rssi} dBm)",
        template_channel="",
        id="c1",
    )
    event = SimpleNamespace(payload=dm())
    result = await AppriseTransport().send(channel, None, event, {"urls": URL})
    assert result.status == "ok"
    assert sent["body"] == "Alice said See you at the summit (-87 dBm)"


async def test_backup_round_trips_the_templates(test_db, client):
    from app.services.backup_notifications import export_notifications

    await post(client, apprise(template_dm="{text}", template_channel="{channel_name}: {text}"))
    section = await export_notifications()
    assert section is not None
    exported = [c for c in section.data["channels"] if c["type"] == "apprise"][0]
    assert exported["template_dm"] == "{text}"
    assert exported["template_channel"] == "{channel_name}: {text}"
