"""Bundled plugins follow meshloom: updated in place at start (MSHL-337).

`update_bundled()` runs from `lifecycle.start()` before any child starts. For each
bundled (`kind = bundled`) instance whose packaged version is newer than the stored
one it installs the new code once per plugin (`installer.swap_bundled`, atomic,
the old tree kept on failure) and rewrites the rows of that plugin in one
transaction: version, manifest, config schema. If that write fails, the old tree
goes back (`installer.restore_previous`). Config, secrets, scope, quotas, KV data
and the label are kept.

- Same permissions: the instance keeps its grants and runs as before.
- Permissions added or changed (wider, narrower or otherwise different
  parameters, compared after normalisation): the grants of those names are dropped and the
  instance is held (`needs_attention`, `attention.type = review_permissions` with
  the names in `added`) until the admin accepts, through the usual PATCH that sends
  `granted_permissions` again. `enabled` is kept, so accepting starts it. A
  removed permission does not hold it (its grant goes, as it exceeds the manifest).
- Privacy declaration widened (a third-party host it did not list, or
  `stores_message_content` turning true): held the same way, grants kept,
  `attention.privacy_widened = true`. A narrower declaration does not hold it.
- Same or older packaged version: nothing happens. Idempotent: the row carries the
  new version afterwards.
- A package that is invalid, or an install or row write that fails, is logged and
  the old version stays; startup goes on. Nothing runs under the host lock
  (`MESHCORE_DISABLE_PLUGINS`): `lifecycle.start()` returns before calling here.

Instances restored without their code (`reinstall`), `revoked` and `incompatible`
ones are left alone.
"""

import asyncio
import logging
from typing import Any

from packaging.version import InvalidVersion, Version

from app.auth import audit
from app.plugins import installer
from app.plugins.host import plugin_manager

logger = logging.getLogger(__name__)

REVIEW_PERMISSIONS = "review_permissions"
_FROZEN = ("revoked", "incompatible")


def _newer(shipped: str, stored: str) -> bool:
    try:
        return Version(shipped) > Version(stored)
    except (InvalidVersion, TypeError):
        return False


def _eligible(row: dict[str, Any]) -> bool:
    return (
        row["kind"] == "bundled"
        and row["status"] not in _FROZEN
        and (row["attention"] or {}).get("type") != "reinstall"
    )


def _changed_permissions(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """The permissions the new manifest declares that the old one did not, or declared
    differently (a wider one counts: the admin consents again)."""
    before = installer.declared_permissions(old)
    after = installer.declared_permissions(new)
    return sorted(name for name, params in after.items() if before.get(name) != params)


def _privacy_widened(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """The new manifest's privacy declaration says more than the old one: a third
    party it did not list, or message content it did not store."""
    before, after = old.get("privacy") or {}, new.get("privacy") or {}
    hosts_before = {str(h).lower() for h in before.get("sends_to_third_parties") or []}
    hosts_after = {str(h).lower() for h in after.get("sends_to_third_parties") or []}
    stores = bool(after.get("stores_message_content")) and not bool(
        before.get("stores_message_content")
    )
    return bool(hosts_after - hosts_before) or stores


def _keep_grants(
    manifest: dict[str, Any], granted: dict[str, Any], changed: list[str]
) -> dict[str, Any]:
    """The previous grants still valid under the new manifest, none for a changed name."""
    problems = set(installer.grant_problems(manifest, granted))
    return {k: v for k, v in granted.items() if k not in problems and k not in changed}


def _row_fields(row: dict[str, Any], bundled: installer.BundledPlugin) -> dict[str, Any]:
    """The row's new fields under the shipped package (no write)."""
    from app.routers.plugins import _check_config

    manifest = bundled.package.manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    manifest["permissions"] = bundled.package.manifest.permissions.declared()
    changed = _changed_permissions(row["manifest"], manifest)
    fields: dict[str, Any] = {
        "version": manifest["version"],
        "manifest": manifest,
        "config_schema": bundled.config_schema,
        "granted_permissions": _keep_grants(manifest, row["granted_permissions"], changed),
    }
    attention = row["attention"]
    privacy = _privacy_widened(row["manifest"], manifest)
    if changed or privacy:
        fields["status"] = "needs_attention"
        fields["attention"] = {
            "type": REVIEW_PERMISSIONS,
            "added": changed,
            "privacy_widened": privacy,
            "from_version": row["version"],
            "to_version": manifest["version"],
        }
    elif (attention or {}).get("type") != "config_required":
        _, missing = _check_config(
            bundled.config_schema, row["config"], frozenset(row["secret_fields"])
        )
        if missing and row["status"] != "needs_attention":
            fields["enabled"] = False
            fields["status"] = "needs_attention"
            fields["attention"] = {"type": "config_required", "fields": missing}
    return fields


async def _audit(row: dict[str, Any], fields: dict[str, Any]) -> None:
    from app.routers.plugins import _target

    await audit.record(
        "plugin.updated",
        "success",
        actor=audit.SYSTEM_ACTOR,
        target=_target(row["instance_id"]),
        details={
            "plugin_id": row["plugin_id"],
            "kind": "bundled",
            "fields": ["version"],
            "from_version": row["version"],
            "to_version": fields["version"],
            "held": (fields.get("attention") or {}).get("type") == REVIEW_PERMISSIONS,
        },
    )


async def _update_plugin(
    plugin_id: str, bundled: installer.BundledPlugin, rows: list[dict[str, Any]]
) -> None:
    """New tree and new rows together: the rows are written in one transaction
    after the swap; if that write fails, the old tree goes back, so the old code
    never runs under the new manifest nor the new code under the old one."""
    from app.repository.plugins import PluginRepository

    try:
        updates = {row["instance_id"]: _row_fields(row, bundled) for row in rows}
    except Exception:
        logger.exception("Bundled plugin %s not updated: rows not computable", plugin_id)
        return
    root = plugin_manager.plugins_root()
    try:
        previous = await asyncio.to_thread(installer.swap_bundled, bundled, root)
    except Exception as exc:
        logger.error(
            "Bundled plugin %s not updated, the old version is kept: %s",
            plugin_id,
            type(exc).__name__,
        )
        return
    try:
        await PluginRepository.update_many(updates)
    except Exception:
        logger.exception("Bundled plugin %s not updated: the rows could not be written", plugin_id)
        try:
            await asyncio.to_thread(installer.restore_previous, bundled, root, previous)
        except OSError as exc:
            # The rows keep the old version: the next start retries the row write.
            logger.error(
                "Bundled plugin %s: the old tree could not be restored (%s): %s",
                plugin_id,
                type(exc).__name__,
                previous,
            )
        return
    await asyncio.to_thread(installer.discard_previous, previous)
    for row in rows:
        try:
            await _audit(row, updates[row["instance_id"]])
        except Exception:
            logger.exception("Bundled plugin instance %s: audit not recorded", row["instance_id"])
        logger.info(
            "Bundled plugin %s updated %s -> %s",
            plugin_id,
            row["version"],
            bundled.package.manifest.version,
        )


async def update_bundled() -> None:
    """Update the bundled instances behind the shipped packages. Never raises."""
    from app.repository.plugins import PluginRepository

    try:
        shipped = await asyncio.to_thread(installer.list_bundled)
        rows = [r for r in await PluginRepository.get_all() if _eligible(r)]
    except Exception:
        logger.exception("Bundled plugin update: could not list plugins")
        return
    by_plugin: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        bundled = shipped.get(row["plugin_id"])
        if bundled is not None and _newer(bundled.package.manifest.version, row["version"]):
            by_plugin.setdefault(row["plugin_id"], []).append(row)
    for plugin_id, plugin_rows in by_plugin.items():
        await _update_plugin(plugin_id, shipped[plugin_id], plugin_rows)
