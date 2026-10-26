"""A full restore that would have to encrypt a secret without a usable ``secrets.key``
is refused before anything is written (MSHL-397), instead of failing with
``secret_store_unavailable`` halfway through the Meshloom step."""

from __future__ import annotations

import asyncio
import json

import pytest

import app.main  # noqa: F401  (setup_logging runs at import, never inside a test)
import app.services.backup_restore as restore_module
from app import secret_store
from app.models import BackupExportV2, BackupRestoreSteps
from app.services.backup_restore import RestoreOptions, restore_needs_secret_store

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
def no_key(tmp_path, monkeypatch):
    """``secrets.key`` can be neither read nor created (its folder is a file)."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")
    key_path = secret_store.key_path
    monkeypatch.setattr(secret_store, "key_path", lambda: blocker / secret_store.KEY_FILENAME)
    secret_store.reset()
    assert secret_store.available() is False
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
def test_restore_needs_secret_store(fanout, steps, expected):
    doc = BackupExportV2.model_validate(_doc(fanout))
    options = RestoreOptions(steps=BackupRestoreSteps(**steps))
    assert restore_needs_secret_store(doc, options) is expected
