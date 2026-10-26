"""The Store (``/api/store``): the activation card and the custom repositories.

The consent itself is ``store_online`` in ``PATCH /api/settings``; every Store
request goes through ``app.plugins.store_egress`` (``store_fetch``).

- ``GET /store/status``: what the activation card says. No egress.
- ``GET /store/repositories``: the custom GitHub repositories (MSHL-196) with the
  catalogue entry of each one's last good release (tier ``custom``, ``signed: false``,
  the warning) and the instances installed from it. No egress.
- ``POST /store/repositories``: add ``owner/name``. Refused (403 ``plugins_disabled``)
  while the host lock or the platform turns plugins off, and (409 ``store_offline``)
  before any request while the Store is off; otherwise the latest release is read and
  checked (``app/plugins/custom_repos.py``) and nothing is stored when it fails.
- ``DELETE /store/repositories/{owner}/{name}``: forget it (403 ``plugins_disabled``
  like the add). No egress; installed
  instances keep running, without updates.

- ``GET /store/repositories/{owner}/{name}/readme?lang=``: the README of the cached
  release in ``lang`` (``en`` fallback, then any), as plain text to show as plain text.
  No egress.
- ``POST /store/refresh``: the Refresh button. Runs the background refresh now (every
  repository's latest release is read again) unless one ran in the last
  ``MIN_REFRESH_GAP_S`` (10 min) or the Store is off (no installed instance needed, so a
  newly added repository can be browsed): ``ran`` is then false and the cached releases stand.
- ``GET /store/updates?lang=``: the installed plugins with a newer compatible release
  cached by their source (MSHL-197, ``app/plugins/updates.py``), with the changelog in
  ``lang`` when the source has one, the permission and config schema diffs and the
  hold each instance would get. No egress.

Installing from a repository is ``POST /api/plugins/install`` with ``kind: custom``
and ``repository``; updating is ``POST /api/plugins/{instance_id}/update``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.auth import audit
from app.auth.permissions import require
from app.models import StoreStatus
from app.plugins import custom_repos, store_egress, updates
from app.plugins.installer import InstallError
from app.plugins.store_egress import store_status
from app.repository.plugins import PluginRepository
from app.routers.plugins import Writable

router = APIRouter(prefix="/store", tags=["store"])
Admin = Depends(require("admin"))

_MESSAGES = {
    "repository_invalid": "Expected a GitHub repository as owner/name",
    "repository_exists": "This repository is already added",
    "repository_limit": "Too many custom repositories",
    "repository_not_found": "No such custom repository",
    "repository_no_release": "The repository has no release",
    "repository_moved": "The repository moved: add it under its new name",
    "repository_tag_invalid": "The latest release has an unusable tag",
    "repository_assets_missing": "The latest release lacks manifest.json, plugin.zip or "
    "plugin.zip.sha256",
    "repository_asset_too_large": "A release asset is too large",
    "repository_manifest_invalid": "The release's manifest.json is invalid",
    "repository_archive_invalid": "The release's plugin.zip is invalid",
    "repository_sha256_invalid": "The release's plugin.zip.sha256 holds no sha256",
    "archive_mismatch": "The archive does not match its sha256 or its manifest",
    "store_offline": "The Store is turned off",
    "download_blocked": "The request was refused",
    "download_failed": "GitHub could not be reached",
}


def _error(exc: InstallError) -> HTTPException:
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": _MESSAGES.get(exc.code, "The repository could not be read"),
    }
    if exc.params:
        detail["params"] = exc.params
    return HTTPException(status_code=exc.status, detail=detail)


class RepositoryAdd(BaseModel):
    repository: str = Field(min_length=3, max_length=200, description="``owner/name`` on GitHub.")


class CustomCatalogueEntry(BaseModel):
    tier: str = "custom"
    signed: bool = False
    repository: str
    plugin_id: str
    version: str
    tag: str
    name: dict[str, str]
    summary: dict[str, str]
    permissions: dict[str, Any]
    privacy: dict[str, Any] | None = None
    compat: dict[str, Any] | None = None
    compatible: bool = Field(description="``compat.meshloom`` admits this Meshloom.")
    archive_sha256: str | None
    archive_size: int | None
    config_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="The release's ``config.schema.json`` ({} without one): the form the "
        "install asks for before the first start.",
    )
    warning: str = Field(description="``custom_unsigned``: the UI shows its own copy of it.")
    warning_text: dict[str, str]


class RepositoryInstance(BaseModel):
    instance_id: str
    label: str
    version: str


class RepositoryView(BaseModel):
    repository: str = Field(description="Lowercase ``owner/name``, the key.")
    display_name: str = Field(description="``owner/name`` as GitHub writes it.")
    added_at: int
    checked_at: int | None
    last_error: str | None = Field(description="Error code of the last check, if it failed.")
    entry: CustomCatalogueEntry | None
    instances: list[RepositoryInstance]
    update_available: bool


class RepositoriesView(BaseModel):
    store_online: bool
    max_repositories: int
    warning: str
    warning_text: dict[str, str]
    repositories: list[RepositoryView]


class PermissionDiff(BaseModel):
    added: list[str]
    changed: list[str]
    removed: list[str]
    privacy_widened: bool


class SchemaDiff(BaseModel):
    added: list[str]
    removed: list[str]
    required_added: list[str]


class UpdateInstance(BaseModel):
    instance_id: str
    label: str
    version: str
    auto_update: bool
    hold: Literal["consent_required", "config_required"] | None = Field(
        description="What the update would hold this instance for (None: it runs on)."
    )
    permissions: list[str] = Field(description="The permissions it would ask consent for.")
    missing_fields: list[str] = Field(description="New required config fields it lacks.")
    dropped_fields: list[str] = Field(
        default_factory=list,
        description="Config fields whose value the new schema rejects: dropped and reported "
        "(attention ``config_dropped``), never silently.",
    )


class PluginUpdate(BaseModel):
    plugin_id: str
    kind: str
    repository: str | None
    from_version: str
    to_version: str
    name: dict[str, str]
    changelog: str | None = Field(description="In ``lang`` (``en`` fallback), when provided.")
    permissions: PermissionDiff
    config_schema: SchemaDiff | None = Field(
        description="None when the cached release predates the schema cache."
    )
    instances: list[UpdateInstance]


async def _views() -> list[RepositoryView]:
    rows = await custom_repos.list_rows()
    custom = [r for r in await PluginRepository.get_all() if r.get("kind") == "custom"]
    views = []
    for row in rows:
        entry = custom_repos.catalogue_entry(row)
        instances = [
            RepositoryInstance(instance_id=r["instance_id"], label=r["label"], version=r["version"])
            for r in custom
            if r.get("repository") == row["repository"]
            and (row["plugin_id"] is None or r["plugin_id"] == row["plugin_id"])
        ]
        views.append(
            RepositoryView(
                repository=row["repository"],
                display_name=row["display_name"],
                added_at=row["added_at"],
                checked_at=row["checked_at"],
                last_error=row["last_error"],
                entry=CustomCatalogueEntry(**entry) if entry else None,
                instances=instances,
                update_available=entry is not None
                and any(i.version != entry["version"] for i in instances),
            )
        )
    return views


@router.get("/status", response_model=StoreStatus, dependencies=[Admin])
async def get_store_status() -> StoreStatus:
    """The Store consent, the hosts it may reach, what leaves and what is missed when off."""
    return await store_status()


@router.get("/repositories", response_model=RepositoriesView, dependencies=[Admin])
async def list_repositories() -> RepositoriesView:
    """The custom repositories and their cached releases. Makes no request."""
    return RepositoriesView(
        store_online=await store_egress.store_egress_allowed(),
        max_repositories=custom_repos.MAX_REPOSITORIES,
        warning=custom_repos.CUSTOM_WARNING_CODE,
        warning_text=dict(custom_repos.CUSTOM_WARNING),
        repositories=await _views(),
    )


class RefreshResult(BaseModel):
    ran: bool = Field(description="False: rate-limited or the Store is off.")
    last_refresh_at: int | None


class ReadmeView(BaseModel):
    lang: str | None = Field(description="Language of ``text`` (None: the release has none).")
    text: str = Field(description="Untrusted text. Render it as plain text, never as markup.")
    languages: list[str]


@router.get("/repositories/{owner}/{name}/readme", response_model=ReadmeView, dependencies=[Admin])
async def get_repository_readme(
    owner: str,
    name: str,
    lang: str = Query(default="en", min_length=2, max_length=10, pattern=r"^[a-zA-Z_-]+$"),
) -> ReadmeView:
    """The cached release's README in ``lang``, ``en`` or the first language otherwise.
    Makes no request; empty for a release cached before migration 128."""
    try:
        repo = custom_repos.parse_repository(f"{owner}/{name}")
    except InstallError as exc:
        raise _error(exc) from None
    row = await custom_repos.get_row(repo.key)
    if row is None:
        raise _error(InstallError("repository_not_found", 404))
    readme: dict[str, str] = row.get("readme") or {}
    languages = sorted(readme)
    short = lang.replace("_", "-").split("-")[0].lower()
    pick = next((k for k in (lang, short, "en") if k in readme), languages[0] if readme else None)
    return ReadmeView(lang=pick, text=readme[pick] if pick else "", languages=languages)


@router.post("/refresh", response_model=RefreshResult, dependencies=[Admin])
async def refresh_store() -> RefreshResult:
    """Read the custom repositories' latest releases again now (rate-limited like the
    background refresh: at most one run per ``MIN_REFRESH_GAP_S``, calls serialised)."""
    ran = await store_egress.run_store_refresh(manual=True)
    return RefreshResult(ran=ran, last_refresh_at=(await store_status()).last_refresh_at)


@router.get("/updates", response_model=list[PluginUpdate], dependencies=[Admin])
async def list_updates(
    lang: str = Query(default="en", min_length=2, max_length=10, pattern=r"^[a-zA-Z_-]+$"),
) -> list[PluginUpdate]:
    """The installed plugins a newer compatible release of their source would update.
    Makes no request: it compares with the releases the Store refresh cached."""
    return [PluginUpdate(**entry) for entry in await updates.available_updates(lang)]


@router.post(
    "/repositories", status_code=201, response_model=RepositoryView, dependencies=[Admin, Writable]
)
async def add_repository(
    body: RepositoryAdd, actor: audit.RequestActor = audit.SYSTEM_ACTOR
) -> RepositoryView:
    """Add a GitHub repository: its latest release must carry ``manifest.json``,
    ``plugin.zip`` and ``plugin.zip.sha256``, all valid. 409 ``store_offline`` while
    the Store is off (no request made)."""
    async with audit.audited(actor, "store.repository_added") as scope:
        try:
            scope.target = f"repository:{custom_repos.parse_repository(body.repository).key}"
        except InstallError:
            pass
        try:
            row = await custom_repos.add_repository(body.repository)
        except InstallError as exc:
            scope.details["code"] = exc.code
            raise _error(exc) from None
        scope.target = f"repository:{row['repository']}"
        scope.details["plugin_id"] = row["plugin_id"]
    return next(v for v in await _views() if v.repository == row["repository"])


@router.delete("/repositories/{owner}/{name}", status_code=204, dependencies=[Admin, Writable])
async def remove_repository(
    owner: str, name: str, actor: audit.RequestActor = audit.SYSTEM_ACTOR
) -> Response:
    """Forget a repository. Installed instances keep running, without updates."""
    try:
        repo = custom_repos.parse_repository(f"{owner}/{name}")
    except InstallError as exc:
        raise _error(exc) from None
    async with audit.audited(actor, "store.repository_removed", target=f"repository:{repo.key}"):
        if not await custom_repos.remove_repository(repo.key):
            raise _error(InstallError("repository_not_found", 404))
    return Response(status_code=204)
