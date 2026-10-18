"""The ``users`` section of the full backup (Access T65, ``services/backup_accounts.py``)."""

from __future__ import annotations

import json
import logging
import time

import pytest

from app.auth.models import TrustedNetworkRule
from app.auth.providers import trusted_network
from app.models import BackupExportV2, BackupRestoreSteps
from app.repository.auth import AuthSessionRepository, AuthSettingsRepository, UserRepository
from app.services.backup import export_backup_v2, export_document
from app.services.backup_accounts import USERS_SECTION
from app.services.backup_sections import NEVER_EXPORTED_SECTIONS, register_backup_section
from tests import test_backup_restore_v2 as restore_v2

run, statuses = restore_v2.run, restore_v2.statuses
setup_stub = restore_v2.setup_stub
_reset_jobs = restore_v2._reset_jobs

HASH_ADMIN = "$argon2id$v=19$m=65536,t=2,p=1$YWRtaW5zYWx0$YWRtaW5oYXNo"
HASH_BOB = "$argon2id$v=19$m=65536,t=2,p=1$Ym9ic2FsdA$Ym9iaGFzaA"
TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def _meshloom_only() -> BackupRestoreSteps:
    return BackupRestoreSteps(
        identity=False, radio_settings=False, channels=False, contacts=False, wifi=False
    )


async def _seed_accounts(test_db) -> None:
    admin = await UserRepository.create("Alice", "admin", password_hash=HASH_ADMIN)
    bob = await UserRepository.create("bob", "member", password_hash=HASH_BOB, display_name="Bob")
    await UserRepository.create("ha-user", "viewer", provider="ingress", external_id="ha-1")
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE users SET totp_secret = ?, totp_enabled_at = 100, totp_last_counter = 7"
            " WHERE id = ?",
            (TOTP_SECRET, bob.id),
        )
    await AuthSessionRepository.create(admin.id, "session-hash-admin", lifetime_s=3600)


async def _accounts(test_db) -> list[tuple]:
    async with test_db.readonly() as conn:
        async with conn.execute(
            "SELECT id, username, username_key, display_name, password_hash, role, provider,"
            " external_id, disabled, totp_secret, totp_enabled_at, totp_last_counter"
            " FROM users ORDER BY id"
        ) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]


async def _count(test_db, table: str) -> int:
    async with test_db.readonly() as conn:
        async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
            row = await cursor.fetchone()
    return int(row[0])


async def _export(**options) -> dict:
    return json.loads(json.dumps(export_document(await export_backup_v2(**options))))


@pytest.mark.asyncio
async def test_a_round_trip_recreates_the_accounts_with_the_same_hashes(
    backup_db, setup_stub, caplog
):
    await _seed_accounts(backup_db)
    before = await _accounts(backup_db)
    exported = await _export()
    section = exported["sections"][USERS_SECTION]
    assert section["sensitive"] is True
    assert {user["username"] for user in section["data"]["users"]} == {"Alice", "bob", "ha-user"}
    assert "section_sensitive_unencrypted:users" in exported["warnings"]

    # Another instance: other accounts, a session of its own.
    async with backup_db.tx() as conn:
        await conn.execute("DELETE FROM users")
    other = await UserRepository.create("mallory", "admin", password_hash="$argon2id$other")
    await AuthSessionRepository.create(other.id, "session-hash-other", lifetime_s=3600)

    caplog.set_level(logging.DEBUG)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert "accounts_restored" in job.result.warnings
    assert await _accounts(backup_db) == before
    # Every session is gone, the restored ones were never in the file.
    assert await _count(backup_db, "sessions") == 0
    # No hash or TOTP secret in any log line.
    for secret in (HASH_ADMIN, HASH_BOB, TOTP_SECRET):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_restored_accounts_move_updated_at_forward(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    for user in exported["sections"][USERS_SECTION]["data"]["users"]:
        user["updated_at"] = 5
    started = int(time.time())
    await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    async with backup_db.readonly() as conn:
        async with conn.execute("SELECT MIN(updated_at) FROM users") as cursor:
            row = await cursor.fetchone()
    assert row[0] >= started  # every known-device cookie is invalidated


@pytest.mark.asyncio
async def test_a_file_without_accounts_keeps_the_current_admin(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export(include_accounts=False)
    assert USERS_SECTION not in exported["sections"]
    assert "section_sensitive_unencrypted:users" not in exported["warnings"]
    before = await _accounts(backup_db)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _accounts(backup_db) == before
    assert await _count(backup_db, "sessions") == 1


@pytest.mark.asyncio
async def test_a_section_without_an_enabled_admin_keeps_the_current_accounts(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    for user in users:
        if user["role"] == "admin":
            user["disabled"] = True
    before = await _accounts(backup_db)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_no_admin" in job.result.warnings
    assert "accounts_restored" not in job.result.warnings
    assert await _accounts(backup_db) == before
    assert await _count(backup_db, "sessions") == 1

    exported["sections"][USERS_SECTION]["data"]["users"] = []
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_no_admin" in job.result.warnings
    assert await _accounts(backup_db) == before


@pytest.mark.asyncio
async def test_an_invalid_section_fails_closed(backup_db, setup_stub, caplog):
    await _seed_accounts(backup_db)
    exported = await _export()
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    users[1]["role"] = "root"  # unknown role
    before = await _accounts(backup_db)
    caplog.set_level(logging.DEBUG)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "section_failed:users" in job.result.warnings
    assert await _accounts(backup_db) == before
    for secret in (HASH_ADMIN, HASH_BOB, TOTP_SECRET):
        assert secret not in caplog.text

    # Two rows with one username: refused by the unique key, rolled back.
    exported = await _export()
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    users[1]["username"] = "ALICE"
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "section_failed:users" in job.result.warnings
    assert await _accounts(backup_db) == before


@pytest.mark.asyncio
async def test_sessions_pairing_codes_audit_and_remote_identity_are_ignored(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    for name in exported["sections"]:
        assert name not in NEVER_EXPORTED_SECTIONS
    text = json.dumps(exported)
    assert "session-hash-admin" not in text
    for name in sorted(NEVER_EXPORTED_SECTIONS):
        exported["sections"][name] = {
            "version": 1,
            "sensitive": True,
            "data": [{"id": "s1", "user_id": 1, "token_hash": "planted"}],
        }
    exported["sections"][USERS_SECTION]["data"]["users"][0]["sessions"] = [
        {"id": "s2", "token_hash": "planted"}
    ]
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _count(backup_db, "sessions") == 0
    async with backup_db.readonly() as conn:
        async with conn.execute("SELECT * FROM auth_audit") as cursor:
            audit = [tuple(row) for row in await cursor.fetchall()]
    assert "planted" not in json.dumps(audit)  # only the restore's own audit row
    for name in NEVER_EXPORTED_SECTIONS:
        with pytest.raises(ValueError):
            register_backup_section(
                name,
                _export,  # never called
                _export,  # never called
                description={"en": "x", "fr": "x"},
                sensitive=True,
            )


@pytest.mark.asyncio
async def test_trusted_networks_keep_only_rules_naming_the_same_account(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    alice = await UserRepository.get_by_username("Alice")
    bob = await UserRepository.get_by_username("bob")
    assert alice is not None and bob is not None
    # The file renames account `bob`'s id to someone else.
    for user in exported["sections"][USERS_SECTION]["data"]["users"]:
        if user["id"] == bob.id:
            user["username"] = "eve"
    await AuthSettingsRepository.update(
        trusted_networks=[
            TrustedNetworkRule(cidr="192.168.1.0/24", user_id=alice.id),
            TrustedNetworkRule(cidr="192.168.2.0/24", user_id=bob.id),
        ]
    )
    trusted_network.apply(await AuthSettingsRepository.get())
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "trusted_network_dropped:192.168.2.0/24" in job.result.warnings
    stored = (await AuthSettingsRepository.get()).trusted_networks
    assert [(rule.cidr, rule.user_id) for rule in stored] == [("192.168.1.0/24", alice.id)]
    rules = await trusted_network.current()
    assert [(rule.cidr, rule.user_id) for rule in rules] == [("192.168.1.0/24", alice.id)]


@pytest.mark.asyncio
async def test_the_export_route_honours_the_include_accounts_box(backup_db, client):
    await _seed_accounts(backup_db)
    response = await client.post("/api/settings/backup/export", json={})
    assert response.status_code == 200, response.text
    assert USERS_SECTION in response.json()["sections"]
    response = await client.post("/api/settings/backup/export", json={"include_accounts": False})
    assert response.status_code == 200, response.text
    body = response.json()
    assert USERS_SECTION not in body["sections"]
    assert HASH_ADMIN not in response.text


@pytest.mark.asyncio
async def test_inspect_describes_the_accounts_section(backup_db, client):
    await _seed_accounts(backup_db)
    exported = export_document(await export_backup_v2())
    response = await client.post("/api/settings/backup/inspect", json={"backup": exported})
    assert response.status_code == 200, response.text
    (summary,) = [s for s in response.json()["sections"] if s["name"] == USERS_SECTION]
    assert summary["known"] is True
    assert summary["sensitive"] is True
    assert set(summary["description"]) == {"en", "fr"}
