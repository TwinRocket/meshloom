"""REST for Meshloom Stats community join + thin Stats HTTP proxies."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models import (
    CommunityAirportSearchResponse,
    CommunityHashtagPut,
    CommunityHashtagsResponse,
    CommunityIataBindRequest,
    CommunityIataBindResult,
    CommunityLiveStatus,
    CommunityLiveSubscribeRequest,
    CommunityMeStats,
    CommunityPublicStats,
    CommunityStatus,
    CommunityUpdate,
)
from app.services.community_live import relancer_live, subscribe_live, unsubscribe_live
from app.services.meshloom_community import (
    community_status,
    radio_gps_or_none,
    search_community_airports,
    stats_json,
    update_community,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/community", tags=["community"])


@router.get("", response_model=CommunityStatus)
async def get_community() -> CommunityStatus:
    return await community_status()


@router.get("/airports", response_model=CommunityAirportSearchResponse)
async def search_airports(q: str = "", locale: str = "en") -> CommunityAirportSearchResponse:
    return CommunityAirportSearchResponse(
        airports=await search_community_airports(q, locale=locale)
    )


@router.patch("", response_model=CommunityStatus)
async def patch_community(body: CommunityUpdate) -> CommunityStatus:
    await update_community(
        enabled=body.enabled,
        iata=body.iata,
        broker_host=body.broker_host,
        api_base=body.api_base,
    )
    return await community_status()


@router.get("/me/stats", response_model=CommunityMeStats)
async def get_me_stats() -> CommunityMeStats:
    payload = await stats_json("GET", "/v1/me/stats", auth=True)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Stats returned an unexpected body")
    return CommunityMeStats.model_validate(payload)


@router.put("/me/iata", response_model=CommunityIataBindResult)
async def put_me_iata(body: CommunityIataBindRequest) -> CommunityIataBindResult:
    lat = body.lat
    lon = body.lon
    if lat is None and lon is None:
        lat, lon = radio_gps_or_none()
    json_body: dict[str, Any] = {"iata": body.iata.upper()}
    if lat is not None and lon is not None:
        json_body["lat"] = lat
        json_body["lon"] = lon
    payload = await stats_json("PUT", "/v1/me/iata", auth=True, json_body=json_body, iata=body.iata)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Stats returned an unexpected body")
    result = CommunityIataBindResult.model_validate(payload)
    await update_community(iata=result.iata)
    return result


@router.post("/me/iata/override", response_model=CommunityIataBindResult)
async def post_me_iata_override() -> CommunityIataBindResult:
    payload = await stats_json("POST", "/v1/me/iata/override", auth=True)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Stats returned an unexpected body")
    return CommunityIataBindResult.model_validate(payload)


@router.get("/stats", response_model=CommunityPublicStats)
async def get_community_stats() -> CommunityPublicStats:
    payload = await stats_json("GET", "/v1/community/stats", auth=False)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Stats returned an unexpected body")
    return CommunityPublicStats.model_validate(payload)


@router.get("/iata/{code}/hashtags", response_model=CommunityHashtagsResponse)
async def get_iata_hashtags(code: str) -> CommunityHashtagsResponse:
    payload = await stats_json("GET", f"/v1/iata/{code.upper()}/hashtags", auth=False)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Stats returned an unexpected body")
    return CommunityHashtagsResponse.model_validate(payload)


@router.post("/live/subscribe", response_model=CommunityLiveStatus)
async def post_live_subscribe(
    body: CommunityLiveSubscribeRequest | None = None,
) -> CommunityLiveStatus:
    payload = body or CommunityLiveSubscribeRequest()
    return CommunityLiveStatus.model_validate(await subscribe_live(payload.session_id))


@router.delete("/live/subscribe/{session_id}", response_model=CommunityLiveStatus)
async def delete_live_subscribe(session_id: str) -> CommunityLiveStatus:
    return CommunityLiveStatus.model_validate(await unsubscribe_live(session_id))


@router.post("/live/relancer", response_model=CommunityLiveStatus)
async def post_live_relancer() -> CommunityLiveStatus:
    return CommunityLiveStatus.model_validate(await relancer_live())


@router.put("/me/hashtags", response_model=CommunityHashtagsResponse)
async def put_me_hashtags(body: CommunityHashtagPut) -> CommunityHashtagsResponse:
    payload = await stats_json(
        "PUT",
        "/v1/me/hashtags",
        auth=True,
        json_body={"names": body.names},
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Stats returned an unexpected body")
    return CommunityHashtagsResponse.model_validate(payload)
