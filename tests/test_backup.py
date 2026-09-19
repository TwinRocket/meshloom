"""JSON backup/restore and SQLite snapshot. Restore must not wipe the live DB."""

import tempfile
from pathlib import Path

import aiosqlite
import pytest
from fastapi import HTTPException

from app.models import (
    BackupContact,
    BackupExport,
    BackupRestoreRequest,
    ContactGroup,
    ContactUpsert,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactGroupRepository,
    ContactRepository,
    MessageRepository,
)
from app.routers.settings import restore_json_backup
from app.services.backup import export_json, restore_json, write_sqlite_backup


class TestSqliteBackup:
    @pytest.mark.asyncio
    async def test_snapshot_contains_settings_row(self, test_db):
        with tempfile.TemporaryDirectory() as tmp:
            dest = str(Path(tmp) / "copy.db")
            await write_sqlite_backup(test_db, dest)
            conn = await aiosqlite.connect(dest)
            try:
                cursor = await conn.execute("SELECT id FROM app_settings WHERE id = 1")
                assert await cursor.fetchone() is not None
            finally:
                await conn.close()


class TestJsonBackup:
    @pytest.mark.asyncio
    async def test_export_includes_contacts_channels_groups(self, test_db):
        key = "aa" * 32
        await ContactRepository.upsert(ContactUpsert(public_key=key, name="Alice", type=1))
        await ChannelRepository.upsert("BB" * 16, "public", is_hashtag=True)
        group = await ContactGroupRepository.create("Home")
        await ContactGroupRepository.set_members(group.id, [key])

        export = await export_json()
        assert export.format == "meshloom-backup-v1"
        assert any(c.public_key == key for c in export.contacts)
        assert any(c.name == "public" for c in export.channels)
        assert export.settings is not None
        assert export.settings.stale_contact_days == 0
        assert export.groups[0].name == "Home"
        assert export.groups[0].public_keys == [key]

    @pytest.mark.asyncio
    async def test_restore_accepts_legacy_remoteterm_format(self, test_db):
        result = await restore_json(
            BackupRestoreRequest(confirm=True, format="remoteterm-backup-v1")
        )
        assert result.contacts_upserted == 0

    @pytest.mark.asyncio
    async def test_restore_requires_confirm(self, test_db):
        with pytest.raises(ValueError, match="confirm"):
            await restore_json(BackupRestoreRequest(confirm=False))

        with pytest.raises(HTTPException) as exc:
            await restore_json_backup(BackupRestoreRequest(confirm=False))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_restore_merges_and_keeps_existing_messages(self, test_db):
        existing = "11" * 32
        incoming = "22" * 32
        await ContactRepository.upsert(ContactUpsert(public_key=existing, name="KeepMe", type=1))
        await MessageRepository.create(
            msg_type="PRIV",
            conversation_key=existing,
            text="hello",
            sender_timestamp=1,
            received_at=1,
        )

        payload = BackupRestoreRequest(
            confirm=True,
            contacts=[BackupContact(public_key=incoming, name="Imported", type=1, favorite=True)],
            settings=None,
            groups=[],
        )
        result = await restore_json(payload)
        assert result.contacts_upserted == 1
        assert await ContactRepository.get_by_key(existing) is not None
        imported = await ContactRepository.get_by_key(incoming)
        assert imported is not None
        assert imported.name == "Imported"
        assert imported.favorite is True

        stored = await MessageRepository.get_all(
            msg_type="PRIV", conversation_key=existing, limit=10
        )
        assert any(m.text == "hello" for m in stored)

    @pytest.mark.asyncio
    async def test_restore_groups_by_name(self, test_db):
        key = "33" * 32
        await ContactRepository.upsert(ContactUpsert(public_key=key, name="Ned", type=1))
        await restore_json(
            BackupRestoreRequest(
                confirm=True,
                contacts=[],
                groups=[
                    ContactGroup(
                        id=99,
                        name="Crew",
                        sort_order=0,
                        created_at=1,
                        public_keys=[key],
                    )
                ],
            )
        )
        groups = await ContactGroupRepository.list_all()
        assert len(groups) == 1
        assert groups[0].name == "Crew"
        assert groups[0].public_keys == [key]


class TestBackupExportShape:
    @pytest.mark.asyncio
    async def test_empty_export_is_valid(self, test_db):
        export = await export_json()
        assert isinstance(export, BackupExport)
        assert export.contacts == []
        assert export.settings is not None

    @pytest.mark.asyncio
    async def test_push_pref_fields_roundtrip(self, test_db):
        from app.repository.settings import DEFAULT_PUSH_DEFAULTS

        await AppSettingsRepository.set_push_defaults({"new_dm": False})
        await AppSettingsRepository.set_push_conversation_overrides(
            {"channel-PUBLIC": True, "contact-" + "aa" * 32: False}
        )
        await AppSettingsRepository.set_vapid_subject("mailto:ops@example.com")

        export = await export_json()
        assert export.push_defaults is not None
        assert export.push_defaults["new_dm"]["push"] is False
        assert export.push_defaults["new_contact"]["push"] is True
        assert export.push_conversation_overrides == {
            "channel-PUBLIC": {"push": True},
            "contact-" + "aa" * 32: {"push": False},
        }
        assert export.vapid_subject == "mailto:ops@example.com"

        await AppSettingsRepository.set_push_defaults(DEFAULT_PUSH_DEFAULTS)
        await AppSettingsRepository.set_push_conversation_overrides({})
        await AppSettingsRepository.set_vapid_subject("")

        result = await restore_json(
            BackupRestoreRequest(
                confirm=True,
                push_defaults=export.push_defaults,
                push_conversation_overrides=export.push_conversation_overrides,
                vapid_subject=export.vapid_subject,
            )
        )
        assert result.settings_updated is True
        assert await AppSettingsRepository.get_push_defaults() == export.push_defaults
        assert (
            await AppSettingsRepository.get_push_conversation_overrides()
            == export.push_conversation_overrides
        )
        assert await AppSettingsRepository.get_vapid_subject() == "mailto:ops@example.com"
