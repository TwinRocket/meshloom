"""Repository for fanout_configs table.

The secret keys of each type (``secret_fields.FANOUT_SECRET_KEYS``) are stored
encrypted inside ``config`` (``app.secret_store``, MSHL-133): sealed on every
write, opened on every read. A value the key cannot open is left out of the
config and named in ``secret_unreadable``; the manager does not start such a
module. A write that does not name that key keeps the stored token, so the
matching ``secrets.key`` still brings it back.
"""

import json
import logging
import time
import uuid
from typing import Any

import aiosqlite

from app import secret_store
from app.database import db
from app.secret_fields import secret_keys

logger = logging.getLogger(__name__)

# In-memory cache of config metadata (name, type) for status reporting.
# Populated by get_all/get/create/update and read by FanoutManager.get_statuses().
_configs_cache: dict[str, dict[str, Any]] = {}


SECRETS_TABLE = "fanout_configs"


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == []


def open_config(kind: str, config_id: str, config: dict[str, Any]) -> tuple[dict, list[str]]:
    """``config`` with its secret keys decrypted, and the keys the key cannot open
    (left out). A value that is not a ``v1:`` token is returned as it is."""
    opened = dict(config)
    unreadable: list[str] = []
    for key in secret_keys(kind):
        if key not in opened:
            continue
        try:
            opened[key] = secret_store.unseal(
                opened[key], secret_store.aad(SECRETS_TABLE, config_id, key)
            )
        except secret_store.SecretUnreadable:
            opened.pop(key)
            unreadable.append(key)
    return opened, sorted(unreadable)


def seal_config(
    kind: str, config_id: str, config: dict[str, Any], stored: dict[str, Any] | None = None
) -> dict[str, Any]:
    """``config`` with its non-empty secret keys encrypted, bound to the row id and key.

    ``stored`` is the raw config in the row: a secret key the new config does not name
    keeps its stored token when that token is unreadable (a write that only toggles the
    row must not drop a secret the right ``secrets.key`` would open)."""
    sealed = dict(config)
    for key in secret_keys(kind):
        aad = secret_store.aad(SECRETS_TABLE, config_id, key)
        if key not in sealed:
            previous = (stored or {}).get(key)
            if secret_store.is_encrypted(previous) and not secret_store.opens(previous, aad):
                sealed[key] = previous
            continue
        value = sealed[key]
        if _is_empty(value):
            continue
        try:
            sealed[key] = secret_store.seal(value, aad)
        except secret_store.SecretStoreUnavailable:
            logger.warning("No secrets key: a fanout secret is stored unencrypted")
    return sealed


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a database row to a config dict (secrets decrypted)."""
    raw_config = json.loads(row["config"]) if row["config"] else {}
    config, unreadable = open_config(row["type"], row["id"], raw_config)
    result = {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "config": config,
        "scope": json.loads(row["scope"]) if row["scope"] else {},
        "sort_order": row["sort_order"] or 0,
        "created_at": row["created_at"] or 0,
    }
    if unreadable:
        # Names only: the manager keeps the module stopped (``secret_unreadable``).
        result["secret_unreadable"] = unreadable
    _configs_cache[result["id"]] = result
    return result


async def _get_in_conn(conn: aiosqlite.Connection, config_id: str) -> dict[str, Any] | None:
    """Fetch a config using an already-acquired connection.

    Used by ``create`` and ``update`` to return the freshly-written row
    without re-entering the non-reentrant DB lock.
    """
    async with conn.execute("SELECT * FROM fanout_configs WHERE id = ?", (config_id,)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


class FanoutConfigRepository:
    """CRUD operations for fanout_configs table."""

    @staticmethod
    async def get_all() -> list[dict[str, Any]]:
        """Get all fanout configs ordered by sort_order."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM fanout_configs ORDER BY sort_order, created_at"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_dict(row) for row in rows]

    @staticmethod
    async def get(config_id: str) -> dict[str, Any] | None:
        """Get a single fanout config by ID."""
        async with db.readonly() as conn:
            return await _get_in_conn(conn, config_id)

    @staticmethod
    async def create(
        config_type: str,
        name: str,
        config: dict,
        scope: dict,
        enabled: bool = True,
        config_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new fanout config."""
        new_id = config_id or str(uuid.uuid4())
        now = int(time.time())

        async with db.tx() as conn:
            # Determine next sort_order under the same lock as the insert,
            # so two concurrent ``create()`` calls cannot collide.
            async with conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM fanout_configs"
            ) as cursor:
                row = await cursor.fetchone()
            sort_order = row[0] if row else 0

            async with conn.execute(
                """
                INSERT INTO fanout_configs (id, type, name, enabled, config, scope, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    config_type,
                    name,
                    1 if enabled else 0,
                    json.dumps(seal_config(config_type, new_id, config)),
                    json.dumps(scope),
                    sort_order,
                    now,
                ),
            ):
                pass

            result = await _get_in_conn(conn, new_id)
        assert result is not None
        return result

    @staticmethod
    async def update(config_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update a fanout config. Only provided fields are updated."""
        values: dict[str, Any] = {}
        for field in ("name", "enabled", "config", "scope", "sort_order"):
            if field in fields:
                value = fields[field]
                if field == "enabled":
                    value = 1 if value else 0
                elif field == "scope":
                    value = json.dumps(value)
                values[field] = value

        if not values:
            return await FanoutConfigRepository.get(config_id)

        query = f"UPDATE fanout_configs SET {', '.join(f'{f} = ?' for f in values)} WHERE id = ?"
        async with db.tx() as conn:
            if "config" in values:
                # The type and the stored tokens are read in the same transaction.
                async with conn.execute(
                    "SELECT type, config FROM fanout_configs WHERE id = ?", (config_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    return None
                stored = json.loads(row["config"]) if row["config"] else {}
                values["config"] = json.dumps(
                    seal_config(row["type"], config_id, values["config"], stored)
                )
            async with conn.execute(query, [*values.values(), config_id]):
                pass
            return await _get_in_conn(conn, config_id)

    @staticmethod
    async def delete(config_id: str) -> None:
        """Delete a fanout config."""
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM fanout_configs WHERE id = ?", (config_id,)):
                pass
        _configs_cache.pop(config_id, None)

    @staticmethod
    async def get_enabled() -> list[dict[str, Any]]:
        """Get all enabled fanout configs."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM fanout_configs WHERE enabled = 1 ORDER BY sort_order, created_at"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_dict(row) for row in rows]
