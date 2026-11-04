"""Custom repositories of the Store: GitHub ``owner/name`` repositories the admin adds
(MSHL-196, Store T22), like HACS custom repositories.

The source is unsigned: the admin takes responsibility for it. Every request goes
through the Store egress gate (``store_fetch`` in ``app/plugins/store_egress.py``):
refused (``store_offline``) before any lookup while ``app_settings.store_online`` is
off, https only, every redirect hop re-validated against ``CUSTOM_REPO_HOSTS`` (the
GitHub hosts of ``STORE_GITHUB_HOSTS``; never ``store.meshloom.app``), each answer
capped. No API, no token:

1. ``GET https://github.com/<owner>/<name>/releases/latest``, not followed: the answer
   must be a redirect whose ``Location`` is exactly
   ``https://github.com/<owner>/<name>/releases/tag/<tag>`` (the repository compared
   case-insensitively, the case GitHub gives kept for the next URLs), the same lookup as
   ``fetch_github_latest`` in ``app/services/oss_updates.py``. A 404 or a redirect to
   the repository page is ``repository_no_release``; a redirect elsewhere (a rename or
   a transfer) is ``repository_moved``: the admin adds the new name.
2. The three release assets under ``releases/download/<tag>/``, followed to the asset
   host: ``manifest.json`` (``MANIFEST_MAX_BYTES``), ``plugin.zip.sha256``
   (``SHA256_MAX_BYTES``, the first 64-hex token) and ``plugin.zip``
   (``MAX_COMPRESSED_BYTES``). A 404 on any of them is ``repository_assets_missing``
   with the missing names.
3. The manifest goes through ``load_manifest_json`` and the archive through
   ``validate_package`` (origin ``third_party``); the archive's sha256 must equal the
   ``.sha256`` asset and its manifest must repeat id, version and permissions. The
   ``.sha256`` comes over the same channel as the archive: it is an integrity check,
   not a security one (the API and the UI say so, ``CUSTOM_WARNING``).

``store_repositories`` (migration 126) keeps each repository and the last release read,
with the archive's config schema (migration 127, MSHL-197: what ``GET
/api/store/updates`` compares, ``app/plugins/updates.py``).
The archive is downloaded when a repository is added and when the refresh sees a new
tag; an install (``resolve_custom``, the installer's ``custom`` resolver) reads the
latest release again and lets the installer download and check the archive. The
background refresh (``refresh_repositories``, a Store refresher: every 6 h while the
Store is on and an online instance is installed) updates the cached releases. Removing
a repository never touches installed instances: they keep running, without updates.
Updating them from the cached release: ``app/plugins/updates.py`` (MSHL-197).
There is no online revocation for custom plugins (Store T19, M2b).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import re
import time
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.net.egress_guard import CheckedResponse, EgressBlocked, HostAllowlist
from app.plugins import store_egress
from app.plugins.errors import PluginValidationError
from app.plugins.installer import (
    InstallError,
    RemoteArchiveSource,
    compatible,
    register_resolver,
)
from app.plugins.manifest import MANIFEST_MAX_BYTES, Manifest, load_manifest_json
from app.plugins.package import MAX_COMPRESSED_BYTES, validate_package
from app.plugins.store_egress import (
    STORE_GITHUB_HOSTS,
    StoreOffline,
    register_store_refresher,
    store_fetch,
    unregister_store_refresher,
)
from app.repository.store_repositories import StoreRepositoriesRepository

logger = logging.getLogger(__name__)

GITHUB = "https://github.com"
CUSTOM_REPO_HOSTS = HostAllowlist.of(*STORE_GITHUB_HOSTS)
ASSET_MANIFEST = "manifest.json"
ASSET_ARCHIVE = "plugin.zip"
ASSET_SHA256 = "plugin.zip.sha256"
SHA256_MAX_BYTES = 1024
REDIRECT_MAX_BYTES = 16 * 1024
# github.com -> the asset host, with room for one more hop.
ASSET_MAX_REDIRECTS = 3
MAX_REPOSITORIES = 50

# GitHub's rules: an owner is 1-39 alphanumerics or single inner hyphens; a name is
# 1-100 of [A-Za-z0-9._-], never "." or "..".
_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")
_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
# A tag usable as one URL path segment.
_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}")
_SHA256_TOKEN_RE = re.compile(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{64})(?![0-9A-Fa-f])")
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

# Sent with every custom catalogue entry and shown by the UI (its own i18n copy).
CUSTOM_WARNING_CODE = "custom_unsigned"
CUSTOM_WARNING = {
    "en": (
        "Custom repository: neither reviewed nor signed by Meshloom. The sha256 next to the "
        "archive comes from the same place as the archive: it only checks that the "
        "download is complete (integrity), not who made it (security). There is no "
        "online revocation for custom plugins. Install only from authors you trust."
    ),
    "fr": (
        "Dépôt personnalisé : ni relu ni signé par Meshloom. Le sha256 publié à côté de "
        "l'archive vient du même endroit que l'archive : il vérifie seulement que le "
        "téléchargement est complet (intégrité), pas qui l'a fait (sécurité). Il n'y a "
        "pas de révocation en ligne pour les plugins personnalisés. N'installez que des "
        "plugins d'auteurs de confiance."
    ),
}


@dataclass(frozen=True)
class Repository:
    key: str  # lowercase owner/name, the primary key
    display: str  # the case GitHub gives (or the admin typed, before the first lookup)


@dataclass(frozen=True)
class Release:
    tag: str
    display: str
    entry: dict[str, Any]
    sha256: str
    size: int | None
    # The archive's config schema ({} without one); None when the archive was not read.
    config_schema: dict[str, Any] | None = None
    # The archive's README files by language ({} none); None when it was not read.
    readme: dict[str, str] | None = None


def parse_repository(text: object) -> Repository:
    """``owner/name`` checked against GitHub's rules; ``repository_invalid`` otherwise.
    Only the bare form is accepted (no URL, no host, no extra segment)."""
    if not isinstance(text, str):
        raise InstallError("repository_invalid")
    value = text.strip()
    owner, sep, name = value.partition("/")
    if (
        not sep
        or not _OWNER_RE.fullmatch(owner)
        or not _NAME_RE.fullmatch(name)
        or name in {".", ".."}
    ):
        raise InstallError("repository_invalid")
    return Repository(value.lower(), value)


def _repo_url(display: str) -> str:
    owner, _, name = display.partition("/")
    return f"{GITHUB}/{quote(owner, safe='')}/{quote(name, safe='')}"


async def _get(url: str, *, max_bytes: int, max_redirects: int) -> CheckedResponse:
    """One GET through ``store_fetch`` on the GitHub hosts, errors as ``InstallError``
    (whose params never hold a URL or a host)."""
    try:
        return await store_fetch(
            "GET",
            url,
            allowed_hosts=CUSTOM_REPO_HOSTS,
            max_redirects=max_redirects,
            max_response_bytes=max_bytes,
        )
    except StoreOffline:
        raise InstallError("store_offline", 409) from None
    except EgressBlocked as exc:
        if exc.code == "egress_response_too_large":
            raise InstallError("repository_asset_too_large") from None
        raise InstallError("download_blocked", 409, reason=exc.code) from None
    except (httpx.HTTPError, TimeoutError, OSError):
        raise InstallError("download_failed", 502) from None


async def latest_tag(repo: Repository) -> tuple[str, str]:
    """``(tag, display)`` of the latest release, from the ``releases/latest`` redirect."""
    base = _repo_url(repo.display)
    response = await _get(f"{base}/releases/latest", max_bytes=REDIRECT_MAX_BYTES, max_redirects=0)
    if response.status_code == 404:
        raise InstallError("repository_no_release", 404)
    if response.status_code not in _REDIRECTS:
        raise InstallError("download_failed", 502, http_status=response.status_code)
    location = (response.headers.get("location") or "").strip()
    prefix, marker = f"{GITHUB}/", "/releases/tag/"
    if not location.startswith(prefix) or marker not in location:
        if location.lower().rstrip("/") in {base.lower(), f"{base.lower()}/releases"}:
            raise InstallError("repository_no_release", 404)
        raise InstallError("repository_moved", 409)
    path, _, tag = location[len(prefix) :].partition(marker)
    if path.lower() != repo.key:
        raise InstallError("repository_moved", 409)
    if not _TAG_RE.fullmatch(tag):
        raise InstallError("repository_tag_invalid")
    return tag, path


async def _asset(display: str, tag: str, name: str, max_bytes: int) -> bytes | None:
    """The bytes of one release asset; None on a 404."""
    url = f"{_repo_url(display)}/releases/download/{quote(tag, safe='')}/{name}"
    response = await _get(url, max_bytes=max_bytes, max_redirects=ASSET_MAX_REDIRECTS)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise InstallError("download_failed", 502, http_status=response.status_code)
    return response.content


def archive_url(display: str, tag: str) -> str:
    return f"{_repo_url(display)}/releases/download/{quote(tag, safe='')}/{ASSET_ARCHIVE}"


def _manifest_entry(manifest: Manifest) -> dict[str, Any]:
    """The stored form of a manifest (``installer.manifest_dict`` for a bare manifest)."""
    entry = manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    entry["permissions"] = manifest.permissions.declared()
    return entry


def _issues(exc: PluginValidationError) -> list[dict[str, str]]:
    """Code and path of each issue, never a value."""
    return [{"code": i.code, "path": i.path} for i in exc.issues][:50]


def _archive_schema(archive: bytes, path: str | None) -> dict[str, Any]:
    """The config schema of an archive ``validate_package`` accepted (its size and JSON
    are already checked), ``{}`` without one."""
    if not path:
        return {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        schema = json.loads(zf.read(path))
    return schema if isinstance(schema, dict) else {}


README_TOTAL_MAX_BYTES = 600 * 1024  # all the cached languages together


def _archive_readme(archive: bytes, manifest: Manifest) -> dict[str, str]:
    """The README of each language of the manifest, as text (``validate_package`` already
    capped each file), kept up to ``README_TOTAL_MAX_BYTES`` in all. Data only: the UI
    shows it as plain text."""
    readme: dict[str, str] = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for lang, path in sorted(manifest.readme.items()):
            text = zf.read(path).decode("utf-8", errors="replace")
            total += len(text.encode("utf-8"))
            if total > README_TOTAL_MAX_BYTES:
                break
            readme[lang] = text
    return readme


def parse_sha256(raw: bytes) -> str:
    """The first 64-hex token of a ``.sha256`` file (``sha256sum`` output or the bare
    digest), lowercase; ``repository_sha256_invalid`` otherwise."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise InstallError("repository_sha256_invalid") from None
    match = _SHA256_TOKEN_RE.search(text)
    if match is None:
        raise InstallError("repository_sha256_invalid")
    return match.group(1).lower()


async def fetch_release(repo: Repository, *, with_archive: bool) -> Release:
    """The latest release: its manifest and announced sha256, and with ``with_archive``
    the archive itself, validated and checked against both."""
    tag, display = await latest_tag(repo)
    manifest_raw = await _asset(display, tag, ASSET_MANIFEST, MANIFEST_MAX_BYTES)
    sha_raw = await _asset(display, tag, ASSET_SHA256, SHA256_MAX_BYTES)
    missing = [
        name
        for name, raw in ((ASSET_MANIFEST, manifest_raw), (ASSET_SHA256, sha_raw))
        if raw is None
    ]
    archive: bytes | None = None
    if with_archive and not missing:
        archive = await _asset(display, tag, ASSET_ARCHIVE, MAX_COMPRESSED_BYTES)
        if archive is None:
            missing.append(ASSET_ARCHIVE)
    if missing or manifest_raw is None or sha_raw is None:
        raise InstallError("repository_assets_missing", 404, missing=missing)
    try:
        manifest = load_manifest_json(manifest_raw, origin="third_party")
    except PluginValidationError as exc:
        raise InstallError("repository_manifest_invalid", issues=_issues(exc)) from None
    entry = _manifest_entry(manifest)
    sha256 = parse_sha256(sha_raw)
    size: int | None = None
    config_schema: dict[str, Any] | None = None
    readme: dict[str, str] | None = None
    if archive is not None:
        if not hmac.compare_digest(hashlib.sha256(archive).hexdigest(), sha256):
            raise InstallError("archive_mismatch", fields=["sha256"])
        try:
            package = validate_package(archive, origin="third_party")
        except PluginValidationError as exc:
            raise InstallError("repository_archive_invalid", issues=_issues(exc)) from None
        inner = _manifest_entry(package.manifest)
        mismatched = [f for f in ("id", "version", "permissions") if inner[f] != entry[f]]
        if mismatched:
            raise InstallError("archive_mismatch", fields=mismatched)
        size = len(archive)
        config_schema = _archive_schema(archive, package.manifest.config_schema)
        readme = _archive_readme(archive, package.manifest)
    return Release(tag, display, entry, sha256, size, config_schema, readme)


# --- Storage (``StoreRepositoriesRepository``) --------------------------------------


async def list_rows() -> list[dict[str, Any]]:
    return await StoreRepositoriesRepository.list()


async def get_row(key: str) -> dict[str, Any] | None:
    return await StoreRepositoriesRepository.get(key)


async def _save_release(key: str, release: Release, now: int) -> None:
    await StoreRepositoriesRepository.save_release(
        key,
        display_name=release.display,
        checked_at=now,
        tag=release.tag,
        entry=release.entry,
        archive_sha256=release.sha256,
        archive_size=release.size,
        config_schema=release.config_schema,
        readme=release.readme,
    )


async def add_repository(text: object) -> dict[str, Any]:
    """Validate, read the latest release (archive included) and store the repository.
    Nothing is stored when any step fails; nothing is fetched while the Store is off."""
    repo = parse_repository(text)
    if not await store_egress.store_egress_allowed():
        raise InstallError("store_offline", 409)
    if await get_row(repo.key) is not None:
        raise InstallError("repository_exists", 409)
    if len(await list_rows()) >= MAX_REPOSITORIES:
        raise InstallError("repository_limit", 409, limit=MAX_REPOSITORIES)
    release = await fetch_release(repo, with_archive=True)
    now = int(time.time())
    if not await StoreRepositoriesRepository.insert(repo.key, release.display, now):
        raise InstallError("repository_exists", 409)
    await _save_release(repo.key, release, now)
    row = await get_row(repo.key)
    assert row is not None
    return row


async def remove_repository(key: str) -> bool:
    """Forget a repository. Installed instances are left alone (no updates any more)."""
    return await StoreRepositoriesRepository.delete(key)


async def refresh_repositories() -> None:
    """Store refresher: read each repository's latest release again; a new tag is
    downloaded and checked like an add. A failure is recorded on the row
    (``last_error``, a code) and the last good release kept."""
    for row in await list_rows():
        repo = Repository(row["repository"], row["display_name"])
        now = int(time.time())
        try:
            tag, _display = await latest_tag(repo)
            if tag == row["tag"] and row["last_error"] is None:
                await StoreRepositoriesRepository.save_check(repo.key, now, None)
                continue
            release = await fetch_release(repo, with_archive=True)
        except InstallError as exc:
            if exc.code == "store_offline":
                raise StoreOffline() from None
            await StoreRepositoriesRepository.save_check(repo.key, now, exc.code)
            continue
        await _save_release(repo.key, release, now)


# --- Catalogue and installer -------------------------------------------------------


def catalogue_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    """The catalogue entry of a repository's cached release (tier ``custom``,
    ``signed: false``, the warning), or None before a good read."""
    manifest = row.get("manifest")
    if not manifest:
        return None
    return {
        "tier": "custom",
        "signed": False,
        "repository": row["repository"],
        "plugin_id": row["plugin_id"],
        "version": row["version"],
        "tag": row["tag"],
        "name": manifest.get("name") or {},
        "summary": manifest.get("summary") or {},
        "permissions": manifest.get("permissions") or {},
        "privacy": manifest.get("privacy"),
        "compat": manifest.get("compat"),
        "compatible": compatible(manifest),
        "archive_sha256": row["archive_sha256"],
        "archive_size": row["archive_size"],
        "config_schema": row.get("config_schema") or {},
        "warning": CUSTOM_WARNING_CODE,
        "warning_text": dict(CUSTOM_WARNING),
    }


async def _repository_allowed(key: str, plugin_id: str) -> bool:
    """A registered repository, or the source of an installed custom instance of this
    plugin (a backup restore brings instances back without the repository list)."""
    if await get_row(key) is not None:
        return True
    from app.repository.plugins import PluginRepository

    return any(
        row.get("kind") == "custom" and row.get("repository") == key
        for row in await PluginRepository.get_by_plugin_id(plugin_id)
    )


async def resolve_custom(
    plugin_id: str, version: str | None, repository: str | None
) -> RemoteArchiveSource | None:
    """Installer resolver for ``kind = custom``: the latest release of ``repository``
    when it provides ``plugin_id`` (and ``version`` when given). The installer then
    downloads the archive and checks its size, sha256 and manifest."""
    if repository is None:
        return None
    try:
        repo = parse_repository(repository)
    except InstallError:
        return None
    if not await _repository_allowed(repo.key, plugin_id):
        return None
    row = await get_row(repo.key)
    if row is not None:
        repo = Repository(repo.key, row["display_name"])
    release = await fetch_release(repo, with_archive=False)
    if release.entry["id"] != plugin_id:
        return None
    if version is not None and release.entry["version"] != version:
        return None
    size = (
        row["archive_size"] if row is not None and row["archive_sha256"] == release.sha256 else None
    )
    return RemoteArchiveSource(
        tier="custom",
        entry=release.entry,
        archive_url=archive_url(release.display, release.tag),
        expected_sha256=release.sha256,
        allowed_hosts=CUSTOM_REPO_HOSTS,
        expected_size=size,
        repository=repo.key,
    )


def register() -> None:
    """Wire the ``custom`` resolver and the refresher (app startup)."""
    register_resolver("custom", resolve_custom)
    register_store_refresher(refresh_repositories)


def unregister() -> None:
    register_resolver("custom", None)
    unregister_store_refresher(refresh_repositories)
