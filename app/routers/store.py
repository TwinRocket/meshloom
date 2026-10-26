"""The Store (``/api/store``): the activation card and the custom repositories.

The consent itself is ``store_online`` in ``PATCH /api/settings``; every Store
request goes through ``app.plugins.store_egress`` (``store_fetch``).

- ``GET /store/status``: what the activation card says. No egress.
- ``GET /store/repositories``: the custom GitHub repositories (MSHL-196) with the
  catalogue entry of each one's last good release (tier ``custom``, ``signed: false``,
  the warning) and the instances installed from it. No egress.
- ``POST /store/repositories``: add ``owner/name``. Refused (409 ``store_offline``)
  before any request while the Store is off; otherwise the latest release is read and
  checked (``app/plugins/custom_repos.py``) and nothing is stored when it fails.
- ``DELETE /store/repositories/{owner}/{name}``: forget it. No egress; installed
  instances keep running, without updates.

Installing from a repository is ``POST /api/plugins/install`` with ``kind: custom``
and ``repository``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.auth import audit
from app.auth.permissions import require
from app.models import StoreStatus
from app.plugins import custom_repos, store_egress
from app.plugins.installer import InstallError
from app.plugins.store_egress import store_status
from app.repository.plugins import PluginRepository

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
    archive_sha256: str | None
    archive_size: int | None
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


@router.post("/repositories", status_code=201, response_model=RepositoryView, dependencies=[Admin])
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


@router.delete("/repositories/{owner}/{name}", status_code=204, dependencies=[Admin])
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
