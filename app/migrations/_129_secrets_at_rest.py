"""Migration 129: encrypt the stored third-party secrets (MSHL-133).

Encrypts in place, with ``app.secret_store`` (AES-256-GCM, the ``secrets.key``
file next to the database, created at startup before the migrations):

- every non-empty value of ``notification_channels.secrets`` (string tokens,
  AAD ``secret_store.aad("notification_channels", id, field)``, as
  ``repository.notification_channels.encode_secrets`` writes them);
- the secret keys of each fanout type inside ``fanout_configs.config`` (sealed
  JSON values, AAD ``secret_store.aad("fanout_configs", id, key)``, as
  ``repository.fanout.seal_config`` writes them). The key list is frozen here.

Idempotent: a value that already has the shape of a ``v1:`` token is left as it
is. Without a usable key the migration logs a warning and changes nothing: the
reads are tolerant of plain values, and the next write of a channel or module
encrypts it.
"""

import json
import logging
from typing import Any

import aiosqlite

from app import secret_store

logger = logging.getLogger(__name__)

# ``secret_fields.FANOUT_SECRET_KEYS`` when this migration was written.
FANOUT_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "apprise": ("urls",),
    "webhook": ("url", "headers", "hmac_secret"),
    "mqtt_private": ("password",),
    "mqtt_ha": ("password",),
    "mqtt_community": ("password",),
    "sqs": ("access_key_id", "secret_access_key", "session_token"),
}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == []


def _loads(raw: object) -> dict[str, Any] | None:
    try:
        loaded = json.loads(raw) if isinstance(raw, str) and raw else {}
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


async def _tables(conn: aiosqlite.Connection) -> set[str]:
    async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
        return {row[0] for row in await cursor.fetchall()}


async def _channels(conn: aiosqlite.Connection) -> int:
    changed = 0
    async with conn.execute("SELECT id, secrets FROM notification_channels") as cursor:
        rows = await cursor.fetchall()
    for channel_id, raw in rows:
        secrets = _loads(raw)
        if not secrets:
            continue
        touched = False
        for name, value in secrets.items():
            if not isinstance(value, str) or not value or secret_store.is_encrypted(value):
                continue
            secrets[name] = secret_store.encrypt(
                value, secret_store.aad("notification_channels", channel_id, name)
            )
            touched = True
        if touched:
            await conn.execute(
                "UPDATE notification_channels SET secrets = ? WHERE id = ?",
                (json.dumps(secrets, sort_keys=True), channel_id),
            )
            changed += 1
    return changed


async def _fanout(conn: aiosqlite.Connection) -> int:
    changed = 0
    async with conn.execute("SELECT id, type, config FROM fanout_configs") as cursor:
        rows = await cursor.fetchall()
    for config_id, kind, raw in rows:
        keys = FANOUT_SECRET_KEYS.get(kind, ())
        config = _loads(raw)
        if not keys or not config:
            continue
        touched = False
        for key in keys:
            value = config.get(key)
            if _is_empty(value) or secret_store.is_encrypted(value):
                continue
            config[key] = secret_store.seal(
                value, secret_store.aad("fanout_configs", config_id, key)
            )
            touched = True
        if touched:
            await conn.execute(
                "UPDATE fanout_configs SET config = ? WHERE id = ?",
                (json.dumps(config), config_id),
            )
            changed += 1
    return changed


async def migrate(conn: aiosqlite.Connection) -> None:
    if not secret_store.ensure_key():
        logger.warning(
            "No secrets key: the stored secrets stay unencrypted until they are saved again"
        )
        return
    tables = await _tables(conn)
    channels = await _channels(conn) if "notification_channels" in tables else 0
    modules = await _fanout(conn) if "fanout_configs" in tables else 0
    if channels or modules:
        logger.info(
            "Encrypted the stored secrets of %d notification channel(s) and %d fanout module(s)",
            channels,
            modules,
        )
