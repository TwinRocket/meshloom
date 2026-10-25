"""Push subscription owners in the full backup, matched by username (MSHL-320)."""

from __future__ import annotations

import json

import pytest

import app.push.vapid as vapid
from app.models import BackupExportV2, BackupRestoreSteps
from app.repository import AppSettingsRepository
from app.repository.auth import UserRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.services.backup import export_backup_v2, export_document
from app.services.backup_accounts import USERS_SECTION
from tests import test_backup_restore_v2 as restore_v2

run, statuses = restore_v2.run, restore_v2.statuses
setup_stub = restore_v2.setup_stub
_reset_jobs = restore_v2._reset_jobs

HASH = "$argon2id$v=19$m=65536,t=2,p=1$YWRtaW5zYWx0$YWRtaW5oYXNo"
ALICE_ENDPOINT = "https://93.184.215.14/push/alice"
BOB_ENDPOINT = "https://93.184.215.15/push/bob"


@pytest.fixture(autouse=True)
def _keep_vapid_cache(monkeypatch):
    for name in ("_cached_private_key", "_cached_public_key", "_cached_subject"):
        monkeypatch.setattr(vapid, name, getattr(vapid, name))


def _meshloom_only() -> BackupRestoreSteps:
    return BackupRestoreSteps(
        identity=False, radio_settings=False, channels=False, contacts=False, wifi=False
    )


async def _seed(backup_db) -> None:
    alice = await UserRepository.create("Alice", "admin", password_hash=HASH)
    bob = await UserRepository.create("bob", "member", password_hash=HASH)
    await AppSettingsRepository.set_vapid_keys("backup-priv", "backup-pub")
    await PushSubscriptionRepository.create(ALICE_ENDPOINT, "k", "a", user_id=alice.id)
    await PushSubscriptionRepository.create(BOB_ENDPOINT, "k", "a", user_id=bob.id)


async def _export(**options) -> dict:
    return json.loads(json.dumps(export_document(await export_backup_v2(**options))))


async def _owners(backup_db) -> dict[str, str | None]:
    """Endpoint -> stored owner's username (None: unowned, the first admin's)."""
    async with backup_db.readonly() as conn:
        async with conn.execute(
            "SELECT ps.endpoint, u.username FROM push_subscriptions ps"
            " LEFT JOIN users u ON u.id = ps.user_id"
        ) as cursor:
            return {row[0]: row[1] for row in await cursor.fetchall()}


async def _effective_owners() -> dict[str, str | None]:
    return {
        sub["endpoint"]: sub["owner_username"]
        for sub in await PushSubscriptionRepository.list_with_owner()
    }


async def _fresh_instance(backup_db, *usernames: str) -> None:
    """Another instance: no subscription, other accounts (the first is the admin)."""
    async with backup_db.tx() as conn:
        await conn.execute("DELETE FROM push_subscriptions")
        await conn.execute("DELETE FROM users")
    for index, name in enumerate(usernames):
        await UserRepository.create(name, "admin" if index == 0 else "member", password_hash=HASH)


@pytest.mark.asyncio
async def test_the_export_names_each_owner_by_username(backup_db, setup_stub):
    await _seed(backup_db)
    exported = await _export()
    subs = {s["endpoint"]: s for s in exported["meshloom_extra"]["push_subscriptions"]}
    assert subs[ALICE_ENDPOINT]["owner"] == "Alice"
    assert subs[BOB_ENDPOINT]["owner"] == "bob"
    assert "user_id" not in subs[BOB_ENDPOINT]


@pytest.mark.asyncio
async def test_full_restore_gives_devices_back_to_owners_found_only_in_the_file(
    backup_db, setup_stub
):
    await _seed(backup_db)
    exported = await _export()
    # Neither Alice nor bob exist here: they come with the accounts section.
    await _fresh_instance(backup_db, "mallory")

    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert "accounts_restored" in job.result.warnings
    assert await _owners(backup_db) == {ALICE_ENDPOINT: "Alice", BOB_ENDPOINT: "bob"}


@pytest.mark.asyncio
async def test_without_accounts_owners_match_by_username_not_by_id(backup_db, setup_stub):
    await _seed(backup_db)  # Alice id 1, bob id 2
    exported = await _export(include_accounts=False)
    assert USERS_SECTION not in exported["sections"]
    # Here id 2 is carol and bob is id 3 (case differs): the username decides.
    await _fresh_instance(backup_db, "root", "carol", "BOB")

    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    owners = await _owners(backup_db)
    assert owners[BOB_ENDPOINT] == "BOB"
    # Alice has no account here: the first administrator (the pre-MSHL-320 rule).
    assert owners[ALICE_ENDPOINT] == "root"


@pytest.mark.asyncio
async def test_an_older_backup_without_owners_goes_to_the_first_admin(backup_db, setup_stub):
    await _seed(backup_db)
    exported = await _export()
    for sub in exported["meshloom_extra"]["push_subscriptions"]:
        del sub["owner"]
    await _fresh_instance(backup_db, "mallory")

    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _effective_owners() == {ALICE_ENDPOINT: "Alice", BOB_ENDPOINT: "Alice"}


@pytest.mark.asyncio
async def test_accounts_restore_keeps_device_owners_by_username(backup_db, setup_stub):
    await _seed(backup_db)
    exported = await _export()
    # The file carries accounts only: no push data, bob under another id.
    exported["meshloom_extra"]["vapid"] = None
    exported["meshloom_extra"]["push_subscriptions"] = []
    users = exported["sections"][USERS_SECTION]["data"]["users"]
    exported["sections"][USERS_SECTION]["data"]["users"] = [
        {**user, "id": 7} if user["username"] == "bob" else user for user in users
    ]
    carol = await UserRepository.create("carol", "member", password_hash=HASH)
    carol_endpoint = "https://93.184.215.16/push/carol"
    await PushSubscriptionRepository.create(carol_endpoint, "k", "a", user_id=carol.id)

    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert "accounts_restored" in job.result.warnings
    owners = await _owners(backup_db)
    assert owners[ALICE_ENDPOINT] == "Alice"
    assert owners[BOB_ENDPOINT] == "bob"
    assert (await UserRepository.get_by_username("bob")).id == 7
    # carol is not in the file: unowned, the first administrator's.
    assert owners[carol_endpoint] is None
    assert (await _effective_owners())[carol_endpoint] == "Alice"
