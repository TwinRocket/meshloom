"""Grouped summaries and per-account push choices (MSHL-361, 362, 363).

A summary ("+12 messages") that a full Web Push bucket produces reaches only the accounts whose
own rules wanted at least one of the held notifications, each with its own counts, and never an
account that is paused when it is sent.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import pytest

from app.notifications.dispatcher import FIRST_SEEN_GROUPED_EVENT, GROUPED_EVENT, Dispatcher
from app.notifications.transports.base import SendResult
from app.repository.auth import UserRepository
from app.repository.notification_channels import NotificationChannelRepository
from app.repository.user_notification_rules import UserNotificationRulesRepository

pytestmark = pytest.mark.async_notifications

PUSH = "builtin:push"
DM_KEY = "ab" * 32
PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"


@dataclass
class PushStub:
    """Records each push as ``{user_id: payload}`` (None: that account's devices are skipped)."""

    users: tuple[int, ...] = (1, 2, 3)
    sent: list[dict[int, dict | None]] = field(default_factory=list)

    async def send(self, channel, rendered, event, secrets) -> SendResult:
        audience = event.audience
        if audience is None:
            self.sent.append({uid: dict(event.payload) for uid in self.users})
        else:
            self.sent.append(
                {
                    uid: (dict(p) if (p := audience.payload_for(uid)) is not None else None)
                    for uid in self.users
                }
            )
        return SendResult("ok")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def accounts(push_owner):
    await UserRepository.create("member", "member")
    await UserRepository.create("other", "member")


async def _push_rules(test_db, **events: list[str]) -> None:
    rules = await NotificationChannelRepository.get_rules()
    rules["events"] = {name.replace("_", "."): {"channels": ids} for name, ids in events.items()}
    await NotificationChannelRepository.set_rules(rules)
    async with test_db.tx() as conn:  # a one-a-minute bucket: the second notification is held
        await conn.execute(
            "UPDATE notification_channels SET rate_per_min = 1 WHERE id = ?", (PUSH,)
        )
    from app.repository.notification_channels import invalidate_cache

    invalidate_cache()


def _dispatcher(stub: PushStub, held_until: asyncio.Event) -> Dispatcher:
    clock = _Clock()

    async def sleep(seconds: float) -> None:
        await held_until.wait()
        clock.now += seconds
        await asyncio.sleep(0)

    return Dispatcher(
        clock=clock,
        sleep=sleep,
        wall_clock=time.time,
        transport_for=lambda _channel: stub,
        aggregation_window_s=60.0,
    )


def _dm(n: int) -> dict:
    return {
        "event": "message",
        "type": "PRIV",
        "conversation_key": DM_KEY,
        "text": "hi",
        "sender_name": "Léa",
        "id": n,
    }


def _chan(n: int) -> dict:
    return {
        "event": "message",
        "type": "CHAN",
        "conversation_key": PUBLIC_KEY,
        "text": "hi",
        "sender_name": "Bob",
        "channel_name": "Public",
        "id": n,
    }


async def _settle(dispatcher: Dispatcher) -> None:
    for _ in range(5):
        await asyncio.sleep(0.02)  # let a released flusher run
        await dispatcher.wait_idle()
        flushers = [w.agg_flusher for w in dispatcher._workers.values() if w.agg_flusher]
        await asyncio.gather(*flushers, return_exceptions=True)
    await dispatcher.wait_idle()


@pytest.mark.asyncio
async def test_a_summary_reaches_only_the_accounts_that_wanted_a_held_message(test_db, accounts):
    await _push_rules(test_db, message_dm=[PUSH], message_channel=[])
    # Account 2 turns DMs off, account 3 turns channel messages on (and keeps DMs).
    await UserNotificationRulesRepository.set(2, {"events": {"message.dm": False}})
    await UserNotificationRulesRepository.set(
        3, {"events": {"message.dm": False, "message.channel": True}}
    )
    stub, gate = PushStub(), asyncio.Event()
    dispatcher = _dispatcher(stub, gate)
    for n in range(4):  # one goes out, three are held
        await dispatcher.emit(None, payload=_dm(n))
    for n in range(10, 12):  # two channel messages, held
        await dispatcher.emit(None, payload=_chan(n))
    gate.set()
    await _settle(dispatcher)

    summaries = [s for s in stub.sent if (s[1] or s[3] or {}).get("event") == GROUPED_EVENT]
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary[1] == {"event": GROUPED_EVENT, "count": 3, "dms": 3}  # the held DMs only
    assert summary[2] is None  # DMs off, channels not asked for: nothing to summarise
    assert summary[3] == {"event": GROUPED_EVENT, "count": 2, "channels": {"Public": 2}}


@pytest.mark.asyncio
async def test_without_any_override_the_summary_is_unchanged(test_db, accounts):
    await _push_rules(test_db, message_dm=[PUSH])
    stub, gate = PushStub(), asyncio.Event()
    dispatcher = _dispatcher(stub, gate)
    for n in range(4):
        await dispatcher.emit(None, payload=_dm(n))
    gate.set()
    await _settle(dispatcher)
    assert stub.sent[-1] == {
        uid: {"event": GROUPED_EVENT, "count": 3, "dms": 3} for uid in (1, 2, 3)
    }


@pytest.mark.asyncio
async def test_nobody_left_to_summarise_sends_no_summary(test_db, accounts):
    await _push_rules(test_db, message_dm=[])
    for uid in (2, 3):
        await UserNotificationRulesRepository.set(uid, {"events": {"message.dm": True}})
    stub, gate = PushStub(), asyncio.Event()
    dispatcher = _dispatcher(stub, gate)
    for n in range(4):
        await dispatcher.emit(None, payload=_dm(n))
    until = int(time.time()) + 3600  # both are paused when the summary is due
    for uid in (2, 3):
        await UserNotificationRulesRepository.set(
            uid, {"events": {"message.dm": True}, "pause": {"until": until}}
        )
    gate.set()
    await _settle(dispatcher)
    assert len(stub.sent) == 1  # the first message only
    assert stub.sent[0][1] is None


@pytest.mark.asyncio
async def test_an_account_paused_when_the_summary_goes_out_gets_none(test_db, accounts):
    await _push_rules(test_db, message_dm=[PUSH])
    await UserNotificationRulesRepository.set(3, {"events": {"message.dm": True}})
    stub, gate = PushStub(), asyncio.Event()
    dispatcher = _dispatcher(stub, gate)
    for n in range(4):
        await dispatcher.emit(None, payload=_dm(n))
    # The pause starts while the messages are held.
    await UserNotificationRulesRepository.set(
        3, {"events": {"message.dm": True}, "pause": {"until": int(time.time()) + 3600}}
    )
    gate.set()
    await _settle(dispatcher)
    summary = stub.sent[-1]
    assert summary[1]["event"] == GROUPED_EVENT and summary[2]["event"] == GROUPED_EVENT
    assert summary[3] is None


@pytest.mark.asyncio
async def test_the_first_seen_summary_follows_the_accounts_contact_types(test_db, accounts):
    rules = await NotificationChannelRepository.get_rules()
    rules["events"] = {"contact.first_seen": {"channels": [PUSH]}}
    await NotificationChannelRepository.set_rules(rules)
    await UserNotificationRulesRepository.set(2, {"events": {"contact.first_seen": False}})
    stub = PushStub()
    dispatcher = Dispatcher(
        transport_for=lambda _channel: stub, first_seen_per_hour=2, first_seen_period_s=0.2
    )
    for n in range(5):
        await dispatcher.emit(
            None, payload={"event": "first_seen", "public_key": f"{n:064x}", "contact_type": 1}
        )
    await dispatcher.wait_idle()
    worker = dispatcher._workers[PUSH]
    assert worker.first_seen_flusher is not None
    await worker.first_seen_flusher
    await _settle(dispatcher)
    summary = stub.sent[-1]
    assert summary[1] == {"event": FIRST_SEEN_GROUPED_EVENT, "count": 3}
    assert summary[2] is None
    assert summary[3] == {"event": FIRST_SEEN_GROUPED_EVENT, "count": 3}
