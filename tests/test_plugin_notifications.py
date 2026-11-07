"""The `notifications.send` capability (MSHL-213): a plugin notifies through the host's
notification dispatcher, as the catalogue event `plugin.notification`, to the channels the
user ticked in the rules matrix, within its quota; without the permission it is refused.

The handler runs against the real dispatcher with a recording transport; one test goes
through the real plugin host and runner (the `probe` plugin of
`tests/test_plugin_capabilities.py`).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.notifications import dispatcher as dispatcher_module
from app.notifications import webhook_v2
from app.notifications.dispatcher import Dispatcher
from app.notifications.events import EVENT_IDS, PluginNotificationEvent, event_from_payload
from app.notifications.markdown import neutralise_mentions
from app.notifications.render import render
from app.notifications.transports.base import SendResult
from app.notifications.user_rules import INSTANCE_RULE_EVENTS, admin_push_payload
from app.plugins.capabilities import REGISTRY, Call, CapabilityError, handle
from app.plugins.capabilities import notifications as notify_cap
from app.plugins.capabilities.grants import effective_permissions
from app.plugins.permissions import RESERVED_PERMISSIONS, Permissions
from app.repository.notification_channels import invalidate_cache
from app.websocket import _notification_event_audience
from tests.test_plugin_capabilities import (  # noqa: F401  (fixtures)
    fanout_dm,
    lines,
    manager,
    probe,
    statuses,
    wait_until,
)

CHANNELS = ("webhook-1", "ntfy-2")


class Recorder:
    """A transport that records what each channel was handed."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any], str, str]] = []

    async def send(self, channel, _rendered, delivery, secrets) -> SendResult:
        # The real transports render the event themselves, in the channel's language.
        rendered = render(delivery.event, "en")
        self.sent.append(
            (channel.id, delivery.name, dict(delivery.payload), rendered.title, rendered.body_text)
        )
        return SendResult("ok")


@pytest.fixture
async def wired(test_db, monkeypatch):
    """Two enabled channels, the real dispatcher with a recording transport, a fresh quota."""
    invalidate_cache()
    async with test_db.tx() as conn:
        for channel_id in CHANNELS:
            await conn.execute(
                "INSERT OR REPLACE INTO notification_channels "
                "(id, type, name, enabled, config, secrets, created_at) "
                "VALUES (?, ?, ?, 1, '{}', '{}', 1)",
                (channel_id, channel_id.split("-")[0], channel_id),
            )
    invalidate_cache()
    recorder = Recorder()
    dispatcher = Dispatcher(transport_for=lambda _channel: recorder)
    monkeypatch.setattr(dispatcher_module, "_instance", dispatcher)
    monkeypatch.setattr(dispatcher_module, "_instance_loop", asyncio.get_running_loop())
    monkeypatch.setattr(notify_cap, "QUOTA", notify_cap.NotifyQuota())

    async def set_rules(rules: dict[str, Any]) -> None:
        async with test_db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
                (json.dumps({"version": 1, **rules}),),
            )
        invalidate_cache()

    recorder.set_rules = set_rules  # type: ignore[attr-defined]
    recorder.dispatcher = dispatcher  # type: ignore[attr-defined]
    yield recorder
    invalidate_cache()


def instance(permission: dict[str, Any] | None = None, **extra: Any) -> SimpleNamespace:
    perms = {} if permission is None else {"notifications.send": permission}
    return SimpleNamespace(
        instance_id="11111111-2222-4333-8444-555555555555",
        plugin_id="acme.weather",
        label="Weather",
        permissions=perms,
        **extra,
    )


async def call(inst: SimpleNamespace, params: dict[str, Any], event: Any = None) -> Any:
    return await handle(Call(inst, "notifications.send", params, event))  # type: ignore[arg-type]


async def refusal(inst: SimpleNamespace, params: dict[str, Any]) -> CapabilityError:
    with pytest.raises(CapabilityError) as caught:
        await call(inst, params)
    return caught.value


# --- the permission -------------------------------------------------------------------


def test_the_permission_is_no_longer_reserved_and_takes_quota_params():
    assert "notifications.send" not in RESERVED_PERMISSIONS
    declared = Permissions.model_validate({"notifications.send": {"per_hour": 5}}).declared()
    assert declared == {"notifications.send": {"per_hour": 5}}
    assert Permissions.model_validate({"notifications.send": {}}).declared() == {
        "notifications.send": {}
    }
    for bad in ({"per_hour": 31}, {"per_day": 101}, {"per_hour": 0}, {"channels": ["x"]}):
        with pytest.raises(ValidationError):
            Permissions.model_validate({"notifications.send": bad})


def test_the_grant_narrows_the_quota_and_both_sides_are_needed():
    manifest = {"permissions": {"notifications.send": {"per_hour": 20, "per_day": 80}}}
    assert effective_permissions(manifest, {"notifications.send": {"per_hour": 4}}) == {
        "notifications.send": {"per_hour": 4, "per_day": 80}
    }
    assert effective_permissions(manifest, {}) == {}
    assert effective_permissions({"permissions": {}}, {"notifications.send": {}}) == {}


def test_registry_and_catalogue():
    assert REGISTRY["notifications.send"].permission == "notifications.send"
    assert "plugin.notification" in EVENT_IDS
    # Web Push and the WebSocket frame: administrators only.
    assert "plugin.notification" in INSTANCE_RULE_EVENTS
    assert admin_push_payload({"event": "plugin.notification"})
    assert _notification_event_audience({"event": "plugin.notification"}) == "admin"


# --- delivery ---------------------------------------------------------------------------


async def test_reaches_only_the_channels_ticked_for_the_event(wired):
    await wired.set_rules({"events": {"plugin.notification": {"channels": ["webhook-1"]}}})
    result = await call(
        instance({}), {"title": "Storm", "body": "Gusts to 90 km/h", "level": "warning"}
    )
    assert result == {"queued": 1}
    [(channel_id, name, payload, title, body)] = wired.sent
    assert (channel_id, name) == ("webhook-1", "plugin.notification")
    assert payload["instance_id"] == "11111111-2222-4333-8444-555555555555"
    assert payload["plugin_id"] == "acme.weather"
    # The plugin's label comes first: its text never reads as Meshloom's own.
    assert title.endswith("Weather: Storm") and body == "Gusts to 90 km/h"


async def test_nothing_is_sent_until_a_channel_is_ticked(wired):
    await wired.set_rules({})
    assert await call(instance({}), {"title": "Hello"}) == {"queued": 0}
    assert wired.sent == []


async def test_without_the_permission_it_is_refused_with_no_side_effect(wired):
    await wired.set_rules({"events": {"plugin.notification": {"channels": list(CHANNELS)}}})
    err = await refusal(instance(None), {"title": "Hello"})
    assert err.refusal == "permission_denied"
    assert err.params == {"permission": "notifications.send"}
    assert wired.sent == []


@pytest.mark.parametrize(
    ("params", "fields"),
    [
        ({}, ["title"]),
        ({"title": ""}, ["title"]),
        ({"title": " \t\x00 "}, ["title"]),
        ({"title": "x" * 101}, ["title"]),
        ({"title": "ok", "body": "x" * 1001}, ["body"]),
        ({"title": "ok", "level": "urgent"}, ["level"]),
        ({"title": "ok", "to": "ntfy-2"}, ["to"]),
        ({"title": 3}, ["title"]),
    ],
)
async def test_bad_params_are_invalid_and_use_no_quota(wired, params, fields):
    inst = instance({"per_hour": 1})
    err = await refusal(inst, params)
    assert err.refusal == "invalid_params"
    assert err.params == {"fields": fields}
    assert await call(inst, {"title": "still allowed"}) == {"queued": 0}


async def test_text_is_cleaned(wired):
    await wired.set_rules({"events": {"plugin.notification": {"channels": ["webhook-1"]}}})
    await call(instance({}), {"title": "a\nb\x1b[31m  c", "body": "line 1\r\nline\x07 2 "})
    payload = wired.sent[0][2]
    assert payload["title"] == "a b [31m c"
    assert payload["body"] == "line 1\nline 2"


async def test_quota_per_hour_then_per_day(wired):
    clock = [1_800_000_000.0 - 1_800_000_000.0 % 86400 + 10]
    notify_cap.QUOTA.clock = lambda: clock[0]
    inst = instance({"per_hour": 2, "per_day": 3})
    assert [await call(inst, {"title": str(i)}) for i in range(2)] == [{"queued": 0}] * 2
    err = await refusal(inst, {"title": "third"})
    assert err.refusal == "quota_exceeded"
    assert err.params == {"scope": "plugin", "period": "hour", "limit": 2, "retry_after": 3590}
    clock[0] += 3600
    assert await call(inst, {"title": "next hour"}) == {"queued": 0}
    err = await refusal(inst, {"title": "over the day"})
    assert err.params["period"] == "day" and err.params["limit"] == 3
    # Another instance has its own counters.
    other = instance({"per_hour": 2, "per_day": 3})
    other.instance_id = "other"
    assert await call(other, {"title": "mine"}) == {"queued": 0}


async def test_default_quota_and_caps():
    limits = notify_cap.effective_limits({})
    assert (limits.per_hour, limits.per_day) == (10, 50)
    limits = notify_cap.effective_limits({"per_hour": 999, "per_day": True})
    assert (limits.per_hour, limits.per_day) == (30, 50)


async def test_a_test_event_is_validated_but_never_sent_nor_counted(wired):
    await wired.set_rules({"events": {"plugin.notification": {"channels": ["webhook-1"]}}})
    inst = instance({"per_hour": 1})
    test_event = SimpleNamespace(test=True)
    for _ in range(3):
        assert await call(inst, {"title": "dry"}, test_event) == {"queued": 0, "test": True}
    with pytest.raises(CapabilityError):
        await call(inst, {"title": ""}, test_event)
    assert wired.sent == []
    assert await call(inst, {"title": "real"}) == {"queued": 1}


async def test_never_urgent_so_the_global_pause_holds_it(wired):
    await wired.set_rules(
        {
            "paused_until": int(time.time()) + 3600,
            "events": {"plugin.notification": {"channels": ["webhook-1"]}},
        }
    )
    assert await call(instance({}), {"title": "Fire", "level": "critical"}) == {"queued": 0}
    assert wired.sent == []
    await wired.set_rules(
        {
            "paused_until": int(time.time()) + 3600,
            "events": {"plugin.notification": {"channels": ["webhook-1"], "always_deliver": True}},
        }
    )
    assert await call(instance({}), {"title": "Fire", "level": "critical"}) == {"queued": 1}


# --- rendering ------------------------------------------------------------------------------

PAYLOAD = {
    "event": "plugin.notification",
    "instance_id": "i1",
    "plugin_id": "acme.weather",
    "label": "Weather",
    "title": "Storm",
    "body": "hi @[Base] and @everyone",
    "level": "critical",
}


def test_render_puts_the_label_first_and_neutralises_mentions():
    event = event_from_payload(PAYLOAD)
    assert isinstance(event, PluginNotificationEvent) and event.urgent is False
    assert event.to_payload() == PAYLOAD
    en, fr = render(event, "en"), render(event, "fr")
    assert en.title == "Critical · Weather: Storm"
    assert fr.title == "Critique · Weather: Storm"
    assert en.body_text == neutralise_mentions(PAYLOAD["body"]) != PAYLOAD["body"]
    assert en.url_hash == "#settings/extensions" and en.tag == "meshcore-plugin-i1"
    info = render(event_from_payload({**PAYLOAD, "level": "nope"}), "en")  # type: ignore[arg-type]
    assert info.title == "Weather: Storm"


def test_text_policy_never_keeps_the_title_only():
    event = event_from_payload(PAYLOAD)
    assert event is not None
    rendered = render(event, "en", text_policy="never")
    assert rendered.body_text == rendered.title
    assert webhook_v2.event_data(event, PAYLOAD, "never").body is None  # type: ignore[attr-defined]
    assert webhook_v2.event_data(event, PAYLOAD, "public_only").body == PAYLOAD["body"]  # type: ignore[attr-defined]


# --- through the real host ------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")
async def test_a_running_plugin_notifies_from_a_real_event(manager, tmp_path, wired):  # noqa: F811
    await wired.set_rules({"events": {"plugin.notification": {"channels": ["ntfy-2"]}}})
    read = {"channels": "all", "direct": True, "outgoing": False}
    inst = await probe(
        manager,
        tmp_path,
        "notifier",
        {"messages.read": read, "notifications.send": {"per_hour": 2}},
    )
    batch = json.dumps([["notifications.send", {"title": "Ping", "body": "from a DM"}]] * 3)
    manager.dispatch("message", fanout_dm(batch))
    await wait_until(lambda: lines(inst, "result"), what="the probe's result")
    (result,) = lines(inst, "result")
    assert result[:2] == [{"ok": {"queued": 1}}, {"ok": {"queued": 1}}]
    assert result[2]["error"] == "quota_exceeded"
    await wired.dispatcher.wait_idle()
    assert [(s[0], s[3]) for s in wired.sent] == [("ntfy-2", "notifier: Ping")] * 2

    denied = await probe(manager, tmp_path, "silent", {"messages.read": read})
    manager.dispatch("message", fanout_dm(json.dumps([["notifications.send", {"title": "x"}]])))
    await wait_until(lambda: lines(denied, "result"), what="the refusal")
    assert lines(denied, "result")[0] == [
        {"error": "permission_denied", "params": {"permission": "notifications.send"}}
    ]
