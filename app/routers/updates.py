"""OSS update-badge catalogue (cached Stats /v1/meshloom/latest)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.oss_updates import get_update_status

router = APIRouter(tags=["updates"])


class UpdateStatusResponse(BaseModel):
    current: str = Field(description="Local Meshloom SemVer from get_app_build_info()")
    latest: str | None = Field(description="Latest published SemVer, or null if unknown")
    update_available: bool
    html_url: str | None = Field(description="GitHub release URL from Stats, or null")


@router.get("/updates", response_model=UpdateStatusResponse)
async def get_updates() -> UpdateStatusResponse:
    return UpdateStatusResponse.model_validate(get_update_status())
