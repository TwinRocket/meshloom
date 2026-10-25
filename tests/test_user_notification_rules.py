"""Per-account notification rules for Web Push (MSHL-321, migration 123)."""

from __future__ import annotations

import json

import pytest

from app.notifications import user_rules as ur
from app.notifications.events import event_from_payload
from app.repository.auth import UserRepository
from app.repository.notification_channels import NotificationChannelRepository
from app.repository.user_notification_rules import UserNotificationRulesRepository
from tests import test_backup_restore_v2 as restore_v2

setup_stub = restore_v2.setup_stub
_reset_jobs = restore_v2._reset_jobs

PUSH = "builtin:push"
DM_KEY = "ab" * 32


def _dm_event():
    return event_from_payload(
        {"event": "message", "type": "PRIV", "conversation_key": DM_KEY, "text": "hi"}
    )


def _first_seen(contact_type: int):
    return event_from_payload(
        {"event": "first_seen", "public_key": "cd" * 32, "name": "N", "contact_type": contact_type}
    )


# -- pure functions ---------------------------------------------------------------


def test_sanitize_keeps_known_boolean_choices_only():
    raw = {
        "events": {"message.dm": False, "message.channel": "yes", "nope": True},
        "contact_types": {"repeater": True, "room": True},
        "x": 1,
    }
    assert ur.sanitize_override(raw) == {
        "version": 1,
        "events": {"message.dm": False},
        "contact_types": {"repeater": True},
    }
    assert ur.sanitize_override({"events": {"notify.channel_changed": False}}) == {}
    assert ur.sanitize_override("junk") == {}


def test_an_account_without_choices_follows_the_defaults():
    on = {"events": {"message.dm": {"channels": [PUSH]}}}
    off = {"events": {"message.dm": {"channels": []}}}
    assert ur.push_wanted(on, {}, _dm_event())
    assert not ur.push_wanted(off, {}, _dm_event())


def test_an_account_can_turn_an_event_on_or_off_for_its_devices():
    on = {"events": {"message.dm": {"channels": [PUSH, "legacy:email"]}}}
    off = {"events": {"message.dm": {"channels": ["legacy:email"]}}}
    mine_off = {"events": {"message.dm": False}}
    mine_on = {"events": {"message.dm": True}}
    assert not ur.push_wanted(on, mine_off, _dm_event())
    assert ur.push_wanted(off, mine_on, _dm_event())
    # The defaults are not modified.
    assert on["events"]["message.dm"]["channels"] == [PUSH, "legacy:email"]
    assert off["events"]["message.dm"]["channels"] == ["legacy:email"]


def test_off_beats_a_conversation_exception_and_on_respects_it():
    rules = {
        "events": {"message.dm": {"channels": [PUSH]}},
        "conversations": {f"contact-{DM_KEY}": {PUSH: False}},
    }
    # The admin silenced this conversation for push: an account turning DMs on does not undo it.
    assert not ur.push_wanted(rules, {"events": {"message.dm": True}}, _dm_event())
    rules["conversations"] = {f"contact-{DM_KEY}": {PUSH: True}}
    assert not ur.push_wanted(rules, {"events": {"message.dm": False}}, _dm_event())


def test_contact_types_have_one_choice_per_kind():
    rules = {
        "events": {
            "contact.first_seen": {
                "channels": [],
                "contact_types": {"companion": [PUSH], "repeater": [], "sensor": []},
            }
        }
    }
    mine = {"contact_types": {"companion": False, "repeater": True}}
    assert not ur.push_wanted(rules, mine, _first_seen(1))
    assert ur.push_wanted(rules, mine, _first_seen(2))
    assert not ur.push_wanted(rules, mine, _first_seen(4))
    assert ur.push_wanted(rules, {}, _first_seen(1))
    # A room never notifies, whatever the choice.
    assert not ur.push_wanted(rules, {"events": {"contact.first_seen": True}}, _first_seen(3))


def test_the_audience_gives_each_account_the_mention_or_the_plain_payload():
    rules = {
        "events": {
            "message.channel": {"channels": [PUSH]},
            "message.mention": {"channels": [PUSH]},
        }
    }
    plain = {"event": "message", "type": "CHAN", "conversation_key": "AA" * 16, "text": "@[Bob]"}
    mention_payload = plain | {"mention": True}
    mention_event = event_from_payload(mention_payload)
    event = event_from_payload(plain)
    overrides = {
        2: {"events": {"message.mention": False}},
        3: {"events": {"message.mention": False, "message.channel": False}},
    }
    audience = ur.build_audience(
        rules,
        overrides,
        event,
        plain,
        mention=(mention_event, mention_payload),
        is_public_or_hashtag=True,
    )
    assert audience.payload_for(1) is mention_payload  # no choices: the defaults
    assert audience.payload_for(2) is plain  # no mention, still the channel
    assert audience.payload_for(3) is None  # neither
    assert audience.payload_for(None) is mention_payload
    assert not audience.empty


CHAN_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"


def _chan_event(text: str = "hello"):
    return event_from_payload(
        {"event": "message", "type": "CHAN", "conversation_key": CHAN_KEY, "text": text}
    )


def test_sanitize_keeps_conversation_exceptions_and_a_pause():
    raw = {
        "conversations": {
            f"channel-{CHAN_KEY}": False,
            f"contact-{DM_KEY}": True,
            "channel-nothex!": True,
            f"contact-{DM_KEY}x": True,
            "other-ab": True,
            "channel-ab": "yes",
        },
        "pause": {"until": 2_000_000_000},
    }
    assert ur.sanitize_override(raw) == {
        "version": 1,
        "conversations": {f"channel-{CHAN_KEY}": False, f"contact-{DM_KEY}": True},
        "pause": {"until": 2_000_000_000},
    }
    assert ur.sanitize_override({"pause": {"until": None}})["pause"] == {"until": None}
    assert ur.sanitize_override({"pause": {"until": -5}}) == {}
    assert ur.sanitize_override({"pause": {"until": True}}) == {}
    assert ur.sanitize_override({"pause": "now"}) == {}


def test_a_conversation_exception_beats_the_event_choice_both_ways():
    rules = {"events": {"message.channel": {"channels": [PUSH]}, "message.dm": {"channels": []}}}
    chan_key = f"channel-{CHAN_KEY}"
    dm_key = f"contact-{DM_KEY}"
    # Muted for me, whatever the event says (even an explicit "always notify me").
    mute = {"conversations": {chan_key: False}, "events": {"message.channel": True}}
    assert not ur.push_wanted(rules, mute, _chan_event(), is_public_or_hashtag=True)
    # Other conversations follow the event.
    other = {"conversations": {f"channel-{'00' * 16}": False}}
    assert ur.push_wanted(rules, other, _chan_event(), is_public_or_hashtag=True)
    # Forced on for a DM the defaults leave off, even with the event turned off by me.
    force = {"conversations": {dm_key: True}, "events": {"message.dm": False}}
    assert ur.push_wanted(rules, force, _dm_event())
    assert not ur.push_wanted(rules, {"events": {"message.dm": False}}, _dm_event())
    # Forced on for a private-key channel that notifies no one by default.
    forced_chan = {"conversations": {chan_key: True}}
    assert not ur.push_wanted(rules, {}, _chan_event())
    assert ur.push_wanted(rules, forced_chan, _chan_event())


def test_a_forced_conversation_still_obeys_a_mute_and_a_block():
    rules = {"events": {"message.dm": {"channels": []}}}
    force = {"conversations": {f"contact-{DM_KEY}": True}}
    assert ur.push_wanted(rules, force, _dm_event())
    assert not ur.push_wanted(rules, force, _dm_event(), muted=True)
    assert not ur.push_wanted(rules, force, _dm_event(), blocked=True)


def test_a_conversation_exception_cuts_the_mention_too():
    rules = {"events": {"message.mention": {"channels": [PUSH]}}}
    mention = event_from_payload(
        {
            "event": "message",
            "type": "CHAN",
            "conversation_key": CHAN_KEY,
            "text": "@[Bob]",
            "mention": True,
        }
    )
    assert ur.push_wanted(rules, {}, mention, is_public_or_hashtag=True)
    off = {"conversations": {f"channel-{CHAN_KEY}": False}}
    assert not ur.push_wanted(rules, off, mention, is_public_or_hashtag=True)


def test_a_pause_holds_everything_but_urgent_always_deliver_and_locked_events():
    rules = {
        "events": {
            "message.dm": {"channels": [PUSH]},
            "radio.online": {"channels": [PUSH], "always_deliver": True},
            "radio.offline": {"channels": [PUSH]},
            "notify.channel_changed": {"channels": [PUSH]},
        }
    }
    paused = {"pause": {"until": 5_000}}
    assert not ur.push_wanted(rules, paused, _dm_event(), now=4_000)
    assert ur.push_wanted(rules, paused, _dm_event(), now=6_000)  # over
    assert not ur.push_wanted(rules, {"pause": {"until": None}}, _dm_event(), now=9e12)
    for name, payload in (
        ("radio.online", {"event": "radio_online"}),
        ("radio.offline", {"event": "radio_offline", "outage_started_at": 1}),
    ):
        event = event_from_payload(payload)
        assert event is not None and event.event == name
        assert ur.push_wanted(rules, paused, event, now=4_000), name
    from app.notifications.events import TestData, TestEvent

    rules["events"]["notify.test"] = {"channels": [PUSH]}
    assert ur.push_wanted(
        rules, paused, TestEvent(event="notify.test", dedup_key="t", data=TestData()), now=4_000
    )  # locked


def test_pause_helpers():
    assert ur.is_paused({"pause": {"until": 100}}, 50)
    assert not ur.is_paused({"pause": {"until": 100}}, 100)
    assert ur.is_paused({"pause": {"until": None}}, 1e12)
    assert not ur.is_paused({}, 1)
    assert ur.pause_until({"pause": {"until": 100}}) == 100
    assert ur.pause_until({"pause": {"until": None}}) is None


# -- the dispatcher ---------------------------------------------------------------


@pytest.fixture
async def accounts(push_owner):
    """Account 1 is the admin (the default test principal); 2 and 3 are members."""
    await UserRepository.create("test-member", "member")
    await UserRepository.create("other-member", "member")


@pytest.fixture
def pushes(monkeypatch):
    """What Web Push is asked to send: ``(payload, audience)`` per dispatch."""
    from app.push.manager import PushOutcome, push_manager

    sent: list[tuple[dict, ur.PushAudience | None]] = []

    async def fake_dispatch_event(data, audience=None):
        sent.append((dict(data), audience))
        return PushOutcome(total=1, delivered=1)

    monkeypatch.setattr(push_manager, "dispatch_event", fake_dispatch_event)
    return sent


async def _defaults(**events: list[str]) -> None:
    rules = await NotificationChannelRepository.get_rules()
    rules["events"] = {name.replace("_", "."): {"channels": ids} for name, ids in events.items()}
    await NotificationChannelRepository.set_rules(rules)


async def _emit_dm(n: int = 0) -> None:
    from app.notifications.dispatcher import notification_dispatcher
    from app.notifications.producers import emit_message

    await emit_message(
        {"type": "PRIV", "conversation_key": DM_KEY, "text": "hi", "sender_name": "Léa", "id": n}
    )
    await notification_dispatcher().wait_idle()


@pytest.mark.asyncio
async def test_without_choices_the_delivery_is_what_it_was(accounts, pushes):
    await _defaults(message_dm=[PUSH])
    await _emit_dm()
    assert len(pushes) == 1
    assert pushes[0][1] is None  # every device, one payload: no audience to carry


@pytest.mark.asyncio
async def test_an_account_that_turns_dms_off_is_left_out(accounts, pushes):
    await _defaults(message_dm=[PUSH])
    await UserNotificationRulesRepository.set(2, {"events": {"message.dm": False}})
    await _emit_dm()
    assert len(pushes) == 1
    data, audience = pushes[0]
    assert audience is not None
    assert audience.payload_for(1) is not None
    assert audience.payload_for(2) is None
    assert audience.payload_for(3) is not None


@pytest.mark.asyncio
async def test_an_account_can_turn_on_what_the_defaults_leave_off(accounts, pushes):
    await _defaults(message_dm=[])
    await _emit_dm()
    assert pushes == []
    await UserNotificationRulesRepository.set(3, {"events": {"message.dm": True}})
    await _emit_dm()
    assert len(pushes) == 1
    _, audience = pushes[0]
    assert audience.payload_for(3) is not None
    assert audience.payload_for(1) is None
    assert audience.payload_for(2) is None


@pytest.mark.asyncio
async def test_the_global_pause_still_holds_every_account(accounts, pushes):
    import time

    await _defaults(message_dm=[])
    rules = await NotificationChannelRepository.get_rules()
    rules["paused_until"] = time.time() + 3600
    await NotificationChannelRepository.set_rules(rules)
    await UserNotificationRulesRepository.set(2, {"events": {"message.dm": True}})
    await _emit_dm()
    assert pushes == []


@pytest.mark.asyncio
async def test_a_mention_reaches_each_account_in_the_version_its_rules_give(
    accounts, pushes, monkeypatch
):
    from app.notifications.dispatcher import notification_dispatcher
    from app.notifications.producers import emit_message

    await _defaults(message_channel=[PUSH], message_mention=[PUSH])
    await UserNotificationRulesRepository.set(2, {"events": {"message.mention": False}})
    monkeypatch.setattr("app.notifications.producers._radio_name", lambda: "Bob")
    await emit_message(
        {
            "type": "CHAN",
            "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
            "text": "hi @[Bob]",
            "sender_name": "Léa",
        }
    )
    await notification_dispatcher().wait_idle()
    assert len(pushes) == 1
    _, audience = pushes[0]
    assert audience.payload_for(1).get("mention") is True
    assert not audience.payload_for(2).get("mention")
    assert audience.payload_for(3).get("mention") is True


@pytest.mark.asyncio
async def test_a_conversation_exception_and_a_pause_shape_each_accounts_audience(accounts, pushes):
    import time

    await _defaults(message_dm=[PUSH])
    await UserNotificationRulesRepository.set(2, {"conversations": {f"contact-{DM_KEY}": False}})
    await UserNotificationRulesRepository.set(3, {"pause": {"until": int(time.time()) + 3600}})
    await _emit_dm()
    assert len(pushes) == 1
    _, audience = pushes[0]
    assert audience.payload_for(1) is not None
    assert audience.payload_for(2) is None  # muted this conversation
    assert audience.payload_for(3) is None  # paused
    # The pause ends: the account is back.
    await UserNotificationRulesRepository.set(3, {"pause": {"until": int(time.time()) - 1}})
    await _emit_dm(1)
    assert len(pushes) == 2
    assert pushes[-1][1].payload_for(3) is not None


# -- the API ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_my_rules_show_the_default_and_my_choice(accounts, client, sign_in_as):
    await _defaults(message_dm=[PUSH], message_channel=[])
    sign_in_as("member")
    body = (await client.get("/api/push/my-rules")).json()
    assert body["events"]["message.dm"] == {"default": True, "choice": None}
    assert body["events"]["message.channel"] == {"default": False, "choice": None}
    assert "notify.channel_changed" not in body["events"]
    assert set(body["contact_types"]) == {"companion", "repeater", "sensor"}

    patched = await client.patch(
        "/api/push/my-rules",
        json={"events": {"message.dm": False}, "contact_types": {"repeater": True}},
    )
    assert patched.status_code == 200
    assert patched.json()["events"]["message.dm"] == {"default": True, "choice": False}
    assert patched.json()["contact_types"]["repeater"]["choice"] is True
    assert await UserNotificationRulesRepository.get(2) == {
        "version": 1,
        "events": {"message.dm": False},
        "contact_types": {"repeater": True},
    }
    # Nobody else's choices change, and the admin defaults stay as they were.
    assert await UserNotificationRulesRepository.get(1) == {}
    assert (await NotificationChannelRepository.get_rules())["events"]["message.dm"] == {
        "channels": [PUSH]
    }

    cleared = await client.patch("/api/push/my-rules", json={"events": {"message.dm": None}})
    assert cleared.json()["events"]["message.dm"]["choice"] is None
    reset = await client.delete("/api/push/my-rules")
    assert reset.json()["contact_types"]["repeater"]["choice"] is None
    assert await UserNotificationRulesRepository.get(2) == {}


@pytest.mark.asyncio
async def test_my_conversation_exceptions_and_pause_through_the_api(accounts, client, sign_in_as):
    import time

    sign_in_as("member")
    key = f"contact-{DM_KEY}"
    body = (await client.get("/api/push/my-rules")).json()
    assert body["conversations"] == {}
    assert body["pause"] == {"active": False, "until": None}

    patched = await client.patch("/api/push/my-rules", json={"conversations": {key: False}})
    assert patched.status_code == 200
    assert patched.json()["conversations"] == {key: False}
    # Another patch keeps the conversations; a pause survives a rules patch.
    until = int(time.time()) + 3600
    paused = await client.put("/api/push/my-pause", json={"until": until})
    assert paused.json()["pause"] == {"active": True, "until": until}
    again = await client.patch("/api/push/my-rules", json={"events": {"message.dm": True}})
    assert again.json()["conversations"] == {key: False}
    assert again.json()["pause"]["active"] is True
    assert await UserNotificationRulesRepository.get(1) == {}  # nobody else's data

    reset = await client.delete("/api/push/my-rules")
    assert reset.json()["conversations"] == {}
    assert reset.json()["pause"]["until"] == until  # the pause is not a rule
    resumed = await client.delete("/api/push/my-pause")
    assert resumed.json()["pause"] == {"active": False, "until": None}
    assert await UserNotificationRulesRepository.get(2) == {}

    forever = await client.put("/api/push/my-pause", json={"until": None})
    assert forever.json()["pause"] == {"active": True, "until": None}
    cleared = await client.patch("/api/push/my-rules", json={"conversations": {key: None}})
    assert cleared.json()["pause"]["active"] is True


@pytest.mark.asyncio
async def test_my_conversation_and_pause_inputs_are_checked(accounts, client, sign_in_as):
    import time

    sign_in_as("member")
    bad = await client.patch("/api/push/my-rules", json={"conversations": {"channel-xyz": True}})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "notification_rule_unknown"
    now = int(time.time())
    for until in (now - 10, now + 400 * 24 * 3600):
        refused = await client.put("/api/push/my-pause", json={"until": until})
        assert refused.status_code == 400
        assert refused.json()["detail"]["code"] == "notification_pause_invalid"
    many = {f"channel-{n:032x}": True for n in range(60)}
    for start in range(0, 300, 60):
        batch = {f"channel-{start + n:032x}": True for n in range(60)}
        res = await client.patch("/api/push/my-rules", json={"conversations": batch})
        if res.status_code == 400:
            assert res.json()["detail"]["code"] == "notification_conversations_limit"
            break
    else:
        raise AssertionError("the limit was never reached")
    assert many
    sign_in_as("kiosk")
    assert (await client.put("/api/push/my-pause", json={"until": None})).status_code == 403
    assert (await client.delete("/api/push/my-pause")).status_code == 403


@pytest.mark.asyncio
async def test_my_rules_refuse_an_unknown_id_and_a_role_without_send(accounts, client, sign_in_as):
    sign_in_as("member")
    refused = await client.patch("/api/push/my-rules", json={"events": {"nope": True}})
    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "notification_rule_unknown"
    locked = await client.patch(
        "/api/push/my-rules", json={"events": {"notify.channel_changed": False}}
    )
    assert locked.status_code == 400
    sign_in_as("kiosk")
    assert (await client.get("/api/push/my-rules")).status_code == 403


@pytest.mark.asyncio
async def test_deleting_an_account_drops_its_choices(accounts, test_db):
    await UserNotificationRulesRepository.set(2, {"events": {"message.dm": False}})
    assert await UserNotificationRulesRepository.get(2) != {}
    assert await UserRepository.delete(2)
    assert await UserNotificationRulesRepository.get(2) == {}
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM user_notification_rules") as cur:
            assert (await cur.fetchone())[0] == 0


# -- the backup -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_choices_round_trip_through_the_accounts_section(accounts, test_db):
    from app.models import BackupSection
    from app.services.backup_notifications import (
        export_user_notification_rules,
        restore_user_notification_rules,
    )

    assert await export_user_notification_rules() is None
    await UserNotificationRulesRepository.set(2, {"events": {"message.dm": False}})
    await UserNotificationRulesRepository.set(
        3,
        {
            "contact_types": {"sensor": True},
            "conversations": {f"contact-{DM_KEY}": True},
            "pause": {"until": None},
        },
    )
    section = await export_user_notification_rules()
    assert section is not None
    data = json.loads(json.dumps(section.data))
    assert [a["user_id"] for a in data["accounts"]] == [2, 3]

    await UserNotificationRulesRepository.set(2, {})
    await UserNotificationRulesRepository.set(3, {})
    data["accounts"].append({"user_id": 99, "rules": {"events": {"message.dm": True}}})
    async with test_db.tx() as conn:
        warnings = await restore_user_notification_rules(BackupSection(data=data), conn)
    assert warnings == ["notification_user_rules_dropped:99"]
    from app.repository.user_notification_rules import invalidate_cache

    invalidate_cache()
    assert (await UserNotificationRulesRepository.get(2))["events"] == {"message.dm": False}
    restored = await UserNotificationRulesRepository.get(3)
    assert restored["contact_types"] == {"sensor": True}
    assert restored["conversations"] == {f"contact-{DM_KEY}": True}
    assert restored["pause"] == {"until": None}
    assert await UserNotificationRulesRepository.get(99) == {}


# -- a full backup -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_full_backup_carries_the_choices_with_the_accounts(backup_db, setup_stub):
    from app.models import BackupExportV2, BackupRestoreSteps
    from app.services.backup import export_backup_v2, export_document
    from app.services.backup_accounts import USER_NOTIFICATION_RULES_SECTION
    from tests import test_backup_restore_v2 as restore_v2

    hash_ = "$argon2id$v=19$m=65536,t=2,p=1$YWRtaW5zYWx0$YWRtaW5oYXNo"
    alice = await UserRepository.create("Alice", "admin", password_hash=hash_)
    bob = await UserRepository.create("bob", "member", password_hash=hash_)
    await UserNotificationRulesRepository.set(bob.id, {"events": {"message.dm": False}})
    exported = json.loads(json.dumps(export_document(await export_backup_v2())))
    assert USER_NOTIFICATION_RULES_SECTION in exported["sections"]
    without = json.loads(
        json.dumps(export_document(await export_backup_v2(include_accounts=False)))
    )
    assert USER_NOTIFICATION_RULES_SECTION not in without["sections"]

    # Another instance: no choices, other accounts; the file brings its own back.
    async with backup_db.tx() as conn:
        await conn.execute("DELETE FROM users")
    await UserRepository.create("mallory", "admin", password_hash=hash_)
    steps = BackupRestoreSteps(
        identity=False, radio_settings=False, channels=False, contacts=False, wifi=False
    )
    job = await restore_v2.run(BackupExportV2.model_validate(exported), steps=steps)
    assert "accounts_restored" in job.result.warnings
    restored = {u.username: u.id for u in await UserRepository.list_all()}
    assert restored["bob"] == bob.id and restored["Alice"] == alice.id
    assert (await UserNotificationRulesRepository.get(bob.id))["events"] == {"message.dm": False}
    assert await UserNotificationRulesRepository.get(alice.id) == {}
