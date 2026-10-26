"""Startup backfill (MSHL-133): secrets left plain because migration 129 ran without a
key are encrypted at the next start that has one."""

import json

import aiosqlite
import pytest

from app import secret_store
from app.database import Database
from app.services.secrets_backfill import encrypt_plain_secrets

URL = "https://hooks.example.org/plain-left-1"
PASSWORD = "plain-left-pass-1"


async def _seed_plain(path):
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute(
            "INSERT INTO notification_channels (id, type, preset, name, enabled, language, config,"
            " secrets, allow_private_network, include_message_text, verify_tls,"
            " quiet_hours_exempt, sort_order, created_at) VALUES ('hook-1', 'webhook', NULL,"
            " 'Hook', 1, 'auto', '{}', ?, 0, 'public_only', 1, 0, 0, 1)",
            (json.dumps({"url": URL}),),
        )
        await conn.execute(
            "INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order,"
            " created_at) VALUES ('mq-1', 'mqtt_private', 'Broker', 1, ?, '{}', 0, 1)",
            (json.dumps({"broker_host": "b", "password": PASSWORD}),),
        )
        await conn.commit()
    finally:
        await conn.close()


async def _dump(path):
    conn = await aiosqlite.connect(path)
    try:
        secrets = (
            await (
                await conn.execute("SELECT secrets FROM notification_channels WHERE id='hook-1'")
            ).fetchone()
        )[0]
        config = (
            await (
                await conn.execute("SELECT config FROM fanout_configs WHERE id='mq-1'")
            ).fetchone()
        )[0]
        return secrets, config
    finally:
        await conn.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A database whose key file sits next to it; the default key is reloaded afterwards."""
    path = tmp_path / "data" / "meshloom.db"
    path.parent.mkdir()
    monkeypatch.setattr(secret_store, "key_path", lambda: path.parent / secret_store.KEY_FILENAME)
    secret_store.reset()
    yield path
    secret_store.reset()


@pytest.mark.asyncio
async def test_a_start_with_a_key_encrypts_what_migration_129_left_plain(db_path, monkeypatch):
    # First start without a usable key: migration 129 is recorded, the secrets stay plain.
    monkeypatch.setattr(secret_store, "ensure_key", lambda path=None: False)
    database = Database(str(db_path))
    await database.connect()
    await database.disconnect()
    await _seed_plain(db_path)
    monkeypatch.undo()
    monkeypatch.setattr(
        secret_store, "key_path", lambda: db_path.parent / secret_store.KEY_FILENAME
    )
    secret_store.reset()

    # The next start has a key: everything is encrypted, bound to its row.
    database = Database(str(db_path))
    await database.connect()
    await database.disconnect()
    secrets, config = await _dump(db_path)
    assert URL not in secrets and PASSWORD not in config
    token = json.loads(secrets)["url"]
    assert (
        secret_store.decrypt(token, secret_store.aad("notification_channels", "hook-1", "url"))
        == URL
    )
    sealed = json.loads(config)["password"]
    assert (
        secret_store.unseal(sealed, secret_store.aad("fanout_configs", "mq-1", "password"))
        == PASSWORD
    )

    # Idempotent: another start writes nothing.
    database = Database(str(db_path))
    await database.connect()
    await database.disconnect()
    assert await _dump(db_path) == (secrets, config)


@pytest.mark.asyncio
async def test_without_a_key_the_backfill_changes_nothing(db_path, monkeypatch):
    database = Database(str(db_path))
    await database.connect()
    await database.disconnect()
    await _seed_plain(db_path)
    before = await _dump(db_path)
    monkeypatch.setattr(secret_store, "ensure_key", lambda path=None: False)
    conn = await aiosqlite.connect(db_path)
    try:
        assert await encrypt_plain_secrets(conn) == (0, 0)
        await conn.commit()
    finally:
        await conn.close()
    assert await _dump(db_path) == before


@pytest.mark.asyncio
async def test_a_backfill_failure_never_blocks_startup(db_path, monkeypatch):
    database = Database(str(db_path))
    await database.connect()  # migrations applied first
    await database.disconnect()

    async def boom(conn, fanout_secret_keys=None):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("app.services.secrets_backfill.encrypt_plain_secrets", boom)
    database = Database(str(db_path))
    await database.connect()
    await database.disconnect()
