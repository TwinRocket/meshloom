"""OSS update catalogue plus Meshloom-only apply."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.repository import AppSettingsRepository
from app.services.install_kind import detect_install_kind
from app.services.oss_updates import get_update_status, refresh_oss_update_cache
from app.services.update_apply import (
    UpdateApplyBusy,
    expire_stale_applying_job,
    public_job_for_client,
    start_apply,
)
from app.services.update_window import (
    format_hhmm,
    host_tz_name,
    in_window,
    next_window_start,
    normalize_weekdays,
    parse_hhmm,
)

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
    auto_update_window_start: str
    auto_update_window_end: str
    auto_update_weekdays: list[int]
    checked_at: int | None = Field(description="Unix time of the last successful catalogue fetch")
    tz_name: str
    next_auto_apply_at: int | None = Field(
        description="Unix time when the next auto-apply window opens, or now if already open"
    )
    job: UpdateJobResponse


class _UpdateSettingsFields(TypedDict, total=False):
    auto_update: bool
    auto_update_window_start: str
    auto_update_window_end: str
    auto_update_weekdays: list[int]


class UpdateSettingsPatch(BaseModel):
    auto_update: bool | None = None
    auto_update_window_start: str | None = None
    auto_update_window_end: str | None = None
    auto_update_weekdays: list[int] | None = None

    @field_validator("auto_update_window_start", "auto_update_window_end")
    @classmethod
    def _normalize_hhmm(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return format_hhmm(*parse_hhmm(value))

    @field_validator("auto_update_weekdays")
    @classmethod
    def _normalize_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        return normalize_weekdays(value)


def _next_auto_apply_at(
    *,
    start: str,
    end: str,
    weekdays: list[int],
    now: datetime,
) -> int | None:
    if in_window(now, start, end, weekdays):
        return int(now.timestamp())
    nxt = next_window_start(now, start, end, weekdays)
    if nxt is None:
        return None
    return int(nxt.timestamp())


async def build_update_status() -> UpdateStatusResponse:
    expire_stale_applying_job()
    catalogue = get_update_status()
    kind, supported = detect_install_kind()
    settings = await AppSettingsRepository.get()
    now = datetime.now().astimezone()
    pending_auto = bool(catalogue["update_available"] and settings.auto_update and supported)
    return UpdateStatusResponse(
        current=catalogue["current"],
        latest=catalogue["latest"],
        update_available=catalogue["update_available"],
        html_url=catalogue["html_url"],
        install_kind=kind,
        apply_supported=supported,
        auto_update=settings.auto_update,
        auto_update_window_start=settings.auto_update_window_start,
        auto_update_window_end=settings.auto_update_window_end,
        auto_update_weekdays=settings.auto_update_weekdays,
        checked_at=catalogue.get("checked_at"),
        tz_name=host_tz_name(),
        next_auto_apply_at=(
            _next_auto_apply_at(
                start=settings.auto_update_window_start,
                end=settings.auto_update_window_end,
                weekdays=settings.auto_update_weekdays,
                now=now,
            )
            if pending_auto
            else None
        ),
        job=UpdateJobResponse.model_validate(
            public_job_for_client(
                current=catalogue["current"],
                latest=catalogue["latest"] if isinstance(catalogue.get("latest"), str) else None,
            )
        ),
    )


@router.get("/updates", response_model=UpdateStatusResponse)
async def get_updates() -> UpdateStatusResponse:
    return await build_update_status()


@router.post("/updates/refresh", response_model=UpdateStatusResponse)
async def refresh_updates() -> UpdateStatusResponse:
    await refresh_oss_update_cache()
    return await build_update_status()


@router.post("/updates/apply", response_model=UpdateStatusResponse, status_code=202)
async def apply_updates() -> UpdateStatusResponse:
    status = await build_update_status()
    if not status.apply_supported:
        raise HTTPException(status_code=409, detail="apply_not_supported")
    if not status.update_available:
        raise HTTPException(status_code=409, detail="update_not_available")
    if status.job.state == "applying":
        raise HTTPException(status_code=409, detail="apply_in_progress")
    try:
        await start_apply(status.install_kind, target=status.latest)
    except UpdateApplyBusy as exc:
        raise HTTPException(status_code=409, detail="apply_in_progress") from exc
    return await build_update_status()


@router.patch("/updates/settings", response_model=UpdateStatusResponse)
async def patch_update_settings(body: UpdateSettingsPatch) -> UpdateStatusResponse:
    kwargs: _UpdateSettingsFields = {}
    if body.auto_update is not None:
        kwargs["auto_update"] = body.auto_update
    if body.auto_update_window_start is not None:
        kwargs["auto_update_window_start"] = body.auto_update_window_start
    if body.auto_update_window_end is not None:
        kwargs["auto_update_window_end"] = body.auto_update_window_end
    if body.auto_update_weekdays is not None:
        kwargs["auto_update_weekdays"] = body.auto_update_weekdays
    if kwargs:
        await AppSettingsRepository.update(**kwargs)
    return await build_update_status()
