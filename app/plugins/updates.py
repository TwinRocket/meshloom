"""Updates of installed Store plugins (MSHL-197, Store M2): list, apply, auto-update.

Only ``kind = custom`` instances (a custom GitHub repository, MSHL-196) update here:
``store`` waits for the signed index (M2b) and ``bundled`` follows meshloom at start
(``app/plugins/bundled_update.py``). An instance restored without its code
(``attention.type = reinstall``) is reinstalled first, never updated.

- ``available_updates(lang)`` (``GET /api/store/updates``) compares each plugin's
  instances with the release its repository last cached (``custom_repos``,
  ``store_repositories``): newer (``packaging``), same plugin id and inside this
  meshloom's ``compat.meshloom``. Each entry carries the permission diff (added,
  changed, removed, privacy widened), the config schema diff (fields added, removed,
  newly required; None for a release cached before migration 127), the changelog in
  ``lang`` when the source entry has one (``en`` fallback; custom releases have none)
  and, per instance, the hold the update would put on it (``hold``, ``permissions``,
  ``missing_fields``), the config fields it would drop (``dropped_fields``) and why
  auto-update would leave it for a manual update (``auto_hold``: the ``hold``, else
  ``config_dropped`` when a value would be dropped). No request is made.
- ``update_plugin()`` (``POST /api/plugins/{instance_id}/update``) goes through the
  install pipeline: ``resolve_source`` and ``prepare_install`` (refused while the
  Store is off, ``store_offline``; the archive's size, sha256 and manifest checked;
  extracted in ``_staging``), the ``compat`` check, then the running instances stop,
  ``PreparedInstall.swap`` moves the tree in and every row of the plugin is written in
  one transaction (``PluginRepository.update_many``); the old tree goes back when the
  write fails. The code directory is shared, so all the instances of the plugin move
  together; each one is checked for its own consent and config:

  - permissions the stored grants do not cover (added or changed) or a widened privacy
    declaration hold it: ``needs_attention``, ``attention.type = consent_required``
    with the ``permissions``; the grants of the changed names are dropped, ``enabled``
    is kept, and the usual PATCH that sends ``granted_permissions`` accepts it. The
    caller's ``granted_permissions`` (which must cover the new manifest) count as that
    consent for the instance it names only, never for its siblings;
  - a required config field the kept config lacks holds it with ``config_required``
    (``enabled = false``) until the admin fills it;
  - otherwise it is cleared (``revoked``, ``incompatible``, an earlier update's hold or
    notice) and restarted when enabled.

  Config (plain fields the new schema still declares), secrets the schema still
  declares, scope, quotas, KV data and the label are kept. A kept value the new schema
  rejects (a field it still declares, not required) is dropped and reported, never
  silently: ``attention.type = config_dropped`` with its ``fields`` on an instance that
  is not held (a notice: ``status`` is unchanged and it still runs; a PATCH that saves
  its ``config`` clears it), ``attention.config_dropped`` on a held one, and
  ``config_dropped`` in the ``plugin.updated`` audit details. A dropped required field
  is a ``config_required`` hold instead.
- ``auto_update()`` is a Store evaluator (``register_store_evaluator``): it reads only
  the releases the refresh cached and runs after each refresh tick and every
  ``store_egress.EVALUATE_INTERVAL_S`` (1 h) in between, while the Store is on (MSHL-393),
  so a window of at least 1 h is never missed. Inside the update window of the app
  settings (``auto_update_window_*``, ``app/services/update_window.py``) it applies the
  updates of the plugins whose instances all have ``auto_update`` on, never one that
  would hold any instance (consent or config) or drop a stored config value
  (``config_dropped``): those wait for a manual update, which applies them with the
  hold or the notice above. Applying downloads the archive through ``store_fetch``
  (the install pipeline); a release whose auto-update failed is not tried again for
  ``store_egress.REFRESH_INTERVAL_S`` (nor one held only after its download), so the
  hourly evaluation never downloads more often than the refresh did.
- ``mark_compat()`` runs at start (``lifecycle.start``), before any instance starts:
  a custom or store instance whose manifest's ``compat.meshloom`` excludes this
  meshloom becomes ``incompatible`` (it never starts); one that fits again goes back
  to ``installed``, or to ``needs_attention`` when it carries a hold (its
  ``attention`` is kept across ``incompatible``: a consent hold is never lost).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from packaging.version import InvalidVersion, Version

from app.auth import audit
from app.plugins import installer
from app.plugins.bundled_update import _changed_permissions, _privacy_widened
from app.plugins.config_schema import secret_fields
from app.plugins.host import PluginCapacityError, PluginHostError
from app.plugins.store_egress import (
    REFRESH_INTERVAL_S,
    StoreOffline,
    register_store_evaluator,
    unregister_store_evaluator,
)

logger = logging.getLogger(__name__)

CONSENT_REQUIRED = "consent_required"
CONFIG_REQUIRED = "config_required"
# A notice, not a hold: config values the new schema rejected (the instance still runs).
CONFIG_DROPPED = "config_dropped"
UPDATABLE_KINDS = ("custom",)
# Holds an update clears when it leaves the instance runnable.
_CLEARED_HOLDS = (CONSENT_REQUIRED, CONFIG_REQUIRED, "review_permissions")
_NOT_RUNNABLE = ("needs_attention", "revoked", "incompatible")


def _now() -> datetime:
    return datetime.now().astimezone()


# The auto-update clock (host local time); tests replace it.
clock: Callable[[], datetime] = _now
# The retry clock of failed auto-updates (monotonic seconds); tests replace it.
monotonic: Callable[[], float] = time.monotonic
# (plugin_id, version) -> monotonic time of a failed auto-update: not retried before
# REFRESH_INTERVAL_S, so the hourly evaluation downloads no more often than the refresh.
_failed_auto: dict[tuple[str, str], float] = {}


def _api() -> Any:
    """The plugins router: its error helpers, and the manager (tests replace it there)."""
    from app.routers import plugins

    return plugins


def _newer(candidate: object, current: object) -> bool:
    try:
        return Version(str(candidate)) > Version(str(current))
    except (InvalidVersion, TypeError):
        return False


def _reinstall(row: dict[str, Any]) -> bool:
    return (row.get("attention") or {}).get("type") == "reinstall"


def _permission_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    before = installer.declared_permissions(old)
    after = installer.declared_permissions(new)
    return {
        "added": sorted(set(after) - set(before)),
        "changed": sorted(n for n in set(after) & set(before) if after[n] != before[n]),
        "removed": sorted(set(before) - set(after)),
        "privacy_widened": _privacy_widened(old, new),
    }


def _schema_diff(old: dict[str, Any], new: dict[str, Any] | None) -> dict[str, Any] | None:
    if new is None:
        return None
    before, after = set(old.get("properties") or {}), set(new.get("properties") or {})
    required_before = set(old.get("required") or [])
    return {
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "required_added": sorted(set(new.get("required") or []) - required_before),
    }


def _changelog(entry: dict[str, Any], lang: str) -> str | None:
    """The source entry's changelog in ``lang`` (``en`` fallback), when it has one."""
    value = entry.get("changelog")
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        for key in (lang, "en", *value):
            text = value.get(key)
            if isinstance(text, str) and text:
                return text
    return None


def _kept_config(row: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """The plain config values the new schema still accepts (secrets are kept by the
    repository: an omitted secret keeps its value)."""
    from app.services.backup_plugins import config_for_schema, public_config

    return config_for_schema(schema, public_config(row))


def _dropped_config(
    row: dict[str, Any], schema: dict[str, Any], kept: dict[str, Any], missing: list[str]
) -> list[str]:
    """The plain fields the new schema still declares whose stored value it rejects
    (``config_for_schema`` drops them); a now missing required field is reported by
    ``config_required`` instead."""
    props = schema.get("properties") or {}
    secrets = secret_fields(schema)
    from app.services.backup_plugins import public_config

    return sorted(
        key
        for key in public_config(row)
        if key in props and key not in secrets and key not in kept and key not in missing
    )


def _stored_secrets(row: dict[str, Any], schema: dict[str, Any]) -> frozenset[str]:
    return frozenset(row.get("secret_fields") or ()) & secret_fields(schema)


def _missing(row: dict[str, Any], schema: dict[str, Any], config: dict[str, Any]) -> list[str]:
    return _api()._check_config(schema, config, _stored_secrets(row, schema))[1]


def _grant_hold(
    row: dict[str, Any], manifest: dict[str, Any], consented: bool
) -> tuple[dict[str, Any], list[str], bool]:
    """``(grants, permissions to consent to, privacy widened)`` of a row under the new
    manifest. Never more than the admin granted: without consent, a changed name loses
    its grant."""
    if consented:
        return installer.declared_permissions(manifest), [], False
    changed = _changed_permissions(row["manifest"], manifest)
    granted = row["granted_permissions"] or {}
    problems = set(installer.grant_problems(manifest, granted))
    grants = {k: v for k, v in granted.items() if k not in problems and k not in changed}
    attention = row.get("attention") or {}
    privacy = _privacy_widened(row["manifest"], manifest) or (
        attention.get("type") in _CLEARED_HOLDS and bool(attention.get("privacy_widened"))
    )
    return grants, installer.consent_missing(manifest, grants), privacy


def _hold_of(permissions: list[str], privacy: bool, missing: list[str]) -> str | None:
    if permissions or privacy:
        return CONSENT_REQUIRED
    return CONFIG_REQUIRED if missing else None


def _auto_hold(hold: str | None, dropped: list[str]) -> str | None:
    """Why auto-update leaves the instance for a manual update: its hold, else a stored
    config value the update would drop."""
    return hold or (CONFIG_DROPPED if dropped else None)


# --- List ----------------------------------------------------------------------------


async def _plugins() -> dict[str, list[dict[str, Any]]]:
    from app.repository.plugins import PluginRepository

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in await PluginRepository.get_all():
        if row["kind"] in UPDATABLE_KINDS and row.get("repository") and not _reinstall(row):
            groups.setdefault(row["plugin_id"], []).append(row)
    return groups


async def available_updates(lang: str = "en") -> list[dict[str, Any]]:
    """The plugins with a newer compatible release cached by their source. No request."""
    from app.plugins import custom_repos

    out: list[dict[str, Any]] = []
    for plugin_id, rows in sorted((await _plugins()).items()):
        current = rows[0]
        cached = await custom_repos.get_row(current["repository"])
        if not cached or not cached.get("manifest") or cached.get("plugin_id") != plugin_id:
            continue
        manifest, schema = cached["manifest"], cached.get("config_schema")
        if not _newer(cached["version"], current["version"]) or not installer.compatible(manifest):
            continue
        instances = []
        for row in rows:
            _, permissions, privacy = _grant_hold(row, manifest, consented=False)
            missing, dropped = [], []
            if schema is not None:
                kept = _kept_config(row, schema)
                missing = _missing(row, schema, kept)
                dropped = _dropped_config(row, schema, kept, missing)
            hold = _hold_of(permissions, privacy, missing)
            instances.append(
                {
                    "instance_id": row["instance_id"],
                    "label": row["label"],
                    "version": row["version"],
                    "auto_update": bool(row.get("auto_update")),
                    "hold": hold,
                    "auto_hold": _auto_hold(hold, dropped),
                    "permissions": permissions,
                    "missing_fields": missing,
                    "dropped_fields": dropped,
                }
            )
        out.append(
            {
                "plugin_id": plugin_id,
                "kind": current["kind"],
                "repository": current["repository"],
                "from_version": current["version"],
                "to_version": cached["version"],
                "name": manifest.get("name") or {},
                "changelog": _changelog(manifest, lang),
                "permissions": _permission_diff(current["manifest"], manifest),
                "config_schema": _schema_diff(current["config_schema"] or {}, schema),
                "instances": instances,
            }
        )
    return out


# --- Apply ---------------------------------------------------------------------------


def _row_fields(
    row: dict[str, Any],
    prepared: installer.PreparedInstall,
    *,
    consented: bool,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """The row's fields under the prepared release (no write). Raises the router's 400
    ``config_invalid`` for a ``config`` the new schema refuses."""
    api = _api()
    manifest, schema = prepared.manifest, prepared.config_schema
    grants, permissions, privacy = _grant_hold(row, manifest, consented)
    dropped: list[str] = []
    if config is None:
        config = _kept_config(row, schema)
        missing = _missing(row, schema, config)
        dropped = _dropped_config(row, schema, config, missing)
    else:
        invalid, missing = api._check_config(schema, config, _stored_secrets(row, schema))
        if invalid:
            raise api._config_invalid(invalid)
    fields: dict[str, Any] = {
        "version": manifest["version"],
        "manifest": manifest,
        "config_schema": schema,
        "config": config,
        "granted_permissions": grants,
        "archive_sha256": prepared.archive_sha256,
        "repository": prepared.repository,
    }
    hold = _hold_of(permissions, privacy, missing)
    if missing:
        fields["enabled"] = False
    if hold == CONSENT_REQUIRED:
        fields["status"] = "needs_attention"
        fields["attention"] = {
            "type": CONSENT_REQUIRED,
            "permissions": permissions,
            "privacy_widened": privacy,
            "from_version": row["version"],
            "to_version": manifest["version"],
            **({"fields": missing} if missing else {}),
            **({CONFIG_DROPPED: dropped} if dropped else {}),
        }
    elif hold == CONFIG_REQUIRED:
        fields["status"] = "needs_attention"
        fields["attention"] = {
            "type": CONFIG_REQUIRED,
            "fields": missing,
            **({CONFIG_DROPPED: dropped} if dropped else {}),
        }
    else:
        if row["status"] in ("revoked", "incompatible") or (
            row["status"] == "needs_attention"
            and (row.get("attention") or {}).get("type") in _CLEARED_HOLDS
        ):
            fields["status"] = "installed"
            fields["attention"] = None
        elif (row.get("attention") or {}).get("type") == CONFIG_DROPPED:
            fields["attention"] = None  # an earlier update's notice
        if dropped:
            # Not a hold: the instance runs; the notice stays until its config is saved.
            fields["attention"] = {
                "type": CONFIG_DROPPED,
                "fields": dropped,
                "from_version": row["version"],
                "to_version": manifest["version"],
            }
    return fields


def _held(fields: dict[str, Any]) -> bool:
    return (fields.get("attention") or {}).get("type") in (CONSENT_REQUIRED, CONFIG_REQUIRED)


def config_dropped(fields: dict[str, Any]) -> list[str]:
    """The config fields an update dropped (its notice, or the key on its hold)."""
    attention = fields.get("attention") or {}
    if attention.get("type") == CONFIG_DROPPED:
        return list(attention.get("fields") or [])
    return list(attention.get(CONFIG_DROPPED) or [])


async def _audit(row: dict[str, Any], fields: dict[str, Any], actor: Any, auto: bool) -> None:
    await audit.record(
        "plugin.updated",
        "success",
        actor=actor,
        target=_api()._target(row["instance_id"]),
        details={
            "plugin_id": row["plugin_id"],
            "kind": row["kind"],
            "fields": ["version"],
            "from_version": row["version"],
            "to_version": fields["version"],
            "held": (fields.get("attention") or {}).get("type") if _held(fields) else None,
            "config_dropped": config_dropped(fields),
            "auto": auto,
        },
    )


async def _restart(instance_ids: list[str]) -> None:
    manager = _api().plugin_manager
    for instance_id in instance_ids:
        try:
            await manager.restart(instance_id)
        except (PluginCapacityError, PluginHostError) as exc:
            logger.warning("Updated plugin instance %s not started: %s", instance_id, exc.code)
        except Exception:
            logger.exception("Updated plugin instance %s failed to start", instance_id)


async def update_plugin(
    instance_id: str,
    *,
    version: str | None = None,
    granted: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    auto: bool = False,
    actor: Any = audit.SYSTEM_ACTOR,
    audit_self: bool = True,
) -> dict[str, dict[str, Any]] | None:
    """Update the plugin of ``instance_id`` (and so every instance of it) to ``version``
    (the latest release when None). Returns the fields written per instance; None when
    ``auto`` and an instance would be held or lose a config value (nothing changes). Raises the router's
    ``HTTPException`` (``plugin_not_updatable``, ``plugin_up_to_date``,
    ``plugin_source_unavailable``, ``store_offline``, ``archive_mismatch``,
    ``consent_required``, ``incompatible``, ``config_invalid``, ``update_failed``).
    Each updated instance gets a ``plugin.updated`` audit row; ``audit_self=False``
    leaves out ``instance_id``'s (the route writes it, failures included)."""
    api = _api()
    row = await api._row(instance_id)
    async with installer.plugin_lock(row["plugin_id"]):
        return await _update_locked(
            row["instance_id"],
            version,
            granted,
            config,
            auto=auto,
            actor=actor,
            skip_audit=None if audit_self else row["instance_id"],
        )


async def _update_locked(
    instance_id: str,
    version: str | None,
    granted: dict[str, Any] | None,
    config: dict[str, Any] | None,
    *,
    auto: bool,
    actor: Any,
    skip_audit: str | None,
) -> dict[str, dict[str, Any]] | None:
    from app.repository.plugins import PluginRepository

    api = _api()
    row = await api._row(instance_id)
    if row["kind"] not in UPDATABLE_KINDS or not row.get("repository"):
        raise api._error(409, "plugin_not_updatable", "This plugin does not update from a source")
    if _reinstall(row):
        raise api._error(
            409, "plugin_reinstall_required", "The instance must be reinstalled before it updates"
        )
    rows = [
        r for r in await PluginRepository.get_by_plugin_id(row["plugin_id"]) if not _reinstall(r)
    ]
    source = await api._source(row["kind"], row["plugin_id"], version, row["repository"])
    announced = await source.fetch_manifest()
    if not _newer(announced.get("version"), row["version"]):
        raise api._error(409, "plugin_up_to_date", "No newer release of this plugin")
    if granted is not None:
        api._consent(announced, granted)
    prepared = await api._prepare(source, row["plugin_id"])
    try:
        if granted is not None:
            api._consent(prepared.manifest, granted)
        if not installer.compatible(prepared.manifest):
            raise api._error(
                409,
                "incompatible",
                "This plugin does not support this version of Meshloom",
                compat=(prepared.manifest.get("compat") or {}).get("meshloom"),
            )
        updates = {
            r["instance_id"]: _row_fields(
                r,
                prepared,
                consented=granted is not None and r["instance_id"] == instance_id,
                config=config if r["instance_id"] == instance_id else None,
            )
            for r in rows
        }
        if auto and any(_held(f) for f in updates.values()):
            logger.info("Plugin %s not auto-updated: an instance would be held", row["plugin_id"])
            return None
        if auto and any(config_dropped(f) for f in updates.values()):
            # Held for a manual update: never drop a stored value unattended.
            logger.info(
                "Plugin %s not auto-updated: a config value would be dropped", row["plugin_id"]
            )
            return None
        return await _apply(
            row["plugin_id"], rows, updates, prepared, auto=auto, actor=actor, skip_audit=skip_audit
        )
    finally:
        await asyncio.to_thread(prepared.discard)


async def _apply(
    plugin_id: str,
    rows: list[dict[str, Any]],
    updates: dict[str, dict[str, Any]],
    prepared: installer.PreparedInstall,
    *,
    auto: bool,
    actor: Any,
    skip_audit: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Stop the running instances, swap the tree, write the rows (one transaction),
    restart. A failed swap or write leaves the old tree and rows, restarted."""
    from app.repository.plugins import PluginRepository

    api = _api()
    manager = api.plugin_manager
    running = [r["instance_id"] for r in rows if manager.get(r["instance_id"]) is not None]
    for instance_id in running:
        await manager.stop(instance_id, status="installed")
    root = manager.plugins_root()
    try:
        previous = await asyncio.to_thread(prepared.swap)
    except OSError:
        logger.exception("Plugin %s not updated: the new tree could not be moved in", plugin_id)
        await _restart(running)
        raise api._error(500, "update_failed", "The plugin could not be updated") from None
    try:
        await PluginRepository.update_many(updates)
    except Exception:
        logger.exception("Plugin %s not updated: the rows could not be written", plugin_id)
        try:
            await asyncio.to_thread(installer.restore_tree, root, plugin_id, previous)
        except OSError as exc:
            # New code, old rows: `code_matches` keeps the instances from starting.
            logger.error(
                "Plugin %s: the old tree could not be restored (%s), kept at %s",
                plugin_id,
                type(exc).__name__,
                previous,
            )
        await _restart(running)
        raise api._error(500, "update_failed", "The plugin could not be updated") from None
    await asyncio.to_thread(installer.discard_previous, previous)
    start = []
    for r in rows:
        fields = updates[r["instance_id"]]
        if fields.get("enabled", r["enabled"]) and fields.get("status", r["status"]) not in (
            _NOT_RUNNABLE
        ):
            start.append(r["instance_id"])
    await _restart(start)
    for r in rows:
        manager.announce(r["instance_id"])
        if r["instance_id"] == skip_audit:
            continue
        try:
            await _audit(r, updates[r["instance_id"]], actor, auto)
        except Exception:
            logger.exception("Plugin instance %s: update audit not recorded", r["instance_id"])
    logger.info(
        "Plugin %s updated %s -> %s", plugin_id, rows[0]["version"], prepared.manifest["version"]
    )
    return updates


# --- Auto-update and start -----------------------------------------------------------


async def auto_update() -> None:
    """Store evaluator: apply the auto-updates inside the update window. Reads only the
    cached releases; applying one downloads its archive (``update_plugin``)."""
    from app.repository import AppSettingsRepository
    from app.services.update_window import in_window

    if _api().plugin_manager.disabled_source() is not None:
        return
    settings = await AppSettingsRepository.get()
    try:
        inside = in_window(
            clock(),
            settings.auto_update_window_start,
            settings.auto_update_window_end,
            settings.auto_update_weekdays,
        )
    except ValueError:
        logger.warning("Plugin auto-update: the update window is invalid")
        return
    if not inside:
        return
    for entry in await available_updates():
        instances = entry["instances"]
        if not instances or not all(i["auto_update"] for i in instances):
            continue
        holds = sorted({i["auto_hold"] for i in instances if i["auto_hold"]})
        if holds:
            logger.info(
                "Plugin %s not auto-updated: it waits for a manual update (%s)",
                entry["plugin_id"],
                ", ".join(holds),
            )
            continue
        key = (entry["plugin_id"], entry["to_version"])
        failed_at = _failed_auto.get(key)
        if failed_at is not None and monotonic() - failed_at < REFRESH_INTERVAL_S:
            continue
        try:
            done = await update_plugin(
                instances[0]["instance_id"], version=entry["to_version"], auto=True
            )
        except HTTPException as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if code == "store_offline":
                raise StoreOffline() from None
            _failed_auto[key] = monotonic()
            logger.warning("Plugin %s not auto-updated: %s", entry["plugin_id"], code)
            continue
        except (StoreOffline, asyncio.CancelledError):
            raise
        except Exception as exc:
            # Any other failure waits too. Logged by type only: a message could carry
            # a config value or a secret.
            _failed_auto[key] = monotonic()
            logger.warning(
                "Plugin %s not auto-updated: unexpected %s", entry["plugin_id"], type(exc).__name__
            )
            continue
        if done is None:  # held after the download (a release cached without its schema)
            _failed_auto[key] = monotonic()
        else:
            _failed_auto.pop(key, None)


async def mark_compat() -> None:
    """At start: an installed custom/store instance outside its ``compat.meshloom``
    becomes ``incompatible``; one inside it again goes back to ``installed``. Never
    raises."""
    from app.repository.plugins import PluginRepository

    try:
        rows = await PluginRepository.get_all()
    except Exception:
        logger.exception("Plugin compat check: could not list plugins")
        return
    for row in rows:
        if row["kind"] not in ("custom", "store") or _reinstall(row):
            continue
        fits = installer.compatible(row["manifest"] or {})
        if not fits and row["status"] != "incompatible":
            status = "incompatible"
        elif fits and row["status"] == "incompatible":
            # The attention survives `incompatible`: a hold (consent, config, review)
            # comes back with it, so nothing it guards runs without the admin.
            kind = (row.get("attention") or {}).get("type")
            status = "needs_attention" if kind and kind != CONFIG_DROPPED else "installed"
        else:
            continue
        try:
            await PluginRepository.update(row["instance_id"], status=status)
        except Exception:
            logger.exception("Plugin instance %s: compat status not stored", row["instance_id"])
            continue
        logger.warning(
            "Plugin instance %s (%s %s) is now %s",
            row["instance_id"],
            row["plugin_id"],
            row["version"],
            status,
        )


def register() -> None:
    register_store_evaluator(auto_update)


def unregister() -> None:
    unregister_store_evaluator(auto_update)
    _failed_auto.clear()
