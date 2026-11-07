"""RF locate: 0-hop coverage zone for one uniquely resolved node."""

from fastapi import APIRouter, Depends, Query

from app.auth.permissions import require
from app.models import LocateResponse
from app.services.rf_locate import DEFAULT_RADIUS_KM, MAX_RADIUS_KM, MIN_RADIUS_KM, locate_query

router = APIRouter(prefix="/locate", tags=["locate"])


@router.get("", response_model=LocateResponse, dependencies=[Depends(require("read"))])
async def get_locate(
    q: str = Query(..., min_length=1, max_length=200),
    radius_km: float | None = Query(default=None, ge=MIN_RADIUS_KM, le=MAX_RADIUS_KM),
) -> LocateResponse:
    """Resolve identity then return conservative 0-hop RF disks. Never writes inferred GPS."""
    return await locate_query(q, radius_km if radius_km is not None else DEFAULT_RADIUS_KM)
