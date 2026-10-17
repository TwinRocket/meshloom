"""Full backup v2 envelope: named sections, identity seeds, restore hooks.

Builds on the full backup tests (``test_backup_restore_v2.py`` helpers) with
two files kept as fixtures: a real 4.18.2 export (``meshloom-backup-v1``) and
an export of the #156 full backup (``meshloom-backup-v2`` without sections).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite
import pytest

import app.keystore as keystore
import app.services.backup_sections as sections_module
from app.models import (
    BackupExportV2,
    BackupRestoreRequest,
    BackupRestoreSteps,
    BackupSection,
    BackupV1Document,
)
from app.repository import AppSettingsRepository, ChannelRepository, ContactRepository
from app.repository.fanout import FanoutConfigRepository
from app.services.backup import (
    BackupError,
    export_backup_v2,
    export_document,
    export_json,
    restore_json,
    write_sqlite_backup,
)
from app.services.backup_sections import (
    SQLITE_SNAPSHOT_MARKER,
    register_backup_section,
    register_on_restore,
    run_sqlite_restore_hooks,
)
from tests import test_backup_restore_v2 as restore_v2
from tests.conftest import patched_database
from tests.fixtures.fake_node import FakeNode, connected_node

# The full backup tests' helpers and fixtures (the job runner, the setup stub).
run, statuses = restore_v2.run, restore_v2.statuses
setup_stub = restore_v2.setup_stub
_reset_jobs = restore_v2._reset_jobs

FIXTURES = Path(__file__).parent / "fixtures" / "backup"
V1_4_18 = json.loads((FIXTURES / "v1_4.18.2.json").read_text())
V2_PR156 = json.loads((FIXTURES / "v2_pr156.json").read_text())
PR156_KEY = bytes(range(1, 65))  # the identity in v2_pr156.json
SEED_REMOTE = "5eed" * 16
SEED_SHARE = "a11ce5eed" * 8


@pytest.fixture(autouse=True)
def _clean_registry():
    keystore.clear_keys()
    sections = dict(sections_module._sections)
    hooks = dict(sections_module._restore_hooks)
    sections_module._sections.clear()
    sections_module._restore_hooks.clear()
    yield
    sections_module._sections.clear()
    sections_module._sections.update(sections)
    sections_module._restore_hooks.clear()
    sections_module._restore_hooks.update(hooks)
    keystore.clear_keys()


@pytest.fixture
def hook_calls() -> list[str]:
    calls: list[str] = []

    async def hook(source):
        calls.append(source)

    register_on_restore("test", hook)
    return calls


def _meshloom_only() -> BackupRestoreSteps:
    return BackupRestoreSteps(
        identity=False, radio_settings=False, channels=False, contacts=False, wifi=False
    )


async def _v1_export() -> dict:
    """What a restored database holds, as the v1 document (no timestamps)."""
    exported = (await export_json()).model_dump(mode="json", exclude={"exported_at"})
    for group in exported["groups"]:
        group.pop("created_at", None)
        group.pop("id", None)
    return exported


class _Store:
    """A feature's data: a token list and a stored secret."""

    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.secret: str | None = None


def _register_store(name: str, store: _Store, *, requires=(), omit_secret=False) -> None:
    async def export():
        if not store.tokens and store.secret is None:
            return None
        return BackupSection(
            secrets_omitted=omit_secret,
            data={"tokens": store.tokens, "secret": None if omit_secret else store.secret},
        )

    async def restore(section: BackupSection) -> list[str]:
        store.tokens = list(section.data["tokens"])
        if section.data.get("secret") is not None or not section.secrets_omitted:
            store.secret = section.data.get("secret")
        return []

    register_backup_section(name, export, restore, requires=requires)


# -- v1 and #156 files still restore -----------------------------------------


@pytest.mark.asyncio
async def test_a_4_18_export_restores_flat_and_through_the_job(backup_db, setup_stub, hook_calls):
    result = await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    assert (result.contacts_upserted, result.channels_upserted, result.groups_upserted) == (
        2,
        3,
        1,
    )
    flat = await _v1_export()
    job = await run(BackupV1Document.model_validate(V1_4_18))
    assert statuses(job)["meshloom"] == ("done", None)
    assert await _v1_export() == flat
    assert (await ContactRepository.get_by_key("a1" * 32)).favorite
    assert (await AppSettingsRepository.get()).advert_interval == 7200
    assert hook_calls == ["json", "json"]


@pytest.mark.asyncio
async def test_a_pr156_full_backup_restores(backup_db, setup_stub, hook_calls):
    from app.repository.radio_transport import RadioTransportRepository

    doc = BackupExportV2.model_validate(V2_PR156)
    assert doc.sections == {}
    node = FakeNode(private_key=PR156_KEY)
    await RadioTransportRepository.bind_public_key(node.public_key)
    async with connected_node(node):
        job = await run(doc)
    assert job.state == "done", statuses(job)
    assert statuses(job)["meshloom"] == ("done", None)
    assert statuses(job)["wifi"][0] == "done"
    assert (node.stored_ssid, node.stored_pwd) == ("Home Net", "wifi-pass-1")
    assert (await FanoutConfigRepository.get("hook-1"))["type"] == "webhook"
    assert await ChannelRepository.get_by_key("C0" * 16) is not None
    assert hook_calls == ["json"]


@pytest.mark.asyncio
async def test_a_v2_round_trip_without_sections_equals_a_v1_round_trip(monkeypatch, setup_stub):
    import app.database as database_module
    import app.services.radio_identity as radio_identity

    async def fresh():
        context = patched_database()
        database = await context.__aenter__()
        monkeypatch.setattr(database_module, "db", database)
        monkeypatch.setattr(radio_identity, "db", database)
        return context

    source = await fresh()
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    v1 = json.loads(json.dumps((await export_json()).model_dump(mode="json")))
    v2 = json.loads(json.dumps(export_document(await export_backup_v2())))
    await source.__aexit__(None, None, None)
    assert v2["sections"] == {}

    through_v1 = await fresh()
    await restore_json(BackupRestoreRequest(confirm=True, **v1))
    after_v1 = await _v1_export()
    await through_v1.__aexit__(None, None, None)

    through_v2 = await fresh()
    job = await run(BackupExportV2.model_validate(v2), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    after_v2 = await _v1_export()
    await through_v2.__aexit__(None, None, None)

    assert after_v2 == after_v1


# -- sections ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_registered_section_is_exported_and_restored(backup_db, setup_stub):
    store = _Store()
    store.tokens, store.secret = ["t1", "t2"], "s3cr3t"
    _register_store("tokens", store)

    exported = json.loads(json.dumps(export_document(await export_backup_v2())))
    assert exported["sections"]["tokens"] == {
        "version": 1,
        "secrets_omitted": False,
        "data": {"tokens": ["t1", "t2"], "secret": "s3cr3t"},
    }
    store.tokens, store.secret = [], None
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert statuses(job)["meshloom"] == ("done", None)
    assert (store.tokens, store.secret) == (["t1", "t2"], "s3cr3t")


@pytest.mark.asyncio
async def test_secrets_omitted_keeps_the_stored_secret(backup_db, setup_stub):
    store = _Store()
    store.tokens, store.secret = ["t1"], "kept"
    _register_store("tokens", store, omit_secret=True)
    exported = export_document(await export_backup_v2())
    assert "kept" not in json.dumps(exported)
    store.tokens = []
    await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert (store.tokens, store.secret) == (["t1"], "kept")


@pytest.mark.asyncio
async def test_a_section_missing_from_the_file_leaves_its_data(backup_db, setup_stub):
    store = _Store()
    store.tokens = ["mine"]
    _register_store("tokens", store)
    await run(BackupExportV2.model_validate(V2_PR156), steps=_meshloom_only())
    assert store.tokens == ["mine"]


@pytest.mark.asyncio
async def test_an_unknown_section_is_ignored_with_a_log_line(backup_db, setup_stub, caplog):
    raw = {**V2_PR156, "sections": {"from_the_future": {"version": 7, "data": {"x": [1]}}}}
    with caplog.at_level(logging.INFO, logger="app.services.backup_sections"):
        job = await run(BackupExportV2.model_validate(raw), steps=_meshloom_only())
    assert job.state == "done"
    assert statuses(job)["meshloom"] == ("done", None)
    assert job.result is not None and not any("from_the_future" in w for w in job.result.warnings)
    assert any("'from_the_future' is not known" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_requires_orders_the_restore_and_skips_without_its_dependency(backup_db, setup_stub):
    order: list[str] = []

    def section(name: str):
        async def export():
            return BackupSection(data=name)

        async def restore(sec: BackupSection) -> list[str]:
            order.append(sec.data)
            return []

        return export, restore

    register_backup_section("api_tokens", *section("api_tokens"), requires=["users"])
    register_backup_section("users", *section("users"))
    exported = export_document(await export_backup_v2())
    await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert order == ["users", "api_tokens"]

    order.clear()
    del exported["sections"]["users"]
    job = await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert order == []
    assert job.result is not None and "section_skipped:api_tokens" in job.result.warnings


@pytest.mark.asyncio
async def test_a_failing_section_is_a_warning_not_a_failed_restore(backup_db, setup_stub):
    async def export():
        return BackupSection(data=1)

    async def broken(_section) -> list[str]:
        raise RuntimeError("boom")

    async def broken_export():
        raise RuntimeError("boom")

    register_backup_section("broken", export, broken)
    register_backup_section("unexportable", broken_export, broken)
    doc = await export_backup_v2()
    assert "unexportable" not in doc.sections
    assert "section_export_failed:unexportable" in doc.warnings
    job = await run(BackupExportV2.model_validate(export_document(doc)), steps=_meshloom_only())
    assert job.state == "done"
    assert job.result is not None and "section_failed:broken" in job.result.warnings


def test_registration_refuses_bad_names_duplicates_and_cycles():
    async def export():
        return None

    async def restore(_section) -> list[str]:
        return []

    for bad in ("", "Users", "1st", "a-b", "x" * 49):
        with pytest.raises(ValueError):
            register_backup_section(bad, export, restore)
    register_backup_section("a", export, restore, requires=["b"])
    with pytest.raises(ValueError):
        register_backup_section("a", export, restore)
    with pytest.raises(ValueError):
        register_backup_section("b", export, restore, requires=["a"])
    assert sections_module.registered_backup_sections() == ["a"]


# -- identity seeds --------------------------------------------------------------


@pytest.fixture
async def seeded(backup_db):
    """The per-service instance identity seeds as their tickets add them."""
    async with backup_db.tx() as conn:
        for column, value in (("remote_instance_seed", SEED_REMOTE), ("share_seed", SEED_SHARE)):
            await conn.execute(f"ALTER TABLE app_settings ADD COLUMN {column} TEXT")
            await conn.execute(f"UPDATE app_settings SET {column} = ? WHERE id = 1", (value,))
    return backup_db


@pytest.mark.asyncio
@pytest.mark.parametrize("with_private_key", [False, True])
async def test_no_identity_seed_in_any_export(seeded, with_private_key):
    node = FakeNode(private_key=PR156_KEY)
    if with_private_key:
        keystore.set_private_key(node.private_key)
    else:
        node.export_disabled = True
    async with connected_node(node):
        full = export_document(await export_backup_v2())
    assert (full["identity"]["private_key"] is not None) is with_private_key
    v1 = (await export_json()).model_dump(mode="json")
    for text in (json.dumps(full), json.dumps(v1)):
        assert SEED_REMOTE not in text
        assert SEED_SHARE not in text


@pytest.mark.asyncio
async def test_a_section_leaking_a_seed_refuses_the_export(seeded):
    async def leak():
        return BackupSection(data={"oops": SEED_REMOTE})

    async def restore(_section) -> list[str]:
        return []

    register_backup_section("leaky", leak, restore)
    with pytest.raises(BackupError) as caught:
        await export_backup_v2()
    assert caught.value.code == "backup_identity_seed_leak"
    assert SEED_REMOTE not in json.dumps(caught.value.detail())

    from fastapi import HTTPException

    from app.routers.settings import export_full_backup

    with pytest.raises(HTTPException) as answered:
        await export_full_backup()
    assert answered.value.status_code == 500
    assert answered.value.detail["code"] == "backup_identity_seed_leak"


# -- on_restore ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_hook_fires_once_per_job(backup_db, setup_stub, hook_calls):
    store = _Store()
    store.tokens = ["t"]
    _register_store("tokens", store)
    exported = export_document(await export_backup_v2())
    await run(BackupExportV2.model_validate(exported), steps=_meshloom_only())
    assert hook_calls == ["json"]


@pytest.mark.asyncio
async def test_no_hook_for_a_job_that_wrote_nothing(backup_db, setup_stub, hook_calls):
    steps = _meshloom_only()
    steps.meshloom = False
    job = await run(BackupExportV2.model_validate(V2_PR156), steps=steps)
    assert statuses(job)["meshloom"] == ("skipped", "step_off")
    assert hook_calls == []


@pytest.mark.asyncio
async def test_a_failing_hook_does_not_stop_the_others(backup_db):
    calls: list[str] = []

    async def broken(_source):
        raise RuntimeError("boom")

    async def recorder(source):
        calls.append(source)

    register_on_restore("broken", broken)
    register_on_restore("recorder", recorder)
    await restore_json(BackupRestoreRequest(confirm=True, **V1_4_18))
    assert calls == ["json"]
    with pytest.raises(ValueError):
        register_on_restore("recorder", recorder)


@pytest.mark.asyncio
async def test_a_restored_sqlite_snapshot_fires_the_hook_once(test_db, tmp_path, hook_calls):
    snapshot = tmp_path / "meshcore.db"
    await write_sqlite_backup(test_db, str(snapshot))
    # The live database is not marked; only the copy is.
    assert await run_sqlite_restore_hooks(test_db) is False

    from app.database import Database

    restored = Database(str(snapshot))
    await restored.connect()
    try:
        assert await run_sqlite_restore_hooks(restored) is True
        assert await run_sqlite_restore_hooks(restored) is False
    finally:
        await restored.disconnect()
    assert hook_calls == ["sqlite"]
    async with aiosqlite.connect(snapshot) as conn:
        async with conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (SQLITE_SNAPSHOT_MARKER,)
        ) as cursor:
            assert await cursor.fetchone() is None
