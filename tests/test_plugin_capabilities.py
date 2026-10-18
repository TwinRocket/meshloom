"""The capability API v1 (`app/plugins/capabilities/`) through the plugin host.

Every instance runs the real runner on the `probe` fixture plugin: a message
whose text is a JSON list of `[method, params]` makes it call the host and
report each answer (`result <json>` on stderr for real events, the event result
for test events). Radio sends are replaced by a recorder (`_transmit`); quota
windows run on a controlled clock.
"""

import asyncio
import json
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from app.channel_constants import PUBLIC_CHANNEL_KEY
from app.data.meshcore_channels import hashtag_key_from_name
from app.plugins import host as host_module
from app.plugins.capabilities import REGISTRY, messages
from app.plugins.capabilities.grants import (
    effective_permissions,
    message_visible,
    raw_packet_for,
)
from app.plugins.host import Budgets, PluginManager
from app.plugins.quotas import (
    DEFAULT_PER_DAY,
    DEFAULT_PER_HOUR,
    QuotaExceeded,
    SendLimits,
    SendQuota,
    effective_send_limits,
    send_quota,
    send_spacer,
)
from app.repository import AppSettingsRepository, ChannelRepository, ContactRepository
from app.repository.plugins import KV_QUOTA_BYTES, PluginRepository

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

BUDGETS = Budgets(
    init_timeout=10.0,
    event_timeout=20.0,
    cancel_grace=1.0,
    shutdown_grace=1.0,
    backoff_initial=0.05,
    backoff_max=0.2,
    stagger=0.05,
    message_settle=0.0,
)

PROBE = """
    import json
    import sys

    import meshloom_plugin
    from meshloom_plugin.errors import HostError

    INIT = {}

    def say(*parts):
        print(*parts, file=sys.stderr, flush=True)

    async def on_init(params):
        INIT.update(params)
        say("init", json.dumps({k: params.get(k) for k in ("lang", "isolation", "granted")}))

    async def run(calls, event_id):
        out = []
        for method, p in calls:
            if isinstance(p, dict) and event_id is not None and method == "messages.send":
                p = {**p, "event_id": event_id}
            try:
                out.append({"ok": await meshloom_plugin.host().call(method, p)})
            except HostError as exc:
                data = exc.error.get("data") or {}
                out.append({"error": data.get("code") or exc.error.get("code"),
                            "params": data.get("params")})
        return out

    async def on_message(params):
        text = params.get("message_text") or ""
        say("got", json.dumps({k: params.get(k) for k in
            ("conversation_key", "is_dm", "is_outgoing", "own_send", "message_text")}))
        if text.startswith("["):
            out = await run(json.loads(text), params.get("event_id"))
            say("result", json.dumps(out))
            return out

    async def on_timer(params):
        say("timer", json.dumps(params))

    def on_config_changed(params):
        pass
"""

ANN = "ab" * 32
BOB = "cd" * 32
PUBLIC_TAG = "#test"
PUBLIC_TAG_KEY = hashtag_key_from_name(PUBLIC_TAG).hex().upper()
OTHER_TAG = "#other"
OTHER_TAG_KEY = hashtag_key_from_name(OTHER_TAG).hex().upper()
SECRET_KEY = "11" * 16

READ_ALL = {"channels": "all", "direct": True, "outgoing": False}


@pytest.fixture
def statuses(monkeypatch):
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(host_module, "_broadcast_status", lambda payload: seen.append(payload))
    return seen


@pytest.fixture
async def manager(test_db, tmp_path, statuses, monkeypatch):
    mgr = PluginManager(
        BUDGETS,
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    monkeypatch.setattr(host_module, "plugin_manager", mgr)
    monkeypatch.setattr(send_spacer, "spacing", 0.0)
    monkeypatch.setattr(send_spacer, "last_send", None)
    await ContactRepository.upsert({"public_key": ANN, "name": "Ann"})
    await ContactRepository.upsert({"public_key": BOB, "name": "Bob"})
    await ChannelRepository.upsert(PUBLIC_TAG_KEY, PUBLIC_TAG, is_hashtag=True)
    await ChannelRepository.upsert(OTHER_TAG_KEY, OTHER_TAG, is_hashtag=True)
    await ChannelRepository.upsert(SECRET_KEY, "Secret")
    yield mgr
    await mgr.stop_all(5.0)


@pytest.fixture
def sent(monkeypatch):
    """Replace the radio send: record `(instance, target, text, region, time)` and
    hand the outgoing message to the host as the real send path would."""
    log: list[dict[str, Any]] = []

    async def transmit(target, text, region):
        log.append(
            {
                "instance": messages.SENDING_INSTANCE.get(),
                "is_dm": target.is_dm,
                "key": target.key,
                "text": text,
                "region": region,
                "at": time.monotonic(),
            }
        )
        host_module.plugin_manager.dispatch(
            "message",
            {
                "type": "PRIV" if target.is_dm else "CHAN",
                "conversation_key": target.key,
                "text": text,
                "outgoing": True,
                "sender_name": "Me",
            },
        )
        return type("Sent", (), {"id": len(log)})()

    monkeypatch.setattr(messages, "_transmit", transmit)
    return log


def write_probe(root: Path, plugin_id: str) -> None:
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text('{"entry": "plugin.py"}', encoding="utf-8")
    (directory / "plugin.py").write_text(textwrap.dedent(PROBE), encoding="utf-8")


async def probe(
    mgr: PluginManager,
    tmp_path: Path,
    name: str,
    permissions: dict[str, Any],
    *,
    granted: dict[str, Any] | None = None,
    **kwargs: Any,
):
    plugin_id = f"test.{name}"
    write_probe(tmp_path / "plugins", plugin_id)
    row = await PluginRepository.create(
        plugin_id=plugin_id,
        label=name,
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": ["message", "timer"], "permissions": permissions},
        granted_permissions=permissions if granted is None else granted,
        enabled=True,
        **kwargs,
    )
    await mgr.start(row["instance_id"])
    instance = mgr.get(row["instance_id"])
    assert instance is not None
    await wait_until(lambda: instance.status == "running", what="running")
    return instance


async def wait_until(predicate, timeout: float = 15.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def dm_event(text: str, key: str = ANN) -> dict[str, Any]:
    """`event.message` params of a direct message from `key` (what a test event carries)."""
    return {"is_dm": True, "sender_key": key, "conversation_key": key, "message_text": text}


def calls(*items: tuple[str, Any]) -> str:
    return json.dumps([list(item) for item in items])


async def run_test(mgr, instance, *items: tuple[str, Any], event=None) -> list[dict[str, Any]]:
    """Run `items` from a test event (a DM from Ann unless `event` says otherwise)."""
    params = {**(event or dm_event("")), "message_text": calls(*items)}
    outcome = await mgr.dispatch_test(instance.instance_id, "message", params, timeout=30)
    return outcome["result"]


def fanout_dm(text: str, key: str = ANN, **extra: Any) -> dict[str, Any]:
    return {"type": "PRIV", "conversation_key": key, "sender_name": "Ann", "text": text, **extra}


def fanout_chan(text: str, key: str, name: str) -> dict[str, Any]:
    return {
        "type": "CHAN",
        "conversation_key": key,
        "channel_name": name,
        "sender_name": "Bob",
        "text": f"Bob: {text}",
    }


def lines(instance, prefix: str) -> list[Any]:
    return [
        json.loads(line[len(prefix) + 1 :])
        for line in instance.log.lines()
        if line.startswith(prefix + " ")
    ]


def received(instance) -> list[str]:
    return [g["message_text"] for g in lines(instance, "got")]


# --- permission checks ------------------------------------------------------------


async def test_every_method_is_refused_without_its_permission(manager, tmp_path):
    instance = await probe(manager, tmp_path, "bare", {"messages.read": READ_ALL})
    result = await run_test(
        manager,
        instance,
        ("messages.send", {"to": "reply", "text": "hi"}),
        ("storage.get", {"key": "a"}),
        ("timer.set", {"seconds": 60}),
        ("contacts.list", {}),
        ("contacts.get", {"key": ANN}),
        ("channels.list", {}),
        ("status.set", {"summary": "ok"}),
        ("no.such", {}),
    )
    assert [r.get("error") for r in result] == [
        "permission_denied",
        "permission_denied",
        "permission_denied",
        "permission_denied",
        "permission_denied",
        "permission_denied",
        None,
        -32601,
    ]
    assert result[0]["params"] == {"permission": "messages.send"}


async def test_a_permission_must_be_declared_and_granted(manager, tmp_path):
    declared = {"messages.read": READ_ALL, "storage": {}}
    instance = await probe(
        manager, tmp_path, "ungranted", declared, granted={"messages.read": READ_ALL, "timer": {}}
    )
    assert set(instance.permissions) == {"messages.read"}
    result = await run_test(
        manager, instance, ("storage.get", {"key": "a"}), ("timer.set", {"seconds": 60})
    )
    assert [r["error"] for r in result] == ["permission_denied", "permission_denied"]


def test_effective_permissions_narrow_the_grant():
    declared = {
        "messages.read": {"channels": "public", "direct": True},
        "messages.send": {"targets": "scope", "per_hour": 50},
        "http": {"hosts": ["api.example.org"]},
    }
    granted = {
        "messages.read": {"channels": "all", "direct": True, "outgoing": True},
        "messages.send": {"targets": "any", "per_hour": 10},
        "http": {"hosts": ["api.example.org", "evil.example.com"]},
        "storage": {},
    }
    assert effective_permissions({"permissions": declared}, granted) == {
        "messages.read": {"channels": "public", "direct": True, "outgoing": False},
        "messages.send": {"targets": "scope", "per_hour": 10},
        "http": {"hosts": ["api.example.org"]},
    }
    # A malformed grant is dropped, never widened.
    assert (
        effective_permissions(
            {"permissions": declared}, {"messages.send": {"targets": "everything"}}
        )
        == {}
    )
    assert effective_permissions({}, granted) == {}
    assert effective_permissions({"permissions": declared}, None) == {}


# --- messages.send ----------------------------------------------------------------


async def test_the_31st_send_in_an_hour_is_quota_exceeded(manager, tmp_path, sent, monkeypatch):
    monkeypatch.setattr(send_quota, "clock", lambda: 1_000_000.0)
    instance = await probe(
        manager,
        tmp_path,
        "quota",
        {"messages.read": READ_ALL, "messages.send": {"targets": "reply"}},
    )
    burst = calls(*[("messages.send", {"to": "reply", "text": f"n{n}"}) for n in range(31)])
    manager.dispatch("message", fanout_dm(burst))
    await wait_until(lambda: lines(instance, "result"), what="the burst result")
    (result,) = lines(instance, "result")
    assert [r.get("ok", {}).get("sent") for r in result[:30]] == [True] * 30
    assert result[30]["error"] == "quota_exceeded"
    assert result[30]["params"]["period"] == "hour"
    assert result[30]["params"]["limit"] == DEFAULT_PER_HOUR
    assert 0 < result[30]["params"]["retry_after"] <= 3600
    assert len(sent) == 30
    assert {s["instance"] for s in sent} == {instance.instance_id}


async def test_quota_counters_survive_a_restart_and_follow_the_window(test_db):
    now = [7200.0 * 100 + 10]
    quota = SendQuota(clock=lambda: now[0])
    limits = SendLimits(per_hour=2, per_day=3)
    for _ in range(2):
        await quota.check("i1", limits)
        await quota.record("i1")
    with pytest.raises(QuotaExceeded) as hour:
        await quota.check("i1", limits)
    assert (hour.value.period, hour.value.limit, hour.value.retry_after) == ("hour", 2, 3590)

    restarted = SendQuota(clock=lambda: now[0])  # a new process reads the same rows
    with pytest.raises(QuotaExceeded):
        await restarted.check("i1", limits)
    await restarted.check("i2", limits)  # per instance

    now[0] += 3600  # next hour: one more fits, then the day is full
    await restarted.check("i1", limits)
    await restarted.record("i1")
    with pytest.raises(QuotaExceeded) as day:
        await restarted.check("i1", limits)
    assert day.value.period == "day"
    assert await restarted.counts("i1") == {"hour": 1, "day": 3}

    await restarted.forget("i1")
    assert await restarted.counts("i1") == {"hour": 0, "day": 0}


def test_send_limits_default_cap_and_user_lowering():
    assert effective_send_limits({}, None) == SendLimits(DEFAULT_PER_HOUR, DEFAULT_PER_DAY)
    assert effective_send_limits({"per_hour": 60, "per_day": 500}, {}) == SendLimits(60, 500)
    assert effective_send_limits({"per_hour": 999}, {}) == SendLimits(60, DEFAULT_PER_DAY)
    user = {"messages.send": {"per_hour": 5, "per_day": 9999}}
    assert effective_send_limits({"per_hour": 50}, user) == SendLimits(5, DEFAULT_PER_DAY)
    # The user can lower, never raise.
    assert effective_send_limits({}, {"messages.send": {"per_hour": 59}}).per_hour == 30


async def test_targets_reply_allows_only_the_conversation_of_the_event(manager, tmp_path):
    instance = await probe(
        manager, tmp_path, "reply", {"messages.read": READ_ALL, "messages.send": {}}
    )
    result = await run_test(
        manager,
        instance,
        ("messages.send", {"to": "reply", "text": "a"}),
        ("messages.send", {"to": {"contact_key": ANN}, "text": "b"}),
        ("messages.send", {"to": {"contact_key": BOB}, "text": "c"}),
        ("messages.send", {"to": {"channel_key": PUBLIC_TAG_KEY}, "text": "d"}),
    )
    assert [r.get("ok") or r["error"] for r in result] == [
        {"captured": True},
        {"captured": True},
        "target_not_allowed",
        "target_not_allowed",
    ]
    assert result[2]["params"] == {"targets": "reply"}


async def test_reply_without_a_message_event_is_not_allowed(manager, tmp_path):
    instance = await probe(
        manager, tmp_path, "noevent", {"messages.read": READ_ALL, "messages.send": {}}
    )
    outcome = await manager.dispatch_test(
        instance.instance_id,
        "timer",
        {"x": 1},
    )
    assert outcome["result"] is None  # on_timer only logs
    call = messages.Call(instance, "messages.send", {"to": "reply", "text": "x"}, None)
    with pytest.raises(messages.CapabilityError) as exc:
        await messages.send(call)
    assert exc.value.refusal == "target_not_allowed"


async def test_targets_scope_allows_the_read_scope_only(manager, tmp_path):
    instance = await probe(
        manager,
        tmp_path,
        "scope",
        {
            "messages.read": {"channels": "public", "direct": True},
            "messages.send": {"targets": "scope"},
        },
        scope={"messages": {"channels": "all", "contacts": [ANN]}},
    )
    result = await run_test(
        manager,
        instance,
        ("messages.send", {"to": {"channel_key": PUBLIC_TAG_KEY}, "text": "public"}),
        ("messages.send", {"to": {"channel_key": PUBLIC_CHANNEL_KEY.lower()}, "text": "Public"}),
        ("messages.send", {"to": {"channel_key": SECRET_KEY}, "text": "private"}),
        ("messages.send", {"to": {"contact_key": ANN}, "text": "in scope"}),
        ("messages.send", {"to": {"contact_key": BOB}, "text": "out of scope"}),
        event=dm_event("", key=BOB),  # the event's own conversation passes
    )
    assert [r.get("ok") or r["error"] for r in result] == [
        {"captured": True},
        {"captured": True},
        "target_not_allowed",
        {"captured": True},
        {"captured": True},
    ]


async def test_targets_any_passes_every_known_destination(manager, tmp_path):
    instance = await probe(
        manager, tmp_path, "any", {"messages.read": READ_ALL, "messages.send": {"targets": "any"}}
    )
    result = await run_test(
        manager,
        instance,
        ("messages.send", {"to": {"channel_key": SECRET_KEY}, "text": "a"}),
        ("messages.send", {"to": {"contact_key": BOB}, "text": "b"}),
    )
    assert [r.get("ok") for r in result] == [{"captured": True}] * 2


async def test_unknown_destinations_and_bad_params_are_refused(manager, tmp_path, sent):
    instance = await probe(
        manager, tmp_path, "bad", {"messages.read": READ_ALL, "messages.send": {"targets": "any"}}
    )
    batch = calls(
        ("messages.send", {"to": {"contact_key": "ef" * 32}, "text": "who"}),
        ("messages.send", {"to": {"contact_key": "ef"}, "text": "short"}),
        ("messages.send", {"to": "reply"}),
        ("messages.send", {"to": "reply", "text": "x", "extra": 1}),
    )
    manager.dispatch("message", fanout_dm(batch))
    await wait_until(lambda: lines(instance, "result"))
    (result,) = lines(instance, "result")
    assert [r["error"] for r in result] == [
        "target_unknown",
        "invalid_params",
        "invalid_params",
        "invalid_params",
    ]
    assert result[2]["params"] == {"fields": ["text"]}
    assert sent == []


async def test_region_must_exist(manager, tmp_path, sent):
    await AppSettingsRepository.update(known_regions=["Esperance"])
    instance = await probe(
        manager, tmp_path, "region", {"messages.read": READ_ALL, "messages.send": {}}
    )
    batch = calls(
        ("messages.send", {"to": "reply", "text": "a", "region": "Nowhere"}),
        ("messages.send", {"to": "reply", "text": "b", "region": "Esperance"}),
        ("messages.send", {"to": "reply", "text": "c", "region": "#Esperance"}),
        ("messages.send", {"to": "reply", "text": "d", "region": None}),
        ("messages.send", {"to": "reply", "text": "e"}),
    )
    manager.dispatch("message", fanout_chan(batch, PUBLIC_TAG_KEY, PUBLIC_TAG))
    await wait_until(lambda: lines(instance, "result"))
    (result,) = lines(instance, "result")
    assert result[0]["error"] == "region_unknown"
    assert [r.get("ok", {}).get("sent") for r in result[1:]] == [True] * 4
    # Absent keeps the channel's default (None), null sends unscoped ("").
    assert [s["region"] for s in sent] == ["Esperance", "#Esperance", "", None]


async def test_message_too_long_carries_the_limit(manager, tmp_path):
    instance = await probe(
        manager, tmp_path, "long", {"messages.read": READ_ALL, "messages.send": {}}
    )
    result = await run_test(
        manager, instance, ("messages.send", {"to": "reply", "text": "é" * 100})
    )
    assert result[0]["error"] == "message_too_long"
    assert result[0]["params"] == {"limit": 156, "bytes": 200}


async def test_test_events_capture_without_using_the_quota(manager, tmp_path, sent):
    instance = await probe(
        manager, tmp_path, "capture", {"messages.read": READ_ALL, "messages.send": {}}
    )
    result = await run_test(manager, instance, ("messages.send", {"to": "reply", "text": "x"}))
    assert result == [{"ok": {"captured": True}}]
    assert sent == []
    assert await send_quota.counts(instance.instance_id) == {"hour": 0, "day": 0}


async def test_two_instances_sending_at_once_are_spaced_by_two_seconds(
    manager, tmp_path, sent, monkeypatch
):
    monkeypatch.setattr(send_spacer, "spacing", 2.0)
    permissions = {"messages.read": READ_ALL, "messages.send": {}}
    first = await probe(manager, tmp_path, "first", permissions)
    second = await probe(manager, tmp_path, "second", permissions)
    manager.dispatch("message", fanout_dm(calls(("messages.send", {"to": "reply", "text": "x"}))))
    await wait_until(lambda: lines(first, "result") and lines(second, "result"), timeout=20)
    assert len(sent) == 2
    assert {s["instance"] for s in sent} == {first.instance_id, second.instance_id}
    assert sent[1]["at"] - sent[0]["at"] >= 1.95


# --- reads ------------------------------------------------------------------------


async def test_public_channels_only_never_see_private_channels_or_dms(manager, tmp_path):
    instance = await probe(manager, tmp_path, "public", {"messages.read": {}})
    manager.dispatch("message", fanout_dm("dm"))
    manager.dispatch("message", fanout_chan("secret", SECRET_KEY, "Secret"))
    manager.dispatch("message", fanout_chan("disguised", SECRET_KEY, "#test"))
    manager.dispatch("message", fanout_chan("public", PUBLIC_TAG_KEY, PUBLIC_TAG))
    manager.dispatch("message", fanout_chan("Public", PUBLIC_CHANNEL_KEY, "Public"))
    await wait_until(lambda: {"public", "Public"} <= set(received(instance)))
    await asyncio.sleep(0.2)
    assert sorted(received(instance)) == ["Public", "public"]


async def test_the_user_scope_restricts_on_top(manager, tmp_path):
    instance = await probe(
        manager,
        tmp_path,
        "scoped",
        {"messages.read": {"channels": "public"}},
        scope={"messages": {"channels": [PUBLIC_TAG_KEY.lower()], "contacts": "none"}},
    )
    manager.dispatch("message", fanout_chan("other", OTHER_TAG_KEY, OTHER_TAG))
    manager.dispatch("message", fanout_chan("mine", PUBLIC_TAG_KEY, PUBLIC_TAG))
    await wait_until(lambda: "mine" in received(instance))
    await asyncio.sleep(0.2)
    assert received(instance) == ["mine"]


def test_message_visibility_rules():
    chan = {"is_dm": False, "conversation_key": SECRET_KEY, "is_public_channel": False}
    dm = {"is_dm": True, "conversation_key": ANN, "is_public_channel": False}
    out = {**dm, "is_outgoing": True}
    read = {"messages.read": {"channels": "public", "direct": False, "outgoing": False}}
    assert not message_visible({}, None, chan)
    assert not message_visible(read, None, chan)
    assert not message_visible(read, None, dm)
    full = {"messages.read": {"channels": "all", "direct": True, "outgoing": False}}
    assert message_visible(full, None, chan) and message_visible(full, None, dm)
    assert not message_visible(full, None, out)
    assert not message_visible(full, {"messages": "none"}, dm)
    assert message_visible(full, {"messages": {"contacts": {"except": [BOB]}}}, dm)
    assert not message_visible(full, {"messages": {"contacts": [BOB]}}, dm)


async def test_own_sends_come_back_only_with_outgoing(manager, tmp_path, sent):
    quiet = await probe(
        manager, tmp_path, "quiet", {"messages.read": READ_ALL, "messages.send": {}}
    )
    loud = await probe(
        manager,
        tmp_path,
        "loud",
        {"messages.read": {**READ_ALL, "outgoing": True}, "messages.send": {}},
    )
    batch = calls(("messages.send", {"to": "reply", "text": "echo-me"}))
    manager.dispatch("message", fanout_dm(batch))
    await wait_until(lambda: len(sent) == 2)
    await wait_until(lambda: len([g for g in lines(loud, "got") if g["is_outgoing"]]) == 2)
    await asyncio.sleep(0.2)
    assert not [g for g in lines(quiet, "got") if g["is_outgoing"]]
    outgoing = [g for g in lines(loud, "got") if g["is_outgoing"]]
    # Its own send is marked; the other instance's is not.
    assert sorted(bool(g["own_send"]) for g in outgoing) == [False, True]


async def test_hooks_are_gated_by_their_permissions(manager, tmp_path):
    instance = await probe(manager, tmp_path, "gates", {"messages.read": READ_ALL})
    for hook in ("raw_packet", "contact", "telemetry", "health"):
        assert not manager.accepts(instance, hook, {})
    instance.permissions.update({"raw_packets.read": {}, "contacts.read": {}, "telemetry.read": {}})
    for hook in ("raw_packet", "contact", "telemetry", "health"):
        assert manager.accepts(instance, hook, {})
    instance.scope = {"raw_packets": "none"}
    assert not manager.accepts(instance, "raw_packet", {})


def test_raw_packets_keep_their_decryption_only_when_readable():
    dm_packet = {
        "payload_type": "TEXT_MESSAGE",
        "decrypted": True,
        "decrypted_info": {"contact_key": ANN, "channel_key": None},
    }
    chan_packet = {
        "payload_type": "GROUP_TEXT",
        "decrypted": True,
        "decrypted_info": {"channel_key": SECRET_KEY, "channel_name": "Secret"},
    }
    public_packet = {
        "payload_type": "GROUP_TEXT",
        "decrypted": True,
        "decrypted_info": {"channel_key": PUBLIC_TAG_KEY, "channel_name": PUBLIC_TAG},
    }
    raw_only = {"raw_packets.read": {}}
    public = {**raw_only, "messages.read": {"channels": "public", "direct": False}}
    full = {**raw_only, "messages.read": {"channels": "all", "direct": True}}
    for packet in (dm_packet, chan_packet, public_packet):
        assert raw_packet_for(raw_only, None, packet)["decrypted_info"] is None
        assert raw_packet_for(full, None, packet) is packet
    assert raw_packet_for(public, None, public_packet) is public_packet
    assert raw_packet_for(public, None, chan_packet)["decrypted"] is False
    assert raw_packet_for(public, None, dm_packet)["decrypted_info"] is None


# --- channels, contacts -----------------------------------------------------------


async def test_channels_list_needs_channels_read_and_hides_private_keys(manager, tmp_path):
    public_only = await probe(
        manager, tmp_path, "chans", {"messages.read": READ_ALL | {"channels": "public"}}
    )
    result = await run_test(manager, public_only, ("channels.list", {}))
    assert result[0]["error"] == "permission_denied"

    reader = await probe(
        manager,
        tmp_path,
        "chans2",
        {"messages.read": {"channels": "public"}, "channels.read": {}},
    )
    listed = (await run_test(manager, reader, ("channels.list", {})))[0]["ok"]
    assert {PUBLIC_TAG_KEY, OTHER_TAG_KEY} <= {c["key"] for c in listed}
    assert SECRET_KEY not in {c["key"] for c in listed}
    assert all(c["is_public"] for c in listed)

    everything = await probe(
        manager, tmp_path, "chans3", {"messages.read": READ_ALL, "channels.read": {}}
    )
    listed = (await run_test(manager, everything, ("channels.list", {})))[0]["ok"]
    assert {c["key"]: c["is_public"] for c in listed}[SECRET_KEY] is False


async def test_contacts_are_a_read_only_projection(manager, tmp_path):
    instance = await probe(manager, tmp_path, "contacts", {"contacts.read": {}})
    listed, one, missing, bad = (
        r.get("ok", r.get("error"))
        for r in await run_test(
            manager,
            instance,
            ("contacts.list", {"limit": 10}),
            ("contacts.get", {"key": ANN.upper()}),
            ("contacts.get", {"key": "ef" * 32}),
            ("contacts.get", {"key": "ab"}),
        )
    )
    assert {c["public_key"] for c in listed} == {ANN, BOB}
    assert set(one) == {
        "public_key",
        "name",
        "type",
        "last_seen",
        "last_advert",
        "lat",
        "lon",
        "favorite",
    }
    assert one["name"] == "Ann"
    assert missing is None
    assert bad == "invalid_params"


# --- storage, timer, status, log --------------------------------------------------


async def test_storage_round_trip_and_quota(manager, tmp_path, monkeypatch):
    assert KV_QUOTA_BYTES == 5 * 1024 * 1024
    instance = await probe(manager, tmp_path, "kv", {"storage": {}})
    result = await run_test(
        manager,
        instance,
        ("storage.set", {"key": "a/1", "value": {"n": 1}}),
        ("storage.set", {"key": "a/2", "value": [1, 2]}),
        ("storage.set", {"key": "b", "value": "x"}),
        ("storage.get", {"key": "a/1"}),
        ("storage.get", {"key": "nope"}),
        ("storage.list", {"prefix": "a/"}),
        ("storage.list", {"after": "a/1", "limit": 1}),
        ("storage.delete", {"key": "b"}),
        ("storage.list", {}),
        ("storage.set", {"key": "", "value": 1}),
        ("storage.set", {"key": "k"}),
        event={"message_text": "x"},
    )
    assert [r.get("ok", r.get("error")) for r in result] == [
        None,
        None,
        None,
        {"n": 1},
        None,
        ["a/1", "a/2"],
        ["a/2"],
        None,
        ["a/1", "a/2"],
        "invalid_params",
        "invalid_params",
    ]

    # The 5 MiB quota, scaled down so the test writes little.
    monkeypatch.setattr("app.repository.plugins.KV_QUOTA_BYTES", 64)
    result = await run_test(
        manager,
        instance,
        ("storage.set", {"key": "big", "value": "y" * 100}),
        event={"message_text": "x"},
    )
    assert result[0]["error"] == "quota_exceeded"
    assert result[0]["params"] == {"limit": 64}  # the repository's quota, here scaled


async def test_timer_minimum_and_firing(manager, tmp_path):
    instance = await probe(manager, tmp_path, "timer", {"timer": {}})
    result = await run_test(
        manager,
        instance,
        ("timer.set", {"seconds": 5}),
        ("timer.set", {"seconds": True}),
        ("timer.set", {"seconds": 10}),
        event={"message_text": "x"},
    )
    assert result[0]["error"] == "invalid_params"
    assert result[0]["params"]["min"] == 10
    assert result[1]["error"] == "invalid_params"
    assert result[2]["ok"] == {"timer_id": 1}

    # A short timer armed directly reaches on_timer.
    timer_id = instance.add_timer(0.05, 16)
    await wait_until(lambda: any(t["timer_id"] == timer_id for t in lines(instance, "timer")))
    assert len(instance._timers) == 1  # the 10 s one is still pending
    await manager.stop(instance.instance_id)
    assert instance._timers == {}


async def test_status_and_log(manager, tmp_path, statuses):
    instance = await probe(manager, tmp_path, "status", {})
    result = await run_test(
        manager,
        instance,
        ("status.set", {"summary": "line\none\x1b[31m" + "z" * 300}),
        ("status.set", {"summary": "again"}),
        ("log", {"level": "warning", "message": "hello\nworld"}),
        event={"message_text": "x"},
    )
    assert result[0] == {"ok": None}
    assert result[1]["error"] == "quota_exceeded"
    assert instance.summary is not None and instance.summary.startswith("line one [31m")
    assert len(instance.summary) == 140
    assert statuses[-1]["summary"] == instance.summary
    assert "[plugin warning] hello world" in instance.log.lines()

    instance._on_child_notification("log", {"level": "nope", "message": "via notify"})
    assert instance.log.lines()[-1] == "[plugin info] via notify"


async def test_init_carries_language_isolation_and_effective_grants(manager, tmp_path):
    instance = await probe(
        manager,
        tmp_path,
        "init",
        {"storage": {}, "timer": {}},
        granted={"storage": {}},
        config={"language": "fr"},
    )
    await wait_until(lambda: lines(instance, "init"))
    assert lines(instance, "init")[0] == {"lang": "fr", "isolation": "none", "granted": ["storage"]}

    other = await probe(manager, tmp_path, "init2", {}, config={"language": "de"})
    await wait_until(lambda: lines(other, "init"))
    assert lines(other, "init")[0]["lang"] == "en"


def test_registry_lists_the_v1_methods():
    assert {name: entry.permission for name, entry in REGISTRY.items()} == {
        "messages.send": "messages.send",
        "storage.get": "storage",
        "storage.set": "storage",
        "storage.delete": "storage",
        "storage.list": "storage",
        "timer.set": "timer",
        "contacts.list": "contacts.read",
        "contacts.get": "contacts.read",
        "channels.list": "channels.read",
        "http.fetch": "http",
        "status.set": None,
        "log": None,
    }


# --- user-facing wording ----------------------------------------------------------

SLICES = Path(__file__).resolve().parent.parent / "frontend" / "src" / "i18n" / "locales" / "slices"


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_every_permission_has_its_wording(lang):
    from app.plugins.permissions import Permissions

    wording = json.loads((SLICES / f"al.{lang}.json").read_text(encoding="utf-8"))["plugins"]
    permissions = wording["permissions"]
    names = [f.alias or name for name, f in Permissions.model_fields.items()]
    for name in names:
        entry = permissions[name.replace(".", "_")]
        assert entry["title"] and entry["description"], name
    read = permissions["messages_read"]
    assert read["channels_public"] and read["channels_all"] and read["direct"] and read["outgoing"]
    send = permissions["messages_send"]
    assert send["targets_reply"] and send["targets_scope"] and send["targets_any"]
    assert send["quota"]
    for code in (
        "permission_denied",
        "quota_exceeded",
        "target_not_allowed",
        "target_unknown",
        "region_unknown",
        "message_too_long",
        "send_failed",
        "invalid_params",
    ):
        assert wording["capabilityErrors"][code], code


async def test_reply_and_scope_do_not_reveal_which_contacts_exist(manager, tmp_path, sent):
    for name, targets in (("probe-reply", "reply"), ("probe-scope", "scope")):
        instance = await probe(
            manager,
            tmp_path,
            name,
            {"messages.read": READ_ALL, "messages.send": {"targets": targets}},
            scope={"messages": {"channels": "none", "contacts": [ANN]}},
        )
        batch = calls(
            ("messages.send", {"to": {"contact_key": BOB}, "text": "known"}),
            ("messages.send", {"to": {"contact_key": "ef" * 32}, "text": "unknown"}),
            ("messages.send", {"to": {"channel_key": "22" * 16}, "text": "unknown channel"}),
        )
        manager.dispatch("message", fanout_dm(batch))
        await wait_until(lambda instance=instance: lines(instance, "result"))
        (result,) = lines(instance, "result")
        assert result == [{"error": "target_not_allowed", "params": {"targets": targets}}] * 3, (
            targets
        )
    assert sent == []


async def test_a_failed_count_still_moves_the_spacing_and_frees_the_slot(
    manager, tmp_path, sent, monkeypatch
):
    async def broken_record(_instance_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(send_quota, "record", broken_record)
    instance = await probe(
        manager, tmp_path, "norecord", {"messages.read": READ_ALL, "messages.send": {}}
    )
    batch = calls(
        ("messages.send", {"to": "reply", "text": "one"}),
        ("messages.send", {"to": "reply", "text": "two"}),
    )
    manager.dispatch("message", fanout_dm(batch))
    await wait_until(lambda: lines(instance, "result"))
    (result,) = lines(instance, "result")
    assert [r["ok"]["sent"] for r in result] == [True, True]  # the slot was released
    assert len(sent) == 2
    assert send_spacer.last_send is not None


async def test_any_send_error_is_send_failed(manager, tmp_path, monkeypatch):
    async def explode(target, text, region):
        raise RuntimeError("serial port vanished")

    monkeypatch.setattr(messages, "_transmit", explode)
    instance = await probe(
        manager, tmp_path, "explode", {"messages.read": READ_ALL, "messages.send": {}}
    )
    manager.dispatch("message", fanout_dm(calls(("messages.send", {"to": "reply", "text": "x"}))))
    await wait_until(lambda: lines(instance, "result"))
    assert lines(instance, "result") == [[{"error": "send_failed", "params": None}]]
    assert send_spacer.last_send is None  # nothing went out: the spacing does not move
    assert await send_quota.counts(instance.instance_id) == {"hour": 0, "day": 0}
