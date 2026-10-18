"""Repository for the plugin instances (``plugins``), their key-value store
(``plugin_kv``), their write-only secrets (``plugin_secrets``) and their send
counters (``plugin_send_quota``, rules in ``app/plugins/quotas.py``).

One row of ``plugins`` is one instance: a plugin (``plugin_id``) can be installed
several times, each instance with its own label, config, scope, granted
permissions, quotas, KV and secrets; the code directory is shared per
``plugin_id`` (one version). Model and rules: ``docs/dev/plugins.md``, "Data model".

Which config fields are secret is decided by the instance's config schema
(``plugins.config_schema``, the ``"writeOnly": true`` fields of
``config.schema.json``), never by the caller: every such field passed in
``config`` goes to ``plugin_secrets`` and never sits in ``plugins.config``. Every
read here returns a stored secret as ``REDACTED`` (``********``, the write-only
pattern of ``app/secret_fields.py``). Only ``config_for_init()`` merges the clear
values, for the runtime's ``plugin.init``. A write follows the same rules as the
other write-only secrets: a secret field omitted, ``None`` or ``REDACTED`` keeps
the stored value, ``""`` clears it, any other string replaces it. ``REDACTED``
for a field with no stored value is refused (``secret_required``): the mask is
never stored as a value. Secrets are stored in clear until the Notifications key
file (``app/secret_store.py``) exists, like the repeater passwords today; never
exported (no JSON backup section, emptied in the SQLite download).
"""

import json
import time
import unicodedata
import uuid
from collections.abc import Mapping
from typing import Any, Literal

import aiosqlite

from app.database import db
from app.plugins.config_schema import secret_fields
from app.secret_fields import REDACTED

PluginKind = Literal["bundled", "store", "custom", "local"]

# KV bounds (docs/dev/plugins.md, "Data model"). The `storage` capability enforces
# the same quota; these are the repository's backstop.
KV_MAX_KEY_BYTES = 256
KV_MAX_VALUE_BYTES = 256 * 1024
KV_QUOTA_BYTES = 5 * 1024 * 1024

# In-memory copy of the instances (secrets masked) for status reporting, like
# ``repository/fanout.py``. Filled by every read and write here.
_instances_cache: dict[str, dict[str, Any]] = {}

_JSON_FIELDS = ("manifest", "scope", "granted_permissions", "quotas")
_FLAG_FIELDS = ("enabled", "auto_update")
_UPDATABLE = (
    "label",
    "version",
    "enabled",
    "manifest",
    "config",
    "config_schema",
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


class PluginStoreError(ValueError):
    """A coded refusal of the plugin repository. ``code`` and ``fields`` never
    hold a value, only field or key names."""

    def __init__(self, code: str, fields: list[str] | None = None) -> None:
        self.code = code
        self.fields = fields or []
        super().__init__(f"{code}: {', '.join(self.fields)}" if self.fields else code)


def _loads(value: str | None, default: Any) -> Any:
    return json.loads(value) if value else default


def _row_to_dict(row: Any, stored_secrets: frozenset[str]) -> dict[str, Any]:
    config = _loads(row["config"], {})
    config.update(dict.fromkeys(stored_secrets, REDACTED))
    result = {
        "instance_id": row["instance_id"],
        "plugin_id": row["plugin_id"],
        "label": row["label"],
        "kind": row["kind"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "manifest": _loads(row["manifest"], {}),
        "config": config,
        "config_schema": _loads(row["config_schema"], {}),
        "secret_fields": sorted(stored_secrets),
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


def _split_config(
    config: Mapping[str, Any], secret_names: frozenset[str], stored: frozenset[str]
) -> tuple[dict[str, Any], dict[str, str | None]]:
    """``(plain config, secret writes)``: the schema's secret fields leave ``config``.
    A secret write of ``None`` keeps, ``""`` clears, a string replaces."""
    plain: dict[str, Any] = {}
    writes: dict[str, str | None] = {}
    required: list[str] = []
    for key, value in config.items():
        if key not in secret_names:
            plain[key] = value
        elif value is None or value == REDACTED:
            if value == REDACTED and key not in stored:
                required.append(key)
            writes[key] = None
        elif isinstance(value, str):
            writes[key] = value
        else:
            raise PluginStoreError("secret_not_string", [key])
    if required:
        raise PluginStoreError("secret_required", sorted(required))
    return plain, writes


async def _write_secrets_in_conn(
    conn: aiosqlite.Connection, instance_id: str, writes: Mapping[str, str | None], now: int
) -> None:
    for field, value in writes.items():
        if value is None:
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
        config_schema: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
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
        """Create an instance (a new uuid unless ``instance_id`` is given).
        ``config_schema`` is the plugin's validated ``config.schema.json``."""
        new_id = instance_id or str(uuid.uuid4())
        now = int(time.time())
        schema = dict(config_schema or {})
        plain, writes = _split_config(config or {}, secret_fields(schema), frozenset())
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
                    config_schema, config, scope, granted_permissions, quotas, auto_update,
                    source, archive_sha256, repository, installed_at, updated_at, status,
                    last_error, attention, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    new_id,
                    plugin_id,
                    label,
                    kind,
                    version,
                    1 if enabled else 0,
                    json.dumps(manifest),
                    json.dumps(schema),
                    json.dumps(plain),
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
            await _write_secrets_in_conn(conn, new_id, writes, now)
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
    async def update(instance_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update the given fields (``_UPDATABLE``). ``config`` replaces the plain
        config and merges the secret fields (module docstring); a new
        ``config_schema`` moves fields that became secret into ``plugin_secrets``
        and drops stored secrets the schema no longer declares. None when the
        instance is unknown."""
        unknown = set(fields) - set(_UPDATABLE)
        if unknown:
            raise ValueError(f"not updatable: {', '.join(sorted(unknown))}")
        now = int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT config, config_schema FROM plugins WHERE instance_id = ?", (instance_id,)
            ) as cursor:
                current = await cursor.fetchone()
            if current is None:
                return None
            stored = (await _secret_fields_in_conn(conn, instance_id)).get(instance_id, frozenset())
            schema = fields.get("config_schema")
            schema = dict(schema) if schema is not None else _loads(current["config_schema"], {})
            names = secret_fields(schema)
            plain: dict[str, Any] = {}
            writes: dict[str, str | None] = {}
            if "config" in fields:
                plain, writes = _split_config(fields["config"] or {}, names, stored)
            elif "config_schema" in fields:
                plain, writes = _split_config(_loads(current["config"], {}), names, stored)
            updates: list[str] = []
            params: list[Any] = []
            for field in _UPDATABLE:
                if field not in fields:
                    continue
                value = fields[field]
                if field == "config":
                    value = json.dumps(plain)
                elif field == "config_schema":
                    value = json.dumps(schema)
                elif field in _JSON_FIELDS:
                    value = json.dumps(value)
                elif field == "attention":
                    value = json.dumps(value) if value is not None else None
                elif field in _FLAG_FIELDS:
                    value = 1 if value else 0
                updates.append(f"{field} = ?")
                params.append(value)
            if "config_schema" in fields and "config" not in fields:
                updates.append("config = ?")
                params.append(json.dumps(plain))
            updates.append("updated_at = ?")
            params.extend([now, instance_id])
            await conn.execute(
                f"UPDATE plugins SET {', '.join(updates)} WHERE instance_id = ?", params
            )
            await _write_secrets_in_conn(conn, instance_id, writes, now)
            if "config_schema" in fields:
                for field in stored - names:
                    await conn.execute(
                        "DELETE FROM plugin_secrets WHERE instance_id = ? AND field = ?",
                        (instance_id, field),
                    )
            return await _get_in_conn(conn, instance_id)

    @staticmethod
    async def delete(instance_id: str, *, keep_data: bool = False) -> None:
        """Remove an instance, its secrets and its send counters; its KV too unless
        ``keep_data``."""
        async with db.tx() as conn:
            await conn.execute("DELETE FROM plugins WHERE instance_id = ?", (instance_id,))
            await conn.execute("DELETE FROM plugin_secrets WHERE instance_id = ?", (instance_id,))
            await conn.execute(
                "DELETE FROM plugin_send_quota WHERE instance_id = ?", (instance_id,)
            )
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


def _check_kv_key(key: str) -> None:
    """A key is 1 to ``KV_MAX_KEY_BYTES`` UTF-8 bytes with no control, format,
    surrogate or unassigned character."""
    if not isinstance(key, str) or not key:
        raise PluginStoreError("kv_key_invalid")
    try:
        size = len(key.encode("utf-8"))
    except UnicodeEncodeError:
        raise PluginStoreError("kv_key_invalid") from None
    if size > KV_MAX_KEY_BYTES or any(unicodedata.category(c).startswith("C") for c in key):
        raise PluginStoreError("kv_key_invalid")


class PluginKvRepository:
    """The per-instance key-value store (``storage`` capability). Values are JSON;
    keys and sizes are bounded (``KV_*``, ``PluginStoreError``)."""

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
        """Store ``value`` (JSON). Refused: an invalid key (``kv_key_invalid``), a
        value over ``KV_MAX_VALUE_BYTES`` (``kv_value_too_large``), or a write that
        takes the instance over ``KV_QUOTA_BYTES`` (``kv_quota_exceeded``)."""
        _check_kv_key(key)
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            value_bytes = len(encoded.encode("utf-8"))
        except (TypeError, ValueError, UnicodeEncodeError):
            raise PluginStoreError("kv_value_invalid", [key]) from None
        if value_bytes > KV_MAX_VALUE_BYTES:
            raise PluginStoreError("kv_value_too_large", [key])
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT COALESCE(SUM(LENGTH(CAST(key AS BLOB)) + LENGTH(CAST(value AS BLOB))), 0)"
                " FROM plugin_kv WHERE instance_id = ? AND key != ?",
                (instance_id, key),
            ) as cursor:
                row = await cursor.fetchone()
            others = int(row[0]) if row else 0
            if others + len(key.encode("utf-8")) + value_bytes > KV_QUOTA_BYTES:
                raise PluginStoreError("kv_quota_exceeded", [key])
            await conn.execute(
                """
                INSERT INTO plugin_kv (instance_id, key, value, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (instance_id, key) DO UPDATE
                SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (instance_id, key, encoded, int(time.time())),
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


class PluginSendQuotaRepository:
    """The fixed-window send counters of the instances (``plugin_send_quota``).
    The windows and limits are ``app/plugins/quotas.py``'s."""

    @staticmethod
    async def counts(instance_id: str) -> dict[str, tuple[int, int]]:
        """``period -> (period_start, count)`` as stored (a stale window included)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT period, period_start, count FROM plugin_send_quota WHERE instance_id = ?",
                (instance_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return {row["period"]: (int(row["period_start"]), int(row["count"])) for row in rows}

    @staticmethod
    async def increment(instance_id: str, starts: Mapping[str, int]) -> None:
        """Count one send in each ``period`` whose current window starts at
        ``starts[period]``; a row of an older window restarts at 1."""
        async with db.tx() as conn:
            for period, start in starts.items():
                await conn.execute(
                    """
                    INSERT INTO plugin_send_quota (instance_id, period, period_start, count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT (instance_id, period) DO UPDATE SET
                        count = CASE WHEN period_start = excluded.period_start
                                     THEN count + 1 ELSE 1 END,
                        period_start = excluded.period_start
                    """,
                    (instance_id, period, start),
                )

    @staticmethod
    async def forget(instance_id: str) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "DELETE FROM plugin_send_quota WHERE instance_id = ?", (instance_id,)
            )
