"""The ``notifications`` backup section and the restore of 4.18.x notification settings
(MSHL-117, ``services/backup_notifications.py``).

Two files kept as fixtures: a 4.18.2 export (``meshloom-backup-v1``) whose destinations and
preferences were filled in, and a full backup (``meshloom-backup-v2``) with a notifications section.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from pathlib import Path

import pytest

import app.main  # noqa: F401  (setup_logging runs at import, never inside a test)
from app.models import BackupExportV2, BackupRestoreRequest, BackupRestoreSteps, BackupSection
from app.notifications.channels import ChannelCreate, ChannelError, ChannelUpdate
from app.repository import AppSettingsRepository
from app.repository.notification_channels import NotificationChannelRepository as Channels
from app.repository.notification_channels import invalidate_cache
from app.services.backup import export_backup_v2, export_document, restore_json
from app.services.backup_notifications import NOTIFICATIONS_SECTION
from app.services.backup_sections import restore_sections as _restore_sections
from app.services.backup_sections import run_on_restore_hooks
from tests import test_backup_restore_v2 as restore_v2

run, statuses = restore_v2.run, restore_v2.statuses
setup_stub = restore_v2.setup_stub
_reset_jobs = restore_v2._reset_jobs

FIXTURES = Path(__file__).parent / "fixtures" / "backup"
V1_4_18 = json.loads((FIXTURES / "v1_4.18.2_destinations.json").read_text())
V1_4_18_PLAIN = json.loads((FIXTURES / "v1_4.18.2.json").read_text())
V2_NOTIFICATIONS = json.loads((FIXTURES / "v2_notifications.json").read_text())
SECRETS = ("smtp-pass-4182", "hmac-secret-4182", "hooks.example.org/in/abc123")


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


async def restore_sections(sections):
    """The section restore, then the hooks the job runs once at its end."""
    warnings = await _restore_sections(sections)
    await run_on_restore_hooks("json")
    return warnings


def _meshloom_only() -> BackupRestoreSteps:
    return BackupRestoreSteps(
        identity=False, radio_settings=False, channels=False, contacts=False, wifi=False
    )


def _section(name: str = "notifications", doc: dict | None = None) -> BackupSection:
    return BackupSection.model_validate((doc or V2_NOTIFICATIONS)["sections"][name])


# -- 4.18.x files -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_4_18_export_lands_in_the_engine_model(backup_db):
    result = await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    assert result.settings_updated

    channels = {c.id: c for c in await Channels.list_all()}
    assert set(channels) == {"builtin:push", "legacy:email", "legacy:webhook"}
    email, webhook = channels["legacy:email"], channels["legacy:webhook"]
    assert email.config["host"] == "smtp.example.org"
    assert email.config["port"] == 465
    assert email.config["mode"] == "ssl"
    assert email.config["to"] == "ops@example.org"
    assert email.secret_fields_set == ["password"]
    assert sorted(webhook.secret_fields_set) == ["hmac_secret", "url"]
    # The stored secrets are the file's, nothing was lost on the way in.
    found = await Channels.get_for_send("legacy:email")
    assert found is not None and found[1]["password"] == "smtp-pass-4182"
    found = await Channels.get_for_send("legacy:webhook")
    assert found is not None and found[1]["hmac_secret"] == "hmac-secret-4182"

    events = (await Channels.get_rules())["events"]
    assert events["message.dm"]["channels"] == ["builtin:push", "legacy:email"]
    assert events["telemetry.alert"]["channels"] == ["builtin:push", "legacy:email"]
    assert events["app.update"]["channels"] == ["legacy:email", "legacy:webhook"]
    assert events["contact.first_seen"]["contact_types"]["repeater"] == ["legacy:webhook"]
    assert events["channel.found"]["channels"] == ["builtin:push"]
    conversations = (await Channels.get_rules())["conversations"]
    assert conversations["channel:public"] == {"legacy:email": True}
    assert conversations["pubkey:" + "ab" * 32] == {"builtin:push": False, "legacy:webhook": True}


@pytest.mark.asyncio
async def test_a_4_18_restore_keeps_the_deprecated_columns_as_derived_values(backup_db):
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    defaults = await AppSettingsRepository.get_push_defaults()
    assert defaults["new_dm"] == {"push": True, "email": True, "webhook": False, "apprise": False}
    assert defaults["oss_update"]["webhook"] is True
    overrides = await AppSettingsRepository.get_push_conversation_overrides()
    assert overrides["channel:public"]["email"] is True


@pytest.mark.asyncio
async def test_a_4_18_export_without_destinations_gets_only_the_push_channel(backup_db):
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18_PLAIN))
    assert [c.id for c in await Channels.list_all()] == ["builtin:push"]
    events = (await Channels.get_rules())["events"]
    assert events["message.dm"]["channels"] == ["builtin:push"]


@pytest.mark.asyncio
async def test_a_4_18_restore_leaves_the_other_channels_and_their_rules_alone(backup_db):
    ntfy = await Channels.create(
        ChannelCreate(type="ntfy", name="Phone", config={"topic": "a-b-c-topic-0123456789"})
    )
    rules = await Channels.get_rules()
    rules.setdefault("events", {})["message.dm"] = {
        "channels": ["builtin:push", ntfy.id],
        "always_deliver": False,
    }
    await Channels.set_rules(rules)

    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))

    assert (await Channels.get(ntfy.id)) is not None
    assert (await Channels.get_rules())["events"]["message.dm"]["channels"] == [
        "builtin:push",
        "legacy:email",
        ntfy.id,
    ]


@pytest.mark.asyncio
async def test_a_4_18_file_through_the_restore_job(backup_db, setup_stub):
    from app.models import BackupV1Document

    job = await run(BackupV1Document.model_validate(V1_4_18), steps=_meshloom_only())
    assert statuses(job)["meshloom"][0] == "done"
    assert {c.id for c in await Channels.list_all()} == {
        "builtin:push",
        "legacy:email",
        "legacy:webhook",
    }


# -- v2 section ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_v2_file_recreates_channels_without_secrets_and_the_rules(backup_db):
    warnings = await restore_sections({NOTIFICATIONS_SECTION: _section()})
    assert warnings == ["notification_secrets_missing:2"]

    channels = {c.id: c for c in await Channels.list_all()}
    assert set(channels) == {"builtin:push", "webhook-1a2b3c4d", "ntfy-0f0f0f0f", "email-9e8d7c6b"}
    webhook = channels["webhook-1a2b3c4d"]
    assert (webhook.enabled, webhook.last_error, webhook.secret_fields_set) == (
        False,
        "secret_missing",
        [],
    )
    ntfy = channels["ntfy-0f0f0f0f"]
    assert (ntfy.enabled, ntfy.last_error, ntfy.rate_per_hour) == (False, "secret_missing", 30)
    assert ntfy.include_message_text == "public_only"
    email = channels["email-9e8d7c6b"]
    assert (email.enabled, email.last_error) == (True, None)
    assert email.config["host"] == "smtp.example.org"

    rules = await Channels.get_rules()
    assert rules["events"]["message.dm"]["channels"] == [
        "builtin:push",
        "webhook-1a2b3c4d",
        "ntfy-0f0f0f0f",
    ]
    # An id that exists nowhere is not left in the rules.
    assert rules["events"]["radio.offline"]["channels"] == ["ntfy-0f0f0f0f", "email-9e8d7c6b"]
    assert rules["conversations"] == {"channel:public": {"ntfy-0f0f0f0f": False}}
    assert rules["keywords"][0]["channels"] == ["ntfy-0f0f0f0f"]


@pytest.mark.asyncio
async def test_enabling_a_restored_channel_needs_its_secret_and_clears_the_state(backup_db):
    from app.notifications.channels import ChannelError, ChannelUpdate

    await restore_sections({NOTIFICATIONS_SECTION: _section()})
    with pytest.raises(ChannelError) as refused:
        await Channels.update("ntfy-0f0f0f0f", ChannelUpdate(enabled=True))
    assert refused.value.code == "config_required"

    updated = await Channels.update(
        "ntfy-0f0f0f0f",
        ChannelUpdate(config={"topic": "restored-topic-0123456789"}, enabled=True),
    )
    assert updated.enabled and updated.last_error is None
    assert updated.secret_fields_set == ["topic"]


@pytest.mark.asyncio
async def test_restoring_onto_the_same_channel_keeps_its_secret(backup_db):
    created = await Channels.create(
        ChannelCreate(
            type="ntfy", id="ntfy-0f0f0f0f", name="Phone", config={"topic": "kept-topic-0123456789"}
        )
    )
    await restore_sections({NOTIFICATIONS_SECTION: _section()})
    channel = await Channels.get(created.id)
    assert channel is not None
    assert (channel.enabled, channel.last_error, channel.secret_fields_set) == (
        True,
        None,
        ["topic"],
    )
    found = await Channels.get_for_send(created.id)
    assert found is not None and found[1]["topic"] == "kept-topic-0123456789"


@pytest.mark.asyncio
async def test_a_moved_destination_never_keeps_the_old_secret(backup_db):
    await Channels.create(
        ChannelCreate(
            type="ntfy", id="ntfy-0f0f0f0f", name="Phone", config={"topic": "old-topic-0123456789"}
        )
    )
    # The stored channel points somewhere else than the file's (set apart: no DNS in tests).
    async with backup_db.tx() as conn:
        await conn.execute(
            "UPDATE notification_channels SET config = ? WHERE id = 'ntfy-0f0f0f0f'",
            (json.dumps({"server": "https://ntfy.example.net"}),),
        )
    invalidate_cache()
    await restore_sections({NOTIFICATIONS_SECTION: _section()})
    channel = await Channels.get("ntfy-0f0f0f0f")
    assert channel is not None
    assert (channel.enabled, channel.last_error, channel.secret_fields_set) == (
        False,
        "secret_missing",
        [],
    )
    assert channel.config["server"] == "https://ntfy.sh"


@pytest.mark.asyncio
async def test_a_bad_channel_is_dropped_and_the_others_restored(backup_db):
    section = _section()
    channels = section.data["channels"]
    channels.append({**channels[1], "id": "mystery-00000000", "type": "carrier-pigeon"})
    channels.append({**channels[3], "id": "email-bad", "config": {"port": "not-a-port"}})
    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})
    assert "notification_channel_dropped:mystery-00000000" in warnings
    assert "notification_channel_dropped:email-bad" in warnings
    assert await Channels.get("email-9e8d7c6b") is not None


@pytest.mark.asyncio
async def test_the_legacy_channels_only_take_their_own_options(backup_db):
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    section = BackupSection(
        data={
            "channels": [
                {
                    "id": "legacy:email",
                    "type": "email",
                    "name": "Mail",
                    "enabled": False,
                    "config": {"host": "evil.example.net", "to": "x@example.net"},
                    "secret_omitted": {"password": True},
                    "language": "fr",
                }
            ],
            "rules": {},
        }
    )
    await restore_sections({NOTIFICATIONS_SECTION: section})
    email = await Channels.get("legacy:email")
    assert email is not None
    assert (email.enabled, email.language) == (False, "fr")
    assert email.config["host"] == "smtp.example.org"
    assert email.secret_fields_set == ["password"]


@pytest.mark.asyncio
async def test_a_legacy_channel_absent_from_this_database_is_not_recreated(backup_db):
    section = BackupSection(
        data={
            "channels": [{"id": "legacy:webhook", "type": "webhook", "name": "Webhook"}],
            "rules": {},
        }
    )
    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})
    assert warnings == []
    assert await Channels.get("legacy:webhook") is None


@pytest.mark.asyncio
async def test_restore_runs_in_a_transaction_and_a_pause_is_not_brought_back(backup_db):
    section = _section()
    section.data["rules"]["paused_until"] = 4_000_000_000
    await restore_sections({NOTIFICATIONS_SECTION: section})
    assert (await Channels.get_rules())["paused_until"] is None

    broken = BackupSection(data={"channels": "nope"})
    warnings = await restore_sections({NOTIFICATIONS_SECTION: broken})
    assert warnings == ["section_failed:notifications"]
    assert await Channels.get("email-9e8d7c6b") is not None  # the earlier restore stands


@pytest.mark.asyncio
async def test_the_restore_hook_mirrors_the_rules_into_the_preference_columns(backup_db):
    from app.services.backup_sections import run_on_restore_hooks

    await restore_sections({NOTIFICATIONS_SECTION: _section()})
    await run_on_restore_hooks("json")
    defaults = await AppSettingsRepository.get_push_defaults()
    assert defaults["new_dm"]["push"] is True
    assert (await Channels.list_all()) and await Channels.get("webhook-1a2b3c4d")


# -- export --------------------------------------------------------------------


async def _engine_with_secrets() -> None:
    await Channels.create(
        ChannelCreate(
            type="webhook",
            id="webhook-aaaa1111",
            name="Ops",
            config={"url": "https://hooks.example.org/in/zzz999", "hmac_secret": "hmac-zzz999"},
        )
    )
    await Channels.create(
        ChannelCreate(
            type="ntfy", id="ntfy-bbbb2222", name="Phone", config={"topic": "topic-zzz999-0123456"}
        )
    )
    rules = await Channels.get_rules()
    rules["paused_until"] = 4_000_000_000
    rules.setdefault("events", {})["message.dm"] = {
        "channels": ["builtin:push", "webhook-aaaa1111", "ntfy-bbbb2222"],
        "always_deliver": False,
    }
    await Channels.set_rules(rules)


@pytest.mark.asyncio
async def test_the_full_backup_holds_no_channel_secret(backup_db):
    await _engine_with_secrets()
    doc = await export_backup_v2()
    text = json.dumps(export_document(doc))
    for secret in ("hooks.example.org", "hmac-zzz999", "topic-zzz999"):
        assert secret not in text

    section = doc.sections[NOTIFICATIONS_SECTION]
    assert section.sensitive is False
    assert section.secrets_omitted is True
    by_id = {c["id"]: c for c in section.data["channels"]}
    assert by_id["webhook-aaaa1111"]["secret_omitted"] == {"hmac_secret": True, "url": True}
    assert by_id["ntfy-bbbb2222"]["secret_omitted"] == {"topic": True}
    assert "url" not in by_id["webhook-aaaa1111"]["config"]
    assert section.data["rules"]["paused_until"] is None
    assert section.data["rules"]["events"]["message.dm"]["channels"][1:] == [
        "webhook-aaaa1111",
        "ntfy-bbbb2222",
    ]


@pytest.mark.asyncio
async def test_round_trip_recreates_channels_disabled_and_the_rules(backup_db):
    await _engine_with_secrets()
    exported = await export_backup_v2()
    section = exported.sections[NOTIFICATIONS_SECTION]
    rules_before = await Channels.get_rules()
    for channel_id in ("webhook-aaaa1111", "ntfy-bbbb2222"):
        await Channels.delete(channel_id)
    await Channels.set_rules(
        {**rules_before, "events": {"message.dm": {"channels": [], "always_deliver": False}}}
    )

    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})

    assert warnings == ["notification_secrets_missing:2"]
    for channel_id in ("webhook-aaaa1111", "ntfy-bbbb2222"):
        channel = await Channels.get(channel_id)
        assert channel is not None
        assert (channel.enabled, channel.last_error, channel.secret_fields_set) == (
            False,
            "secret_missing",
            [],
        )
        assert (await Channels.get_for_send(channel_id)) is not None
        assert (await Channels.get_for_send(channel_id))[1] == {}  # type: ignore[index]
    rules = await Channels.get_rules()
    assert rules["events"]["message.dm"]["channels"] == [
        "builtin:push",
        "webhook-aaaa1111",
        "ntfy-bbbb2222",
    ]
    assert rules["paused_until"] is None


@pytest.mark.asyncio
async def test_a_round_trip_through_the_restore_job(backup_db, setup_stub):
    await _engine_with_secrets()
    exported = await export_backup_v2()
    for channel_id in ("webhook-aaaa1111", "ntfy-bbbb2222"):
        await Channels.delete(channel_id)
    doc = BackupExportV2.model_validate(export_document(exported))
    job = await run(doc, steps=_meshloom_only())
    assert statuses(job)["meshloom"][0] == "done"
    assert "notification_secrets_missing:2" in job.result.warnings
    assert (await Channels.get("ntfy-bbbb2222")) is not None


@pytest.mark.asyncio
async def test_the_json_download_and_the_debug_snapshot_hold_no_channel_secret(backup_db, client):
    await _engine_with_secrets()

    download = await client.get("/api/settings/backup/json")
    debug = await client.get("/api/debug")

    for response in (download, debug):
        assert response.status_code == 200, response.text
        for secret in ("hooks.example.org", "hmac-zzz999", "topic-zzz999"):
            assert secret not in response.text
    assert "notification_channels" not in download.text
    listed = {c["id"]: c for c in debug.json()["notification_channels"]}
    assert listed["webhook-aaaa1111"]["secret_fields_set"] == ["hmac_secret", "url"]
    assert listed["ntfy-bbbb2222"]["type"] == "ntfy"
    assert "config" not in listed["ntfy-bbbb2222"]


@pytest.mark.asyncio
async def test_the_v2_fixture_is_a_valid_document(backup_db):
    doc = BackupExportV2.model_validate(V2_NOTIFICATIONS)
    assert NOTIFICATIONS_SECTION in doc.sections


# -- review fixes (PR #272, round 1) --------------------------------------------


def _email_section(**overrides):
    channel = {
        "id": "email-aaaa0001",
        "type": "email",
        "name": "Mail",
        "config": {"host": "smtp.example.org", "to": "ops@example.org"},
        "secret_omitted": {"password": True},
    }
    channel.update(overrides)
    return BackupSection(data={"channels": [channel], "rules": {}})


@pytest.mark.asyncio
async def test_an_omitted_optional_secret_blocks_enabling_until_typed_or_cleared(backup_db):
    await restore_sections({NOTIFICATIONS_SECTION: _email_section()})
    channel = await Channels.get("email-aaaa0001")
    assert channel is not None
    assert (channel.enabled, channel.last_error) == (False, "secret_missing")

    with pytest.raises(ChannelError) as refused:
        await Channels.update("email-aaaa0001", ChannelUpdate(enabled=True))
    assert refused.value.code == "secret_missing"
    assert refused.value.params == {"fields": ["password"]}
    # Editing another field does not clear the state.
    await Channels.update(
        "email-aaaa0001",
        ChannelUpdate(
            config={"host": "smtp.example.org", "to": "ops@example.org", "cc": "a@example.org"}
        ),
    )
    again = await Channels.get("email-aaaa0001")
    assert again is not None and again.last_error == "secret_missing"

    # Typing the password again lets the channel be enabled.
    done = await Channels.update(
        "email-aaaa0001", ChannelUpdate(config={"password": "typed-again"}, enabled=True)
    )
    assert done.enabled and done.last_error is None


@pytest.mark.asyncio
async def test_clearing_an_omitted_secret_on_purpose_lets_the_channel_be_enabled(backup_db):
    await restore_sections({NOTIFICATIONS_SECTION: _email_section()})
    done = await Channels.update(
        "email-aaaa0001", ChannelUpdate(config={"password": ""}, enabled=True)
    )
    assert done.enabled and done.last_error is None


@pytest.mark.asyncio
async def test_a_webhook_needs_every_omitted_secret_not_only_the_url(backup_db):
    section = BackupSection(
        data={
            "channels": [
                {
                    "id": "webhook-aaaa0002",
                    "type": "webhook",
                    "name": "Hook",
                    "secret_omitted": {"url": True, "hmac_secret": True},
                }
            ],
            "rules": {},
        }
    )
    await restore_sections({NOTIFICATIONS_SECTION: section})
    with pytest.raises(ChannelError) as refused:
        await Channels.update(
            "webhook-aaaa0002",
            ChannelUpdate(config={"url": "https://hooks.example.org/x"}, enabled=True),
        )
    assert refused.value.params == {"fields": ["hmac_secret"]}
    saved = await Channels.update(
        "webhook-aaaa0002",
        ChannelUpdate(config={"url": "https://hooks.example.org/x"}),
    )
    assert not saved.enabled and saved.last_error == "secret_missing"


@pytest.mark.asyncio
async def test_deleting_a_waiting_channel_forgets_its_pending_secrets(backup_db):
    await restore_sections({NOTIFICATIONS_SECTION: _email_section()})
    await Channels.delete("email-aaaa0001")
    async with backup_db.readonly() as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM notification_state WHERE key LIKE 'secret_pending:%'"
        ) as cursor:
            assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_malformed_rules_are_sanitized_and_served_in_a_shape_the_ui_reads(backup_db, client):
    section = BackupSection(
        data={
            "channels": [],
            "rules": {
                "events": {
                    "message.dm": {"channels": 5},
                    "radio.offline": {"channels": ["builtin:push"], "always_deliver": True},
                    "bogus.event": {"channels": []},
                },
                "keywords": "zz",
                "conversations": "q",
                "watched_nodes": [{"channels": "x"}, {"channels": ["builtin:push"], "key": "ab"}],
            },
        }
    )
    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})
    assert "notification_rules_sanitized" in warnings
    response = await client.get("/api/notifications/rules")
    body = response.json()
    assert body["events"] == {
        "radio.offline": {"channels": ["builtin:push"], "always_deliver": True}
    }
    assert body["keywords"] == [] and body["conversations"] == {}
    assert [w["channels"] for w in body["watched_nodes"]] == [["builtin:push"]]


@pytest.mark.asyncio
async def test_the_restore_keeps_the_invariants_of_create(backup_db):
    base = {"name": "X", "config": {"host": "smtp.example.org", "to": "ops@example.org"}}
    section = BackupSection(
        data={
            "channels": [
                {"id": "push-2", "type": "web_push", "name": "Second push"},
                {"id": "evil:id", **base, "type": "email"},
                {"id": "email-empty", "type": "email", "name": "Empty", "config": {}},
                {"id": "builtin:other", "type": "web_push", "name": "Other"},
                {"id": "email-ok", **base, "type": "email"},
                {"id": "email-ok", **base, "type": "email"},
            ],
            "rules": {},
        }
    )
    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})
    assert sorted(w for w in warnings if w.startswith("notification_channel_dropped")) == [
        "notification_channel_dropped:builtin:other",
        "notification_channel_dropped:duplicate",
        "notification_channel_dropped:email-empty",
        "notification_channel_dropped:invalid_id",
        "notification_channel_dropped:push-2",
    ]
    ids = sorted(c.id for c in await Channels.list_all())
    assert ids == ["builtin:push", "email-ok"]


@pytest.mark.asyncio
async def test_the_full_backup_carries_no_legacy_destination_secret(backup_db):
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    doc = await export_backup_v2()
    text = json.dumps(export_document(doc))
    for secret in SECRETS:
        assert secret not in text
    assert doc.settings is not None
    assert doc.secret_omitted["settings.notification_destinations.email.password"] is True
    section = doc.sections[NOTIFICATIONS_SECTION]
    legacy = {c["id"]: c for c in section.data["channels"]}
    assert legacy["legacy:email"]["secret_omitted"] == {"password": True}


@pytest.mark.asyncio
async def test_restoring_that_full_backup_keeps_the_stored_destination_secrets(backup_db):
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    doc = await export_backup_v2()
    exported = BackupExportV2.model_validate(export_document(doc))
    request = BackupRestoreRequest(
        confirm=True,
        settings=exported.settings,
        push_defaults=exported.push_defaults,
        push_conversation_overrides=exported.push_conversation_overrides,
    )
    await restore_json(request)
    found = await Channels.get_for_send("legacy:email")
    assert found is not None and found[1]["password"] == "smtp-pass-4182"


@pytest.mark.asyncio
async def test_a_fresh_instance_warns_when_the_destination_secrets_are_missing(backup_db):
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    doc = await export_backup_v2()
    section = doc.sections[NOTIFICATIONS_SECTION]
    async with backup_db.tx() as conn:
        await conn.execute("DELETE FROM notification_channels WHERE id LIKE 'legacy:%'")
    from app.repository.notification_channels import invalidate_cache

    invalidate_cache()
    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})
    assert "notification_destination_secrets_missing" in warnings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preset_id", "config"),
    [
        ("sms_free", {"user": "12345678"}),
        ("sms_sfr", {"username": "u", "space_id": "s1", "to": "+33600000000"}),
    ],
)
async def test_a_restore_cannot_loosen_a_locked_sms_text_policy(backup_db, preset_id, config):
    section = _section()
    section.data["channels"].append(
        {
            **section.data["channels"][1],
            "id": f"sms-{preset_id}",
            "type": "apprise",
            "preset": preset_id,
            "config": config,
            "secret_omitted": {},
            "include_message_text": "always",
        }
    )
    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})
    assert f"notification_text_policy_forced:sms-{preset_id}" in warnings
    stored = await Channels.get(f"sms-{preset_id}")
    assert stored is not None and stored.include_message_text == "never"


@pytest.mark.asyncio
async def test_a_pre_release_slack_channel_is_dropped_by_the_restore(backup_db):
    section = _section()
    section.data["channels"].append(
        {
            **section.data["channels"][1],
            "id": "slack-old00001",
            "type": "apprise",
            "preset": "slack",
            "config": {"token_a": "T0AAAAAA", "token_b": "B0BBBBBB"},
            "secret_omitted": {"token_c": True},
        }
    )
    warnings = await restore_sections({NOTIFICATIONS_SECTION: section})
    assert "notification_channel_dropped:slack-old00001" in warnings
    assert await Channels.get("slack-old00001") is None
