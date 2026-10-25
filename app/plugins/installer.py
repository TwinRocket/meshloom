"""Where an instance's code comes from: archive sources, the bundled packages and the
local templates.

Every installed kind goes through one pipeline (`prepare_install` then
`PreparedInstall.commit`): an `ArchiveSource` gives the source's entry for the plugin
(id, version, permissions) and the archive with its sha256. A remote archive
(`RemoteArchiveSource`, tiers `custom` and `store`) is downloaded only when the
download gate says yes (`set_download_gate`, wired at startup to the Store consent
`store_egress_allowed`; fail closed: no gate, no download), through the Store egress
gate `store_fetch` (`app/plugins/store_egress.py`: the consent checked again, the
download cancelled when the Store is turned off, `STORE_ALLOWLIST` narrowed by the
source's hosts, no user info in the URL) and so the egress guard
(`app/net/egress_guard.py`: https, public addresses, checked redirects), with the
declared length checked first and the 5 MiB cap enforced while streaming; its size and
sha256 must match the entry before anything is written.
The archive is then validated and extracted (`app/plugins/package.py`: zip-slip,
symlinks, special files, size and entry caps) into `<data>/plugins/_staging/<uuid>/`,
its manifest must equal the entry (id, version, permissions), and the tree is moved
atomically into `<data>/plugins/<plugin_id>/`, shared by the instances of that plugin.
`custom` and `store` sources come from resolvers (`register_resolver`): `custom` is
registered by `app/plugins/custom_repos.py` (MSHL-196); `store` has none yet and
answers `plugin_source_unavailable`.

`bundled` plugins are packages in the app tree (`meshloom_plugin/official/<dir>/`),
versioned with meshloom and installed offline (`BundledSource`, origin `bundled`;
`install_bundled` and `swap_bundled` for the backup restore and the bundled update).
A local plugin has no directory: its manifest and source live in the `plugins` row and
are written out at each start (`PluginManager.code_dir`). The REST layer is
`app/routers/plugins.py`; the supervisor is `app/plugins/host.py`.
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import stat
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard

import httpx

from app.net.egress_guard import EgressBlocked, HostAllowlist
from app.plugins.errors import PluginValidationError
from app.plugins.package import (
    MAX_COMPRESSED_BYTES,
    ValidatedPackage,
    extract_package,
    validate_package,
)

logger = logging.getLogger(__name__)

# Tests point this at a temporary tree; production reads the shipped packages.
BUNDLED_ROOT = Path(__file__).resolve().parent.parent.parent / "meshloom_plugin" / "official"

LOCAL_TEMPLATES = ("blank", "echo")
LOCAL_VERSION = "0.1.0"
_STAGING = "_staging"
_PREVIOUS = "previous-"
# A manifest id (`app/plugins/manifest.py`): no separator, never `.` or `..`.
_PLUGIN_ID_RE = r"[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+(-[a-z0-9]+)*"
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def _is_plugin_id(value: object) -> TypeGuard[str]:
    """``_PLUGIN_ID_RE`` without a regex: two dot-separated parts of lowercase letters and
    digits, each part possibly hyphenated (no leading, trailing or doubled hyphen)."""
    if not isinstance(value, str) or not 3 <= len(value) <= 64:
        return False
    parts = value.split(".")
    return len(parts) == 2 and all(
        word and set(word) <= _ID_CHARS for part in parts for word in part.split("-")
    )


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
    discard_previous(swap_bundled(plugin, plugins_root))


def swap_bundled(plugin: BundledPlugin, plugins_root: Path) -> Path | None:
    """`install_bundled`, but the old tree is kept: returns where it was moved
    (`_staging/previous-<hex>`), None when there was none or nothing changed. The
    caller then calls `discard_previous` or `restore_previous`.

    A failed swap puts the old tree back. If even that fails, the old tree stays in
    `_staging/previous-<hex>` (logged, never deleted by a later install), so the
    instance can be recovered by hand; the next start installs the new tree, and a
    later startup sweep (`sweep_staging`) removes the old one."""
    manifest = plugin.package.manifest
    target = plugins_root / manifest.id
    current = target / "manifest.json"
    if current.is_file():
        try:
            if json.loads(current.read_text("utf-8")).get("version") == manifest.version:
                return None
        except (OSError, ValueError):
            pass
    staging = plugins_root / _STAGING / uuid.uuid4().hex
    try:
        extract_package(_zip(plugin.directory), staging, origin="bundled")
        return _swap_tree(staging, target, manifest.id)
    finally:
        _clean_staging(staging.parent, staging)


def _swap_tree(staging: Path, target: Path, plugin_id: str) -> Path | None:
    """Move the extracted tree `staging` to `target`; the old tree (if any) goes to
    `_staging/previous-<hex>` and its path is returned. A failed move puts the old tree
    back (logged and kept there when even that fails)."""
    aside = staging.parent / f"{_PREVIOUS}{uuid.uuid4().hex}"
    target.parent.mkdir(parents=True, exist_ok=True)
    moved = os.path.lexists(target)
    if moved:
        target.rename(aside)
    try:
        staging.rename(target)
    except OSError:
        if moved:
            try:
                aside.rename(target)
            except OSError:
                logger.error(
                    "Plugin %s: swap and restore failed, old tree kept at %s", plugin_id, aside
                )
        raise
    return aside if moved else None


def restore_previous(plugin: BundledPlugin, plugins_root: Path, previous: Path | None) -> None:
    """Undo `swap_bundled`: the old tree back in place, the new one removed (no-op
    when `previous` is None). Raises `OSError` when the old tree cannot go back; it
    then stays where it is."""
    if previous is None:
        return
    target = plugins_root / plugin.package.manifest.id
    discarded = previous.parent / uuid.uuid4().hex
    target.rename(discarded)
    try:
        previous.rename(target)
    except OSError:
        discarded.rename(target)
        raise
    _clean_staging(previous.parent, discarded)


def discard_previous(previous: Path | None) -> None:
    if previous is not None:
        _clean_staging(previous.parent, previous)


def _clean_staging(staging_root: Path, *paths: Path) -> None:
    """Remove `paths` (this install's own), then `_staging` itself when empty. An old
    tree a failed restore kept there is never touched."""
    for path in paths:
        _remove_path(path)
    with contextlib.suppress(OSError):
        staging_root.rmdir()


def sweep_staging(plugins_root: Path) -> None:
    """Startup: remove what a crash left in `<plugins_root>/_staging`. Never raises.

    A half-extracted tree or a discarded one goes. An old tree (`previous-<hex>`)
    goes only once `<plugins_root>/<its plugin id>/` holds a tree again; until then
    it is kept (logged) so the instance can be recovered by hand. Symlinks are
    removed as links, never followed, and `_staging` itself is left alone when it
    is a symlink or not a directory."""
    staging = plugins_root / _STAGING
    try:
        st = staging.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Plugin staging not swept: %s", type(exc).__name__)
        return
    if not stat.S_ISDIR(st.st_mode):
        logger.warning("Plugin staging %s is not a directory: left alone", staging)
        return
    try:
        entries = list(os.scandir(staging))
    except OSError as exc:
        logger.warning("Plugin staging not swept: %s", type(exc).__name__)
        return
    for entry in entries:
        path = Path(entry.path)
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                path.unlink()
                continue
            if entry.name.startswith(_PREVIOUS):
                plugin_id = _tree_plugin_id(path)
                if plugin_id is None or not _has_tree(plugins_root, plugin_id):
                    logger.warning("Plugin staging: old tree %s kept for recovery", path)
                    continue
            shutil.rmtree(path)
        except OSError as exc:
            logger.warning("Plugin staging: %s not removed: %s", path, type(exc).__name__)
    with contextlib.suppress(OSError):
        staging.rmdir()


def _tree_plugin_id(tree: Path) -> str | None:
    """The plugin id an extracted tree's `manifest.json` names (a regular file only)."""
    manifest = tree / "manifest.json"
    try:
        if not stat.S_ISREG(manifest.lstat().st_mode):
            return None
        plugin_id = json.loads(manifest.read_text("utf-8")).get("id")
    except (OSError, ValueError, AttributeError):
        return None
    if not _is_plugin_id(plugin_id):
        return None
    return plugin_id


def _has_tree(plugins_root: Path, plugin_id: str) -> bool:
    target = plugins_root / plugin_id
    try:
        return stat.S_ISDIR(target.lstat().st_mode) and (target / "manifest.json").is_file()
    except OSError:
        return False


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


# --- generic installer (sources, download, staging) ---------------------------------
#
# One path for every source (MSHL-195): the source announces a manifest entry (id,
# version, permissions) and an archive with its sha256; the archive is downloaded under
# the 5 MiB cap, its size and sha256 checked before anything is extracted, validated and
# extracted into `_staging/<hex>` (`package.py`), compared with the entry, and only then
# moved into `<plugins_root>/<plugin_id>/`. Every refusal leaves nothing on disk.

Tier = Literal["bundled", "custom", "store"]
DOWNLOAD_TIMEOUT = 60.0
DOWNLOAD_MAX_REDIRECTS = 5
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class InstallError(Exception):
    """A coded refusal of the installer. ``params`` never holds a URL, a host or a path."""

    def __init__(self, code: str, status: int = 400, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params


class ArchiveSource(ABC):
    """Where a plugin's archive comes from. ``remote`` sources reach the network and are
    refused unless the download gate allows them (`set_download_gate`)."""

    tier: Tier
    remote: bool = True
    repository: str | None = None

    @property
    @abstractmethod
    def expected_sha256(self) -> str:
        """The archive's sha256 announced by the source (lowercase hex)."""

    @property
    def expected_size(self) -> int | None:
        """The archive's size announced by the source, when it gives one."""
        return None

    @abstractmethod
    async def fetch_manifest(self) -> dict[str, Any]:
        """The source's entry for the plugin: at least ``id``, ``version`` and
        ``permissions``, which the archive's manifest must repeat exactly."""

    @abstractmethod
    async def fetch_archive(self) -> bytes:
        """The archive, never more than `package.MAX_COMPRESSED_BYTES` (enforced while
        it streams for a remote source)."""


class BundledSource(ArchiveSource):
    """A package shipped in the app tree (offline, never gated)."""

    tier: Tier = "bundled"
    remote = False

    def __init__(self, plugin: BundledPlugin) -> None:
        self.plugin = plugin
        self._archive = _zip(plugin.directory)
        self._sha256 = hashlib.sha256(self._archive).hexdigest()

    @property
    def expected_sha256(self) -> str:
        return self._sha256

    @property
    def expected_size(self) -> int | None:
        return len(self._archive)

    async def fetch_manifest(self) -> dict[str, Any]:
        return manifest_dict(self.plugin.package)

    async def fetch_archive(self) -> bytes:
        return self._archive


class RemoteArchiveSource(ArchiveSource):
    """An archive at an ``https://`` URL on a fixed set of hosts, announced by a source
    (a custom repository, the Store index). The sha256 is mandatory: without it nothing
    is downloaded. ``allowed_hosts`` can only narrow ``STORE_ALLOWLIST``."""

    def __init__(
        self,
        *,
        tier: Literal["custom", "store"],
        entry: dict[str, Any],
        archive_url: str,
        expected_sha256: str,
        allowed_hosts: HostAllowlist,
        expected_size: int | None = None,
        repository: str | None = None,
    ) -> None:
        if not _SHA256_RE.fullmatch(expected_sha256 or ""):
            raise ValueError("expected_sha256 must be 64 lowercase hex characters")
        if expected_size is not None and expected_size <= 0:
            raise ValueError("expected_size must be positive")
        self.tier = tier
        self.entry = dict(entry)
        self.archive_url = archive_url
        self._sha256 = expected_sha256
        self._size = expected_size
        self.allowed_hosts = allowed_hosts
        self.repository = repository

    @property
    def expected_sha256(self) -> str:
        return self._sha256

    @property
    def expected_size(self) -> int | None:
        return self._size

    async def fetch_manifest(self) -> dict[str, Any]:
        return dict(self.entry)

    async def fetch_archive(self) -> bytes:
        limit = (
            MAX_COMPRESSED_BYTES if self._size is None else min(self._size, MAX_COMPRESSED_BYTES)
        )
        try:
            return await download_archive(
                self.archive_url, allowed_hosts=self.allowed_hosts, max_bytes=limit
            )
        except InstallError as exc:
            if (
                exc.code == "archive_too_large"
                and self._size is not None
                and self._size < MAX_COMPRESSED_BYTES
            ):
                # More bytes than announced: the archive is not the one announced.
                raise InstallError("archive_mismatch", fields=["size"]) from None
            raise


async def download_archive(url: str, *, allowed_hosts: HostAllowlist, max_bytes: int) -> bytes:
    """GET ``url`` through the Store egress gate (``store_fetch``): refused
    (``store_offline``) while the Store consent is off and cancelled when it is turned
    off mid-download; https only, the hosts in both ``allowed_hosts`` and
    ``STORE_ALLOWLIST`` on every redirect hop, no user info, public addresses only,
    ``Content-Length`` checked first and the body aborted as soon as it streams past
    ``max_bytes``."""
    from app.plugins.store_egress import StoreOffline, store_fetch

    try:
        response = await store_fetch(
            "GET",
            url,
            allowed_hosts=allowed_hosts,
            timeout=httpx.Timeout(15.0),
            max_redirects=DOWNLOAD_MAX_REDIRECTS,
            max_response_bytes=max_bytes,
            total_timeout=DOWNLOAD_TIMEOUT,
            headers={"Accept": "application/octet-stream"},
        )
    except StoreOffline:
        raise InstallError("store_offline", 409) from None
    except EgressBlocked as exc:
        if exc.code == "egress_response_too_large":
            raise InstallError("archive_too_large") from None
        raise InstallError("download_blocked", 409, reason=exc.code) from None
    except (httpx.HTTPError, TimeoutError, OSError):
        raise InstallError("download_failed", 502) from None
    if response.status_code != 200:
        raise InstallError("download_failed", 502, http_status=response.status_code)
    return response.content


# Store egress (MSHL-194): remote downloads are refused until a gate says yes. The app
# wires `store_egress_allowed` at startup (`app/main.py`); `download_archive` checks the
# consent again through `store_fetch`.
DownloadGate = Callable[[], Awaitable[bool]]


async def _downloads_refused() -> bool:
    return False


_download_gate: DownloadGate = _downloads_refused


def set_download_gate(gate: DownloadGate | None) -> None:
    """Install the check that allows remote downloads (the Store egress setting); None
    puts the default back: every remote download refused."""
    global _download_gate
    _download_gate = gate or _downloads_refused


async def downloads_allowed() -> bool:
    try:
        return bool(await _download_gate())
    except Exception:
        logger.exception("Plugin download gate failed: downloads refused")
        return False


# Resolvers for the remote kinds (custom repositories: Store T22; Store index: M2b).
SourceResolver = Callable[[str, str | None, str | None], Awaitable[ArchiveSource | None]]
_resolvers: dict[str, SourceResolver] = {}


def register_resolver(kind: Literal["custom", "store"], resolver: SourceResolver | None) -> None:
    """Make ``kind`` installable: ``resolver(plugin_id, version, repository)`` returns the
    source or None (unknown). None unregisters it."""
    if resolver is None:
        _resolvers.pop(kind, None)
    else:
        _resolvers[kind] = resolver


async def resolve_source(
    kind: Tier, plugin_id: str, version: str | None = None, repository: str | None = None
) -> ArchiveSource | None:
    """The source of ``plugin_id`` for ``kind``; None when nothing provides it. Resolving
    a remote kind may reach the network, so it is gated like the download."""
    if kind == "bundled":
        bundled = (await asyncio.to_thread(list_bundled)).get(plugin_id)
        if bundled is None or (version is not None and version != bundled.package.manifest.version):
            return None
        return await asyncio.to_thread(BundledSource, bundled)
    resolver = _resolvers.get(kind)
    if resolver is None:
        return None
    if not await downloads_allowed():
        raise InstallError("store_offline", 409)
    return await resolver(plugin_id, version, repository)


def manifest_dict(package: ValidatedPackage) -> dict[str, Any]:
    """The stored form of a validated manifest (``permissions`` as declared)."""
    manifest = package.manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    manifest["permissions"] = package.manifest.permissions.declared()
    return manifest


def entry_identity(entry: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """``(id, version, declared permissions)`` of a source entry; ``archive_mismatch``
    when the entry cannot even say that."""
    try:
        plugin_id, version = entry["id"], entry["version"]
        if not isinstance(plugin_id, str) or not isinstance(version, str):
            raise TypeError
        return plugin_id, version, declared_permissions(entry)
    except (KeyError, TypeError, ValueError):
        raise InstallError("archive_mismatch", fields=["entry"]) from None


def compatible(manifest: dict[str, Any]) -> bool:
    """Whether this meshloom is in the manifest's ``compat.meshloom`` range (its release
    segment, so a pre-release of X.Y.Z counts as X.Y.Z). An unparsable running version
    does not block."""
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version

    from app.version_info import get_app_build_info

    spec = (manifest.get("compat") or {}).get("meshloom")
    if not spec:
        return True
    try:
        running = Version(Version(get_app_build_info().version).base_version)
        return SpecifierSet(spec).contains(running, prereleases=True)
    except (InvalidVersion, InvalidSpecifier):
        return True


@dataclass
class PreparedInstall:
    """An archive checked and extracted in `_staging`, not yet in place."""

    plugins_root: Path
    staging: Path
    package: ValidatedPackage
    manifest: dict[str, Any]
    config_schema: dict[str, Any]
    archive_sha256: str
    tier: Tier
    repository: str | None

    @property
    def plugin_id(self) -> str:
        return self.package.manifest.id

    def discard(self) -> None:
        _clean_staging(self.staging.parent, self.staging)

    def commit(self) -> None:
        """Move the tree into `<plugins_root>/<plugin_id>/` (atomic rename). A bundled
        tree already there at the same version is kept; any other old tree is replaced."""
        target = self.plugins_root / self.plugin_id
        try:
            if self.tier == "bundled" and _tree_version(target) == self.package.manifest.version:
                return
            discard_previous(_swap_tree(self.staging, target, self.plugin_id))
        finally:
            self.discard()


def _tree_version(target: Path) -> str | None:
    if not _is_plugin_id(target.name) or _tree_plugin_id(target) is None:
        return None
    try:
        version = json.loads((target / "manifest.json").read_text("utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def _plugin_dir(plugins_root: Path, plugin_id: str) -> Path | None:
    """``<plugins_root>/<plugin_id>``, or None when ``plugin_id`` is not a plugin id or the
    path would leave ``plugins_root``."""
    if not _is_plugin_id(plugin_id):
        return None
    root = os.path.normpath(plugins_root)
    target = os.path.normpath(os.path.join(root, plugin_id))
    if not target.startswith(root + os.sep) or os.path.dirname(target) != root:
        return None
    return Path(target)


def code_installed(plugins_root: Path, plugin_id: str, version: str) -> bool:
    """Whether ``<plugins_root>/<plugin_id>`` is a real tree (no symlink) of that plugin
    at that version: what a second instance of a custom or store plugin reuses."""
    target = _plugin_dir(plugins_root, plugin_id)
    if target is None:
        return False
    try:
        if not stat.S_ISDIR(target.lstat().st_mode):
            return False
    except OSError:
        return False
    return _tree_plugin_id(target) == plugin_id and _tree_version(target) == version


async def prepare_install(
    source: ArchiveSource, plugins_root: Path, *, plugin_id: str
) -> PreparedInstall:
    """Fetch, check and extract ``source`` into `_staging`; the caller then commits or
    discards. Raises `InstallError` (``store_offline``, ``archive_too_large``,
    ``archive_mismatch``, ``download_*``) or `PluginValidationError` (the archive),
    leaving nothing on disk."""
    if source.remote and not await downloads_allowed():
        raise InstallError("store_offline", 409)
    announced = entry_identity(await source.fetch_manifest())
    if announced[0] != plugin_id:
        raise InstallError("archive_mismatch", fields=["id"])
    size = source.expected_size
    if size is not None and size > MAX_COMPRESSED_BYTES:
        raise InstallError("archive_too_large")
    data = await source.fetch_archive()
    if len(data) > MAX_COMPRESSED_BYTES:
        raise InstallError("archive_too_large")
    if size is not None and len(data) != size:
        raise InstallError("archive_mismatch", fields=["size"])
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, source.expected_sha256.lower()):
        raise InstallError("archive_mismatch", fields=["sha256"])
    return await asyncio.to_thread(_stage, data, digest, source, announced, plugins_root)


def _stage(
    data: bytes,
    digest: str,
    source: ArchiveSource,
    announced: tuple[str, str, dict[str, Any]],
    plugins_root: Path,
) -> PreparedInstall:
    staging = plugins_root / _STAGING / uuid.uuid4().hex
    # Created here, so a failed extraction only ever empties this install's own subdir.
    staging.mkdir(parents=True)
    origin = "bundled" if source.tier == "bundled" else "third_party"
    try:
        package = extract_package(data, staging, origin=origin)
        manifest = manifest_dict(package)
        mismatched = [
            field
            for field, ours, theirs in zip(
                ("id", "version", "permissions"),
                (manifest["id"], manifest["version"], manifest["permissions"]),
                announced,
                strict=True,
            )
            if ours != theirs
        ]
        if mismatched:
            raise InstallError("archive_mismatch", fields=mismatched)
        schema_path = package.manifest.config_schema
        schema = json.loads((staging / schema_path).read_text("utf-8")) if schema_path else {}
    except BaseException:
        _clean_staging(staging.parent, staging)
        raise
    return PreparedInstall(
        plugins_root, staging, package, manifest, schema, digest, source.tier, source.repository
    )


def remove_code(plugins_root: Path, plugin_id: str) -> None:
    """Remove `<plugins_root>/<plugin_id>/` (the last instance went). A symlink is removed
    as a link, never followed; an id that is not a plugin id is ignored."""
    target = _plugin_dir(plugins_root, plugin_id)
    if target is not None:
        _remove_path(target)


def _remove_path(path: Path) -> None:
    try:
        st = path.lstat()
    except OSError:
        return
    try:
        if stat.S_ISDIR(st.st_mode):
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink()
    except OSError as exc:
        logger.warning("Plugin tree %s not removed: %s", path, type(exc).__name__)


# One install, reinstall or uninstall at a time per plugin id (they share the code dir).
_plugin_locks: dict[str, tuple[asyncio.Lock, int]] = {}


@contextlib.asynccontextmanager
async def plugin_lock(plugin_id: str) -> AsyncIterator[None]:
    lock, users = _plugin_locks.get(plugin_id, (asyncio.Lock(), 0))
    _plugin_locks[plugin_id] = (lock, users + 1)
    try:
        async with lock:
            yield
    finally:
        lock, users = _plugin_locks[plugin_id]
        if users <= 1:
            del _plugin_locks[plugin_id]
        else:
            _plugin_locks[plugin_id] = (lock, users - 1)
