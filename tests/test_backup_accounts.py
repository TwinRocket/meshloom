"""The ``users`` section of the full backup (Access T65, ``services/backup_accounts.py``)."""

from __future__ import annotations

import json
import logging
import time

import pytest

from app.auth.models import TrustedNetworkRule
from app.auth.providers import trusted_network
from app.models import BackupExportV2, BackupRestoreSteps
from app.repository.auth import (
    ApiTokenRepository,
    AuthSessionRepository,
    AuthSettingsRepository,
    UserRepository,
)
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
RECOVERY_HASH = "$argon2id$v=19$m=1024,t=2,p=1$cmVjb3Zlcnk$cmVjb3ZlcnloYXNo"


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
        await conn.execute(
            "INSERT INTO recovery_codes (user_id, code_hash, created_at, used_at)"
            " VALUES (?, ?, 100, NULL), (?, ?, 100, 150)",
            (bob.id, RECOVERY_HASH, bob.id, RECOVERY_HASH),
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
        ],
        lan_login_free=True,
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


def _only_admin(exported: dict, **changes) -> list[dict]:
    """Make the file's only admin look like ``changes``."""
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    for user in users:
        if user["role"] == "admin":
            user.update(changes)
    return users


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "ingress", "external_id": "ha-admin", "password_hash": None},
        {"password_hash": None},
        {"provider": "system", "role": "kiosk"},
    ],
)
async def test_an_admin_who_cannot_sign_in_here_keeps_the_current_accounts(
    backup_db, setup_stub, changes
):
    await _seed_accounts(backup_db)
    exported = await _export()
    _only_admin(exported, **changes)
    before = await _accounts(backup_db)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_no_admin" in job.result.warnings
    assert await _accounts(backup_db) == before
    assert await _count(backup_db, "sessions") == 1


@pytest.mark.asyncio
async def test_a_home_assistant_admin_counts_on_the_add_on(backup_db, setup_stub, monkeypatch):
    monkeypatch.setattr("app.services.backup_accounts._is_addon", lambda: True)
    await _seed_accounts(backup_db)
    exported = await _export()
    _only_admin(exported, provider="ingress", external_id="ha-admin", password_hash=HASH_ADMIN)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_restored" in job.result.warnings
    async with backup_db.readonly() as conn:
        async with conn.execute(
            "SELECT provider, password_hash FROM users WHERE role = 'admin'"
        ) as cursor:
            # The hash a Home Assistant identity must not carry is stripped.
            assert [tuple(row) for row in await cursor.fetchall()] == [("ingress", None)]


@pytest.mark.asyncio
async def test_password_material_is_stripped_where_the_api_forbids_it(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    users.append(
        {
            "id": 50,
            "username": "wall",
            "role": "kiosk",
            "provider": "local",
            "password_hash": HASH_BOB,
            "totp_secret": TOTP_SECRET,
            "totp_enabled_at": 1,
            "must_change_password": True,
            "created_at": 1,
            "updated_at": 1,
        }
    )
    for user in users:
        if user["provider"] == "ingress":
            user.update(password_hash=HASH_BOB, totp_secret=TOTP_SECRET)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_restored" in job.result.warnings
    async with backup_db.readonly() as conn:
        async with conn.execute(
            "SELECT password_hash, totp_secret, totp_enabled_at, must_change_password"
            " FROM users WHERE role = 'kiosk' OR provider = 'ingress'"
        ) as cursor:
            rows = [tuple(row) for row in await cursor.fetchall()]
    assert rows == [(None, None, None, 0), (None, None, None, 0)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"provider": "system", "role": "admin"},
        {"password_hash": "$argon2id$v=19$m=4194304,t=2,p=1$c2FsdA$aGFzaA"},
        {"password_hash": "$argon2id$v=19$m=65536,t=99,p=1$c2FsdA$aGFzaA"},
        {"password_hash": "$2b$12$notargon2"},
    ],
)
async def test_a_forbidden_account_or_costly_hash_fails_closed(backup_db, setup_stub, change):
    await _seed_accounts(backup_db)
    exported = await _export()
    for user in exported["sections"][USERS_SECTION]["data"]["users"]:
        if user["username"] == "bob":
            user.update(change)
    before = await _accounts(backup_db)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "section_failed:users" in job.result.warnings
    assert await _accounts(backup_db) == before


@pytest.mark.asyncio
async def test_updated_at_moves_past_the_replaced_row(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    future = int(time.time()) + 1000
    async with backup_db.tx() as conn:
        await conn.execute("UPDATE users SET updated_at = ?", (future,))
    for user in exported["sections"][USERS_SECTION]["data"]["users"]:
        user["updated_at"] = future - 1  # +1 would equal the stored generation
    await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    async with backup_db.readonly() as conn:
        async with conn.execute("SELECT MIN(updated_at) FROM users") as cursor:
            row = await cursor.fetchone()
    assert row[0] == future + 1


@pytest.mark.asyncio
async def test_a_restore_closes_the_sockets_of_every_account_at_once(
    backup_db, setup_stub, monkeypatch
):
    from app.websocket import ws_manager

    closed: list[int] = []
    monkeypatch.setattr(ws_manager, "close_user", lambda user_id, **_: closed.append(user_id) or 0)
    await _seed_accounts(backup_db)
    exported = await _export()
    ids = {user["id"] for user in exported["sections"][USERS_SECTION]["data"]["users"]}
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_restored" in job.result.warnings
    assert set(closed) == ids

    closed.clear()
    _only_admin(exported, password_hash=None)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_no_admin" in job.result.warnings
    assert closed == []  # a refused file leaves every socket open


async def _tokens(test_db) -> list[tuple]:
    async with test_db.readonly() as conn:
        async with conn.execute(
            "SELECT id, user_id, name, token_hash, scopes, expires_at, origin FROM api_tokens"
            " ORDER BY id"
        ) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_api_tokens_round_trip_with_their_hashes_never_their_secrets(
    backup_db, setup_stub, caplog
):
    from app.auth.providers import token as token_provider
    from app.services.backup_accounts import API_TOKENS_SECTION

    await _seed_accounts(backup_db)
    bob = await UserRepository.get_by_username("bob")
    alice = await UserRepository.get_by_username("Alice")
    assert bob is not None and alice is not None
    _, secret = await token_provider.issue_token(bob, "phone", ["read", "send"])
    revoked, _ = await token_provider.issue_token(alice, "old", ["admin"])
    await token_provider.revoke_token(revoked.id)
    live = [row for row in await _tokens(backup_db) if row[0] != revoked.id]

    exported = await _export()
    section = exported["sections"][API_TOKENS_SECTION]
    assert section["sensitive"] is True
    assert [t["name"] for t in section["data"]["tokens"]] == ["phone"]
    assert secret not in json.dumps(exported)

    # Without accounts, no token leaves either.
    assert API_TOKENS_SECTION not in (await _export(include_accounts=False))["sections"]

    async with backup_db.tx() as conn:
        await conn.execute("DELETE FROM users")
    await UserRepository.create("mallory", "admin", password_hash="$argon2id$other")
    caplog.set_level(logging.DEBUG)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _tokens(backup_db) == live
    assert await token_provider.resolve_token(secret, "lan", None) is not None
    assert live[0][3] not in caplog.text


@pytest.mark.asyncio
async def test_a_restored_token_beyond_its_owners_role_or_without_owner_is_dropped(
    backup_db, setup_stub
):
    from app.auth.providers import token as token_provider
    from app.services.backup_accounts import API_TOKENS_SECTION

    await _seed_accounts(backup_db)
    bob = await UserRepository.get_by_username("bob")
    assert bob is not None
    kept, _ = await token_provider.issue_token(bob, "phone", ["read"])
    exported = await _export()
    tokens = exported["sections"][API_TOKENS_SECTION]["data"]["tokens"]
    tokens.append({**tokens[0], "id": "a" * 32, "token_hash": "b" * 64, "scopes": ["admin"]})
    tokens.append({**tokens[0], "id": "c" * 32, "token_hash": "d" * 64, "user_id": 999})

    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert f"api_token_dropped:{'a' * 32}" in job.result.warnings
    assert f"api_token_dropped:{'c' * 32}" in job.result.warnings
    assert [row[0] for row in await _tokens(backup_db)] == [kept.id]


@pytest.mark.asyncio
async def test_a_users_restore_without_the_tokens_section_leaves_no_token(backup_db, setup_stub):
    from app.auth.providers import token as token_provider
    from app.services.backup_accounts import API_TOKENS_SECTION

    await _seed_accounts(backup_db)
    exported = await _export()
    assert API_TOKENS_SECTION not in exported["sections"]
    bob = await UserRepository.get_by_username("bob")
    assert bob is not None
    await token_provider.issue_token(bob, "made-after-the-backup", ["read"])
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _tokens(backup_db) == []


@pytest.mark.asyncio
async def test_a_refused_accounts_section_never_restores_the_files_tokens(backup_db, setup_stub):
    """Review of #238: the file's tokens must never sign in as a current account
    that only shares an id with their owner."""
    from app.auth.providers import token as token_provider
    from app.services.backup_accounts import API_TOKENS_SECTION

    await _seed_accounts(backup_db)
    bob = await UserRepository.get_by_username("bob")
    assert bob is not None
    _, file_secret = await token_provider.issue_token(bob, "phone", ["read", "send"])
    exported = await _export()
    for user in exported["sections"][USERS_SECTION]["data"]["users"]:
        if user["role"] == "admin":
            user["disabled"] = True

    # This instance: id 2 is now carol, with a token of her own.
    # The file's token was revoked here since the backup.
    (here,) = await ApiTokenRepository.list_live()
    await token_provider.revoke_token(here.id)
    async with backup_db.tx() as conn:
        await conn.execute(
            "UPDATE users SET username = 'carol', username_key = 'carol' WHERE id = ?", (bob.id,)
        )
    carol = await UserRepository.get(bob.id)
    assert carol is not None
    current, current_secret = await token_provider.issue_token(carol, "laptop", ["read"])

    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_no_admin" in job.result.warnings
    assert f"section_skipped:{API_TOKENS_SECTION}" in job.result.warnings
    assert await token_provider.resolve_token(file_secret, "lan", None) is None
    principal = await token_provider.resolve_token(current_secret, "lan", None)
    assert principal is not None and principal.token_id == current.id


@pytest.mark.asyncio
async def test_a_failed_token_section_rolls_the_accounts_back(backup_db, setup_stub):
    from app.auth.providers import token as token_provider
    from app.services.backup_accounts import API_TOKENS_SECTION

    await _seed_accounts(backup_db)
    bob = await UserRepository.get_by_username("bob")
    assert bob is not None
    await token_provider.issue_token(bob, "phone", ["read"])
    exported = await _export()
    exported["sections"][API_TOKENS_SECTION]["data"]["tokens"][0]["token_hash"] = "not-hex"
    async with backup_db.tx() as conn:
        await conn.execute("UPDATE users SET display_name = 'changed' WHERE id = ?", (bob.id,))
    before = await _accounts(backup_db)
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert f"section_failed:{USERS_SECTION}" in job.result.warnings
    assert f"section_failed:{API_TOKENS_SECTION}" in job.result.warnings
    assert await _accounts(backup_db) == before


async def _recovery_codes(test_db) -> list[tuple]:
    async with test_db.readonly() as conn:
        async with conn.execute(
            "SELECT user_id, code_hash, created_at, used_at FROM recovery_codes ORDER BY id"
        ) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]


@pytest.mark.asyncio
async def test_recovery_codes_travel_with_the_accounts_only(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    before = await _recovery_codes(backup_db)
    assert len(before) == 2
    exported = await _export()
    section = exported["sections"][USERS_SECTION]
    assert section["version"] == 3
    bob = next(user for user in section["data"]["users"] if user["username"] == "bob")
    assert [code["used_at"] for code in bob["recovery_codes"]] == [None, 150]

    without = json.dumps(await _export(include_accounts=False))
    assert TOTP_SECRET not in without and RECOVERY_HASH not in without

    async with backup_db.tx() as conn:
        await conn.execute("DELETE FROM recovery_codes")
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _recovery_codes(backup_db) == before


@pytest.mark.asyncio
async def test_a_version_1_section_restores_without_recovery_codes(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    section = exported["sections"][USERS_SECTION]
    section["version"] = 1
    for user in section["data"]["users"]:
        user.pop("recovery_codes")
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _recovery_codes(backup_db) == []
    accounts = {row[1]: row for row in await _accounts(backup_db)}
    assert accounts["bob"][9] == TOTP_SECRET


@pytest.mark.asyncio
async def test_recovery_codes_need_an_enabled_factor_and_an_affordable_hash(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    alice = next(user for user in users if user["username"] == "Alice")
    alice["recovery_codes"] = [{"code_hash": RECOVERY_HASH, "created_at": 1}]
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert {row[0] for row in await _recovery_codes(backup_db)} == {
        next(user["id"] for user in users if user["username"] == "bob")
    }

    bob = next(user for user in users if user["username"] == "bob")
    bob["recovery_codes"] = [
        {"code_hash": "$argon2id$v=19$m=4194304,t=9,p=1$c2FsdA$aGFzaA", "created_at": 1}
    ]
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert RECOVERY_HASH not in json.dumps(job.model_dump(), default=str)
    assert len(await _recovery_codes(backup_db)) == 2  # the section was refused, nothing changed


async def _passkeys(test_db) -> list[tuple]:
    async with test_db.readonly() as conn:
        async with conn.execute(
            "SELECT user_id, credential_id, public_key, sign_count, transports, aaguid, name,"
            " backup_eligible, backed_up, created_at, last_used_at"
            " FROM webauthn_credentials ORDER BY id"
        ) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]


async def _seed_passkeys(test_db) -> None:
    users = {user.username: user.id for user in await UserRepository.list_all()}
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, sign_count,"
            " transports, aaguid, name, backup_eligible, backed_up, created_at, last_used_at)"
            " VALUES (?, x'0a0b0c', x'a50102', 7, '[\"usb\"]', 'aa-guid', 'Key', 1, 0, 100, 200),"
            " (?, x'0d0e0f', x'a50103', 0, '[]', '', 'Phone', 0, 0, 100, NULL)",
            (users["Alice"], users["bob"]),
        )


@pytest.mark.asyncio
async def test_passkeys_travel_with_the_accounts_only(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    await _seed_passkeys(backup_db)
    before = await _passkeys(backup_db)
    exported = await _export()
    section = exported["sections"][USERS_SECTION]
    assert section["version"] == 3
    alice = next(user for user in section["data"]["users"] if user["username"] == "Alice")
    assert alice["passkeys"][0]["credential_id"] == "CgsM"
    assert alice["passkeys"][0]["transports"] == ["usb"]

    without = json.dumps(await _export(include_accounts=False))
    assert "CgsM" not in without and "passkeys" not in without

    async with backup_db.tx() as conn:
        await conn.execute("DELETE FROM webauthn_credentials")
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _passkeys(backup_db) == before


@pytest.mark.asyncio
async def test_passkeys_restore_only_on_accounts_that_can_have_them(backup_db, setup_stub):
    await _seed_accounts(backup_db)
    exported = await _export()
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    passkey = {"credential_id": "AQID", "public_key": "pQEC", "name": "Key", "created_at": 1}
    for user in users:
        user["passkeys"] = [passkey]
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    # One credential id once; the Home Assistant identity gets none.
    rows = await _passkeys(backup_db)
    assert [row[0] for row in rows] == [next(u["id"] for u in users if u["username"] == "Alice")]

    section = exported["sections"][USERS_SECTION]
    section["version"] = 2
    for user in section["data"]["users"]:
        user.pop("passkeys")
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _passkeys(backup_db) == []

    users[0]["passkeys"] = [{**passkey, "credential_id": "not base64!"}]
    section["version"] = 3
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert f"section_failed:{USERS_SECTION}" in job.result.warnings
