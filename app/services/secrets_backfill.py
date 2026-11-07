"""Encrypt the secrets still stored in plain text (MSHL-133).

Migration 129 encrypts the stored secrets once; when it ran without a usable key
it changed nothing and is still recorded as applied. ``Database.connect`` therefore
runs ``encrypt_plain_secrets`` at every start, after the migrations: as soon as a
key is available, every plain value left in ``notification_channels.secrets`` and in
the fanout secret keys of ``fanout_configs.config`` is encrypted in place.

Idempotent: a value that already has the shape of a ``v1:`` token is left as it is,
so a run with nothing plain left writes nothing. Without a key it does nothing.
Never logs a value, only counts.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

import aiosqlite

from app import secret_store

logger = logging.getLogger(__name__)


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


async def _fanout(conn: aiosqlite.Connection, secret_keys: Mapping[str, tuple[str, ...]]) -> int:
    changed = 0
    async with conn.execute("SELECT id, type, config FROM fanout_configs") as cursor:
        rows = await cursor.fetchall()
    for config_id, kind, raw in rows:
        keys = secret_keys.get(kind, ())
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


async def encrypt_plain_secrets(
    conn: aiosqlite.Connection, fanout_secret_keys: Mapping[str, tuple[str, ...]] | None = None
) -> tuple[int, int]:
    """Encrypt the plain stored secrets; returns (channels, fanout modules) changed.

    ``fanout_secret_keys`` defaults to the live ``FANOUT_SECRET_KEYS`` (a migration
    passes its frozen copy). No key: nothing is changed and (0, 0) is returned.
    The caller commits."""
    if not secret_store.ensure_key():
        return 0, 0
    if fanout_secret_keys is None:
        from app.secret_fields import FANOUT_SECRET_KEYS

        fanout_secret_keys = FANOUT_SECRET_KEYS
    tables = await _tables(conn)
    channels = await _channels(conn) if "notification_channels" in tables else 0
    modules = await _fanout(conn, fanout_secret_keys) if "fanout_configs" in tables else 0
    if channels or modules:
        logger.info(
            "Encrypted the stored secrets of %d notification channel(s) and %d fanout module(s)",
            channels,
            modules,
        )
    return channels, modules
