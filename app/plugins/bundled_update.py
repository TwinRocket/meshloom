"""Bundled plugins follow meshloom: updated in place at start (MSHL-337).

`update_bundled()` runs from `lifecycle.start()` before any child starts. For each
bundled (`kind = bundled`) instance whose packaged version is newer than the stored
one it installs the new code once per plugin (`installer.install_bundled`, atomic,
the old tree kept on failure) and rewrites the row: version, manifest, config schema.
Config, secrets, scope, quotas, KV data and the label are kept.

- Same permissions: the instance keeps its grants and runs as before.
- Permissions added or changed: the grants of those names are dropped and the
  instance is held (`needs_attention`, `attention.type = review_permissions` with
  the names in `added`) until the admin accepts, through the usual PATCH that sends
  `granted_permissions` again. `enabled` is kept, so accepting starts it.
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


def _keep_grants(
    manifest: dict[str, Any], granted: dict[str, Any], changed: list[str]
) -> dict[str, Any]:
    """The previous grants still valid under the new manifest, none for a changed name."""
    problems = set(installer.grant_problems(manifest, granted))
    return {k: v for k, v in granted.items() if k not in problems and k not in changed}


async def _update_row(row: dict[str, Any], bundled: installer.BundledPlugin) -> None:
    from app.repository.plugins import PluginRepository
    from app.routers.plugins import _check_config, _target

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
    if changed:
        fields["status"] = "needs_attention"
        fields["attention"] = {
            "type": REVIEW_PERMISSIONS,
            "added": changed,
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
    await PluginRepository.update(row["instance_id"], **fields)
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
            "to_version": manifest["version"],
            "held": bool(changed),
        },
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
        bundled = shipped[plugin_id]
        try:
            await asyncio.to_thread(
                installer.install_bundled, bundled, plugin_manager.plugins_root()
            )
        except Exception as exc:
            logger.error(
                "Bundled plugin %s not updated, the old version is kept: %s",
                plugin_id,
                type(exc).__name__,
            )
            continue
        for row in plugin_rows:
            try:
                await _update_row(row, bundled)
            except Exception:
                logger.exception("Bundled plugin instance %s not updated", row["instance_id"])
                continue
            logger.info(
                "Bundled plugin %s updated %s -> %s",
                plugin_id,
                row["version"],
                bundled.package.manifest.version,
            )
