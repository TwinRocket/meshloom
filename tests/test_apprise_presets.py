"""Apprise presets (``app/notifications/presets.py``): services described as data.

The first half is generic and runs on every preset of ``PRESETS`` (and on a preset added
here as pure data); the second half pins what is specific to Telegram, Gotify and
Home Assistant.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from app.net.apprise_urls import parse_apprise_urls
from app.notifications import apprise_medium, presets
from app.notifications.apprise_medium import _Response
from app.notifications.channels import (
    Channel,
    ChannelCreate,
    ChannelError,
    Limits,
    get_type,
    min_interval_s,
    types_schema,
)
from app.notifications.events import event_from_payload
from app.notifications.presets import (
    PRESETS,
    Preset,
    PresetError,
    PresetField,
    compile_url,
    preset_channel_type,
)
from app.notifications.transports.apprise import AppriseTransport
from app.notifications.transports.base import Delivery
from app.repository.notification_channels import NotificationChannelRepository as Channels
from app.secret_fields import scrub_error

SLICES = Path(__file__).parents[1] / "frontend" / "src" / "i18n" / "locales" / "slices"
DM = {"type": "PRIV", "conversation_key": "aa" * 32, "sender_name": "Léa", "text": "plans"}


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Every host name resolves to one public address; IP literals resolve as themselves."""
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = "93.184.216.34"
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def examples(preset: Preset) -> dict[str, str]:
    return {spec.name: spec.example for spec in preset.fields if spec.example}


def secret_values(preset: Preset) -> list[str]:
    return [spec.example for spec in preset.fields if spec.secret]


def body(preset: Preset, **overrides: str) -> ChannelCreate:
    config = {**examples(preset), **overrides}
    return ChannelCreate(type="apprise", preset=preset.id, name=preset.id, config=config)


def delivery() -> Delivery:
    return Delivery(name="message.dm", payload=DM, event=event_from_payload(DM))


# ── Generic: runs on every preset, so a new one is covered by adding data only ──────


@pytest.fixture(params=PRESETS, ids=lambda p: p.id)
def preset(request) -> Preset:
    return request.param


def test_the_compiled_url_is_accepted_by_the_apprise_parser(preset):
    url = compile_url(preset, examples(preset))
    (validated,) = parse_apprise_urls([url])
    assert validated.url == url
    # The URL carries every secret (it is only ever built to be sent) ...
    assert all(
        secret in url or secret.replace(".", "%2E") in url for secret in secret_values(preset)
    )


def test_the_registry_entry_comes_from_the_data(preset):
    entry = get_type("apprise", preset.id)
    assert entry.id == preset.id and entry.row_type == "apprise" and entry.preset is preset
    assert entry.cloud is preset.cloud and entry.default_limits == preset.default_limits
    assert entry.secret_fields == {s.name for s in preset.fields if s.secret}
    assert min_interval_s("apprise", preset.id) == preset.min_interval_s
    with pytest.raises(ChannelError) as wrong_base:
        get_type("webhook", preset.id)
    assert wrong_base.value.code == "channel_preset_unknown"


def test_types_lists_the_preset_next_to_the_natives(preset):
    entry = next(e for e in types_schema() if e["type"] == preset.id)
    assert entry["preset"] is True and entry["base_type"] == "apprise"
    assert entry["singleton"] is False and entry["wiki_url"] == preset.wiki_url
    properties = entry["schema"]["properties"]
    assert set(properties) == {s.name for s in preset.fields}
    for spec in preset.fields:
        prop = properties[spec.name]
        assert prop["label_key"] == f"notifications.presets.{preset.id}.fields.{spec.name}"
        assert (
            prop.get("writeOnly") or any(o.get("writeOnly") for o in prop.get("anyOf", []))
        ) == (spec.secret)
        assert ("format" in prop and prop["format"] == "uri") == (spec.kind == "url")
        assert ("enum" in prop) == (spec.kind == "choice")
    required = set(entry["schema"].get("required", []))
    assert required == {s.name for s in preset.fields if s.required and not s.secret}


def test_texts_exist_in_both_languages(preset):
    """Every key a preset's schema names, plus its name and description, in FR and EN."""
    for lang in ("en", "fr"):
        merged: dict = {}
        for path in SLICES.glob(f"*.{lang}.json"):
            _merge(merged, json.loads(path.read_text()))
        base = merged["settings"]["notifications"]["types"][preset.id]
        assert base["name"] and base["description"]
        texts = merged["notifications"]["presets"][preset.id]
        assert texts["about"]
        for spec in preset.fields:
            assert texts["fields"][spec.name], (lang, spec.name)
            if spec.help:
                assert texts["help"][spec.name], (lang, spec.name)
            for choice in spec.choices:
                assert base[spec.name]["options"][choice], (lang, choice)


def _merge(into: dict, other: dict) -> None:
    for key, value in other.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _merge(into[key], value)
        else:
            into[key] = value


@pytest.mark.asyncio
async def test_an_invalid_field_is_invalid_config_and_never_echoed(test_db, preset):
    constrained = [
        s for s in preset.fields if s.kind in ("url", "choice") or (s.pattern and not s.secret)
    ]
    assert constrained, "a preset should constrain at least one field"
    for spec in constrained:
        bad = "ftp://not-allowed" if spec.kind == "url" else "bad value ✗"
        with pytest.raises(ChannelError) as refused:
            await Channels.prepare(body(preset, **{spec.name: bad}))
        assert refused.value.code == "invalid_config"
        assert refused.value.params["fields"] == [spec.name]
        assert bad not in repr(refused.value.params)


@pytest.mark.asyncio
async def test_a_required_field_is_required(test_db, preset):
    for spec in (s for s in preset.fields if s.required):
        config = examples(preset)
        del config[spec.name]
        with pytest.raises(ChannelError) as refused:
            await Channels.prepare(
                ChannelCreate(type="apprise", preset=preset.id, name="x", config=config)
            )
        assert refused.value.code in ("invalid_config", "config_required")
        assert spec.name in refused.value.params["fields"]


@pytest.mark.asyncio
async def test_a_secret_is_stored_apart_and_no_read_path_shows_it(test_db, preset):
    created = await Channels.create(body(preset))
    assert created.type == "apprise" and created.preset == preset.id
    expected = sorted(s.name for s in preset.fields if s.secret)
    assert sorted(created.secret_fields_set) == expected
    assert not set(expected) & set(created.config)
    blobs = [
        created.model_dump_json(),
        repr(created),
        *(c.model_dump_json() for c in await Channels.list_all()),
        json.dumps(types_schema()),
    ]
    for blob in blobs:
        assert not any(secret in blob for secret in secret_values(preset))
    _channel, secrets = await Channels.get_for_send(created.id)  # type: ignore[misc]
    assert secrets == {s.name: s.example for s in preset.fields if s.secret}


@pytest.mark.asyncio
async def test_a_failure_with_the_url_in_its_text_is_scrubbed(test_db, preset):
    """A fake Apprise error that quotes the compiled URL: the stored status shows none of
    the secrets."""
    created = await Channels.create(body(preset))
    _channel, secrets = await Channels.get_for_send(created.id)  # type: ignore[misc]
    url = compile_url(preset, {**created.config, **secrets})
    text = scrub_error(f"HTTPSConnectionPool: failed for {url}", secrets.values())
    assert text is not None
    assert not any(secret in text for secret in secret_values(preset))
    assert "****" in text


@pytest.mark.asyncio
async def test_the_transport_sends_the_compiled_url(test_db, preset, monkeypatch):
    sent: list[dict] = []

    def fake(server, title, body_text, markdown):
        sent.append({"scheme": type(server).__name__, "markdown": markdown})
        return True, _Response(status=200)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(apprise_medium, "_send_sync", fake)
    monkeypatch.setattr(apprise_medium, "_sleep", no_sleep)
    monkeypatch.setattr("app.email_template.resolve_instance_language", _en)
    created = await Channels.create(body(preset))
    channel, secrets = await Channels.get_for_send(created.id)  # type: ignore[misc]
    result = await AppriseTransport().send(channel, None, delivery(), secrets)
    assert result.ok and len(sent) == 1
    assert sent[0]["markdown"] is (preset.format == "markdown")


async def _en():
    return "en"


@pytest.mark.asyncio
async def test_a_preset_added_as_data_only_passes_the_same_checks(test_db, monkeypatch):
    """No code per preset: a ``Preset`` value is enough (here a Pushover-like service)."""
    spec = Preset(
        "demo",
        "pover://{user_key}@{app_token}",
        (
            PresetField(
                "user_key", secret=True, required=True, pattern=r"[a-z0-9]{30}", example="u" * 30
            ),
            PresetField("app_token", required=True, pattern=r"[a-z0-9]{30}", example="a" * 30),
        ),
        Limits(10, None, None),
        cloud=True,
    )
    monkeypatch.setattr(presets, "PRESETS_BY_ID", {**presets.PRESETS_BY_ID, "demo": spec})
    monkeypatch.setattr(
        presets, "PRESET_TYPES", {**presets.PRESET_TYPES, "demo": preset_channel_type(spec)}
    )
    (validated,) = parse_apprise_urls([compile_url(spec, examples(spec))])
    assert validated.scheme == "pover"
    created = await Channels.create(body(spec))
    assert created.preset == "demo" and created.secret_fields_set == ["user_key"]
    assert created.include_message_text == "public_only"
    entry = next(e for e in types_schema() if e["type"] == "demo")
    assert entry["schema"]["properties"]["user_key"]["writeOnly"] is True
    with pytest.raises(ChannelError) as refused:
        await Channels.prepare(body(spec, app_token="NOT valid"))
    assert refused.value.code == "invalid_config"


def test_template_syntax():
    spec = Preset(
        "t",
        "x://{a}[/{b}][?c={d}]/{u.netloc}{u.path}",
        (
            PresetField("a", required=True),
            PresetField("b"),
            PresetField("d", default="dflt"),
            PresetField("u", kind="url", scheme_map={"http": "x"}),
        ),
        Limits(1, None, None),
    )
    assert compile_url(spec, {"a": "A B", "u": "http://h:81/p/"}) == "x://A%20B?c=dflt/h:81/p"
    assert compile_url(spec, {"a": "A", "b": "B/C", "d": "v", "u": "http://h"}) == (
        "x://A/B%2FC?c=v/h"
    )
    with pytest.raises(PresetError) as missing:
        compile_url(spec, {"u": "http://h"})
    assert missing.value.code == "config_required" and missing.value.fields == ["a"]
    with pytest.raises(PresetError) as bad:
        compile_url(spec, {"a": "A", "u": "ftp://h"})
    assert bad.value.code == "invalid_config" and bad.value.fields == ["u"]
    assert replace(spec, id="t2").id == "t2"


# ── Telegram ────────────────────────────────────────────────────────────


def telegram(**config: str) -> ChannelCreate:
    base = {"bot_token": "123456:ABC-def_GHI", "chat_id": "-1001234567"}
    return ChannelCreate(
        type="apprise", preset="telegram", name="Telegram", config={**base, **config}
    )


def test_telegram_url_with_and_without_a_topic():
    (spec,) = (p for p in PRESETS if p.id == "telegram")
    plain = {"bot_token": "123456:ABC-def_GHI", "chat_id": "@meshloom_news"}
    assert compile_url(spec, plain) == "tgram://123456:ABC-def_GHI/%40meshloom_news"
    assert compile_url(spec, {**plain, "chat_id": "42", "topic_id": "9"}) == (
        "tgram://123456:ABC-def_GHI/42:9"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "code", "fields"),
    [
        ({"chat_id": ""}, "invalid_config", ["chat_id"]),
        ({"chat_id": "not a chat"}, "invalid_config", ["chat_id"]),
        ({"bot_token": "no-colon"}, "invalid_config", ["bot_token"]),
        ({"topic_id": "x1"}, "invalid_config", ["topic_id"]),
        ({"bot_token": ""}, "config_required", ["bot_token"]),
    ],
)
async def test_telegram_refusals(test_db, config, code, fields):
    with pytest.raises(ChannelError) as refused:
        await Channels.prepare(telegram(**config))
    assert (refused.value.code, refused.value.params["fields"]) == (code, fields)
    assert "no-colon" not in repr(refused.value.params)


@pytest.mark.asyncio
async def test_telegram_chat_id_is_required_when_missing(test_db):
    request = telegram()
    del request.config["chat_id"]
    with pytest.raises(ChannelError) as refused:
        await Channels.prepare(request)
    assert refused.value.code == "invalid_config" and refused.value.params["fields"] == ["chat_id"]


@pytest.mark.asyncio
async def test_telegram_defaults_and_spacing(test_db):
    created = await Channels.create(telegram())
    assert created.include_message_text == "public_only"
    entry = get_type("apprise", "telegram")
    assert entry.default_limits == Limits(15, 200, None)
    assert min_interval_s(created.type, created.preset) == 3.0


def test_a_429_body_retry_after_is_read():
    class Answer:
        status_code = 429

        def json(self):
            return {"ok": False, "parameters": {"retry_after": 17}}

    assert apprise_medium._body_retry_after(Answer()) == "17"
    Answer.status_code = 400
    assert apprise_medium._body_retry_after(Answer()) is None

    class Odd(Answer):
        status_code = 429

        def json(self):
            return ["x"]

    assert apprise_medium._body_retry_after(Odd()) is None


@pytest.mark.asyncio
async def test_a_telegram_429_is_retried_after_the_delay_telegram_gave(test_db, monkeypatch):
    waits: list[float] = []
    answers = iter([_Response(status=429, retry_after="17"), _Response(status=200)])

    def fake(server, title, body_text, markdown):
        answer = next(answers)
        return answer.status == 200, answer

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(apprise_medium, "_send_sync", fake)
    monkeypatch.setattr(apprise_medium, "_sleep", sleep)
    monkeypatch.setattr("app.email_template.resolve_instance_language", _en)
    created = await Channels.create(telegram())
    channel, secrets = await Channels.get_for_send(created.id)  # type: ignore[misc]
    result = await AppriseTransport().send(channel, None, delivery(), secrets)
    assert result.ok and waits == [17.0]


# ── Gotify ──────────────────────────────────────────────────────────────


def test_gotify_maps_the_scheme_and_keeps_the_path():
    (spec,) = (p for p in PRESETS if p.id == "gotify")
    values = {"server": "http://gotify.lan:8080/sub/", "token": "Ab12cd34"}
    assert compile_url(spec, values) == "gotify://gotify.lan:8080/sub/Ab12cd34/?priority=normal"
    secure = compile_url(spec, {**values, "server": "https://g.example.org", "priority": "high"})
    assert secure == "gotifys://g.example.org/Ab12cd34/?priority=high"


@pytest.mark.asyncio
async def test_gotify_on_a_lan_needs_the_private_network_box(test_db):
    request = ChannelCreate(
        type="apprise",
        preset="gotify",
        name="Gotify",
        config={"server": "http://192.168.1.20:8080", "token": "Ab12cd34"},
    )
    with pytest.raises(ChannelError) as refused:
        await Channels.prepare(request)
    assert refused.value.code == "egress_private_address"
    await Channels.prepare(request.model_copy(update={"allow_private_network": True}))
    created = await Channels.create(request.model_copy(update={"allow_private_network": True}))
    assert created.include_message_text == "always"
    assert get_type("apprise", "gotify").default_limits == Limits(60, None, None)


@pytest.mark.asyncio
async def test_a_kept_gotify_token_does_not_follow_a_new_server(test_db):
    created = await Channels.create(
        ChannelCreate(
            type="apprise",
            preset="gotify",
            name="G",
            config={"server": "https://g.example.org", "token": "Ab12cd34"},
        )
    )
    from app.notifications.channels import ChannelUpdate

    with pytest.raises(ChannelError) as refused:
        await Channels.update(
            created.id, ChannelUpdate(config={"server": "https://evil.example.net"})
        )
    assert refused.value.code == "secret_required" and refused.value.params["fields"] == ["token"]
    moved = await Channels.update(
        created.id,
        ChannelUpdate(config={"server": "https://other.example.net", "token": "Zz99yy88"}),
    )
    assert moved.config["server"] == "https://other.example.net"


# ── Home Assistant ──────────────────────────────────────────────────────


def home_assistant(**config: str) -> ChannelCreate:
    base = {
        "url": "http://192.168.1.10:8123",
        "token": "eyJhbGciOiJIUzI1NiJ9.dummy.signature",
        "service": "mobile_app_pixel",
    }
    return ChannelCreate(
        type="apprise", preset="home_assistant", name="HA", config={**base, **config}
    )


def test_home_assistant_url_targets_a_notify_service():
    (spec,) = (p for p in PRESETS if p.id == "home_assistant")
    values = {"url": "https://ha.example.org:8123", "token": "a.b-c", "service": "notify.alerts"}
    assert (
        compile_url(spec, values) == "hassios://ha.example.org:8123/notify.alerts?accesstoken=a.b-c"
    )
    (validated,) = parse_apprise_urls([compile_url(spec, values)])
    assert validated.server.targets == [("notify", "alerts", [])]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_home_assistant_says_which_box_to_tick(test_db):
    with pytest.raises(ChannelError) as refused:
        await Channels.prepare(home_assistant())
    # The UI translates this code as "allow private networks for this destination".
    assert refused.value.code == "egress_private_address"
    request = home_assistant().model_copy(update={"allow_private_network": True})
    await Channels.prepare(request)
    created = await Channels.create(request)
    assert (
        created.secret_fields_set == ["token"] and created.config["service"] == "mobile_app_pixel"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config", [{"service": "has space"}, {"url": "http://user:pw@ha.local"}, {"url": "ha.local"}]
)
async def test_home_assistant_refuses_a_bad_service_or_url(test_db, config):
    with pytest.raises(ChannelError) as refused:
        await Channels.prepare(home_assistant(**config))
    assert refused.value.code == "invalid_config"
    assert "pw" not in repr(refused.value.params)


@pytest.mark.asyncio
async def test_a_preset_channel_cannot_be_built_on_another_base_type(test_db):
    with pytest.raises(ChannelError) as refused:
        await Channels.prepare(
            ChannelCreate(type="webhook", preset="telegram", name="x", config={})
        )
    assert refused.value.code == "channel_preset_unknown"
    with pytest.raises(ChannelError) as unknown:
        await Channels.prepare(ChannelCreate(type="apprise", preset="nope", name="x"))
    assert unknown.value.code == "channel_preset_unknown"
    assert isinstance(Channel(id="a", type="apprise", name="a", preset="telegram"), Channel)
