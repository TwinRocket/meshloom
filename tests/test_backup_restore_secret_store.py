"""A full restore that would have to encrypt a secret without a usable ``secrets.key``
is refused before anything is written (MSHL-397), instead of failing with
``secret_store_unavailable`` halfway through the Meshloom step."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import app.main  # noqa: F401  (setup_logging runs at import, never inside a test)
import app.services.backup_restore as restore_module
from app import secret_store
from app.models import BackupExportV2, BackupRestoreSteps
from app.services.backup_restore import RestoreOptions, restore_needs_secret_store

FIXTURES = Path(__file__).parent / "fixtures" / "backup"
V1_4_18 = json.loads((FIXTURES / "v1_4.18.2_destinations.json").read_text())
V1_4_18_PLAIN = json.loads((FIXTURES / "v1_4.18.2.json").read_text())
V2_NOTIFICATIONS = json.loads((FIXTURES / "v2_notifications.json").read_text())
KEPT_TOPIC = "kept-topic-0123456789"

FILE_SECRET = "file-pass-1"
TABLES = ("contacts", "channels", "app_settings", "fanout_configs", "notification_channels")


@pytest.fixture(autouse=True)
def _no_jobs():
    restore_module._jobs.clear()
    restore_module._running_job_id = None
    yield
    restore_module._jobs.clear()
    restore_module._running_job_id = None


@pytest.fixture
def public_dns(monkeypatch):
    """Every host name resolves to one public address (the egress guard of the channels)."""
    import ipaddress
    import socket

    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = "93.184.216.34"
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _block_key(tmp_path, monkeypatch) -> None:
    """From now on ``secrets.key`` can be neither read nor created (its folder is a file)."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")
    monkeypatch.setattr(secret_store, "key_path", lambda: blocker / secret_store.KEY_FILENAME)
    secret_store.reset()
    assert secret_store.available() is False


@pytest.fixture
def no_key(tmp_path, monkeypatch):
    key_path = secret_store.key_path
    _block_key(tmp_path, monkeypatch)
    yield
    monkeypatch.setattr(secret_store, "key_path", key_path)
    secret_store.reset()


def _fanout(entry_id: str = "f-broker", **config) -> dict:
    return {
        "id": entry_id,
        "type": "mqtt_private",
        "name": "Broker",
        "enabled": False,
        "config": {"broker_host": "broker.example.org", **config},
        "scope": {"messages": "all", "raw_packets": "none"},
    }


def _doc(fanout: list[dict]) -> dict:
    return {
        "format": "meshloom-backup-v2",
        "exported_at": 1,
        "contacts": [{"public_key": "11" * 32, "name": "Alice"}],
        "channels": [{"key": "AB" * 16, "name": "#restored"}],
        "settings": {"max_radio_contacts": 123},
        "meshloom_extra": {"fanout_configs": fanout},
    }


async def _fingerprint(db) -> str:
    dump: dict[str, list] = {}
    async with db.tx() as conn:
        for table in TABLES:
            async with conn.execute(f"SELECT * FROM {table} ORDER BY 1") as cursor:  # noqa: S608
                dump[table] = [list(row) for row in await cursor.fetchall()]
    return json.dumps(dump, sort_keys=True, default=str)


async def _wait_job(client, job_id: str) -> dict:
    for _ in range(500):
        job = (await client.get(f"/api/settings/backup/restore/jobs/{job_id}")).json()
        if job["state"] != "running":
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


@pytest.mark.asyncio
async def test_secrets_without_a_key_refuse_the_whole_restore_up_front(backup_db, client, no_key):
    before = await _fingerprint(backup_db)
    response = await client.post(
        "/api/settings/backup/restore",
        json={"confirm": True, "backup": _doc([_fanout(password=FILE_SECRET)])},
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "backup_secret_store_unavailable"
    assert FILE_SECRET not in response.text
    # Nothing applied: not even the contacts, channels and settings that come first.
    assert await _fingerprint(backup_db) == before
    assert restore_module._jobs == {}
    assert restore_module.restore_job_running() is False


@pytest.mark.asyncio
async def test_a_file_without_secrets_restores_without_a_key(backup_db, client, no_key):
    response = await client.post(
        "/api/settings/backup/restore",
        json={"confirm": True, "backup": _doc([_fanout()])},
    )
    assert response.status_code == 202, response.text
    job = await _wait_job(client, response.json()["job_id"])
    steps = {step["step"]: step["status"] for step in job["steps"]}
    assert job["state"] == "done" and steps["meshloom"] == "done", job
    assert job["result"]["contacts_upserted"] == 1


@pytest.mark.asyncio
async def test_meshloom_step_off_needs_no_key(backup_db, client, no_key):
    before = await _fingerprint(backup_db)
    response = await client.post(
        "/api/settings/backup/restore",
        json={
            "confirm": True,
            "backup": _doc([_fanout(password=FILE_SECRET)]),
            "steps": {"meshloom": False},
        },
    )
    assert response.status_code == 202, response.text
    job = await _wait_job(client, response.json()["job_id"])
    assert {s["step"]: s["status"] for s in job["steps"]}["meshloom"] == "skipped"
    assert await _fingerprint(backup_db) == before


@pytest.mark.asyncio
async def test_with_a_key_the_same_file_restores_its_secret_encrypted(backup_db, client):
    response = await client.post(
        "/api/settings/backup/restore",
        json={"confirm": True, "backup": _doc([_fanout(password=FILE_SECRET)])},
    )
    assert response.status_code == 202, response.text
    job = await _wait_job(client, response.json()["job_id"])
    assert job["state"] == "done", job
    async with backup_db.tx() as conn:
        async with conn.execute("SELECT config FROM fanout_configs WHERE id = 'f-broker'") as cur:
            row = await cur.fetchone()
    assert row is not None, job
    assert secret_store.is_encrypted(json.loads(row[0])["password"])
    assert FILE_SECRET not in row[0]


@pytest.mark.parametrize(
    ("fanout", "steps", "expected"),
    [
        ([_fanout(password=FILE_SECRET)], {}, True),
        ([_fanout(password="********")], {}, True),  # keeps, and re-seals, the stored one
        ([_fanout(password="")], {}, False),
        ([_fanout()], {}, False),
        ([_fanout("system:meshloom-stats", password=FILE_SECRET)], {}, False),  # never restored
        ([{**_fanout(), "type": "bot", "config": {"code": "x"}}], {}, False),
        ([_fanout(password=FILE_SECRET)], {"meshloom": False}, False),
        ([], {}, False),
    ],
)
@pytest.mark.asyncio
async def test_restore_needs_secret_store(backup_db, fanout, steps, expected):
    doc = BackupExportV2.model_validate(_doc(fanout))
    options = RestoreOptions(steps=BackupRestoreSteps(**steps))
    assert await restore_needs_secret_store(doc, options) is expected


# --- Notification channels (4.18.x destinations and the v2 ``notifications`` section) ---


async def _plain_kept_channel(db) -> None:
    """``ntfy-0f0f0f0f`` (the channel of the v2 fixture) with its topic stored in plain
    text, as a build before MSHL-133 wrote it: the restore keeps it and encrypts it again."""
    from app.notifications.channels import ChannelCreate
    from app.repository.notification_channels import NotificationChannelRepository, invalidate_cache

    created = await NotificationChannelRepository.create(
        ChannelCreate(type="ntfy", id="ntfy-0f0f0f0f", name="Phone", config={"topic": KEPT_TOPIC})
    )
    async with db.tx() as conn:
        await conn.execute(
            "UPDATE notification_channels SET secrets = ? WHERE id = ?",
            (json.dumps({"topic": KEPT_TOPIC}), created.id),
        )
    invalidate_cache()


def _with_notifications(doc: dict) -> dict:
    return {**doc, "sections": {"notifications": V2_NOTIFICATIONS["sections"]["notifications"]}}


async def _refused(client, db, backup: dict) -> None:
    before = await _fingerprint(db)
    response = await client.post(
        "/api/settings/backup/restore", json={"confirm": True, "backup": backup}
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "backup_secret_store_unavailable"
    assert await _fingerprint(db) == before
    assert restore_module._jobs == {}
    assert restore_module.restore_job_running() is False


@pytest.mark.asyncio
async def test_4_18_destinations_without_a_key_refuse_the_whole_restore(
    backup_db, client, public_dns, no_key
):
    """Before MSHL-397 the job ended ``done``: settings applied, the ``legacy:*`` channels
    never created (``sync_destinations`` swallowed the 503)."""
    await _refused(client, backup_db, V1_4_18)


@pytest.mark.asyncio
async def test_a_kept_channel_secret_without_a_key_refuses_the_whole_restore(
    backup_db, client, public_dns, tmp_path, monkeypatch
):
    """Before MSHL-397 the notifications section failed and rolled back while the contacts
    and settings were already applied."""
    await _plain_kept_channel(backup_db)  # with the key: the channel API encrypts on create
    key_path = secret_store.key_path
    _block_key(tmp_path, monkeypatch)
    try:
        await _refused(client, backup_db, _with_notifications(_doc([])))
    finally:
        monkeypatch.setattr(secret_store, "key_path", key_path)
        secret_store.reset()


@pytest.mark.asyncio
async def test_notifications_without_secrets_restore_without_a_key(
    backup_db, client, public_dns, no_key
):
    """A 4.18.x file with empty destinations, and a section whose channels match no stored
    secret, need no key: the job runs."""
    for backup in (V1_4_18_PLAIN, _with_notifications(_doc([]))):
        restore_module._jobs.clear()
        response = await client.post(
            "/api/settings/backup/restore", json={"confirm": True, "backup": backup}
        )
        assert response.status_code == 202, response.text
        job = await _wait_job(client, response.json()["job_id"])
        assert {s["step"]: s["status"] for s in job["steps"]}["meshloom"] == "done", job


@pytest.mark.asyncio
async def test_with_a_key_the_kept_channel_secret_is_encrypted(backup_db, client, public_dns):
    await _plain_kept_channel(backup_db)
    response = await client.post(
        "/api/settings/backup/restore",
        json={"confirm": True, "backup": _with_notifications(_doc([]))},
    )
    assert response.status_code == 202, response.text
    job = await _wait_job(client, response.json()["job_id"])
    assert job["state"] == "done", job
    async with backup_db.tx() as conn:
        async with conn.execute(
            "SELECT secrets FROM notification_channels WHERE id = 'ntfy-0f0f0f0f'"
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None and KEPT_TOPIC not in row[0]
    assert secret_store.is_encrypted(json.loads(row[0])["topic"])
