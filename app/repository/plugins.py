"""Repository for the plugin instances (``plugins``), their key-value store
(``plugin_kv``) and their write-only secrets (``plugin_secrets``).

One row of ``plugins`` is one instance: a plugin (``plugin_id``) can be installed
several times, each instance with its own label, config, scope, granted
permissions, quotas, KV and secrets; the code directory is shared per
``plugin_id`` (one version). Model and rules: ``docs/dev/plugins.md``, "Data model".

Secrets (the values of the config schema's ``"writeOnly": true`` fields) never
sit in ``plugins.config``: they live in ``plugin_secrets`` and every read here
returns them as ``REDACTED`` (``********``, the write-only pattern of
``app/secret_fields.py``). Only ``config_for_init()`` merges the clear values,
for the runtime's ``plugin.init``. A write follows the same rules as the other
write-only secrets: a field omitted, ``None`` or ``REDACTED`` keeps the stored
value, ``""`` clears it, anything else replaces it. They are stored in clear
until the Notifications key file (``app/secret_store.py``, Notifications T32)
exists, like the repeater passwords today; never exported (no JSON backup
section, emptied in the SQLite download).
"""

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any, Literal

import aiosqlite

from app.database import db
from app.secret_fields import REDACTED

PluginKind = Literal["bundled", "store", "custom", "local"]

# In-memory copy of the instances (secrets masked) for status reporting, like
# ``repository/fanout.py``. Filled by every read and write here.
_instances_cache: dict[str, dict[str, Any]] = {}

_JSON_FIELDS = ("manifest", "config", "scope", "granted_permissions", "quotas")
_FLAG_FIELDS = ("enabled", "auto_update")
_UPDATABLE = (
    "label",
    "version",
    "enabled",
    "manifest",
    "config",
    "scope",
    "granted_permissions",
    "quotas",
    "auto_update",
    "source",
    "archive_sha256",
    "repository",
    "status",
    "last_error",
    "attention",
    "sort_order",
)


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def _row_to_dict(row: Any, secret_fields: frozenset[str]) -> dict[str, Any]:
    config = _loads(row["config"], {})
    config.update(dict.fromkeys(secret_fields, REDACTED))
    result = {
        "instance_id": row["instance_id"],
        "plugin_id": row["plugin_id"],
        "label": row["label"],
        "kind": row["kind"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "manifest": _loads(row["manifest"], {}),
        "config": config,
        "secret_fields": sorted(secret_fields),
        "scope": _loads(row["scope"], {}),
        "granted_permissions": _loads(row["granted_permissions"], {}),
        "quotas": _loads(row["quotas"], {}),
        "auto_update": bool(row["auto_update"]),
        "source": row["source"],
        "archive_sha256": row["archive_sha256"],
        "repository": row["repository"],
        "installed_at": row["installed_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "last_error": row["last_error"],
        "attention": _loads(row["attention"], None),
        "sort_order": row["sort_order"],
    }
    _instances_cache[result["instance_id"]] = result
    return result


async def _secret_fields_in_conn(
    conn: aiosqlite.Connection, instance_id: str | None = None
) -> dict[str, frozenset[str]]:
    query = "SELECT instance_id, field FROM plugin_secrets"
    params: tuple[str, ...] = ()
    if instance_id is not None:
        query += " WHERE instance_id = ?"
        params = (instance_id,)
    fields: dict[str, set[str]] = {}
    async with conn.execute(query, params) as cursor:
        for row in await cursor.fetchall():
            fields.setdefault(row["instance_id"], set()).add(row["field"])
    return {key: frozenset(names) for key, names in fields.items()}


async def _get_in_conn(conn: aiosqlite.Connection, instance_id: str) -> dict[str, Any] | None:
    async with conn.execute("SELECT * FROM plugins WHERE instance_id = ?", (instance_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    secrets = await _secret_fields_in_conn(conn, instance_id)
    return _row_to_dict(row, secrets.get(instance_id, frozenset()))


async def _list_in_conn(conn: aiosqlite.Connection, where: str = "") -> list[dict[str, Any]]:
    async with conn.execute(
        f"SELECT * FROM plugins {where} ORDER BY sort_order, installed_at, instance_id"
    ) as cursor:
        rows = await cursor.fetchall()
    secrets = await _secret_fields_in_conn(conn)
    return [_row_to_dict(row, secrets.get(row["instance_id"], frozenset())) for row in rows]


async def _write_secrets_in_conn(
    conn: aiosqlite.Connection, instance_id: str, secrets: Mapping[str, str | None], now: int
) -> None:
    for field, value in secrets.items():
        if value is None or value == REDACTED:
            continue
        if value == "":
            await conn.execute(
                "DELETE FROM plugin_secrets WHERE instance_id = ? AND field = ?",
                (instance_id, field),
            )
            continue
        await conn.execute(
            """
            INSERT INTO plugin_secrets (instance_id, field, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (instance_id, field) DO UPDATE
            SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (instance_id, field, value, now),
        )


def _plain_config(config: Mapping[str, Any], secret_names: set[str]) -> dict[str, Any]:
    """``config`` without any secret field: a secret never reaches ``plugins.config``,
    even when a caller echoes a masked config back."""
    return {key: value for key, value in config.items() if key not in secret_names}


class PluginRepository:
    """CRUD for plugin instances, secrets included (write-only)."""

    @staticmethod
    async def create(
        *,
        plugin_id: str,
        label: str,
        kind: PluginKind,
        version: str,
        manifest: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
        secrets: Mapping[str, str | None] | None = None,
        scope: Mapping[str, Any] | None = None,
        granted_permissions: Mapping[str, Any] | None = None,
        quotas: Mapping[str, Any] | None = None,
        enabled: bool = False,
        auto_update: bool = False,
        source: str | None = None,
        archive_sha256: str | None = None,
        repository: str | None = None,
        status: str = "installed",
        attention: Mapping[str, Any] | None = None,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an instance (a new uuid unless ``instance_id`` is given)."""
        new_id = instance_id or str(uuid.uuid4())
        now = int(time.time())
        secrets = secrets or {}
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM plugins"
            ) as cursor:
                row = await cursor.fetchone()
            sort_order = row[0] if row else 0
            await conn.execute(
                """
                INSERT INTO plugins (
                    instance_id, plugin_id, label, kind, version, enabled, manifest,
                    config, scope, granted_permissions, quotas, auto_update, source,
                    archive_sha256, repository, installed_at, updated_at, status,
                    last_error, attention, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    new_id,
                    plugin_id,
                    label,
                    kind,
                    version,
                    1 if enabled else 0,
                    json.dumps(manifest),
                    json.dumps(_plain_config(config or {}, set(secrets))),
                    json.dumps(scope or {}),
                    json.dumps(granted_permissions or {}),
                    json.dumps(quotas or {}),
                    1 if auto_update else 0,
                    source,
                    archive_sha256,
                    repository,
                    now,
                    now,
                    status,
                    json.dumps(attention) if attention is not None else None,
                    sort_order,
                ),
            )
            await _write_secrets_in_conn(conn, new_id, secrets, now)
            result = await _get_in_conn(conn, new_id)
        assert result is not None
        return result

    @staticmethod
    async def get(instance_id: str) -> dict[str, Any] | None:
        """One instance, secrets masked."""
        async with db.readonly() as conn:
            return await _get_in_conn(conn, instance_id)

    @staticmethod
    async def get_all() -> list[dict[str, Any]]:
        """Every instance by ``sort_order``, secrets masked."""
        async with db.readonly() as conn:
            return await _list_in_conn(conn)

    @staticmethod
    async def get_enabled() -> list[dict[str, Any]]:
        """The enabled instances by ``sort_order``, secrets masked."""
        async with db.readonly() as conn:
            return await _list_in_conn(conn, "WHERE enabled = 1")

    @staticmethod
    async def get_by_plugin_id(plugin_id: str) -> list[dict[str, Any]]:
        """Every instance of one plugin (they share its code directory)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM plugins WHERE plugin_id = ?"
                " ORDER BY sort_order, installed_at, instance_id",
                (plugin_id,),
            ) as cursor:
                rows = await cursor.fetchall()
            secrets = await _secret_fields_in_conn(conn)
        return [_row_to_dict(row, secrets.get(row["instance_id"], frozenset())) for row in rows]

    @staticmethod
    async def update(
        instance_id: str, *, secrets: Mapping[str, str | None] | None = None, **fields: Any
    ) -> dict[str, Any] | None:
        """Update the given fields (``_UPDATABLE``) and merge ``secrets``
        (write-only rules, module docstring). None when the instance is unknown."""
        unknown = set(fields) - set(_UPDATABLE)
        if unknown:
            raise ValueError(f"not updatable: {', '.join(sorted(unknown))}")
        now = int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT 1 FROM plugins WHERE instance_id = ?", (instance_id,)
            ) as cursor:
                if await cursor.fetchone() is None:
                    return None
            updates: list[str] = []
            params: list[Any] = []
            for field in _UPDATABLE:
                if field not in fields:
                    continue
                value = fields[field]
                if field == "config":
                    stored = await _secret_fields_in_conn(conn, instance_id)
                    names = set(stored.get(instance_id, frozenset())) | set(secrets or {})
                    value = json.dumps(_plain_config(value, names))
                elif field in _JSON_FIELDS:
                    value = json.dumps(value)
                elif field == "attention":
                    value = json.dumps(value) if value is not None else None
                elif field in _FLAG_FIELDS:
                    value = 1 if value else 0
                updates.append(f"{field} = ?")
                params.append(value)
            updates.append("updated_at = ?")
            params.extend([now, instance_id])
            await conn.execute(
                f"UPDATE plugins SET {', '.join(updates)} WHERE instance_id = ?", params
            )
            if secrets:
                await _write_secrets_in_conn(conn, instance_id, secrets, now)
            return await _get_in_conn(conn, instance_id)

    @staticmethod
    async def delete(instance_id: str, *, keep_data: bool = False) -> None:
        """Remove an instance and its secrets; its KV too unless ``keep_data``."""
        async with db.tx() as conn:
            await conn.execute("DELETE FROM plugins WHERE instance_id = ?", (instance_id,))
            await conn.execute("DELETE FROM plugin_secrets WHERE instance_id = ?", (instance_id,))
            if not keep_data:
                await conn.execute("DELETE FROM plugin_kv WHERE instance_id = ?", (instance_id,))
        _instances_cache.pop(instance_id, None)

    @staticmethod
    async def config_for_init(instance_id: str) -> dict[str, Any] | None:
        """The config with the secrets in clear, for ``plugin.init`` only. Never
        return it from an API route, log it or export it."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT config FROM plugins WHERE instance_id = ?", (instance_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            config = _loads(row["config"], {})
            async with conn.execute(
                "SELECT field, value FROM plugin_secrets WHERE instance_id = ?", (instance_id,)
            ) as cursor:
                config.update({r["field"]: r["value"] for r in await cursor.fetchall()})
        return config


class PluginKvRepository:
    """The per-instance key-value store (``storage`` capability). Values are JSON."""

    @staticmethod
    async def get(instance_id: str, key: str) -> Any | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT value FROM plugin_kv WHERE instance_id = ? AND key = ?",
                (instance_id, key),
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row["value"]) if row else None

    @staticmethod
    async def set(instance_id: str, key: str, value: Any) -> None:
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO plugin_kv (instance_id, key, value, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (instance_id, key) DO UPDATE
                SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (instance_id, key, json.dumps(value, ensure_ascii=False), int(time.time())),
            )

    @staticmethod
    async def delete(instance_id: str, key: str) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "DELETE FROM plugin_kv WHERE instance_id = ? AND key = ?", (instance_id, key)
            )

    @staticmethod
    async def keys(instance_id: str) -> list[str]:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT key FROM plugin_kv WHERE instance_id = ? ORDER BY key", (instance_id,)
            ) as cursor:
                return [row["key"] for row in await cursor.fetchall()]

    @staticmethod
    async def usage_bytes(instance_id: str) -> int:
        """UTF-8 bytes of the keys and values of one instance (the quota's measure)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COALESCE(SUM(LENGTH(CAST(key AS BLOB)) + LENGTH(CAST(value AS BLOB))), 0)"
                " FROM plugin_kv WHERE instance_id = ?",
                (instance_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0
