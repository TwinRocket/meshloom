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
