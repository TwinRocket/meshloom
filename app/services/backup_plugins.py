"""The ``plugins`` section of the full backup: plugin instances without their secrets.

One entry per row of ``plugins`` (Store T12, ``docs/dev/backup.md``, "Plugins"):

- **No secret leaves.** The values of ``plugin_secrets`` (the ``writeOnly`` fields of the
  instance's config schema) are never read here: the export starts from
  ``PluginRepository.get_all()``, which masks them, and drops every secret field and every
  masked value from ``config``. ``secret_omitted`` names the ones that held a value. The
  key-value store (``plugin_kv``), the send counters, the runtime state (``status``,
  ``last_error``, ``attention``) and ``archive_sha256`` stay out, and so does the archive of
  the converted bots (`fanout_configs_bots_archive`, migration 122).
- **A local plugin comes back whole**: its code lives in ``plugins.source``, so its
  manifest, source, config and grants are restored.
- **Any other instance (bundled, store, custom) is a "to reinstall" entry**: its code is not
  in the file. The row comes back with ``attention.type = "reinstall"``, which refuses every
  start (``_status_block`` in ``app/routers/plugins.py``). When an instance of the same
  plugin is already installed here, the restored one shares its code (manifest, version,
  schema) and is ``restored`` instead; the on_restore hook does the same for a bundled
  plugin this release ships, after writing its code out.
- **Every restored instance comes back disabled**, ``needs_attention`` with
  ``attention.type`` ``restored`` (reviewed and accepted like any other attention) or
  ``reinstall``. An existing row is never changed: the same instance (same ``instance_id``
  and ``plugin_id``) is kept as it is, and an ``instance_id`` or local ``plugin_id`` already
  used by another plugin gets a new one.
- Every entry is checked on its own (manifest, config schema, config values, scope, quotas,
  grants): a bad entry is dropped with a warning, a bad optional value is reset.

A 4.18.x file, and a 5.0 file written before this section, has no ``plugins`` section:
nothing changes for them.
"""

import ast
import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import BackupSection
from app.plugins.config_schema import check_config_schema, secret_fields, validate_config_values
from app.plugins.errors import PluginValidationError
from app.plugins.manifest import Origin, parse_manifest
from app.secret_fields import REDACTED
from app.services.backup_sections import RestoreSource, register_backup_section, register_on_restore

logger = logging.getLogger(__name__)

PLUGINS_SECTION = "plugins"
PLUGINS_SECTION_VERSION = 1
MAX_BACKUP_INSTANCES = 200
# The bound of ``PUT /api/plugins/{id}/source`` (``MAX_SOURCE_BYTES`` in the router).
MAX_SOURCE_BYTES = 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
RESTORED = "restored"
REINSTALL = "reinstall"
CONFIG_LANGUAGES = ("en", "fr")
_ORIGINS: dict[str, Origin] = {
    "bundled": "bundled",
    "store": "third_party",
    "custom": "third_party",
    "local": "local",
}
_PLUGIN_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+(-[a-z0-9]+)*$")
_LOCAL_SUFFIX = re.compile(r"-[0-9a-f]{6}$")


class BackupPluginInstance(BaseModel):
    """One instance as the file holds it: no secret value, no runtime state."""

    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    instance_id: str = Field(min_length=1, max_length=64)
    plugin_id: str = Field(min_length=1, max_length=64)
    kind: Literal["bundled", "store", "custom", "local"]
    label: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=64)
    enabled: bool = False
    manifest: dict[str, Any]
    config_schema: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    # Names of the write-only fields that held a value; the values never leave.
    secret_omitted: dict[str, Literal[True]] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)
    granted_permissions: dict[str, Any] = Field(default_factory=dict)
    quotas: dict[str, Any] = Field(default_factory=dict)
    auto_update: bool = False
    sort_order: int = 0
    source: str | None = Field(default=None, max_length=MAX_SOURCE_BYTES)
    repository: str | None = Field(default=None, max_length=500)


class BackupPluginsData(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    instances: list[Any] = Field(max_length=MAX_BACKUP_INSTANCES)


# -- Export ---------------------------------------------------------------------


def public_config(row: dict[str, Any]) -> dict[str, Any]:
    """The config without any secret: no write-only field of the schema, no field with a
    stored secret, no masked value."""
    hidden = secret_fields(row.get("config_schema") or {}) | set(row.get("secret_fields") or ())
    return {
        key: value
        for key, value in (row.get("config") or {}).items()
        if key not in hidden and value != REDACTED
    }


async def export_plugins() -> BackupSection | None:
    from app.repository.plugins import PluginRepository

    rows = await PluginRepository.get_all()
    if not rows:
        return None
    exported = [
        BackupPluginInstance(
            instance_id=row["instance_id"],
            plugin_id=row["plugin_id"],
            kind=row["kind"],
            label=row["label"],
            version=row["version"],
            enabled=row["enabled"],
            manifest=row["manifest"],
            config_schema=row["config_schema"],
            config=public_config(row),
            secret_omitted=dict.fromkeys(row["secret_fields"], True),
            scope=row["scope"],
            granted_permissions=row["granted_permissions"],
            quotas=row["quotas"],
            auto_update=row["auto_update"],
            sort_order=row["sort_order"],
            source=row["source"] if row["kind"] == "local" else None,
            repository=row["repository"],
        ).model_dump(mode="json")
        for row in rows
    ]
    return BackupSection(
        data={"instances": exported},
        secrets_omitted=any(entry["secret_omitted"] for entry in exported),
    )


# -- Restore --------------------------------------------------------------------


class _Dropped(Exception):
    """An entry that cannot be restored (the reason goes to the log, never a value)."""


class _DroppedSupervisor(_Dropped):
    """Dropped because a ``lan_hosts`` entry is on the Home Assistant supervisor network
    (add-on only, MSHL-350): the warning says why."""


def _warning_id(value: object) -> str:
    return value if isinstance(value, str) and _PLUGIN_ID.match(value) else "invalid"


def _json_size(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _schema(raw: dict[str, Any]) -> dict[str, Any]:
    """A config schema the instance can keep; raises ``_Dropped`` when it is invalid."""
    if not raw:
        return {}
    try:
        check_config_schema(raw)
    except PluginValidationError:
        raise _Dropped("config schema") from None
    return raw


def config_for_schema(schema: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """The values of ``config`` the schema accepts, never a secret field; a value that does
    not validate is dropped (the instance asks for it again)."""
    props = schema.get("properties", {}) if schema else {}
    secrets = secret_fields(schema) if schema else frozenset()
    kept: dict[str, Any] = {}
    for key, value in config.items():
        if key == "language" and key not in props:
            if value in CONFIG_LANGUAGES:
                kept[key] = value
        elif key in props and key not in secrets and value != REDACTED:
            kept[key] = value
    if not schema:
        return kept
    for _ in range(3):
        bad = {
            issue.path.split(".", 1)[0]
            for issue in validate_config_values(schema, kept)
            if issue.code == "config_value_invalid" and issue.path
        }
        if not bad & set(kept):
            break
        kept = {key: value for key, value in kept.items() if key not in bad}
    return kept


def _manifest(item: BackupPluginInstance, plugin_id: str) -> tuple[dict[str, Any], str]:
    """The stored form of a validated manifest (``permissions`` as declared) and its version."""
    draft = {**item.manifest, "id": plugin_id}
    try:
        parsed = parse_manifest(draft, origin=_ORIGINS[item.kind])
    except PluginValidationError as exc:
        if any(i.code == "lan_host_supervisor" for i in exc.issues):
            raise _DroppedSupervisor("manifest: lan_host_supervisor") from None
        raise _Dropped("manifest") from None
    stored = parsed.model_dump(mode="json", by_alias=True, exclude_none=True)
    stored["permissions"] = parsed.permissions.declared()
    return stored, parsed.version


def _new_local_id(plugin_id: str) -> str:
    base = _LOCAL_SUFFIX.sub("", plugin_id)[: 64 - 7]
    return f"{base}-{uuid.uuid4().hex[:6]}"


def granted_within(manifest: dict[str, Any], granted: dict[str, Any]) -> dict[str, Any]:
    """The file's grants when the manifest covers them, else none (the admin consents again)."""
    from app.plugins import installer

    try:
        problems = installer.grant_problems(manifest, granted)
    except (ValueError, TypeError, AttributeError):
        return {}
    return granted if not problems else {}


async def _existing(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    async with conn.execute(
        "SELECT instance_id, plugin_id, kind, version, manifest, config_schema, attention"
        " FROM plugins ORDER BY sort_order, installed_at, instance_id"
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "instance_id": row[0],
            "plugin_id": row[1],
            "kind": row[2],
            "version": row[3],
            "manifest": json.loads(row[4] or "{}"),
            "config_schema": json.loads(row[5] or "{}"),
            "attention": json.loads(row[6]) if row[6] else None,
        }
        for row in rows
    ]


def _sibling(rows: list[dict[str, Any]], item: BackupPluginInstance) -> dict[str, Any] | None:
    """An installed instance of the same plugin whose code the restored one can share."""
    for row in rows:
        if (
            row["plugin_id"] == item.plugin_id
            and row["kind"] == item.kind
            and (row["attention"] or {}).get("type") != REINSTALL
        ):
            return row
    return None


async def _insert(conn: aiosqlite.Connection, values: dict[str, Any]) -> None:
    now = int(time.time())
    async with conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM plugins") as cursor:
        row = await cursor.fetchone()
    sort_order = row[0] if row else 0
    await conn.execute(
        """
        INSERT INTO plugins (
            instance_id, plugin_id, label, kind, version, enabled, manifest,
            config_schema, config, scope, granted_permissions, quotas, auto_update,
            source, archive_sha256, repository, installed_at, updated_at, status,
            last_error, attention, sort_order
        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?,
                  'needs_attention', NULL, ?, ?)
        """,
        (
            values["instance_id"],
            values["plugin_id"],
            values["label"],
            values["kind"],
            values["version"],
            json.dumps(values["manifest"]),
            json.dumps(values["config_schema"]),
            json.dumps(values["config"]),
            json.dumps(values["scope"]),
            json.dumps(values["granted_permissions"]),
            json.dumps(values["quotas"]),
            1 if values["auto_update"] else 0,
            values["source"],
            values["repository"],
            now,
            now,
            json.dumps(values["attention"]),
            sort_order,
        ),
    )


def _plan(
    item: BackupPluginInstance, rows: list[dict[str, Any]], instance_id: str
) -> dict[str, Any]:
    """The row to insert for one entry; raises ``_Dropped``."""
    from app.routers.plugins import _check_quotas, _check_scope

    for value in (item.manifest, item.config_schema, item.config, item.granted_permissions):
        if _json_size(value) > MAX_JSON_BYTES:
            raise _Dropped("too large")
    if item.manifest.get("id") != item.plugin_id:
        raise _Dropped("manifest id")
    plugin_id = item.plugin_id
    source: str | None = None
    sibling = None
    if item.kind == "local":
        if item.source is None or len(item.source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise _Dropped("source")
        try:
            ast.parse(item.source)  # as PUT /source does: a broken body is never kept
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            raise _Dropped("source not valid Python") from None
        source = item.source
        if any(row["plugin_id"] == plugin_id for row in rows):
            plugin_id = _new_local_id(plugin_id)
        manifest, version = _manifest(item, plugin_id)
        schema = _schema(item.config_schema)
        granted = manifest["permissions"]
    else:
        manifest, version = _manifest(item, plugin_id)
        schema = _schema(item.config_schema)
        sibling = _sibling(rows, item)
        if sibling is not None:
            manifest, version = sibling["manifest"], sibling["version"]
            schema = sibling["config_schema"]
        granted = granted_within(manifest, item.granted_permissions)
    secrets = secret_fields(schema) if schema else frozenset()
    attention: dict[str, Any] = {
        "type": RESTORED if item.kind == "local" or sibling is not None else REINSTALL,
        "was_enabled": item.enabled,
        "secrets_missing": sorted(name for name in item.secret_omitted if name in secrets),
    }
    return {
        "instance_id": instance_id,
        "plugin_id": plugin_id,
        "label": item.label,
        "kind": item.kind,
        "version": version,
        "manifest": manifest,
        "config_schema": schema,
        "config": config_for_schema(schema, item.config),
        "scope": item.scope if _check_scope(item.scope) else {},
        "granted_permissions": granted,
        "quotas": item.quotas if not _check_quotas(item.quotas) else {},
        "auto_update": item.auto_update and item.kind != "local",
        "source": source,
        "repository": item.repository if item.kind in ("store", "custom") else None,
        "attention": attention,
    }


def _order(entry: object) -> int:
    value = entry.get("sort_order") if isinstance(entry, dict) else None
    return value if type(value) is int else 0


async def restore_plugins(section: BackupSection, conn: aiosqlite.Connection) -> list[str]:
    data = BackupPluginsData.model_validate(section.data)
    warnings: list[str] = []
    rows = await _existing(conn)
    by_instance = {row["instance_id"]: row for row in rows}
    seen: set[str] = set()
    restored = reinstall = waiting = 0
    for raw in sorted(data.instances, key=_order):
        plugin_id = _warning_id(raw.get("plugin_id") if isinstance(raw, dict) else None)
        try:
            item = BackupPluginInstance.model_validate(raw)
        except ValidationError:
            warnings.append(f"plugin_dropped:{plugin_id}")
            continue
        if item.instance_id in seen:
            warnings.append(f"plugin_dropped:{plugin_id}")
            continue
        seen.add(item.instance_id)
        current = by_instance.get(item.instance_id)
        if current is not None and current["plugin_id"] == item.plugin_id:
            warnings.append(f"plugin_kept:{plugin_id}")
            continue
        instance_id = item.instance_id
        try:
            instance_id = str(uuid.UUID(instance_id))
        except ValueError:
            instance_id = str(uuid.uuid4())
        if instance_id in by_instance:
            instance_id = str(uuid.uuid4())
        try:
            values = _plan(item, rows, instance_id)
        except _Dropped as exc:
            logger.warning("Backup restore: plugin entry %s dropped (%s)", plugin_id, exc)
            kind = (
                "plugin_dropped_supervisor"
                if isinstance(exc, _DroppedSupervisor)
                else "plugin_dropped"
            )
            warnings.append(f"{kind}:{plugin_id}")
            continue
        await _insert(conn, values)
        row = {
            key: values[key]
            for key in (
                "instance_id",
                "plugin_id",
                "kind",
                "version",
                "manifest",
                "config_schema",
                "attention",
            )
        }
        rows.append(row)
        by_instance[instance_id] = row
        if values["attention"]["type"] == REINSTALL:
            reinstall += 1
        else:
            restored += 1
        if values["attention"]["secrets_missing"]:
            waiting += 1

    if restored:
        warnings.append(f"plugins_restored:{restored}")
    if reinstall:
        warnings.append(f"plugins_to_reinstall:{reinstall}")
    if waiting:
        warnings.append(f"plugin_secrets_missing:{waiting}")
    logger.info(
        "Backup restore: %d plugin instance(s) read, %d restored, %d to reinstall",
        len(data.instances),
        restored,
        reinstall,
    )
    return warnings


# -- After the restore ----------------------------------------------------------


async def _promote_bundled(row: dict[str, Any], bundled: Any) -> None:
    """Write out the code of a bundled plugin this release ships, then make the restored
    instance use it (``reinstall`` becomes ``restored``)."""
    from app.plugins import installer
    from app.plugins.host import plugin_manager
    from app.repository.plugins import PluginRepository

    await asyncio.to_thread(installer.install_bundled, bundled, plugin_manager.plugins_root())
    manifest = bundled.package.manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    manifest["permissions"] = bundled.package.manifest.permissions.declared()
    schema = bundled.config_schema
    await PluginRepository.update(
        row["instance_id"],
        manifest=manifest,
        version=manifest["version"],
        config_schema=schema,
        config=config_for_schema(schema, public_config(row)),
        granted_permissions=granted_within(manifest, row["granted_permissions"]),
        attention={**(row["attention"] or {}), "type": RESTORED},
    )


async def _after_restore(source: RestoreSource) -> None:
    """Bring the bundled ``reinstall`` instances this release ships back, then refresh the
    instance cache and tell the UI about the rows."""
    from app.plugins import installer
    from app.plugins.host import plugin_manager
    from app.repository.plugins import PluginRepository

    rows = await PluginRepository.get_all()
    waiting = [
        row
        for row in rows
        if row["kind"] == "bundled" and (row["attention"] or {}).get("type") == REINSTALL
    ]
    if waiting:
        shipped = await asyncio.to_thread(installer.list_bundled)
        for row in waiting:
            bundled = shipped.get(row["plugin_id"])
            if bundled is None:
                continue
            try:
                await _promote_bundled(row, bundled)
            except (OSError, PluginValidationError, ValueError):
                logger.exception("Restoring bundled plugin %s failed", row["plugin_id"])
        rows = await PluginRepository.get_all()
    for row in rows:
        plugin_manager.announce(row["instance_id"])


register_backup_section(
    PLUGINS_SECTION,
    export_plugins,
    restore_plugins,
    description={
        "en": "Extensions (without their secrets): local ones whole, the others to reinstall",
        "fr": "Extensions (sans leurs secrets) : les locales entières, les autres à réinstaller",
    },
    sensitive=False,
    version=PLUGINS_SECTION_VERSION,
)
register_on_restore(PLUGINS_SECTION, _after_restore)
