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


@pytest.fixture(autouse=True)
def _no_announce(monkeypatch):
    """Creating a channel announces it to the other enabled channels; here those send to a
    test address, so the announcement would wait on the network."""

    async def announce(*args, **kwargs):
        return None

    monkeypatch.setattr("app.notifications.api_support.announce_channel_change", announce)


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


async def _channel(client, channel_id):
    response = await client.get(f"/api/notifications/channels/{channel_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _walk_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif value is not None:
        yield str(value)


def _assert_no_url_or_secret(payload, urls) -> None:
    """No secret part, no URL (whole or with its scheme) and no ``config`` anywhere."""
    from app.net.apprise_urls import url_secret_parts

    needles = set(SECRETS)
    for url in urls:
        needles.add(url)
        needles.add(url.split("://", 1)[1])
        needles.update(p for p in url_secret_parts(url) if len(p) >= 4)
    for text in _walk_strings(payload):
        assert "://" not in text, text
        for needle in needles:
            assert needle not in text, needle
    assert "config" not in list(_walk_keys(payload))


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


async def _rules(client):
    return (await client.get("/api/notifications/rules")).json()


async def test_all_scope_creates_a_generic_channel_on_both_events(test_db, client, caplog):
    caplog.set_level(logging.DEBUG)
    row = await _row(markdown_format=False, include_outgoing=True)
    response = await client.post(CONVERT.format(row["id"]))
    assert response.status_code == 200, response.text
    _no_secret(response.text)
    body = response.json()
    assert body["channel"]["type"] == "apprise" and body["channel"]["preset"] is None
    channel = await _channel(client, body["channel"]["id"])
    assert channel["config"]["format"] == "text"
    assert channel["include_message_text"] == "always"
    assert body["already_converted"] is False
    assert "include_outgoing_unsupported" in body["notes"]
    assert "private_channels_need_exception" in body["notes"]
    assert body["fanout"]["enabled"] is False
    assert body["fanout"]["converted_to"] == channel["id"]

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
    channel = await _channel(client, body["channel"]["id"])
    assert channel["template_dm"] == "**{sender_name}**: {text}"
    assert channel["template_channel"] == ""
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
    assert merged.json()["fanout"]["converted_to"] == created["id"]
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


# ── Secret handling and atomicity (review of PR #400) ───────────────────────


@pytest.mark.parametrize("urls", [JSON_URL, TELEGRAM, f"{JSON_URL}\n{TELEGRAM}"])
async def test_convert_response_has_no_config_and_no_url(test_db, client, urls):
    row = await _row(urls=urls)
    for _ in range(2):  # the first conversion, then the already-converted answer
        response = await client.post(CONVERT.format(row["id"]))
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body["channel"]) == {"id", "name", "type", "preset", "enabled"}
        assert set(body["fanout"]) == {"id", "name", "type", "enabled", "converted_to"}
        _assert_no_url_or_secret(body, urls.split("\n"))


@pytest.mark.parametrize(
    "other",
    [
        "json://hooks.example.org/path/secret-path-8",  # same host, other token
        "json://hooks.example.org/other/secret-path-7",  # same host, other path
        "json://hooks.example.org/path",  # a prefix of the path
        "json://hooks.example.org/path/secret-path-7?x=1",  # other query
        "json://user:pw@hooks.example.org/path/secret-path-7",  # other credentials
    ],
)
async def test_same_host_other_path_or_token_is_not_a_duplicate(test_db, client, other):
    await _row(urls=other)
    response = await client.post(
        "/api/notifications/channels",
        json={"type": "apprise", "name": "Same host", "config": {"urls": JSON_URL}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["fanout_duplicates"] == []


async def test_every_refusal_carries_no_config_or_url(test_db, client, monkeypatch):
    urls = [JSON_URL, TELEGRAM]
    apprise = await _row(urls="\n".join(urls))
    hook = await FanoutConfigRepository.create(
        "webhook", "Hook", {"url": JSON_URL.replace("json://", "https://")}, {"messages": "all"}
    )
    private = await _row(urls="json://192.168.1.20/path/secret-path-7")
    unreadable = await _row()

    real_get = FanoutConfigRepository.get

    async def get(config_id):
        found = await real_get(config_id)
        if found is not None and config_id == unreadable["id"]:
            found = {**found, "secret_unreadable": True}
        return found

    monkeypatch.setattr(FanoutConfigRepository, "get", staticmethod(get))
    cases = [
        (CONVERT.format("nope"), None, 404, "fanout_missing"),
        (CONVERT.format("system:meshloom-stats"), None, 404, "fanout_missing"),
        (CONVERT.format(hook["id"]), None, 400, "fanout_not_apprise"),
        (CONVERT.format(unreadable["id"]), None, 409, "secret_unreadable"),
        (CONVERT.format(apprise["id"]), {"channel_id": "apprise-missing0"}, 404, "channel_missing"),
        # The channel checks refuse a private destination: the guard's code, no host.
        (CONVERT.format(private["id"]), None, None, None),
    ]
    for path, payload, status, code in cases:
        response = await client.post(path, json=payload) if payload else await client.post(path)
        assert response.status_code >= 400, (path, response.text)
        if status is not None:
            assert response.status_code == status, response.text
            assert response.json()["detail"]["code"] == code
        body = response.json()
        _assert_no_url_or_secret(body, [*urls, "json://192.168.1.20/path/secret-path-7"])
        assert "192.168.1.20" not in response.text
    # Nothing was converted by the refusals.
    assert "converted_to" not in (await FanoutConfigRepository.get(apprise["id"]))["config"]


async def test_a_converted_row_turned_back_on_is_not_reported_as_a_duplicate(test_db, client):
    """Chosen behaviour: re-enabling a converted row is a confirmed choice to keep both, and
    its channel exists already, so the duplicate prompt does not offer it again."""
    row = await _row()
    await client.post(CONVERT.format(row["id"]))
    # The UI's confirm path is a plain PATCH with enabled=true.
    patched = await client.patch(f"/api/fanout/{row['id']}", json={"enabled": True})
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is True
    response = await client.post(
        "/api/notifications/channels",
        json={"type": "apprise", "name": "Again", "config": {"urls": JSON_URL}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["fanout_duplicates"] == []


async def test_a_failure_after_the_channel_exists_never_makes_a_second_one(
    test_db, client, monkeypatch
):
    from app.notifications import fanout_convert

    row = await _row()
    real = fanout_convert._disable_row

    async def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(fanout_convert, "_disable_row", broken)
    with pytest.raises(RuntimeError):
        await fanout_convert.convert_fanout_row(row["id"])
    first = [c for c in await NotificationChannelRepository.list_all() if c.name == "Alerts"]
    assert len(first) == 1
    pending = await FanoutConfigRepository.get(row["id"])
    assert pending["enabled"] is True
    assert pending["config"]["converting_to"] == first[0].id

    monkeypatch.setattr(fanout_convert, "_disable_row", real)
    response = await client.post(CONVERT.format(row["id"]))
    assert response.status_code == 200, response.text
    assert response.json()["channel"]["id"] == first[0].id
    channels = [c for c in await NotificationChannelRepository.list_all() if c.name == "Alerts"]
    assert [c.id for c in channels] == [first[0].id]
    done = await FanoutConfigRepository.get(row["id"])
    assert done["enabled"] is False
    assert done["config"]["converted_to"] == first[0].id
    assert "converting_to" not in done["config"]
    rules = await _rules(client)
    assert rules["events"]["message.dm"]["channels"].count(first[0].id) == 1


async def test_a_failure_before_the_channel_exists_creates_it_under_the_marked_id(
    test_db, client, monkeypatch
):
    from app.notifications import fanout_convert

    row = await _row()
    real = NotificationChannelRepository.create

    async def broken(body):
        raise RuntimeError("disk full")

    monkeypatch.setattr(NotificationChannelRepository, "create", staticmethod(broken))
    with pytest.raises(RuntimeError):
        await fanout_convert.convert_fanout_row(row["id"])
    marked = (await FanoutConfigRepository.get(row["id"]))["config"]["converting_to"]

    monkeypatch.setattr(NotificationChannelRepository, "create", staticmethod(real))
    response = await client.post(CONVERT.format(row["id"]))
    assert response.status_code == 200, response.text
    assert response.json()["channel"]["id"] == marked
    assert sum(c.name == "Alerts" for c in await NotificationChannelRepository.list_all()) == 1


async def test_clients_cannot_set_or_clear_the_write_ahead_mark(test_db, client, monkeypatch):
    from app.notifications import fanout_convert

    row = await _row()

    async def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(fanout_convert, "_disable_row", broken)
    with pytest.raises(RuntimeError):
        await fanout_convert.convert_fanout_row(row["id"])
    mark = (await FanoutConfigRepository.get(row["id"]))["config"]["converting_to"]
    shown = (await client.get(f"/api/fanout/{row['id']}")).json()["config"]
    edited = {k: v for k, v in shown.items() if k not in ("converting_to", "converted_to")}
    patched = await client.patch(f"/api/fanout/{row['id']}", json={"config": edited})
    assert patched.status_code == 200, patched.text
    assert (await FanoutConfigRepository.get(row["id"]))["config"]["converting_to"] == mark


async def _interrupted(monkeypatch):
    """A row whose conversion created its channel but failed to disable it (still marked)."""
    from app.notifications import fanout_convert

    row = await _row()
    real = fanout_convert._disable_row

    async def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(fanout_convert, "_disable_row", broken)
    with pytest.raises(RuntimeError):
        await fanout_convert.convert_fanout_row(row["id"])
    monkeypatch.setattr(fanout_convert, "_disable_row", real)
    pending = (await FanoutConfigRepository.get(row["id"]))["config"]["converting_to"]
    assert await NotificationChannelRepository.get(pending) is not None
    return row, pending


async def test_the_duplicate_prompt_deletes_the_pending_channel_of_a_marked_row(
    test_db, client, monkeypatch
):
    """MSHL-403: attaching a still-marked row elsewhere leaves no orphan channel, no secret."""
    from app.repository.notification_channels import strip_channel_from_rules

    row, pending = await _interrupted(monkeypatch)
    created = await client.post(
        "/api/notifications/channels",
        json={"type": "apprise", "name": "Same", "config": {"urls": JSON_URL}},
    )
    assert created.status_code == 201, created.text
    target = created.json()["id"]
    assert created.json()["fanout_duplicates"] == [{"id": row["id"], "name": "Alerts"}]

    for _ in range(2):  # a retry is a no-op
        merged = await client.post(CONVERT.format(row["id"]), json={"channel_id": target})
        assert merged.status_code == 200, merged.text
        _assert_no_url_or_secret(merged.json(), [JSON_URL])
        assert merged.json()["channel"]["id"] == target
    assert await NotificationChannelRepository.get(pending) is None
    assert [
        c.id for c in await NotificationChannelRepository.list_all() if c.id in (pending, target)
    ] == [target]
    done = await FanoutConfigRepository.get(row["id"])
    assert done["enabled"] is False
    assert done["config"]["converted_to"] == target
    assert "converting_to" not in done["config"]
    # Neither its rules nor its row (with the sealed secret, checked above) survive.
    rules = await _rules(client)
    assert not strip_channel_from_rules(rules, pending)


async def test_the_duplicate_prompt_into_the_pending_channel_reuses_it(
    test_db, client, monkeypatch
):
    row, pending = await _interrupted(monkeypatch)
    merged = await client.post(CONVERT.format(row["id"]), json={"channel_id": pending})
    assert merged.status_code == 200, merged.text
    assert merged.json()["channel"]["id"] == pending
    assert sum(c.name == "Alerts" for c in await NotificationChannelRepository.list_all()) == 1
    done = await FanoutConfigRepository.get(row["id"])
    assert done["config"]["converted_to"] == pending
    assert "converting_to" not in done["config"]


async def _target(client):
    created = await client.post(
        "/api/notifications/channels",
        json={"type": "apprise", "name": "Same", "config": {"urls": JSON_URL}},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _assert_kept(client, row, pending, target):
    merged = await client.post(CONVERT.format(row["id"]), json={"channel_id": target})
    assert merged.status_code == 200, merged.text
    _assert_no_url_or_secret(merged.json(), [JSON_URL])
    assert "pending_channel_kept" in merged.json()["notes"]
    assert await NotificationChannelRepository.get(pending) is not None
    done = await FanoutConfigRepository.get(row["id"])
    assert done["config"]["converted_to"] == target
    assert "converting_to" not in done["config"]


async def test_the_duplicate_prompt_keeps_an_edited_pending_channel(test_db, client, monkeypatch):
    row, pending = await _interrupted(monkeypatch)
    renamed = await client.patch(
        f"/api/notifications/channels/{pending}", json={"name": "Renamed by hand"}
    )
    assert renamed.status_code == 200, renamed.text
    await _assert_kept(client, row, pending, await _target(client))
    assert (await NotificationChannelRepository.get(pending)).name == "Renamed by hand"


async def test_the_duplicate_prompt_keeps_a_pending_channel_with_a_hand_made_rule(
    test_db, client, monkeypatch
):
    row, pending = await _interrupted(monkeypatch)
    rules = await NotificationChannelRepository.get_rules()
    conversations = dict(rules.get("conversations") or {})
    conversations["contact-abcdef"] = {pending: True}
    await NotificationChannelRepository.set_rules({**rules, "conversations": conversations})
    await _assert_kept(client, row, pending, await _target(client))


async def test_the_duplicate_prompt_keeps_a_pending_channel_another_row_uses(
    test_db, client, monkeypatch
):
    row, pending = await _interrupted(monkeypatch)
    other = await _row(urls="json://hooks.example.org/path/elsewhere")
    joined = await client.post(CONVERT.format(other["id"]), json={"channel_id": pending})
    assert joined.status_code == 200, joined.text
    await _assert_kept(client, row, pending, await _target(client))
    assert (await FanoutConfigRepository.get(other["id"]))["config"]["converted_to"] == pending


async def test_a_retry_after_the_pending_channel_is_deleted_attaches_the_row(
    test_db, client, monkeypatch
):
    from app.notifications import fanout_convert

    row, pending = await _interrupted(monkeypatch)
    target = await _target(client)
    real = fanout_convert._disable_row

    async def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(fanout_convert, "_disable_row", broken)
    with pytest.raises(RuntimeError):
        await fanout_convert.convert_fanout_row(row["id"], into=target)
    assert await NotificationChannelRepository.get(pending) is None
    assert (await FanoutConfigRepository.get(row["id"]))["config"]["converting_to"] == pending

    monkeypatch.setattr(fanout_convert, "_disable_row", real)
    merged = await client.post(CONVERT.format(row["id"]), json={"channel_id": target})
    assert merged.status_code == 200, merged.text
    assert "pending_channel_kept" not in merged.json()["notes"]
    done = await FanoutConfigRepository.get(row["id"])
    assert done["config"]["converted_to"] == target
    assert "converting_to" not in done["config"]


async def test_a_pending_channel_deleted_concurrently_is_not_an_error(test_db, client, monkeypatch):
    from app.notifications.channels import ChannelError

    row, pending = await _interrupted(monkeypatch)
    target = await _target(client)
    real = NotificationChannelRepository.delete

    async def raced(channel_id):
        await real(channel_id)
        raise ChannelError("channel_missing", 404)

    monkeypatch.setattr(NotificationChannelRepository, "delete", staticmethod(raced))
    merged = await client.post(CONVERT.format(row["id"]), json={"channel_id": target})
    assert merged.status_code == 200, merged.text
    assert (await FanoutConfigRepository.get(row["id"]))["config"]["converted_to"] == target


@pytest.mark.parametrize("error", ["runtime", "builtin_channel"])
async def test_a_failed_pending_deletion_leaves_the_row_marked(test_db, client, monkeypatch, error):
    from app.notifications import fanout_convert
    from app.notifications.channels import ChannelError

    row, pending = await _interrupted(monkeypatch)
    target = await _target(client)

    async def broken(channel_id):
        if error == "runtime":
            raise RuntimeError("database is locked")
        raise ChannelError(error, 403)

    monkeypatch.setattr(NotificationChannelRepository, "delete", staticmethod(broken))
    with pytest.raises((RuntimeError, ChannelError)):
        await fanout_convert.convert_fanout_row(row["id"], into=target)
    still = await FanoutConfigRepository.get(row["id"])
    assert still["enabled"] is True
    assert still["config"]["converting_to"] == pending
    assert "converted_to" not in still["config"]
    assert await NotificationChannelRepository.get(pending) is not None
