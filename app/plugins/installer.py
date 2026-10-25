"""Where an instance's code comes from: the bundled packages and the local templates.

`bundled` plugins are packages in the app tree (`meshloom_plugin/official/<dir>/`),
versioned with meshloom and installed offline: `install_bundled()` validates the
directory like any archive (`app/plugins/package.py`, origin `bundled`), then moves
it atomically into `<data>/plugins/<plugin_id>/` (shared by the instances of that
plugin). A local plugin has no directory: its manifest and source live in the
`plugins` row and are written out at each start (`PluginManager.code_dir`).
The REST layer is `app/routers/plugins.py`; the supervisor is `app/plugins/host.py`.
"""

import json
import logging
import re
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.plugins.errors import PluginValidationError
from app.plugins.package import ValidatedPackage, extract_package, validate_package

logger = logging.getLogger(__name__)

# Tests point this at a temporary tree; production reads the shipped packages.
BUNDLED_ROOT = Path(__file__).resolve().parent.parent.parent / "meshloom_plugin" / "official"

LOCAL_TEMPLATES = ("blank", "echo")
LOCAL_VERSION = "0.1.0"
_STAGING = "_staging"

_BLANK_SOURCE = '''"""A local plugin. Docs: docs/dev/plugins.md ("SDK")."""

from meshloom_plugin import host


async def on_message(event):
    return None
'''

_ECHO_SOURCE = '''"""Answers "!pling" with "Plong!" in the conversation it came from."""

from meshloom_plugin import host


async def on_message(event):
    if event["message_text"].strip() == "!pling":
        await host().call("messages.send", {"to": "reply", "text": "Plong!"})
'''

_TEMPLATE_SOURCES = {"blank": _BLANK_SOURCE, "echo": _ECHO_SOURCE}


@dataclass(frozen=True)
class BundledPlugin:
    directory: Path
    package: ValidatedPackage
    config_schema: dict[str, Any]


def _zip(directory: Path) -> bytes:
    from meshloom_plugin.dev import zip_directory

    return zip_directory(directory)


def _load(directory: Path) -> BundledPlugin | None:
    try:
        package = validate_package(_zip(directory), origin="bundled")
        schema_path = package.manifest.config_schema
        schema = json.loads((directory / schema_path).read_text("utf-8")) if schema_path else {}
    except (PluginValidationError, OSError, ValueError) as exc:
        # A broken bundled package is a packaging bug: skip it, say so in the log.
        logger.error("Bundled plugin %s is invalid: %s", directory.name, type(exc).__name__)
        return None
    return BundledPlugin(directory, package, schema)


def list_bundled() -> dict[str, BundledPlugin]:
    """The valid bundled packages by plugin id (none when the tree is absent)."""
    root = BUNDLED_ROOT
    if not root.is_dir():
        return {}
    found: dict[str, BundledPlugin] = {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        plugin = _load(directory)
        if plugin is not None:
            found[plugin.package.manifest.id] = plugin
    return found


def install_bundled(plugin: BundledPlugin, plugins_root: Path) -> None:
    """Make `<plugins_root>/<plugin_id>/` hold the bundled package (no-op when it is
    already there at the same version). Extracted into a staging directory first,
    so a failure leaves nothing behind and a running sibling instance never sees a
    half-written tree."""
    manifest = plugin.package.manifest
    target = plugins_root / manifest.id
    current = target / "manifest.json"
    if current.is_file():
        try:
            if json.loads(current.read_text("utf-8")).get("version") == manifest.version:
                return
        except (OSError, ValueError):
            pass
    staging = plugins_root / _STAGING / uuid.uuid4().hex
    try:
        extract_package(_zip(plugin.directory), staging, origin="bundled")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(target)
    finally:
        shutil.rmtree(staging.parent, ignore_errors=True)


def local_template_source(template: str) -> str:
    return _TEMPLATE_SOURCES[template]


def local_manifest(label: str, hooks: Sequence[str], permissions: dict[str, Any]) -> dict[str, Any]:
    """A minimal manifest for a local plugin; the caller validates it with
    `parse_manifest(origin="local")`."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30].strip("-") or "plugin"
    return {
        "manifest": 1,
        "id": f"local.{slug}-{uuid.uuid4().hex[:6]}",
        "version": LOCAL_VERSION,
        "name": {"en": label},
        "summary": {"en": label},
        "authors": [{"name": "Local"}],
        "license": "MIT",
        "categories": ["utility"],
        "runtime": {"type": "python", "api": 1, "python": ">=3.11"},
        "compat": {"meshloom": ">=5.0.0"},
        "entry": "plugin.py",
        "hooks": list(hooks),
        "permissions": permissions,
        "privacy": {"sends_to_third_parties": [], "stores_message_content": False},
        "readme": {"en": "README.md"},
    }


# --- consent ---------------------------------------------------------------------


def declared_permissions(manifest: dict[str, Any]) -> dict[str, Any]:
    """The permissions a manifest declares, normalised by the registry."""
    from app.plugins.permissions import Permissions

    return Permissions.model_validate(manifest.get("permissions") or {}).declared()


def _normalised(name: str, params: Any) -> Any:
    from pydantic import ValidationError

    from app.plugins.permissions import Permissions

    try:
        return Permissions.model_validate({name: params}).declared().get(name)
    except ValidationError:
        return None


def consent_missing(manifest: dict[str, Any], granted: dict[str, Any]) -> list[str]:
    """The declared permissions the grant does not cover in full (the install consent:
    `granted_permissions` must cover the manifest, nothing narrower)."""
    from app.plugins.capabilities.grants import effective_permissions

    declared = declared_permissions(manifest)
    effective = effective_permissions(manifest, granted)
    return sorted(name for name, params in declared.items() if effective.get(name) != params)


def grant_problems(manifest: dict[str, Any], granted: dict[str, Any]) -> list[str]:
    """The names in `granted` that exceed the manifest (not declared, invalid, or wider
    than declared): a grant is a subset of what the manifest asks for."""
    from app.plugins.capabilities.grants import effective_permissions

    effective = effective_permissions(manifest, granted)
    return sorted(
        name
        for name, params in granted.items()
        if name not in effective or effective[name] != _normalised(name, params)
    )
