"""The host-side send guards (`app/plugins/quotas.py`, `SendGuard`): the Meshloom-wide
budget, the per-destination quotas, the sender cooldown and the quiet window, on a
controlled clock; plus the two-host simulation of two echo-like plugins."""

import pytest

from app.plugins import quotas
from app.plugins.quotas import (
    GUARD_LIMITS,
    GuardLimits,
    GuardRefused,
    SendGuard,
    destination_subject,
    effective_guard_limits,
)
from app.repository.plugins import PluginRepository, PluginSendGuardRepository

HOUR = 3600
T0 = 1_000 * HOUR  # a clock hour boundary
CHANNEL = "AB" * 16
CONTACT = "cd" * 32


class Clock:
    def __init__(self, now: float = T0 + 10) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


LONG_AGO = int(T0) - 100  # a message that arrived well before any send of the tests


async def send(
    guard: SendGuard, instance="i1", *, is_dm=False, key=CHANNEL, sender=None, at=LONG_AGO
):
    await guard.check(instance, is_dm=is_dm, key=key, sender=sender, received_at=at)
    await guard.record(instance, is_dm=is_dm, key=key, sender=sender)


async def refusal(guard: SendGuard, instance="i1", **kwargs) -> GuardRefused:
    with pytest.raises(GuardRefused) as caught:
        await send(guard, instance, **kwargs)
    return caught.value


# --- limits -----------------------------------------------------------------------


def test_defaults_and_caps_are_the_ticket_values():
    assert GUARD_LIMITS == {
        "instance_per_hour": (120, 300),
        "channel_per_hour": (20, 60),
        "contact_per_hour": (10, 30),
    }
    assert effective_guard_limits(None) == GuardLimits(120, 20, 10)


def test_stored_limits_only_go_down():
    stored = {"instance_per_hour": 50, "channel_per_hour": 200, "contact_per_hour": 0}
    # 200 is above the default (and the cap), 0 is not a limit: both read as the default.
    assert effective_guard_limits(stored) == GuardLimits(50, 20, 10)
    assert effective_guard_limits({"instance_per_hour": "5"}) == GuardLimits(120, 20, 10)


async def test_set_limits_lowers_persists_and_refuses_a_raise(test_db):
    guard = SendGuard(Clock())
    assert await guard.set_limits(channel_per_hour=5) == GuardLimits(120, 5, 10)
    assert await SendGuard(Clock()).limits() == GuardLimits(120, 5, 10)  # a restart
    for bad in (21, 300, 0, True, 2.5):
        with pytest.raises(ValueError):
            await guard.set_limits(channel_per_hour=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await guard.set_limits(nonsense=1)
    assert (await guard.limits()).channel_per_hour == 5  # nothing stored by a refusal
    assert await guard.set_limits(channel_per_hour=None) == GuardLimits(120, 20, 10)


# --- the budgets ------------------------------------------------------------------


async def test_destination_quota_per_channel_and_per_contact(test_db):
    clock = Clock()
    guard = SendGuard(clock)
    for _ in range(20):
        await send(guard)
    stop = await refusal(guard)
    assert stop.scope == "destination"
    assert stop.params["limit"] == 20
    assert stop.params["kind"] == "channel"
    assert 0 < stop.params["retry_after"] <= HOUR
    await send(guard, key="EF" * 16)  # another channel is separate
    for _ in range(10):
        await send(guard, is_dm=True, key=CONTACT)
    contact = await refusal(guard, is_dm=True, key=CONTACT)
    assert (contact.scope, contact.params["limit"], contact.params["kind"]) == (
        "destination",
        10,
        "contact",
    )
    clock.now += HOUR  # the next window
    await send(guard)


async def test_the_destination_quota_is_shared_by_every_plugin_instance(test_db):
    guard = SendGuard(Clock())
    for n in range(20):
        await send(guard, f"plugin-{n % 4}")
    assert (await refusal(guard, "plugin-9")).scope == "destination"


async def test_instance_budget_caps_all_plugins_together(test_db):
    guard = SendGuard(Clock())
    await guard.set_limits(instance_per_hour=7)
    for n in range(7):
        await send(guard, f"plugin-{n}", key=f"{n:032X}")  # different instances and channels
    stop = await refusal(guard, "plugin-99", key="FF" * 16)
    assert stop.scope == "instance"
    assert stop.params["limit"] == 7


async def test_counters_survive_a_restart_and_follow_the_hour(test_db):
    clock = Clock()
    guard = SendGuard(clock)
    await guard.set_limits(instance_per_hour=3)
    for n in range(3):
        await send(guard, key=f"{n:032X}")
    restarted = SendGuard(clock)  # a new process reads the same rows
    assert (await refusal(restarted, key="FF" * 16)).scope == "instance"
    clock.now += HOUR
    await send(restarted, key="FF" * 16)


async def test_a_refused_send_counts_nothing(test_db):
    guard = SendGuard(Clock())
    await guard.set_limits(contact_per_hour=2)
    for _ in range(2):
        await send(guard, is_dm=True, key=CONTACT)
    for _ in range(3):
        await refusal(guard, is_dm=True, key=CONTACT)
    assert await PluginSendGuardRepository.get("instance", "") == (T0, 2)


# --- the loop guards --------------------------------------------------------------


async def test_sender_cooldown_is_per_instance_and_per_sender(test_db):
    clock = Clock()
    guard = SendGuard(clock)
    await send(guard, sender="key:ann")
    clock.now += 29
    stop = await refusal(guard, sender="key:ann")
    assert stop.scope == "sender_cooldown"
    assert stop.params["retry_after"] == 1
    await send(guard, sender="key:bob")  # another sender
    await send(guard, "i2", sender="key:ann")  # another plugin instance
    clock.now += 1
    await send(guard, sender="key:ann")  # 30 s passed


async def test_a_send_that_answers_nothing_has_no_cooldown_nor_quiet_window(test_db):
    guard = SendGuard(Clock())
    for _ in range(3):
        await send(guard, sender=None)


async def test_quiet_window_refuses_a_reply_to_a_message_that_just_followed_a_send(test_db):
    clock = Clock()
    guard = SendGuard(clock)
    await send(guard, sender="key:ann")  # the instance sends at T0 + 10
    # Bob's message arrived 3 s after the send: likely a reaction to it.
    stop = await refusal(guard, sender="key:bob", at=int(clock.now) + 3)
    assert stop.scope == "quiet_window"
    assert stop.params["limit"] == 5
    # Arrived before the send, or 5 s or more after: answered.
    await send(guard, sender="key:bob", at=int(clock.now) - 1)
    await send(guard, sender="key:cat", at=int(clock.now) + 5)
    # Another destination is not concerned.
    await send(guard, sender="key:dan", key="EF" * 16, at=int(clock.now) + 1)


async def test_cooldown_is_checked_before_the_budgets(test_db):
    guard = SendGuard(Clock())
    await guard.set_limits(instance_per_hour=1)
    await send(guard, sender="key:ann")
    assert (await refusal(guard, sender="key:ann")).scope == "sender_cooldown"
    assert (await refusal(guard, sender="key:bob")).scope == "instance"


async def test_old_stamps_are_pruned_and_the_instance_rows_deleted_with_it(test_db):
    clock = Clock()
    guard = SendGuard(clock)
    row = await PluginRepository.create(
        plugin_id="p", label="P", kind="local", version="1.0.0", manifest={}
    )
    instance_id = row["instance_id"]
    await send(guard, instance_id, sender="key:ann")
    clock.now += 60
    await send(guard, "other", sender="key:bob", key="EF" * 16)  # prunes the old stamps
    assert await PluginSendGuardRepository.get("cooldown", f"{instance_id}|key:ann") is None
    quiet = f"{instance_id}|{destination_subject(is_dm=False, key=CHANNEL)}"
    assert await PluginSendGuardRepository.get("quiet", quiet) is None
    await send(guard, instance_id, sender="key:ann")
    await PluginRepository.delete(instance_id)
    assert await PluginSendGuardRepository.get("cooldown", f"{instance_id}|key:ann") is None
    assert await PluginSendGuardRepository.get("cooldown", "other|key:bob") is not None


def test_refusals_are_visible_and_logged_once_a_minute(caplog):
    clock = Clock()
    guard = SendGuard(clock)
    assert guard.hits("i1") == {}
    first = GuardRefused("destination", "too many", limit=20)
    with caplog.at_level("WARNING", logger="app.plugins.quotas"):
        for _ in range(5):
            guard.note_refusal("i1", "Echo", first)
        guard.note_refusal("i1", "Echo", GuardRefused("quiet_window", "quiet"))
        clock.now += 61
        guard.note_refusal("i1", "Echo", first)
    assert guard.hits("i1") == {
        "total": 7,
        "by_scope": {"destination": 6, "quiet_window": 1},
        "last": {"scope": "destination", "at": int(T0 + 10 + 61)},
    }
    assert len(caplog.records) == 3  # destination, quiet_window, destination again
    assert "destination guard" in caplog.records[0].getMessage()
    guard.forget("i1")
    assert guard.hits("i1") == {}


# --- two hosts, two echo plugins --------------------------------------------------


class MemoryStore:
    """A host's own `plugin_send_guard` tables (the real repository is one process-wide
    database; each simulated host needs its own)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], tuple[int, int]] = {}

    async def get(self, scope, subject):
        return self.rows.get((scope, subject))

    async def limits(self):
        return {}

    async def set_limits(self, values):
        raise NotImplementedError

    async def record_send(
        self,
        *,
        window_start,
        destination,
        instance_id,
        now,
        sender,
        keep_cooldown_after,
        keep_quiet_after,
    ):
        for key in (("instance", ""), ("destination", destination)):
            start, count = self.rows.get(key, (window_start, 0))
            self.rows[key] = (window_start, count + 1 if start == window_start else 1)
        self.rows[("quiet", f"{instance_id}|{destination}")] = (now, 0)
        if sender is not None:
            self.rows[("cooldown", f"{instance_id}|{sender}")] = (now, 0)


class Host:
    """One Meshloom instance with one echo plugin: it answers every message of the
    channel that is not its own, one second after hearing it."""

    def __init__(self, name: str, clock: Clock) -> None:
        self.name = name
        self.guard = SendGuard(clock, MemoryStore())
        self.sent = 0
        self.refused: list[str] = []


async def simulate(
    hours: float = 2.0, *, tied: bool = True
) -> tuple[list[tuple[int, str]], dict[str, Host]]:
    """Two hosts on one fake channel. A person posts at t=0; each echo plugin answers
    what the other hears. Returns the channel log `(time, author)` and the hosts."""
    clock = Clock(T0)
    hosts = {"A": Host("A", clock), "B": Host("B", clock)}
    channel: list[tuple[int, str]] = [(int(T0), "human")]
    heard: list[tuple[float, str, str]] = [(T0 + 1, "A", "human"), (T0 + 1, "B", "human")]
    # (time the plugin hears it, host, author); the radio takes 1 s to carry a message.
    end = T0 + hours * HOUR
    while heard:
        heard.sort(key=lambda h: h[0])
        at, name, author = heard.pop(0)
        if at > end:
            break
        clock.now = at
        host = hosts[name]
        if author == host.name:
            continue  # a plugin never answers its own message
        instance = f"echo-{name}"
        heard_params = {"channel_key": CHANNEL, "sender_name": author, "received_at": int(at)}
        host.guard.note_inbound(instance, heard_params)
        sender: str | None = f"name:{author}"
        received: int | None = int(at)
        if not tied:
            # The bypass shapes: the send names no event (several in flight, after the
            # handler returned, no event_id), so the host falls back to the last inbound.
            last = host.guard.last_inbound(instance, is_dm=False, key=CHANNEL)
            assert last is not None
            sender, received = last
        try:
            await host.guard.check(
                instance, is_dm=False, key=CHANNEL, sender=sender, received_at=received
            )
        except GuardRefused as exc:
            host.refused.append(exc.scope)
            continue
        await host.guard.record(instance, is_dm=False, key=CHANNEL, sender=sender)
        host.sent += 1
        channel.append((int(at), name))
        for other in hosts:
            heard.append((at + 1, other, name))
    return channel, hosts


async def test_two_echo_plugins_on_two_hosts_stop_with_the_default_guards():
    channel, hosts = await simulate()
    assert len(channel) <= 6  # the person's message and a handful of answers
    assert sum(h.sent for h in hosts.values()) == len(channel) - 1
    reasons = hosts["A"].refused + hosts["B"].refused
    assert reasons  # the exchange was cut, with a reason, not by running dry
    assert set(reasons) <= {"sender_cooldown", "quiet_window"}


async def test_untied_sends_are_cut_like_replies():
    """A plugin that names no event (or whose event cannot be found) gets no way round
    the loop guards: the exchange stops as fast as with tied sends."""
    channel, hosts = await simulate(tied=False)
    assert len(channel) <= 6
    reasons = hosts["A"].refused + hosts["B"].refused
    assert reasons
    assert set(reasons) <= {"sender_cooldown", "quiet_window"}


def test_an_untied_send_is_a_reply_to_the_last_inbound_of_its_destination():
    from types import SimpleNamespace

    from app.plugins.capabilities import messages

    guard = SendGuard(Clock())
    call = SimpleNamespace(instance=SimpleNamespace(instance_id="i1"), event=None)
    target = messages.Target(False, CHANNEL)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(messages, "send_guard", guard)
        assert messages._reply_context(call, target) == (None, None)  # nothing heard yet
        guard.note_inbound("i1", {"channel_key": CHANNEL, "sender_name": "Bob", "received_at": 7})
        guard.note_inbound("i1", {"channel_key": CHANNEL, "is_outgoing": True, "sender_name": "Me"})
        assert messages._reply_context(call, target) == ("name:bob", 7)
        other = messages.Target(False, "EF" * 16)
        assert messages._reply_context(call, other) == (None, None)
        timer = SimpleNamespace(hook="timer", params={})
        call.event = timer
        assert messages._reply_context(call, target) == ("name:bob", 7)


async def test_removing_an_instance_forgets_its_refusals(test_db):
    from app.plugins.quotas import send_guard

    row = await PluginRepository.create(
        plugin_id="p", label="P", kind="local", version="1.0.0", manifest={}
    )
    instance_id = row["instance_id"]
    send_guard.note_refusal(instance_id, "P", GuardRefused("instance", "x"))
    send_guard.note_inbound(instance_id, {"channel_key": CHANNEL, "sender_name": "Bob"})
    assert send_guard.hits(instance_id)
    await PluginRepository.delete(instance_id)
    assert send_guard.hits(instance_id) == {}
    assert send_guard.last_inbound(instance_id, is_dm=False, key=CHANNEL) is None


async def test_without_the_loop_cuts_the_destination_quota_still_bounds_the_exchange(monkeypatch):
    monkeypatch.setattr(quotas, "SENDER_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(quotas, "QUIET_WINDOW_SECONDS", 0)
    channel, hosts = await simulate(hours=1.0)
    default = GUARD_LIMITS["channel_per_hour"][0]
    assert all(h.sent <= default for h in hosts.values())
    assert 0 < len(channel) - 1 <= 2 * default
    assert "destination" in hosts["A"].refused + hosts["B"].refused
    assert not {"sender_cooldown", "quiet_window"} & set(hosts["A"].refused + hosts["B"].refused)
