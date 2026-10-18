"""REST for Meshloom Stats community join + thin Stats HTTP proxies."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.permissions import require
from app.models import (
    CommunityAirportSearchResponse,
    CommunityHashtagPut,
    CommunityHashtagsResponse,
    CommunityIataActiveResponse,
    CommunityIataBindRequest,
    CommunityIataBindResult,
    CommunityIataSuggestion,
    CommunityLiveStatus,
    CommunityLiveSubscribeRequest,
    CommunityMeStats,
    CommunityPublicStats,
    CommunityStatus,
    CommunityUpdate,
)
from app.services.community_live import (
    active_iata_codes,
    restart_live,
    subscribe_live,
    unsubscribe_live,
)
from app.services.meshloom_community import (
    bind_community_iata,
    community_status,
    lookup_airport,
    retry_opt_out,
    stats_json,
    suggest_iata,
    update_community,
)
from app.services.observer_position import put_iata_bind

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/community", tags=["community"])


@router.get("", response_model=CommunityStatus, dependencies=[Depends(require("admin"))])
async def get_community() -> CommunityStatus:
    return await community_status()


@router.get(
    "/airports",
    response_model=CommunityAirportSearchResponse,
    dependencies=[Depends(require("admin"))],
)
async def search_airports(q: str = "") -> CommunityAirportSearchResponse:
    """Where an IATA code is (``q``, 3 letters), from the bundled list: no request."""
    return CommunityAirportSearchResponse(airports=lookup_airport(q))


@router.get(
    "/iata/suggest",
    response_model=CommunityIataSuggestion,
    dependencies=[Depends(require("admin"))],
)
async def get_iata_suggestion() -> CommunityIataSuggestion:
    """Codes the IATA form proposes. The settings ask it only on a click ("Suggest codes").

    The one route that may reach Community while it is off or waiting for a
    position (``GET /v1/iata/suggest``, the radio's position rounded to 2
    decimals); without a position nothing leaves and nothing is suggested.
    """
    return await suggest_iata()


@router.patch("", response_model=CommunityStatus, dependencies=[Depends(require("admin"))])
async def patch_community(body: CommunityUpdate) -> CommunityStatus:
    await update_community(
        enabled=body.enabled,
        iata=body.iata,
        # Another IATA: 409, an IATA change is PUT /community/me/iata (a bind).
        iata_through_bind=True,
        # Off to on: 409, a join is PUT /community/me/iata with enable (bind first).
        enable_through_bind=True,
        opt_out_reason=body.opt_out_reason,
    )
    return await community_status()


@router.post(
    "/opt-out/retry", response_model=CommunityStatus, dependencies=[Depends(require("admin"))]
)
async def post_opt_out_retry() -> CommunityStatus:
    # Manual only: sends the opt-out Community did not confirm, the one
    # Community request allowed by hand while off (409 when none is pending).
    await retry_opt_out()
    return await community_status()


@router.get("/me/stats", response_model=CommunityMeStats, dependencies=[Depends(require("admin"))])
async def get_me_stats() -> CommunityMeStats:
    payload = await stats_json("GET", "/v1/me/stats", auth=True)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Community returned an unexpected body")
    return CommunityMeStats.model_validate(payload)


@router.put(
    "/me/iata", response_model=CommunityIataBindResult, dependencies=[Depends(require("admin"))]
)
async def put_me_iata(body: CommunityIataBindRequest) -> CommunityIataBindResult:
    # Clears the old retained status first, then binds, then reconnects. With
    # ``enable`` (join), Community is turned on only after a successful bind.
    # The bind carries only the publishable position (observer_position).
    return await bind_community_iata(
        body.iata, lambda: put_iata_bind(body.iata), enable=body.enable
    )


@router.post(
    "/me/iata/override",
    response_model=CommunityIataBindResult,
    dependencies=[Depends(require("admin"))],
)
async def post_me_iata_override() -> CommunityIataBindResult:
    payload = await stats_json("POST", "/v1/me/iata/override", auth=True)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Community returned an unexpected body")
    return CommunityIataBindResult.model_validate(payload)


@router.get("/stats", response_model=CommunityPublicStats, dependencies=[Depends(require("read"))])
async def get_community_stats() -> CommunityPublicStats:
    payload = await stats_json("GET", "/v1/community/stats", auth=False)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Community returned an unexpected body")
    return CommunityPublicStats.model_validate(payload)


@router.get(
    "/hashtags", response_model=CommunityHashtagsResponse, dependencies=[Depends(require("read"))]
)
async def get_hashtags() -> CommunityHashtagsResponse:
    payload = await stats_json("GET", "/v1/hashtags", auth=False)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Community returned an unexpected body")
    return CommunityHashtagsResponse.model_validate(payload)


@router.post(
    "/live/subscribe", response_model=CommunityLiveStatus, dependencies=[Depends(require("read"))]
)
async def post_live_subscribe(
    body: CommunityLiveSubscribeRequest | None = None,
) -> CommunityLiveStatus:
    payload = body or CommunityLiveSubscribeRequest()
    return CommunityLiveStatus.model_validate(
        await subscribe_live(payload.session_id, payload.iata)
    )


@router.delete(
    "/live/subscribe/{session_id}",
    response_model=CommunityLiveStatus,
    dependencies=[Depends(require("read"))],
)
async def delete_live_subscribe(session_id: str) -> CommunityLiveStatus:
    return CommunityLiveStatus.model_validate(await unsubscribe_live(session_id))


@router.get(
    "/iata/active",
    response_model=CommunityIataActiveResponse,
    dependencies=[Depends(require("read"))],
)
async def get_active_iata() -> CommunityIataActiveResponse:
    """Regions with traffic in the last 24 h (Community ``GET /v1/iata/active``), for Live."""
    return CommunityIataActiveResponse.model_validate(await active_iata_codes())


@router.post(
    "/live/restart", response_model=CommunityLiveStatus, dependencies=[Depends(require("admin"))]
)
async def post_live_restart() -> CommunityLiveStatus:
    """Mint a new live token and reconnect; clears a 4002 stop and a given-up 401."""
    return CommunityLiveStatus.model_validate(await restart_live())


@router.put(
    "/me/hashtags",
    response_model=CommunityHashtagsResponse,
    dependencies=[Depends(require("admin"))],
)
async def put_me_hashtags(body: CommunityHashtagPut) -> CommunityHashtagsResponse:
    payload = await stats_json(
        "PUT",
        "/v1/me/hashtags",
        auth=True,
        json_body={"names": body.names},
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Community returned an unexpected body")
    return CommunityHashtagsResponse.model_validate(payload)
