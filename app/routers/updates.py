"""OSS update catalogue plus Meshloom-only apply."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.repository import AppSettingsRepository
from app.services.install_kind import detect_install_kind
from app.services.oss_updates import get_update_status
from app.services.update_apply import UpdateApplyBusy, public_job, start_apply

router = APIRouter(tags=["updates"])


class UpdateJobResponse(BaseModel):
    state: Literal["idle", "applying", "succeeded", "failed"] = "idle"
    phase: Literal["preparing", "downloading", "installing", "restarting", "done"] | None = None
    percent: int | None = None
    error: str | None = None
    started_at: int | None = None


class UpdateStatusResponse(BaseModel):
    current: str = Field(description="Local Meshloom SemVer from get_app_build_info()")
    latest: str | None = Field(description="Latest published SemVer, or null if unknown")
    update_available: bool
    html_url: str | None = Field(description="GitHub release URL from Stats, or null")
    install_kind: Literal["package", "compose", "addon", "container", "source"]
    apply_supported: bool
    auto_update: bool
    job: UpdateJobResponse


class UpdateSettingsPatch(BaseModel):
    auto_update: bool


async def build_update_status() -> UpdateStatusResponse:
    catalogue = get_update_status()
    kind, supported = detect_install_kind()
    settings = await AppSettingsRepository.get()
    return UpdateStatusResponse(
        current=catalogue["current"],
        latest=catalogue["latest"],
        update_available=catalogue["update_available"],
        html_url=catalogue["html_url"],
        install_kind=kind,
        apply_supported=supported,
        auto_update=settings.auto_update,
        job=UpdateJobResponse.model_validate(public_job()),
    )


@router.get("/updates", response_model=UpdateStatusResponse)
async def get_updates() -> UpdateStatusResponse:
    return await build_update_status()


@router.post("/updates/apply", response_model=UpdateStatusResponse, status_code=202)
async def apply_updates() -> UpdateStatusResponse:
    status = await build_update_status()
    if not status.apply_supported:
        raise HTTPException(status_code=409, detail="apply_not_supported")
    if status.job.state == "applying":
        raise HTTPException(status_code=409, detail="apply_in_progress")
    try:
        await start_apply(status.install_kind, target=status.latest)
    except UpdateApplyBusy as exc:
        raise HTTPException(status_code=409, detail="apply_in_progress") from exc
    return await build_update_status()


@router.patch("/updates/settings", response_model=UpdateStatusResponse)
async def patch_update_settings(body: UpdateSettingsPatch) -> UpdateStatusResponse:
    await AppSettingsRepository.update(auto_update=body.auto_update)
    return await build_update_status()
