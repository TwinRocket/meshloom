"""Repository for the plugin instances (``plugins``), their key-value store
(``plugin_kv``), their write-only secrets (``plugin_secrets``) and their send
counters (``plugin_send_quota`` and the loop guards' ``plugin_send_guard`` and
``plugin_send_limits``, rules in ``app/plugins/quotas.py``).

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

An uninstall with ``keep_data`` leaves the KV and secret rows of an ``instance_id``
that no longer exists and records their owner in ``plugin_kept_data``
(``PluginKeptDataRepository``): the next install of the same ``plugin_id`` from the same
source (``kind`` and ``repository``, never ``local``) takes them back, and
``prune()`` deletes them ``KEPT_DATA_RETENTION_S`` after the uninstall.
"""

import asyncio
import json
import logging
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
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


def _check_updatable(fields: Mapping[str, Any]) -> None:
    unknown = set(fields) - set(_UPDATABLE)
    if unknown:
        raise ValueError(f"not updatable: {', '.join(sorted(unknown))}")


async def _update_in_conn(
    conn: aiosqlite.Connection, instance_id: str, fields: Mapping[str, Any]
) -> dict[str, Any] | None:
    now = int(time.time())
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
    await conn.execute(f"UPDATE plugins SET {', '.join(updates)} WHERE instance_id = ?", params)
    await _write_secrets_in_conn(conn, instance_id, writes, now)
    if "config_schema" in fields:
        for field in stored - names:
            await conn.execute(
                "DELETE FROM plugin_secrets WHERE instance_id = ? AND field = ?",
                (instance_id, field),
            )
    return await _get_in_conn(conn, instance_id)


@dataclass(frozen=True)
class KeptData:
    """The data an uninstall kept, as found for a new install: the old ``instance_id``
    and the names of its stored secrets (never their values)."""

    instance_id: str
    secret_fields: frozenset[str]


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
        kept: KeptData | None = None,
    ) -> dict[str, Any]:
        """Create an instance (a new uuid unless ``instance_id`` is given).
        ``config_schema`` is the plugin's validated ``config.schema.json``. ``kept``
        (from ``PluginKeptDataRepository.find``) is the data an uninstall kept: its KV
        and the secrets the schema still declares move to the new instance, in the same
        transaction, unless ``config`` replaces or clears them."""
        new_id = instance_id or str(uuid.uuid4())
        now = int(time.time())
        schema = dict(config_schema or {})
        names = secret_fields(schema)
        stored = kept.secret_fields & names if kept is not None else frozenset()
        plain, writes = _split_config(config or {}, names, stored)
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
            if kept is not None:
                await _adopt_in_conn(conn, kept, new_id, names)
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
        _check_updatable(fields)
        async with db.tx() as conn:
            return await _update_in_conn(conn, instance_id, fields)

    @staticmethod
    async def update_many(updates: Mapping[str, Mapping[str, Any]]) -> None:
        """``update`` for several instances in one transaction: all rows change, or
        none (the bundled update, ``app/plugins/bundled_update.py``)."""
        for fields in updates.values():
            _check_updatable(fields)
        async with db.tx() as conn:
            for instance_id, fields in updates.items():
                await _update_in_conn(conn, instance_id, fields)

    @staticmethod
    async def delete(instance_id: str, *, keep_data: bool = False) -> None:
        """Remove an instance and its send counters; its KV and secrets too unless
        ``keep_data``, which records them in ``plugin_kept_data`` instead."""
        async with db.tx() as conn:
            if keep_data:
                await _keep_in_conn(conn, instance_id, int(time.time()))
            await conn.execute("DELETE FROM plugins WHERE instance_id = ?", (instance_id,))
            await conn.execute(
                "DELETE FROM plugin_send_quota WHERE instance_id = ?", (instance_id,)
            )
            await _forget_guard_rows(conn, instance_id)
            if not keep_data:
                await conn.execute("DELETE FROM plugin_kv WHERE instance_id = ?", (instance_id,))
                await conn.execute(
                    "DELETE FROM plugin_secrets WHERE instance_id = ?", (instance_id,)
                )
        _instances_cache.pop(instance_id, None)
        from app.plugins.quotas import send_guard

        send_guard.forget(instance_id)

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


# -- Data kept by an uninstall (plugin_kept_data, migration 125) ---------------------

# How long an uninstall with keep_data keeps the KV and secrets nobody took back.
KEPT_DATA_RETENTION_S = 90 * 24 * 3600
# Kinds whose kept data a new install can take back. A local plugin's plugin_id is
# whatever its pasted manifest says, so another local plugin could claim it.
_REATTACHABLE_KINDS = frozenset({"bundled", "store", "custom"})


async def _has_data(conn: aiosqlite.Connection, instance_id: str) -> bool:
    async with conn.execute(
        "SELECT EXISTS (SELECT 1 FROM plugin_kv WHERE instance_id = ?)"
        " OR EXISTS (SELECT 1 FROM plugin_secrets WHERE instance_id = ?)",
        (instance_id, instance_id),
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row and row[0])


async def _keep_in_conn(conn: aiosqlite.Connection, instance_id: str, now: int) -> None:
    """Record who owned the data of an instance being uninstalled with ``keep_data``
    (nothing when it has none)."""
    async with conn.execute(
        "SELECT plugin_id, kind, repository FROM plugins WHERE instance_id = ?", (instance_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None or not await _has_data(conn, instance_id):
        return
    await conn.execute(
        """
        INSERT OR REPLACE INTO plugin_kept_data
            (instance_id, plugin_id, kind, repository, kept_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (instance_id, row["plugin_id"], row["kind"], row["repository"], now),
    )


async def _adopt_in_conn(
    conn: aiosqlite.Connection, kept: KeptData, new_id: str, secret_names: frozenset[str]
) -> None:
    """Move the kept KV and the secrets ``secret_names`` still declares to ``new_id``;
    the other kept secrets are deleted. Does nothing if the record went meanwhile."""
    async with conn.execute(
        "DELETE FROM plugin_kept_data WHERE instance_id = ?", (kept.instance_id,)
    ) as cursor:
        if cursor.rowcount < 1:
            return
    await conn.execute(
        "UPDATE plugin_kv SET instance_id = ? WHERE instance_id = ?", (new_id, kept.instance_id)
    )
    for field in sorted(secret_names):
        await conn.execute(
            "UPDATE plugin_secrets SET instance_id = ? WHERE instance_id = ? AND field = ?",
            (new_id, kept.instance_id, field),
        )
    await conn.execute("DELETE FROM plugin_secrets WHERE instance_id = ?", (kept.instance_id,))


class PluginKeptDataRepository:
    """The KV and secrets an uninstall with ``keep_data`` left (``plugin_kept_data``)."""

    @staticmethod
    async def find(plugin_id: str, kind: str, repository: str | None) -> KeptData | None:
        """The latest data kept by an uninstall of ``plugin_id`` from the same source
        (``kind`` and ``repository``), or ``None``; never for a ``local`` plugin."""
        if kind not in _REATTACHABLE_KINDS:
            return None
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT instance_id FROM plugin_kept_data
                WHERE plugin_id = ? AND kind = ? AND repository IS ?
                  AND instance_id NOT IN (SELECT instance_id FROM plugins)
                ORDER BY kept_at DESC, instance_id LIMIT 1
                """,
                (plugin_id, kind, repository),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return None
            async with conn.execute(
                "SELECT field FROM plugin_secrets WHERE instance_id = ?", (row["instance_id"],)
            ) as cursor:
                fields = frozenset(r["field"] for r in await cursor.fetchall())
        return KeptData(row["instance_id"], fields)

    @staticmethod
    async def restorable_id(
        conn: aiosqlite.Connection,
        instance_id: str,
        plugin_id: str,
        kind: str,
        repository: str | None,
    ) -> bool:
        """Whether a backup restore may create ``instance_id`` (inside its transaction).
        Kept data under that id goes to the new row only when it was kept from the same
        plugin and source, never for a ``local`` one; any other data under it (another
        owner, or none recorded)
        means the restore must pick a new id. On ``True`` the record is dropped: the
        data is the restored instance's again."""
        async with conn.execute(
            "SELECT plugin_id, kind, repository FROM plugin_kept_data WHERE instance_id = ?",
            (instance_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return not await _has_data(conn, instance_id)
        if kind not in _REATTACHABLE_KINDS:
            # A local plugin never takes kept data back, not even through a restore.
            return False
        if (row["plugin_id"], row["kind"], row["repository"]) != (plugin_id, kind, repository):
            return False
        await conn.execute("DELETE FROM plugin_kept_data WHERE instance_id = ?", (instance_id,))
        return True

    @staticmethod
    async def prune(now: int | None = None) -> int:
        """Housekeeping (hourly): drop the records of ids that have an instance again,
        delete the data kept more than ``KEPT_DATA_RETENTION_S`` ago, and record the
        KV or secret rows nobody owns so their retention starts. Returns the number of
        kept data sets deleted."""
        now = int(time.time()) if now is None else now
        async with db.tx() as conn:
            await conn.execute(
                "DELETE FROM plugin_kept_data WHERE instance_id IN (SELECT instance_id FROM plugins)"
            )
            async with conn.execute(
                "SELECT instance_id FROM plugin_kept_data WHERE kept_at <= ?",
                (now - KEPT_DATA_RETENTION_S,),
            ) as cursor:
                expired = [r["instance_id"] for r in await cursor.fetchall()]
            for instance_id in expired:
                for table in ("plugin_kv", "plugin_secrets", "plugin_kept_data"):
                    await conn.execute(
                        f"DELETE FROM {table} WHERE instance_id = ?",  # noqa: S608
                        (instance_id,),
                    )
            await conn.execute(
                """
                INSERT OR IGNORE INTO plugin_kept_data
                    (instance_id, plugin_id, kind, repository, kept_at)
                SELECT instance_id, '', '', NULL, ? FROM (
                    SELECT instance_id FROM plugin_kv
                    UNION
                    SELECT instance_id FROM plugin_secrets
                )
                WHERE instance_id NOT IN (SELECT instance_id FROM plugins)
                """,
                (now,),
            )
        if expired:
            logger.info("Deleted the kept data of %d uninstalled plugin instance(s)", len(expired))
        return len(expired)


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


class PluginUidError(Exception):
    """No uid of the range is free."""


logger = logging.getLogger(__name__)


def _first_free_uid(
    candidates: Iterable[int], taken: set[int], is_free: Callable[[int], bool]
) -> int | None:
    return next((uid for uid in candidates if uid not in taken and is_free(uid)), None)


async def _give_back_uid(uid: int, release: Callable[[int], None]) -> None:
    """``release(uid)`` when no row holds ``uid``, checked inside a write transaction
    so no allocation can insert it in between. Best effort: when the database
    still fails, the lease stays (it only keeps the uid from other installs)."""
    try:
        async with db.tx() as conn:
            async with conn.execute("SELECT 1 FROM plugin_uids WHERE uid = ?", (uid,)) as cursor:
                if await cursor.fetchone() is None:
                    await asyncio.to_thread(release, uid)
    except Exception as exc:
        logger.warning("Plugin uid %s: lease kept, the allocation check failed: %s", uid, exc)


class PluginUidRepository:
    """The uid each instance runs under at the ``setuid`` level (migration 121).

    Allocated once and kept: the same instance gets the same uid at every start.
    Released only after a purge as that uid (``PluginManager.remove``), so a row
    may outlive its instance until startup purges it."""

    @staticmethod
    async def get(instance_id: str) -> int | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT uid FROM plugin_uids WHERE instance_id = ?", (instance_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else None

    @staticmethod
    async def all() -> dict[str, int]:
        async with db.readonly() as conn:
            async with conn.execute("SELECT instance_id, uid FROM plugin_uids") as cursor:
                return {row[0]: int(row[1]) for row in await cursor.fetchall()}

    @staticmethod
    async def orphans() -> dict[str, int]:
        """The rows whose instance is gone."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT instance_id, uid FROM plugin_uids"
                " WHERE instance_id NOT IN (SELECT instance_id FROM plugins)"
            ) as cursor:
                return {row[0]: int(row[1]) for row in await cursor.fetchall()}

    @staticmethod
    async def allocate(
        instance_id: str,
        candidates: Iterable[int],
        is_free: Callable[[int], bool],
        *,
        release: Callable[[int], None] | None = None,
    ) -> int:
        """The instance's uid: the stored one, else the first free candidate no row
        holds, in the order of ``candidates`` (under ``setuid`` it starts at an
        offset derived from the install id). Raises ``PluginUidError`` when none
        is left.

        ``is_free`` may block (``getpwuid`` asks NSS, maybe LDAP): it runs in a
        thread, outside the write transaction. The pick is then inserted only if
        no row took it meanwhile; otherwise the search goes on past it.

        ``is_free`` may also take something for the pick (its host-wide lease,
        MSHL-357). When the race is lost because a concurrent call stored another
        uid for this instance, ``release`` gives the pick back, inside the write
        transaction so no other allocation can insert it in between (MSHL-359).
        A pick another instance stored keeps what was taken: it is that row's now.
        A pick whose transaction fails (a database error, a cancellation) gives
        it back too, once a new write transaction shows no row holds it; when
        that check cannot run either, the lease stays (fail closed, MSHL-360)."""
        stored = await PluginUidRepository.get(instance_id)
        if stored is not None:
            return stored
        pool = list(candidates)
        lost: set[int] = set()
        while True:
            taken = set((await PluginUidRepository.all()).values()) | lost
            uid = await asyncio.to_thread(_first_free_uid, pool, taken, is_free)
            if uid is None:
                raise PluginUidError("no free plugin uid left")
            try:
                async with db.tx() as conn:
                    async with conn.execute(
                        "SELECT uid FROM plugin_uids WHERE instance_id = ?", (instance_id,)
                    ) as cursor:
                        row = await cursor.fetchone()
                    async with conn.execute(
                        "SELECT 1 FROM plugin_uids WHERE uid = ?", (uid,)
                    ) as cursor:
                        held = await cursor.fetchone() is not None
                    if row is not None:
                        if release is not None and not held:
                            await asyncio.to_thread(release, uid)
                        return int(row[0])
                    if not held:
                        await conn.execute(
                            "INSERT INTO plugin_uids (instance_id, uid, allocated_at)"
                            " VALUES (?, ?, ?)",
                            (instance_id, uid, int(time.time())),
                        )
                        return uid
            except BaseException:
                if release is not None:
                    await _give_back_uid(uid, release)
                raise
            lost.add(uid)

    @staticmethod
    async def release(instance_id: str) -> None:
        async with db.tx() as conn:
            await conn.execute("DELETE FROM plugin_uids WHERE instance_id = ?", (instance_id,))


# `plugin_send_guard.subject` of the per-instance scopes is `<instance_id>|<what>`.
_GUARD_SEPARATOR = "|"


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _forget_guard_rows(conn: aiosqlite.Connection, instance_id: str) -> None:
    pattern = _escape_like(instance_id + _GUARD_SEPARATOR) + "%"
    await conn.execute(
        "DELETE FROM plugin_send_guard WHERE scope IN ('cooldown', 'quiet')"
        " AND subject LIKE ? ESCAPE '\\'",
        (pattern,),
    )


class PluginSendGuardRepository:
    """The Meshloom-wide send counters and loop-guard timestamps (``plugin_send_guard``)
    and the user's lowered limits (``plugin_send_limits``). Windows, limits and
    meaning are ``app/plugins/quotas.py``'s.

    Scopes: ``instance`` and ``destination`` hold an hourly window (``period_start``,
    ``count``); ``cooldown`` (last reply, subject ``<instance>|<sender>``) and
    ``quiet`` (last send, subject ``<instance>|<destination>``) hold a time in
    ``period_start``.
    """

    @staticmethod
    async def get(scope: str, subject: str) -> tuple[int, int] | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT period_start, count FROM plugin_send_guard WHERE scope = ? AND subject = ?",
                (scope, subject),
            ) as cursor:
                row = await cursor.fetchone()
        return (int(row["period_start"]), int(row["count"])) if row else None

    @staticmethod
    async def record_send(
        *,
        window_start: int,
        destination: str,
        instance_id: str,
        now: int,
        sender: str | None,
        keep_cooldown_after: int,
        keep_quiet_after: int,
    ) -> None:
        """Count one send in the instance budget and the destination's window (a row
        of an older window restarts at 1), stamp the quiet window of
        ``(instance, destination)`` and the cooldown of ``(instance, sender)``, and
        drop the rows that no longer matter (``keep_*_after``: the oldest stamp
        still inside its window)."""
        async with db.tx() as conn:
            for scope, subject in (("instance", ""), ("destination", destination)):
                await conn.execute(
                    """
                    INSERT INTO plugin_send_guard (scope, subject, period_start, count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT (scope, subject) DO UPDATE SET
                        count = CASE WHEN period_start = excluded.period_start
                                     THEN count + 1 ELSE 1 END,
                        period_start = excluded.period_start
                    """,
                    (scope, subject, window_start),
                )
            stamps = [("quiet", instance_id + _GUARD_SEPARATOR + destination)]
            if sender is not None:
                stamps.append(("cooldown", instance_id + _GUARD_SEPARATOR + sender))
            for scope, subject in stamps:
                await conn.execute(
                    """
                    INSERT INTO plugin_send_guard (scope, subject, period_start, count)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT (scope, subject) DO UPDATE SET period_start = excluded.period_start
                    """,
                    (scope, subject, now),
                )
            await conn.execute(
                "DELETE FROM plugin_send_guard WHERE"
                " (scope = 'destination' AND period_start < ?)"
                " OR (scope = 'cooldown' AND period_start < ?)"
                " OR (scope = 'quiet' AND period_start < ?)",
                (window_start, keep_cooldown_after, keep_quiet_after),
            )

    @staticmethod
    async def limits() -> dict[str, int]:
        async with db.readonly() as conn:
            async with conn.execute("SELECT name, value FROM plugin_send_limits") as cursor:
                rows = await cursor.fetchall()
        return {row["name"]: int(row["value"]) for row in rows}

    @staticmethod
    async def set_limits(values: Mapping[str, int | None]) -> None:
        """Store each limit; ``None`` removes it (back to the default)."""
        async with db.tx() as conn:
            for name, value in values.items():
                if value is None:
                    await conn.execute("DELETE FROM plugin_send_limits WHERE name = ?", (name,))
                else:
                    await conn.execute(
                        "INSERT INTO plugin_send_limits (name, value) VALUES (?, ?)"
                        " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                        (name, value),
                    )
