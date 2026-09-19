"""Vague A: push notification prefs repo, insert reporting, wipe, and prefix remap."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.models import ContactUpsert, TelemetryAlertChannels, TelemetryAlertRules
from app.repository import AppSettingsRepository, ContactRepository
from app.repository.settings import DEFAULT_PUSH_DEFAULTS, _coerce_push_defaults
from app.services.contact_reconciliation import promote_prefix_contacts_for_contact
from app.services.radio_identity import wipe_mesh_identity_data


class TestPushNotificationSettingsRepo:
    @pytest.mark.asyncio
    async def test_defaults_are_all_true(self, test_db):
        defaults = await AppSettingsRepository.get_push_defaults()
        assert defaults == DEFAULT_PUSH_DEFAULTS
        assert all(value["push"] is True for value in defaults.values())
        assert all(value["email"] is False for value in defaults.values())
        assert all(value["webhook"] is False for value in defaults.values())

    @pytest.mark.asyncio
    async def test_set_push_defaults_merges_known_keys(self, test_db):
        updated = await AppSettingsRepository.set_push_defaults({"new_dm": False})
        assert updated["new_dm"]["push"] is False
        assert updated["new_contact"]["push"] is True
        assert updated["channel_found"]["push"] is True
        assert updated["telemetry_alert"]["push"] is True
        stored = await AppSettingsRepository.get_push_defaults()
        assert stored == updated

    @pytest.mark.asyncio
    async def test_set_push_defaults_keeps_other_event_media(self, test_db):
        await AppSettingsRepository.set_push_defaults({"new_dm": {"email": True}})
        stored = await AppSettingsRepository.get_push_defaults()
        assert stored["new_dm"]["email"] is True
        assert stored["new_dm"]["push"] is True
        await AppSettingsRepository.set_push_defaults({"channel_found": False})
        stored = await AppSettingsRepository.get_push_defaults()
        assert stored["new_dm"]["email"] is True
        assert stored["channel_found"]["push"] is False

    def test_coerce_push_defaults_drops_unknown_keys(self):
        coerced = _coerce_push_defaults({"new_dm": False, "not_a_real_key": False})
        assert "not_a_real_key" not in coerced
        assert coerced["new_dm"]["push"] is False
        assert set(coerced) == set(DEFAULT_PUSH_DEFAULTS)

    def test_coerce_push_defaults_keeps_channel_found_and_telemetry_alert(self):
        coerced = _coerce_push_defaults({"channel_found": False, "telemetry_alert": False})
        assert coerced["channel_found"]["push"] is False
        assert coerced["telemetry_alert"]["push"] is False
        assert coerced["new_contact"]["push"] is True
        assert coerced["new_dm"]["push"] is True

    @pytest.mark.asyncio
    async def test_telemetry_alert_media_seeds_from_rules_channels(self, test_db):
        await AppSettingsRepository.update(
            telemetry_alert_rules=TelemetryAlertRules(
                channels=TelemetryAlertChannels(push=False, email=True, webhook=True)
            )
        )
        defaults = await AppSettingsRepository.get_push_defaults()
        assert defaults["telemetry_alert"] == {"push": False, "email": True, "webhook": True}

    @pytest.mark.asyncio
    async def test_set_override_true_false_none(self, test_db):
        key = "contact-" + "aa" * 32
        written = await AppSettingsRepository.set_push_conversation_override(key, True)
        assert written[key] == {"push": True}

        written = await AppSettingsRepository.set_push_conversation_override(key, False)
        assert written[key] == {"push": False}

        written = await AppSettingsRepository.set_push_conversation_override(key, None)
        assert key not in written
        assert await AppSettingsRepository.get_push_conversation_overrides() == {}

    @pytest.mark.asyncio
    async def test_vapid_subject_roundtrip(self, test_db):
        assert await AppSettingsRepository.get_vapid_subject() == ""
        stored = await AppSettingsRepository.set_vapid_subject("mailto:ops@example.com")
        assert stored == "mailto:ops@example.com"
        assert await AppSettingsRepository.get_vapid_subject() == "mailto:ops@example.com"
        await AppSettingsRepository.set_vapid_subject("")
        assert await AppSettingsRepository.get_vapid_subject() == ""


class TestUpsertReportingInsert:
    @pytest.mark.asyncio
    async def test_insert_returns_true_then_false(self, test_db):
        contact = ContactUpsert(public_key="aa" * 32, name="Alice", type=1)
        inserted = await ContactRepository.upsert_reporting_insert(contact)
        assert inserted is True

        again = await ContactRepository.upsert_reporting_insert(
            ContactUpsert(public_key="aa" * 32, name="Alice-2", type=1)
        )
        assert again is False
        stored = await ContactRepository.get_by_key("aa" * 32)
        assert stored is not None
        assert stored.name == "Alice-2"

    @pytest.mark.asyncio
    async def test_does_not_call_get_by_key(self, test_db, monkeypatch):
        monkeypatch.setattr(
            ContactRepository,
            "get_by_key",
            AsyncMock(side_effect=AssertionError("get_by_key must not be called")),
        )
        inserted = await ContactRepository.upsert_reporting_insert(
            ContactUpsert(public_key="bb" * 32, name="Bob", type=1)
        )
        assert inserted is True

    @pytest.mark.asyncio
    async def test_concurrent_upserts_do_not_deadlock(self, test_db):
        contact = ContactUpsert(public_key="cc" * 32, name="Carol", type=1)
        results = await asyncio.gather(
            ContactRepository.upsert_reporting_insert(contact),
            ContactRepository.upsert_reporting_insert(contact),
        )
        assert results.count(True) == 1
        assert results.count(False) == 1
        stored = await ContactRepository.get_by_key("cc" * 32)
        assert stored is not None


class TestWipePushOverrides:
    @pytest.mark.asyncio
    async def test_wipe_drops_contact_overrides_keeps_channel(self, test_db, monkeypatch):
        import app.services.radio_identity as identity

        monkeypatch.setattr(identity, "db", test_db)
        contact_key = "contact-" + "aa" * 32
        channel_key = "channel-" + "BB" * 16
        await AppSettingsRepository.set_push_conversations([contact_key, channel_key])
        await AppSettingsRepository.set_push_conversation_overrides(
            {contact_key: True, channel_key: False}
        )
        async with test_db.tx() as conn:
            await conn.execute(
                "INSERT INTO contacts (public_key, name) VALUES (?, ?)",
                ("aa" * 32, "Alice"),
            )

        await wipe_mesh_identity_data()

        assert await AppSettingsRepository.get_push_conversations() == [channel_key]
        assert await AppSettingsRepository.get_push_conversation_overrides() == {
            channel_key: {"push": False}
        }


class TestPrefixOverrideRemap:
    @pytest.mark.asyncio
    async def test_promote_moves_prefix_override_to_full_key(self, test_db):
        prefix = "aabbccddeeff"
        full_key = prefix + "11" * 26
        await ContactRepository.upsert({"public_key": prefix, "name": "Alice", "type": 0})
        await ContactRepository.upsert({"public_key": full_key, "name": "Alice", "type": 1})
        await AppSettingsRepository.set_push_conversation_override(f"contact-{prefix}", True)

        promoted = await promote_prefix_contacts_for_contact(public_key=full_key)
        assert prefix in promoted

        overrides = await AppSettingsRepository.get_push_conversation_overrides()
        assert f"contact-{prefix}" not in overrides
        assert overrides[f"contact-{full_key}"] == {"push": True}

    @pytest.mark.asyncio
    async def test_promote_does_not_overwrite_existing_full_key_override(self, test_db):
        prefix = "bbccddeeff00"
        full_key = prefix + "22" * 26
        await ContactRepository.upsert({"public_key": prefix, "name": "Bob", "type": 0})
        await ContactRepository.upsert({"public_key": full_key, "name": "Bob", "type": 1})
        await AppSettingsRepository.set_push_conversation_overrides(
            {f"contact-{prefix}": False, f"contact-{full_key}": True}
        )

        promoted = await promote_prefix_contacts_for_contact(public_key=full_key)
        assert prefix in promoted

        overrides = await AppSettingsRepository.get_push_conversation_overrides()
        assert f"contact-{prefix}" not in overrides
        assert overrides[f"contact-{full_key}"] == {"push": True}
