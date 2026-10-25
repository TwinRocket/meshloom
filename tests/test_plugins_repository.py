"""Plugin instances, their KV and their write-only secrets (app/repository/plugins.py)."""

import json

import aiosqlite
import pytest

from app.repository.plugins import (
    KV_MAX_KEY_BYTES,
    KV_MAX_VALUE_BYTES,
    KV_QUOTA_BYTES,
    PluginKvRepository,
    PluginRepository,
    PluginStoreError,
)
from app.secret_fields import REDACTED

TOKEN = "tok-3f9c1e7a5b2d"
MANIFEST = {"manifest": 1, "id": "acme.weather", "version": "1.2.0"}
SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "api_key": {"type": "string", "writeOnly": True},
    },
}


async def _create(label: str = "Weather #paris", **kwargs):
    defaults = {
        "plugin_id": "acme.weather",
        "label": label,
        "kind": "store",
        "version": "1.2.0",
        "manifest": MANIFEST,
        "config_schema": SCHEMA,
        "config": {"city": "Paris", "api_key": TOKEN},
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
    await PluginRepository.update(instance_id, config=echoed)
    assert await PluginRepository.config_for_init(instance_id) == {
        "city": "Lyon",
        "api_key": TOKEN,
    }

    # Omitted or None keeps.
    await PluginRepository.update(instance_id, config={"city": "Lyon"})
    await PluginRepository.update(instance_id, config={"city": "Lyon", "api_key": None})
    assert (await PluginRepository.config_for_init(instance_id))["api_key"] == TOKEN

    await PluginRepository.update(instance_id, config={"city": "Lyon", "api_key": "new-token"})
    assert (await PluginRepository.config_for_init(instance_id))["api_key"] == "new-token"

    await PluginRepository.update(instance_id, config={"city": "Lyon", "api_key": ""})
    assert await PluginRepository.config_for_init(instance_id) == {"city": "Lyon"}
    assert (await PluginRepository.get(instance_id))["secret_fields"] == []

    # After a clear, the mask is refused rather than stored as the value.
    with pytest.raises(PluginStoreError) as exc:
        await PluginRepository.update(instance_id, config={"city": "Lyon", "api_key": REDACTED})
    assert (exc.value.code, exc.value.fields) == ("secret_required", ["api_key"])
    assert await PluginRepository.config_for_init(instance_id) == {"city": "Lyon"}


@pytest.mark.asyncio
async def test_secretness_comes_from_the_schema_not_the_caller(test_db):
    # Without a schema declaring it, a field is plain config.
    plain = await _create(config_schema={}, config={"token": "visible"})
    assert plain["config"] == {"token": "visible"}

    # Declared writeOnly: stored as a secret even though the caller passed it in config.
    created = await _create(config={"city": "Paris", "api_key": TOKEN})
    assert created["config"]["api_key"] == REDACTED
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT config FROM plugins") as cursor:
            assert not _holds([tuple(r) for r in await cursor.fetchall()], TOKEN)

    with pytest.raises(PluginStoreError) as exc:
        await _create(config={"api_key": REDACTED})
    assert exc.value.code == "secret_required"
    with pytest.raises(PluginStoreError) as exc:
        await _create(config={"api_key": 12})
    assert exc.value.code == "secret_not_string"


@pytest.mark.asyncio
async def test_a_new_schema_moves_and_drops_secrets(test_db):
    instance_id = (await _create(config_schema={}, config={"city": "Paris", "pin": "1234"}))[
        "instance_id"
    ]
    schema = {"type": "object", "properties": {"pin": {"type": "string", "writeOnly": True}}}
    moved = await PluginRepository.update(instance_id, config_schema=schema)
    assert moved is not None
    assert moved["config"] == {"city": "Paris", "pin": REDACTED}
    assert await PluginRepository.config_for_init(instance_id) == {"city": "Paris", "pin": "1234"}

    # The schema no longer declares it: the stored secret goes.
    dropped = await PluginRepository.update(instance_id, config_schema={})
    assert dropped is not None
    assert dropped["secret_fields"] == []
    assert await PluginRepository.config_for_init(instance_id) == {"city": "Paris"}


@pytest.mark.asyncio
async def test_two_instances_of_one_plugin_keep_separate_config_kv_and_secrets(test_db):
    paris = (await _create("Weather #paris"))["instance_id"]
    lyon = (await _create("Weather #lyon", config={"city": "Lyon", "api_key": "other"}))[
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
async def test_delete_with_keep_data_keeps_the_kv_and_the_secrets(test_db):
    instance_id = (await _create())["instance_id"]
    await PluginKvRepository.set(instance_id, "k", "v")
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM plugin_secrets") as cursor:
            (secrets,) = tuple(await cursor.fetchone())
    assert secrets > 0
    await PluginRepository.delete(instance_id, keep_data=True)
    assert await PluginKvRepository.get(instance_id, "k") == "v"
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM plugin_secrets") as cursor:
            assert tuple(await cursor.fetchone()) == (secrets,)


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
async def test_kv_keys_and_sizes_are_bounded(test_db):
    instance_id = (await _create())["instance_id"]
    bad_keys = ("", "a\x00b", "line\nbreak", "\u202eevil", "x" * (KV_MAX_KEY_BYTES + 1), "\ud800")
    for key in bad_keys:
        with pytest.raises(PluginStoreError) as exc:
            await PluginKvRepository.set(instance_id, key, 1)
        assert exc.value.code == "kv_key_invalid"
    await PluginKvRepository.set(instance_id, "é" * (KV_MAX_KEY_BYTES // 2), 1)

    with pytest.raises(PluginStoreError) as exc:
        await PluginKvRepository.set(instance_id, "big", "x" * KV_MAX_VALUE_BYTES)
    assert exc.value.code == "kv_value_too_large"
    with pytest.raises(PluginStoreError) as exc:
        await PluginKvRepository.set(instance_id, "nan", float("nan"))
    assert exc.value.code == "kv_value_invalid"

    chunk = "x" * (KV_MAX_VALUE_BYTES - 16)
    with pytest.raises(PluginStoreError) as exc:
        for index in range(KV_QUOTA_BYTES // KV_MAX_VALUE_BYTES + 2):
            await PluginKvRepository.set(instance_id, f"k{index}", chunk)
    assert exc.value.code == "kv_quota_exceeded"
    assert await PluginKvRepository.usage_bytes(instance_id) <= KV_QUOTA_BYTES
    # Rewriting a key counts only its new size; another instance has its own quota.
    await PluginKvRepository.set(instance_id, "k0", "small")
    other = (await _create("Weather #lyon"))["instance_id"]
    await PluginKvRepository.set(other, "k0", chunk)


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
