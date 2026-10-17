"""Every app_settings column and every table has a backup decision.

One direction only: a new column or table fails until it is listed in
BACKUP_COVERAGE; extra entries (a column another branch adds) are allowed.
"""

import pytest

from app.services.backup import BACKUP_COVERAGE


@pytest.mark.asyncio
async def test_every_column_and_table_has_a_decision(test_db):
    async with test_db.readonly() as conn:
        async with conn.execute("PRAGMA table_info(app_settings)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
            tables = [row[0] for row in await cursor.fetchall()]

    expected = {f"app_settings.{column}" for column in columns}
    expected |= {table for table in tables if not table.startswith("sqlite_")}
    missing = sorted(expected - set(BACKUP_COVERAGE))
    assert not missing, f"no backup decision for: {missing} (add them to BACKUP_COVERAGE)"


def test_every_decision_has_a_reason():
    for name, (decision, reason) in BACKUP_COVERAGE.items():
        assert decision in ("included", "excluded"), name
        assert reason.strip(), name


def test_secrets_of_other_hosts_and_consent_are_excluded():
    assert BACKUP_COVERAGE["app_settings.community_enabled"][0] == "excluded"
    assert BACKUP_COVERAGE["app_settings.radio_ble_pin"][0] == "excluded"
    assert BACKUP_COVERAGE["app_settings.radio_bound_public_key"][0] == "excluded"
    assert BACKUP_COVERAGE["messages"][0] == "excluded"
    assert BACKUP_COVERAGE["app_settings.id"] == ("excluded", "internal")


def test_every_v1_setting_marked_included_is_written_back():
    """A column marked included as a v1 setting is exported in ``settings``
    and must be written back by the restore: an AppSettings field and an
    ``AppSettingsRepository.update`` argument, in the restored list."""
    import inspect

    from app.models import AppSettings
    from app.repository import AppSettingsRepository
    from app.services.backup import _RESTORED_SETTINGS, _V1

    v1_columns = {
        name.removeprefix("app_settings.")
        for name, (decision, reason) in BACKUP_COVERAGE.items()
        if name.startswith("app_settings.") and decision == "included" and reason == _V1
    }
    assert v1_columns == set(_RESTORED_SETTINGS)
    update_args = set(inspect.signature(AppSettingsRepository.update).parameters)
    for column in sorted(v1_columns):
        assert column in AppSettings.model_fields, column
        assert column in update_args, column
