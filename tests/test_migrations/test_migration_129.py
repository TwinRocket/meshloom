"""Migration 129: the stored third-party secrets are encrypted in place (MSHL-133)."""

import json

import aiosqlite
import pytest

from app import secret_store
from app.database import Database
from app.migrations import _129_secrets_at_rest as m129
from app.migrations import run_migrations, set_version
from app.secret_fields import FANOUT_SECRET_KEYS

from .conftest import LATEST_SCHEMA_VERSION, opened_config, opened_secrets

CHANNELS = {
    "legacy:webhook": ("webhook", {"url": "https://hooks.example.org/t0k3n", "hmac_secret": "h"}),
    "email-1": ("email", {"password": "smtp-pass"}),
    "apprise-1": ("apprise", {"urls": "tgram://1:AAAA/1"}),
    "ntfy-1": ("ntfy", {"topic": "topic-secret", "token": "tk_1"}),
    "empty-1": ("webhook", {"url": "", "hmac_secret": None}),
}

FANOUT = {
    "f-apprise": ("apprise", {"urls": "tgram://1:BBBB/2", "body_format": "md"}),
    "f-webhook": (
        "webhook",
        {"url": "https://h.example.org/x", "headers": {"X-Key": "k"}, "hmac_secret": "s", "m": 1},
    ),
    "f-private": ("mqtt_private", {"broker_host": "b", "username": "u", "password": "mq-pass"}),
    "f-ha": ("mqtt_ha", {"broker_host": "b", "password": "ha-pass"}),
    "f-community": ("mqtt_community", {"password": "co-pass"}),
    "f-sqs": (
        "sqs",
        {
            "queue_url": "q",
            "access_key_id": "AKIA1",
            "secret_access_key": "sk",
            "session_token": "",
        },
    ),
    "f-bot": ("bot", {"code": "print(1)"}),
}

PLAIN_VALUES = (
    "t0k3n",
    "smtp-pass",
    "AAAA",
    "topic-secret",
    "tk_1",
    "BBBB",
    "X-Key",
    "mq-pass",
    "ha-pass",
    "co-pass",
    "AKIA1",
)


def test_the_frozen_key_list_matches_secret_fields_today():
    """A new secret key needs its own migration (and the repository seals it from then on)."""
    live = {kind: tuple(keys) for kind, keys in FANOUT_SECRET_KEYS.items() if keys}
    assert live == m129.FANOUT_SECRET_KEYS


async def _seed(conn):
    for channel_id, (kind, secrets) in CHANNELS.items():
        await conn.execute(
            "INSERT INTO notification_channels (id, type, preset, name, enabled, language, config,"
            " secrets, allow_private_network, include_message_text, verify_tls,"
            " quiet_hours_exempt, sort_order, created_at) VALUES (?, ?, NULL, ?, 1, 'auto', '{}',"
            " ?, 0, 'public_only', 1, 0, 0, 1)",
            (channel_id, kind, channel_id, json.dumps(secrets)),
        )
    for config_id, (kind, config) in FANOUT.items():
        await conn.execute(
            "INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order,"
            " created_at) VALUES (?, ?, ?, 1, ?, '{}', 0, 1)",
            (config_id, kind, config_id, json.dumps(config)),
        )


async def _dump(conn):
    channels = dict(
        await (await conn.execute("SELECT id, secrets FROM notification_channels")).fetchall()
    )
    fanout = {
        r[0]: (r[1], r[2])
        for r in await (
            await conn.execute("SELECT id, type, config FROM fanout_configs")
        ).fetchall()
    }
    return channels, fanout


@pytest.fixture
async def conn(tmp_path):
    path = tmp_path / "a.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    connection = await aiosqlite.connect(path)
    await connection.execute("DELETE FROM notification_channels")
    await connection.execute("DELETE FROM fanout_configs")
    await _seed(connection)
    await set_version(connection, 128)
    await connection.commit()
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_plain_rows_of_every_type_are_encrypted(conn):
    assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 128
    channels, fanout = await _dump(conn)
    dump = json.dumps([channels, fanout])
    for plain in PLAIN_VALUES:
        assert plain not in dump

    for channel_id, (_kind, secrets) in CHANNELS.items():
        stored = json.loads(channels[channel_id])
        for name, value in secrets.items():
            assert secret_store.is_encrypted(stored[name]) == bool(value)
        assert opened_secrets(channels[channel_id], channel_id) == secrets

    for config_id, (kind, config) in FANOUT.items():
        stored_kind, raw = fanout[config_id]
        assert stored_kind == kind
        stored = json.loads(raw)
        for key, value in config.items():
            secret = key in m129.FANOUT_SECRET_KEYS.get(kind, ()) and value not in ("", None)
            assert secret_store.is_encrypted(stored[key]) == secret, (config_id, key)
        assert opened_config(kind, config_id, raw) == config  # headers come back as a dict


@pytest.mark.asyncio
async def test_a_token_is_bound_to_its_row(conn):
    await run_migrations(conn)
    channels, _ = await _dump(conn)
    token = json.loads(channels["email-1"])["password"]
    with pytest.raises(secret_store.SecretUnreadable):
        secret_store.decrypt(
            token, secret_store.aad("notification_channels", "apprise-1", "password")
        )


@pytest.mark.asyncio
async def test_a_second_run_changes_nothing(conn):
    await run_migrations(conn)
    first = await _dump(conn)
    await m129.migrate(conn)
    assert await _dump(conn) == first


@pytest.mark.asyncio
async def test_without_a_key_the_migration_changes_nothing(conn, monkeypatch):
    monkeypatch.setattr(secret_store, "ensure_key", lambda path=None: False)
    before = await _dump(conn)
    await m129.migrate(conn)
    assert await _dump(conn) == before


@pytest.mark.asyncio
async def test_missing_tables_are_tolerated(tmp_path):
    conn = await aiosqlite.connect(tmp_path / "bare.db")
    try:
        await m129.migrate(conn)
    finally:
        await conn.close()
