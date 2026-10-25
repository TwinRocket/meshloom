"""Share settings: defaults, PATCH /api/settings, the write-only seed, backup rules."""

import json
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import AppSettings, BackupRestoreRequest
from app.repository import AppSettingsRepository
from app.routers.settings import AppSettingsUpdate, update_settings
from app.services import share
from app.services.backup import BACKUP_COVERAGE, export_json, restore_json
from app.services.share import SHARE_TERMS_VERSION

SEED = "ab" * 32


@pytest.mark.asyncio
async def test_defaults_are_off_auto_private(test_db):
    settings = await AppSettingsRepository.get()
    assert settings.share_enabled is False
    assert settings.share_shorten_mode == "auto"
    assert settings.share_preview_policy == "private"
    assert await AppSettingsRepository.get_instance_seed("share") == ""


@pytest.mark.asyncio
async def test_update_stores_valid_values(test_db):
    result = await update_settings(
        AppSettingsUpdate(
            share_enabled=True,
            share_terms_accepted=True,
            share_shorten_mode="always",
            share_preview_policy="never",
        )
    )
    assert (result.share_enabled, result.share_shorten_mode, result.share_preview_policy) == (
        True,
        "always",
        "never",
    )
    stored = await AppSettingsRepository.get()
    assert (stored.share_enabled, stored.share_shorten_mode, stored.share_preview_policy) == (
        True,
        "always",
        "never",
    )
    # A partial update leaves the rest alone.
    await update_settings(AppSettingsUpdate(share_enabled=False))
    assert (await AppSettingsRepository.get()).share_shorten_mode == "always"


def test_update_refuses_an_unknown_mode():
    with pytest.raises(ValidationError):
        AppSettingsUpdate(share_shorten_mode="sometimes")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        AppSettingsUpdate(share_preview_policy="public")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_patch_route_answers_422_for_an_unknown_mode(test_db, client):
    for body in ({"share_shorten_mode": "x"}, {"share_preview_policy": "x"}):
        assert (await client.patch("/api/settings", json=body)).status_code == 422
    ok = await client.patch(
        "/api/settings", json={"share_shorten_mode": "never", "share_preview_policy": "always"}
    )
    assert ok.status_code == 200
    assert ok.json()["share_shorten_mode"] == "never"


@pytest.mark.asyncio
async def test_the_seed_is_never_returned_by_the_settings_api(test_db, client):
    await AppSettingsRepository.set_instance_seed("share", SEED)
    assert await AppSettingsRepository.get_instance_seed("share") == SEED
    assert "share_instance_seed" not in AppSettings.model_fields
    assert SEED not in (await client.get("/api/settings")).text
    assert SEED not in (await AppSettingsRepository.get()).model_dump_json()


@pytest.mark.asyncio
async def test_a_bad_stored_value_reads_as_the_default(test_db):
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE app_settings SET share_shorten_mode = 'bogus', share_preview_policy = NULL"
        )
    settings = await AppSettingsRepository.get()
    assert settings.share_shorten_mode == "auto"
    assert settings.share_preview_policy == "private"


@pytest.mark.asyncio
async def test_export_never_holds_the_seed(test_db):
    await AppSettingsRepository.set_instance_seed("share", SEED)
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    export = await export_json()
    assert SEED not in json.dumps(export.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_restore_leaves_consent_rotates_seed_and_restores_the_behaviours(test_db):
    await AppSettingsRepository.update(share_shorten_mode="never", share_preview_policy="always")
    export = await export_json()
    assert export.settings is not None
    # The file claims the consent; it must not travel.
    exported = export.settings.model_copy(update={"share_enabled": True})

    await AppSettingsRepository.update(
        share_enabled=False, share_shorten_mode="auto", share_preview_policy="private"
    )
    await AppSettingsRepository.set_instance_seed("share", SEED)
    await restore_json(BackupRestoreRequest(confirm=True, settings=exported))

    stored = await AppSettingsRepository.get()
    assert stored.share_enabled is False
    assert stored.share_shorten_mode == "never"
    assert stored.share_preview_policy == "always"
    # The on_restore hook of app/services/instance_identity.py gives a new identity.
    rotated = await AppSettingsRepository.get_instance_seed("share")
    assert rotated not in ("", SEED)
    assert len(bytes.fromhex(rotated)) == 32

    # And a restore never switches an enabled instance off either.
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    await restore_json(BackupRestoreRequest(confirm=True, settings=exported))
    assert (await AppSettingsRepository.get()).share_enabled is True


@pytest.mark.asyncio
async def test_a_4_18_backup_without_share_fields_restores(test_db):
    await AppSettingsRepository.update(share_shorten_mode="never")
    old = AppSettings.model_validate({"max_radio_contacts": 150})
    assert "share_shorten_mode" not in old.model_fields_set
    await restore_json(BackupRestoreRequest(confirm=True, settings=old))
    stored = await AppSettingsRepository.get()
    assert stored.max_radio_contacts == 150
    assert stored.share_shorten_mode == "never"


def test_backup_decisions():
    assert BACKUP_COVERAGE["app_settings.share_enabled"][0] == "excluded"
    assert BACKUP_COVERAGE["app_settings.share_instance_seed"][0] == "excluded"
    assert BACKUP_COVERAGE["app_settings.share_terms_version"][0] == "excluded"
    assert BACKUP_COVERAGE["app_settings.share_terms_accepted_at"][0] == "excluded"
    assert BACKUP_COVERAGE["app_settings.share_shorten_mode"][0] == "included"
    assert BACKUP_COVERAGE["app_settings.share_preview_policy"][0] == "included"


@pytest.mark.asyncio
async def test_json_export_leaves_the_consent_out(test_db):
    await AppSettingsRepository.update(
        share_enabled=True, share_terms_accepted=True, share_shorten_mode="never"
    )
    dumped = (await export_json()).model_dump(mode="json")
    assert "share_enabled" not in dumped["settings"]
    assert "share_effective" not in dumped["settings"]
    assert "share_terms_version" not in dumped["settings"]
    assert "share_terms_accepted_at" not in dumped["settings"]
    assert dumped["settings"]["share_shorten_mode"] == "never"


@pytest.mark.asyncio
async def test_database_copy_resets_the_consent_and_clears_the_seed(test_db, tmp_path):
    import aiosqlite

    from app.services.backup import write_sqlite_backup

    await AppSettingsRepository.update(
        share_enabled=True, share_terms_accepted=True, share_shorten_mode="never"
    )
    await AppSettingsRepository.set_instance_seed("share", SEED)
    path = tmp_path / "copy.db"
    await write_sqlite_backup(test_db, str(path))
    async with aiosqlite.connect(path) as conn:
        async with conn.execute(
            "SELECT share_enabled, share_terms_version, share_terms_accepted_at,"
            " share_instance_seed, share_shorten_mode FROM app_settings"
        ) as cursor:
            assert tuple(await cursor.fetchone()) == (0, 0, 0, None, "never")
    # The live database keeps the consent.
    assert (await AppSettingsRepository.get()).share_enabled is True


@pytest.mark.asyncio
async def test_defaults_record_no_acceptance(test_db):
    settings = await AppSettingsRepository.get()
    assert (settings.share_terms_version, settings.share_terms_accepted_at) == (0, 0)


@pytest.mark.asyncio
async def test_enabling_without_an_acceptance_is_refused(test_db):
    with pytest.raises(HTTPException) as refused:
        await update_settings(AppSettingsUpdate(share_enabled=True))
    assert (refused.value.status_code, refused.value.detail) == (409, "share_terms_required")
    assert (await AppSettingsRepository.get()).share_enabled is False


@pytest.mark.asyncio
async def test_acceptance_records_the_current_version_and_time(test_db):
    before = int(time.time())
    await update_settings(AppSettingsUpdate(share_enabled=True, share_terms_accepted=True))
    stored = await AppSettingsRepository.get()
    assert stored.share_enabled is True
    assert stored.share_terms_version == SHARE_TERMS_VERSION
    assert before <= stored.share_terms_accepted_at <= int(time.time())


@pytest.mark.asyncio
async def test_a_current_acceptance_survives_turning_sharing_off_and_on(test_db):
    await update_settings(AppSettingsUpdate(share_enabled=True, share_terms_accepted=True))
    accepted_at = (await AppSettingsRepository.get()).share_terms_accepted_at
    await update_settings(AppSettingsUpdate(share_enabled=False))
    await update_settings(AppSettingsUpdate(share_enabled=True))
    stored = await AppSettingsRepository.get()
    assert stored.share_enabled is True
    assert stored.share_terms_accepted_at == accepted_at


@pytest.mark.asyncio
async def test_a_new_terms_version_requires_a_new_acceptance(test_db, monkeypatch):
    await update_settings(AppSettingsUpdate(share_enabled=True, share_terms_accepted=True))
    assert await share.share_egress_state() is not None
    monkeypatch.setattr(share, "SHARE_TERMS_VERSION", SHARE_TERMS_VERSION + 1)
    # Stored consent is stale: no egress, and enabling again needs the dialog.
    assert await share.share_egress_state() is None
    await update_settings(AppSettingsUpdate(share_enabled=False))
    with pytest.raises(HTTPException) as refused:
        await update_settings(AppSettingsUpdate(share_enabled=True))
    assert refused.value.status_code == 409
    await update_settings(AppSettingsUpdate(share_enabled=True, share_terms_accepted=True))
    assert (await AppSettingsRepository.get()).share_terms_version == SHARE_TERMS_VERSION + 1
    assert await share.share_egress_state() is not None


@pytest.mark.asyncio
async def test_acceptance_without_enabling_is_refused(test_db):
    with pytest.raises(HTTPException) as refused:
        await update_settings(AppSettingsUpdate(share_terms_accepted=True))
    assert refused.value.status_code == 422
    assert (await AppSettingsRepository.get()).share_terms_version == 0


@pytest.mark.asyncio
async def test_share_effective_follows_the_accepted_terms(test_db, monkeypatch):
    """``share_enabled`` is the stored choice, ``share_effective`` what is actually on."""
    from app.routers.settings import get_settings

    assert (await get_settings()).share_effective is False

    await update_settings(AppSettingsUpdate(share_enabled=True, share_terms_accepted=True))
    settings = await get_settings()
    assert settings.share_enabled is True
    assert settings.share_effective is True
    assert await share.share_egress_state() is not None

    # The terms moved on since the owner accepted: the choice stays, the effect stops.
    monkeypatch.setattr(share, "SHARE_TERMS_VERSION", SHARE_TERMS_VERSION + 1)
    settings = await get_settings()
    assert settings.share_enabled is True
    assert settings.share_effective is False
    assert await share.share_egress_state() is None

    await update_settings(AppSettingsUpdate(share_enabled=False))
    settings = await get_settings()
    assert settings.share_enabled is False
    assert settings.share_effective is False
