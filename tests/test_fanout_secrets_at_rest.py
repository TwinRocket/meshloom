"""Fanout secrets at rest (MSHL-133): sealed in ``fanout_configs.config``, opened on read,
and a module whose secret the key cannot open stays stopped with ``secret_unreadable``."""

import json
import logging

import pytest

from app import secret_store
from app.fanout.manager import FanoutManager, _register_module_types
from app.repository.fanout import FanoutConfigRepository

PASSWORD = "mqtt-pass-at-rest-1"
HEADERS = {"Authorization": "Bearer header-secret-1"}


@pytest.fixture
def other_key(tmp_path):
    """Swap the secrets key, as a database copied without its secrets.key would see it."""

    def swap():
        secret_store.reset()
        assert secret_store.ensure_key(tmp_path / "other" / secret_store.KEY_FILENAME)

    yield swap
    secret_store.reset()


async def _raw(test_db, config_id):
    async with test_db.tx() as conn:
        async with conn.execute(
            "SELECT config FROM fanout_configs WHERE id = ?", (config_id,)
        ) as cursor:
            return json.loads((await cursor.fetchone())[0])


async def _mqtt(enabled=True):
    return await FanoutConfigRepository.create(
        "mqtt_private",
        "Broker",
        {"broker_host": "broker.example.org", "username": "u", "password": PASSWORD},
        {"messages": "all", "raw_packets": "none"},
        enabled=enabled,
    )


async def test_secret_keys_are_sealed_and_read_back(test_db):
    created = await _mqtt()
    raw = await _raw(test_db, created["id"])
    assert secret_store.is_encrypted(raw["password"]) and raw["username"] == "u"
    assert created["config"]["password"] == PASSWORD
    fetched = await FanoutConfigRepository.get(created["id"])
    assert fetched["config"]["password"] == PASSWORD and "secret_unreadable" not in fetched

    hook = await FanoutConfigRepository.create(
        "webhook",
        "Hook",
        {"url": "https://h.example.org/x", "headers": HEADERS, "method": "POST"},
        {"messages": "all", "raw_packets": "none"},
    )
    raw = await _raw(test_db, hook["id"])
    assert "header-secret-1" not in json.dumps(raw) and raw["method"] == "POST"
    assert (await FanoutConfigRepository.get(hook["id"]))["config"]["headers"] == HEADERS


async def test_a_legacy_plain_secret_is_read_and_sealed_on_the_next_write(test_db):
    created = await _mqtt()
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE fanout_configs SET config = ? WHERE id = ?",
            (json.dumps({"broker_host": "b", "password": PASSWORD}), created["id"]),
        )
    fetched = await FanoutConfigRepository.get(created["id"])
    assert fetched["config"]["password"] == PASSWORD
    await FanoutConfigRepository.update(created["id"], config=fetched["config"])
    assert secret_store.is_encrypted((await _raw(test_db, created["id"]))["password"])


async def test_an_unreadable_secret_keeps_the_module_stopped(test_db, other_key, caplog):
    caplog.set_level(logging.DEBUG)
    created = await _mqtt()
    token = (await _raw(test_db, created["id"]))["password"]
    other_key()

    cfg = await FanoutConfigRepository.get(created["id"])
    assert cfg["secret_unreadable"] == ["password"]
    assert "password" not in cfg["config"]

    _register_module_types()
    manager = FanoutManager()
    manager._broadcast_health_update = lambda: None
    await manager._start_module(cfg)
    assert created["id"] not in manager._modules
    status = manager.get_statuses()[created["id"]]
    assert status["status"] == "error" and status["last_error"] == "secret_unreadable"

    # A write that does not name the secret keeps the stored token for the right key.
    await FanoutConfigRepository.update(created["id"], enabled=False, config=cfg["config"])
    assert (await _raw(test_db, created["id"]))["password"] == token

    # Typing it again seals it with the current key, and the module may start.
    await FanoutConfigRepository.update(
        created["id"], enabled=True, config={**cfg["config"], "password": "new-pass-1"}
    )
    reopened = await FanoutConfigRepository.get(created["id"])
    assert reopened["config"]["password"] == "new-pass-1"
    assert "secret_unreadable" not in reopened
    assert PASSWORD not in caplog.text and "new-pass-1" not in caplog.text


async def test_the_matching_key_brings_the_secret_back(test_db, other_key):
    created = await _mqtt()
    other_key()
    cfg = await FanoutConfigRepository.get(created["id"])
    await FanoutConfigRepository.update(created["id"], name="Renamed", config=cfg["config"])
    secret_store.reset()  # the database's own secrets.key again
    back = await FanoutConfigRepository.get(created["id"])
    assert back["config"]["password"] == PASSWORD and back["name"] == "Renamed"


async def test_the_api_never_returns_a_secret(test_db, client, other_key):
    created = await _mqtt(enabled=False)
    for response in (
        await client.get("/api/fanout"),
        await client.get(f"/api/fanout/{created['id']}"),
    ):
        assert response.status_code == 200
        assert PASSWORD not in response.text and "v1:" not in response.text
    other_key()
    unreadable = await client.get(f"/api/fanout/{created['id']}")
    assert unreadable.json()["secret_unreadable"] == ["password"]
    assert "v1:" not in unreadable.text


HOOK_URL = "https://hooks.example.org/services/secret-path-1"
APPRISE_URLS = "tgram://111111:AAAA-token-fanout/1001"


@pytest.fixture
def no_reload(monkeypatch):
    from unittest.mock import AsyncMock

    from app.fanout.manager import fanout_manager

    monkeypatch.setattr(fanout_manager, "reload_config", AsyncMock())


@pytest.mark.parametrize(
    ("kind", "config", "key", "new_value"),
    [
        ("webhook", {"url": HOOK_URL, "method": "POST"}, "url", "https://93.184.216.34/new-1"),
        ("apprise", {"urls": APPRISE_URLS}, "urls", "json://example.org/new-1"),
    ],
)
async def test_an_unreadable_url_still_takes_a_patch_that_leaves_it_alone(
    test_db, client, other_key, no_reload, kind, config, key, new_value
):
    created = await FanoutConfigRepository.create(
        kind, "Hook", config, {"messages": "all", "raw_packets": "none"}, enabled=False
    )
    token = (await _raw(test_db, created["id"]))[key]
    other_key()
    url = f"/api/fanout/{created['id']}"

    for body in ({"name": "Renamed"}, {"enabled": True}):
        response = await client.patch(url, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["secret_unreadable"] == [key]
    shown = (await client.get(url)).json()
    assert shown["name"] == "Renamed" and shown["enabled"] is True
    assert key not in shown["config"]  # nothing to show for a secret the key cannot open

    # The editor sends the whole config back without the secret: nothing changed, 200.
    same = await client.patch(url, json={"config": shown["config"]})
    assert same.status_code == 200, same.text
    assert (await _raw(test_db, created["id"]))[key] == token  # kept for the right key
    stored = await FanoutConfigRepository.get(created["id"])
    assert stored["secret_unreadable"] == [key]  # the manager keeps it stopped

    # Another setting cannot be checked without the secret.
    edited = await client.patch(url, json={"config": {**shown["config"], "extra_setting": 1}})
    assert edited.status_code == 409

    typed = await client.patch(url, json={"config": {**shown["config"], key: new_value}})
    assert typed.status_code == 200, typed.text
    reopened = await FanoutConfigRepository.get(created["id"])
    assert "secret_unreadable" not in reopened and reopened["config"][key] == new_value


async def test_without_a_key_a_fanout_secret_is_never_stored_in_plain_text(
    test_db, client, tmp_path, monkeypatch, no_reload, caplog
):
    existing = await _mqtt(enabled=False)
    before = await _raw(test_db, existing["id"])
    key_path = secret_store.key_path
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")
    monkeypatch.setattr(secret_store, "key_path", lambda: blocker / secret_store.KEY_FILENAME)
    secret_store.reset()
    try:
        assert secret_store.ensure_key() is False
        created = await client.post(
            "/api/fanout",
            json={
                "type": "mqtt_private",
                "name": "New broker",
                "config": {"broker_host": "new-broker.example.org", "password": "fresh-pass-1"},
                "scope": {"messages": "all", "raw_packets": "none"},
                "enabled": False,
            },
        )
        assert created.status_code == 503, created.text
        assert created.json()["detail"]["code"] == "secret_store_unavailable"

        shown = (await client.get(f"/api/fanout/{existing['id']}")).json()["config"]
        patched = await client.patch(
            f"/api/fanout/{existing['id']}", json={"config": {**shown, "password": "new-pass-2"}}
        )
        assert patched.status_code == 503 and "new-pass-2" not in patched.text
    finally:
        monkeypatch.setattr(secret_store, "key_path", key_path)
        secret_store.reset()

    async with test_db.tx() as conn:
        async with conn.execute("SELECT id, config FROM fanout_configs") as cursor:
            rows = dict(await cursor.fetchall())
    dump = json.dumps(rows)
    assert "fresh-pass-1" not in dump and "new-pass-2" not in dump
    assert not any(
        json.loads(raw).get("broker_host") == "new-broker.example.org" for raw in rows.values()
    )
    assert await _raw(test_db, existing["id"]) == before
    assert "fresh-pass-1" not in caplog.text and "new-pass-2" not in caplog.text
