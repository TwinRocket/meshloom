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


async def _emit_dm() -> None:
    from app.notifications.dispatcher import notification_dispatcher
    from app.notifications.producers import emit_message

    await emit_message(
        {"type": "PRIV", "conversation_key": DM_KEY, "text": "hi", "sender_name": "Léa"}
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
    await UserNotificationRulesRepository.set(3, {"contact_types": {"sensor": True}})
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
    assert (await UserNotificationRulesRepository.get(3))["contact_types"] == {"sensor": True}
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
