"""Share settings: defaults, PATCH /api/settings, the write-only seed, backup rules."""

import json

import pytest
from pydantic import ValidationError

from app.models import AppSettings, BackupRestoreRequest
from app.repository import AppSettingsRepository
from app.routers.settings import AppSettingsUpdate, update_settings
from app.services.backup import BACKUP_COVERAGE, export_json, restore_json

SEED = "ab" * 32


@pytest.mark.asyncio
async def test_defaults_are_off_auto_private(test_db):
    settings = await AppSettingsRepository.get()
    assert settings.share_enabled is False
    assert settings.share_shorten_mode == "auto"
    assert settings.share_preview_policy == "private"
    assert await AppSettingsRepository.get_share_seed() == ""


@pytest.mark.asyncio
async def test_update_stores_valid_values(test_db):
    result = await update_settings(
        AppSettingsUpdate(
            share_enabled=True, share_shorten_mode="always", share_preview_policy="never"
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
    await AppSettingsRepository.set_share_seed(SEED)
    assert await AppSettingsRepository.get_share_seed() == SEED
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
    await AppSettingsRepository.set_share_seed(SEED)
    await AppSettingsRepository.update(share_enabled=True)
    export = await export_json()
    assert SEED not in json.dumps(export.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_restore_leaves_consent_and_seed_and_restores_the_behaviours(test_db):
    await AppSettingsRepository.update(share_shorten_mode="never", share_preview_policy="always")
    export = await export_json()
    assert export.settings is not None
    # The file claims the consent; it must not travel.
    exported = export.settings.model_copy(update={"share_enabled": True})

    await AppSettingsRepository.update(
        share_enabled=False, share_shorten_mode="auto", share_preview_policy="private"
    )
    await AppSettingsRepository.set_share_seed(SEED)
    await restore_json(BackupRestoreRequest(confirm=True, settings=exported))

    stored = await AppSettingsRepository.get()
    assert stored.share_enabled is False
    assert stored.share_shorten_mode == "never"
    assert stored.share_preview_policy == "always"
    assert await AppSettingsRepository.get_share_seed() == SEED

    # And a restore never switches an enabled instance off either.
    await AppSettingsRepository.update(share_enabled=True)
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
    assert BACKUP_COVERAGE["app_settings.share_shorten_mode"][0] == "included"
    assert BACKUP_COVERAGE["app_settings.share_preview_policy"][0] == "included"


@pytest.mark.asyncio
async def test_json_export_leaves_the_consent_out(test_db):
    await AppSettingsRepository.update(share_enabled=True, share_shorten_mode="never")
    dumped = (await export_json()).model_dump(mode="json")
    assert "share_enabled" not in dumped["settings"]
    assert dumped["settings"]["share_shorten_mode"] == "never"


@pytest.mark.asyncio
async def test_database_copy_resets_the_consent_and_clears_the_seed(test_db, tmp_path):
    import aiosqlite

    from app.services.backup import write_sqlite_backup

    await AppSettingsRepository.update(share_enabled=True, share_shorten_mode="never")
    await AppSettingsRepository.set_share_seed(SEED)
    path = tmp_path / "copy.db"
    await write_sqlite_backup(test_db, str(path))
    async with aiosqlite.connect(path) as conn:
        async with conn.execute(
            "SELECT share_enabled, share_instance_seed, share_shorten_mode FROM app_settings"
        ) as cursor:
            assert tuple(await cursor.fetchone()) == (0, None, "never")
    # The live database keeps the consent.
    assert (await AppSettingsRepository.get()).share_enabled is True
