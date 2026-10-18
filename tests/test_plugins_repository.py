"""Plugin instances, their KV and their write-only secrets (app/repository/plugins.py)."""

import json

import aiosqlite
import pytest

from app.repository.plugins import PluginKvRepository, PluginRepository
from app.secret_fields import REDACTED

TOKEN = "tok-3f9c1e7a5b2d"
MANIFEST = {"manifest": 1, "id": "acme.weather", "version": "1.2.0"}


async def _create(label: str = "Weather #paris", **kwargs):
    defaults = {
        "plugin_id": "acme.weather",
        "label": label,
        "kind": "store",
        "version": "1.2.0",
        "manifest": MANIFEST,
        "config": {"city": "Paris"},
        "secrets": {"api_key": TOKEN},
    }
    defaults.update(kwargs)
    return await PluginRepository.create(**defaults)


def _holds(value, secret: str) -> bool:
    return secret in json.dumps(value)


@pytest.mark.asyncio
async def test_secret_is_masked_everywhere_except_for_init(test_db):
    created = await _create()
    instance_id = created["instance_id"]

    for result in (
        created,
        await PluginRepository.get(instance_id),
        await PluginRepository.get_all(),
        await PluginRepository.get_enabled(),
        await PluginRepository.get_by_plugin_id("acme.weather"),
        await PluginRepository.update(instance_id, label="Weather"),
    ):
        assert not _holds(result, TOKEN)
    stored = await PluginRepository.get(instance_id)
    assert stored is not None
    assert stored["config"] == {"city": "Paris", "api_key": REDACTED}
    assert stored["secret_fields"] == ["api_key"]

    assert await PluginRepository.config_for_init(instance_id) == {
        "city": "Paris",
        "api_key": TOKEN,
    }
    # Never in plugins.config either.
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT config FROM plugins") as cursor:
            assert not _holds([tuple(r) for r in await cursor.fetchall()], TOKEN)


@pytest.mark.asyncio
async def test_secret_writes_follow_the_write_only_rules(test_db):
    instance_id = (await _create())["instance_id"]

    # A masked config echoed back keeps the secret and never stores the mask.
    echoed = (await PluginRepository.get(instance_id))["config"]
    echoed["city"] = "Lyon"
    await PluginRepository.update(instance_id, config=echoed, secrets={"api_key": REDACTED})
    assert await PluginRepository.config_for_init(instance_id) == {
        "city": "Lyon",
        "api_key": TOKEN,
    }

    await PluginRepository.update(instance_id, secrets={"api_key": None})
    assert (await PluginRepository.config_for_init(instance_id))["api_key"] == TOKEN

    await PluginRepository.update(instance_id, secrets={"api_key": "new-token"})
    assert (await PluginRepository.config_for_init(instance_id))["api_key"] == "new-token"

    await PluginRepository.update(instance_id, secrets={"api_key": ""})
    assert await PluginRepository.config_for_init(instance_id) == {"city": "Lyon"}
    assert (await PluginRepository.get(instance_id))["secret_fields"] == []


@pytest.mark.asyncio
async def test_two_instances_of_one_plugin_keep_separate_config_kv_and_secrets(test_db):
    paris = (await _create("Weather #paris"))["instance_id"]
    lyon = (await _create("Weather #lyon", config={"city": "Lyon"}, secrets={"api_key": "other"}))[
        "instance_id"
    ]
    assert paris != lyon

    await PluginKvRepository.set(paris, "last", {"temp": 12})
    await PluginKvRepository.set(lyon, "last", {"temp": 18})
    await PluginKvRepository.set(lyon, "count", 3)

    assert await PluginKvRepository.get(paris, "last") == {"temp": 12}
    assert await PluginKvRepository.get(lyon, "last") == {"temp": 18}
    assert await PluginKvRepository.keys(paris) == ["last"]
    assert await PluginKvRepository.keys(lyon) == ["count", "last"]
    assert (await PluginRepository.config_for_init(paris)) == {"city": "Paris", "api_key": TOKEN}
    assert (await PluginRepository.config_for_init(lyon)) == {"city": "Lyon", "api_key": "other"}
    instances = await PluginRepository.get_by_plugin_id("acme.weather")
    assert [i["label"] for i in instances] == ["Weather #paris", "Weather #lyon"]
    assert [i["sort_order"] for i in instances] == [0, 1]

    await PluginRepository.delete(paris)
    assert await PluginRepository.get(paris) is None
    assert await PluginRepository.config_for_init(paris) is None
    assert await PluginKvRepository.keys(paris) == []
    assert await PluginKvRepository.keys(lyon) == ["count", "last"]


@pytest.mark.asyncio
async def test_delete_with_keep_data_keeps_the_kv_and_drops_the_secrets(test_db):
    instance_id = (await _create())["instance_id"]
    await PluginKvRepository.set(instance_id, "k", "v")
    await PluginRepository.delete(instance_id, keep_data=True)
    assert await PluginKvRepository.get(instance_id, "k") == "v"
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM plugin_secrets") as cursor:
            assert tuple(await cursor.fetchone()) == (0,)


@pytest.mark.asyncio
async def test_update_fields_status_and_attention(test_db):
    instance_id = (await _create(enabled=False))["instance_id"]
    updated = await PluginRepository.update(
        instance_id,
        enabled=True,
        status="needs_attention",
        last_error="boom",
        attention={"type": "migrated", "reasons": []},
        granted_permissions={"http": {"hosts": ["api.open-meteo.com"]}},
    )
    assert updated is not None
    assert updated["enabled"] is True
    assert (updated["status"], updated["last_error"]) == ("needs_attention", "boom")
    assert updated["attention"] == {"type": "migrated", "reasons": []}
    assert updated["granted_permissions"] == {"http": {"hosts": ["api.open-meteo.com"]}}
    assert [i["instance_id"] for i in await PluginRepository.get_enabled()] == [instance_id]
    assert await PluginRepository.update("missing", label="x") is None
    with pytest.raises(ValueError):
        await PluginRepository.update(instance_id, plugin_id="other.plugin")


@pytest.mark.asyncio
async def test_kv_delete_and_usage(test_db):
    instance_id = (await _create())["instance_id"]
    await PluginKvRepository.set(instance_id, "clé", "été")
    # key 4 bytes + value '"été"' 7 bytes
    assert await PluginKvRepository.usage_bytes(instance_id) == 11
    await PluginKvRepository.delete(instance_id, "clé")
    assert await PluginKvRepository.get(instance_id, "clé") is None
    assert await PluginKvRepository.usage_bytes(instance_id) == 0


@pytest.mark.asyncio
async def test_secrets_never_leave_in_a_backup(test_db, tmp_path):
    from app.services.backup import export_json, write_sqlite_backup

    await _create()
    async with test_db.tx() as conn:
        await conn.execute("UPDATE app_settings SET store_online = 1")

    # The full backup builds on export_json(include_secrets=True).
    for include_secrets in (False, True):
        export = await export_json(include_secrets=include_secrets)
        assert not _holds(export.model_dump(mode="json"), TOKEN)

    path = tmp_path / "copy.db"
    await write_sqlite_backup(test_db, str(path))
    async with aiosqlite.connect(path) as conn:
        async with conn.execute("SELECT COUNT(*) FROM plugin_secrets") as cursor:
            assert tuple(await cursor.fetchone()) == (0,)
        async with conn.execute("SELECT COUNT(*) FROM plugins") as cursor:
            assert tuple(await cursor.fetchone()) == (1,)
        async with conn.execute("SELECT store_online FROM app_settings") as cursor:
            assert tuple(await cursor.fetchone()) == (0,)
    assert TOKEN.encode() not in path.read_bytes()
    # The live database keeps the consent.
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT store_online FROM app_settings") as cursor:
            assert tuple(await cursor.fetchone()) == (1,)
