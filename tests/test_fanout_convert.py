"""Fanout Apprise rows converted into notification channels (MSHL-134,
``app/notifications/fanout_convert.py``): the scope as rules, the templates, idempotence,
the disabled row, duplicate detection, and URLs that never leave the secret store."""

import ipaddress
import logging
import socket

import pytest

from app.notifications.fanout_convert import match_preset, scope_to_rules
from app.repository.fanout import FanoutConfigRepository
from app.repository.notification_channels import NotificationChannelRepository

TELEGRAM = "tgram://111111:AAAA-token-a/1001"
JSON_URL = "json://hooks.example.org/path/secret-path-7"
SECRETS = ("AAAA-token-a", "secret-path-7", "111111:")
CONVERT = "/api/fanout/{}/convert-to-notification-channel"


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


@pytest.fixture(autouse=True)
def _quiet_manager(monkeypatch):
    async def reload(self, config_id):
        return None

    monkeypatch.setattr("app.fanout.manager.FanoutManager.reload_config", reload)


async def _row(urls=JSON_URL, scope=None, enabled=True, **config):
    return await FanoutConfigRepository.create(
        "apprise",
        "Alerts",
        {"urls": urls, **config},
        scope or {"messages": "all", "raw_packets": "none"},
        enabled=enabled,
    )


def _no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


async def _rules(client):
    return (await client.get("/api/notifications/rules")).json()


async def test_all_scope_creates_a_generic_channel_on_both_events(test_db, client, caplog):
    caplog.set_level(logging.DEBUG)
    row = await _row(markdown_format=False, include_outgoing=True)
    response = await client.post(CONVERT.format(row["id"]))
    assert response.status_code == 200, response.text
    _no_secret(response.text)
    body = response.json()
    channel = body["channel"]
    assert channel["type"] == "apprise" and channel["preset"] is None
    assert channel["config"]["format"] == "text"
    assert channel["include_message_text"] == "always"
    assert body["already_converted"] is False
    assert "include_outgoing_unsupported" in body["notes"]
    assert "private_channels_need_exception" in body["notes"]
    assert body["fanout"]["enabled"] is False
    assert body["fanout"]["config"]["converted_to"] == channel["id"]

    rules = await _rules(client)
    assert channel["id"] in rules["events"]["message.dm"]["channels"]
    assert channel["id"] in rules["events"]["message.channel"]["channels"]
    _, secrets = await NotificationChannelRepository.get_for_send(channel["id"])
    assert secrets["urls"] == JSON_URL
    # Meshloom's own log lines (the apprise library's DEBUG output is redacted by the
    # handler filter, not by its caller).
    _no_secret("\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app.")))
    _no_secret((await client.get("/api/notifications/channels")).text)
    _no_secret((await client.get("/api/fanout")).text)


async def test_list_scope_becomes_exceptions(test_db, client):
    scope = {"messages": {"channels": ["ABCD"], "contacts": ["aa11"]}, "raw_packets": "none"}
    row = await _row(scope=scope)
    channel = (await client.post(CONVERT.format(row["id"]))).json()["channel"]
    rules = await _rules(client)
    assert channel["id"] not in rules["events"]["message.dm"]["channels"]
    assert channel["id"] not in rules["events"]["message.channel"]["channels"]
    assert rules["conversations"]["channel-ABCD"] == {channel["id"]: True}
    assert rules["conversations"]["contact-aa11"] == {channel["id"]: True}


async def test_except_scope_lists_the_channel_with_off_exceptions(test_db, client):
    scope = {"messages": {"channels": {"except": ["ABCD"]}}, "raw_packets": "none"}
    row = await _row(scope=scope)
    channel = (await client.post(CONVERT.format(row["id"]))).json()["channel"]
    rules = await _rules(client)
    assert channel["id"] in rules["events"]["message.channel"]["channels"]
    assert channel["id"] not in rules["events"]["message.dm"]["channels"]
    assert rules["conversations"]["channel-ABCD"] == {channel["id"]: False}


def test_scope_to_rules_shapes():
    assert scope_to_rules({"messages": "none"}) == ([], {}, [])
    events, exceptions, _ = scope_to_rules(
        {"messages": {"contacts": {"except": ["k1", "bad key!"]}}}
    )
    assert events == ["message.dm"] and exceptions == {"contact-k1": False}


async def test_templates_are_mapped_and_a_long_one_dropped(test_db, client):
    row = await _row(body_format_dm="**{sender_name}**: {text}", body_format_channel="x" * 600)
    body = (await client.post(CONVERT.format(row["id"]))).json()
    assert body["channel"]["template_dm"] == "**{sender_name}**: {text}"
    assert body["channel"]["template_channel"] == ""
    assert "template_channel_dropped" in body["notes"]


async def test_a_telegram_url_becomes_the_preset(test_db, client):
    assert match_preset(TELEGRAM) == (
        "telegram",
        {"bot_token": "111111:AAAA-token-a", "chat_id": "1001"},
    )
    row = await _row(urls=TELEGRAM)
    response = await client.post(CONVERT.format(row["id"]))
    assert response.status_code == 200, response.text
    _no_secret(response.text)
    assert response.json()["channel"]["preset"] == "telegram"


async def test_conversion_is_idempotent(test_db, client):
    row = await _row()
    first = (await client.post(CONVERT.format(row["id"]))).json()
    # Turned back on by hand: converting again only turns it off.
    await FanoutConfigRepository.update(row["id"], enabled=True)
    again = await client.post(CONVERT.format(row["id"]))
    assert again.status_code == 200
    assert again.json()["already_converted"] is True
    assert again.json()["channel"]["id"] == first["channel"]["id"]
    assert again.json()["fanout"]["enabled"] is False
    channels = (await client.get("/api/notifications/channels")).json()
    assert sum(c["name"] == "Alerts" for c in channels) == 1
    rules = await _rules(client)
    assert rules["events"]["message.dm"]["channels"].count(first["channel"]["id"]) == 1


async def test_refusals(test_db, client):
    assert (await client.post(CONVERT.format("nope"))).status_code == 404
    hook = await FanoutConfigRepository.create(
        "webhook", "Hook", {"url": "https://h.example.org/x"}, {"messages": "all"}
    )
    response = await client.post(CONVERT.format(hook["id"]))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "fanout_not_apprise"


async def test_edit_keeps_the_converted_mark_and_create_drops_it(test_db, client):
    row = await _row()
    channel_id = (await client.post(CONVERT.format(row["id"]))).json()["channel"]["id"]
    shown = (await client.get(f"/api/fanout/{row['id']}")).json()["config"]
    edited = {k: v for k, v in shown.items() if k != "converted_to"}
    patched = await client.patch(f"/api/fanout/{row['id']}", json={"config": edited})
    assert patched.status_code == 200, patched.text
    assert patched.json()["config"]["converted_to"] == channel_id

    created = await client.post(
        "/api/fanout",
        json={
            "type": "apprise",
            "name": "New",
            "config": {"urls": JSON_URL, "converted_to": "x"},
            "scope": {"messages": "all", "raw_packets": "none"},
            "enabled": False,
        },
    )
    assert created.status_code == 200, created.text
    assert created.headers["Deprecation"] == "true"
    assert "converted_to" not in created.json()["config"]


async def test_duplicate_detection_on_channel_creation(test_db, client):
    row = await _row(urls="jsons://hooks.example.org/path/secret-path-7/")
    await _row(urls=JSON_URL, enabled=False)
    response = await client.post(
        "/api/notifications/channels",
        json={"type": "apprise", "name": "Same", "config": {"urls": JSON_URL}},
    )
    assert response.status_code == 201, response.text
    _no_secret(response.text)
    created = response.json()
    assert created["fanout_duplicates"] == [{"id": row["id"], "name": "Alerts"}]

    # The prompt attaches the row to the new channel: no second channel.
    merged = await client.post(CONVERT.format(row["id"]), json={"channel_id": created["id"]})
    assert merged.status_code == 200, merged.text
    assert merged.json()["channel"]["id"] == created["id"]
    assert merged.json()["fanout"]["config"]["converted_to"] == created["id"]
    other = await client.post(
        "/api/notifications/channels",
        json={"type": "apprise", "name": "Again", "config": {"urls": JSON_URL}},
    )
    assert other.json()["fanout_duplicates"] == []


async def test_backup_round_trips_the_converted_row(test_db, client):
    from app.services import backup

    row = await _row()
    channel_id = (await client.post(CONVERT.format(row["id"]))).json()["channel"]["id"]
    extra = await backup.export_meshloom_extra()
    entry = next(e for e in extra.fanout_configs if e.id == row["id"])
    assert entry.enabled is False and entry.config["converted_to"] == channel_id

    # The row as it was before the conversion; the restore brings the converted state back.
    await FanoutConfigRepository.update(row["id"], enabled=True, config={"urls": JSON_URL})
    warnings: list[str] = []
    await backup._restore_fanout_configs(extra.fanout_configs, warnings)
    restored = await FanoutConfigRepository.get(row["id"])
    assert restored["enabled"] is False
    assert restored["config"]["converted_to"] == channel_id
    assert restored["config"]["urls"] == JSON_URL
