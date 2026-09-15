import logging

from fastapi import APIRouter

from app.models import (
    DirectoryCacheResetResponse,
    DirectoryMapNodesResponse,
    DirectoryNeighborsResponse,
    DirectoryNodeSearchResponse,
    DirectoryReachResponse,
    DirectoryResolveHopsRequest,
    DirectoryResolveHopsResponse,
    PacketObserverReachCountsRequest,
    PacketObserverReachCountsResponse,
    PacketObserverReachResponse,
)
from app.services.directory import (
    get_directory_node_neighbors,
    get_directory_node_reach,
    list_directory_map_nodes,
    reset_directory_cache,
    resolve_directory_hops,
    search_directory_nodes,
)
from app.services.observer_reach import (
    get_packet_observer_reach,
    get_packet_observer_reach_counts,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/directory", tags=["directory"])


@router.post("/resolve-hops", response_model=DirectoryResolveHopsResponse)
async def post_resolve_hops(request: DirectoryResolveHopsRequest) -> DirectoryResolveHopsResponse:
    """Community hop resolution. Rejects 1-byte prefixes. No-op when Community is off."""
    return await resolve_directory_hops(request.hops)


@router.get("/nodes", response_model=DirectoryMapNodesResponse)
async def get_directory_map_nodes(include_local: bool = False) -> DirectoryMapNodesResponse:
    """Directory GPS pins for the #map overlay. Local contacts stay off by default."""
    return await list_directory_map_nodes(include_local=include_local)


@router.get("/nodes/live", response_model=DirectoryMapNodesResponse)
async def get_live_directory_map_nodes() -> DirectoryMapNodesResponse:
    """Live-map pins: directory plus local GPS, minus observers.

    #live draws packets and hops. Observer catalog entries are not hops and
    never appear there, so they are dropped server-side rather than filtered
    by every client.
    """
    return await list_directory_map_nodes(include_local=True, include_observers=False)


@router.get("/nodes/search", response_model=DirectoryNodeSearchResponse)
async def get_directory_node_search(q: str) -> DirectoryNodeSearchResponse:
    """Community name/key search. Not for hop prefixes — use resolve-hops."""
    return await search_directory_nodes(q)


@router.get("/packets/{packet_hash}/reach", response_model=PacketObserverReachResponse)
async def get_packet_reach(packet_hash: str) -> PacketObserverReachResponse:
    """Nodes that heard this firmware packet hash, from stored Community events."""
    return await get_packet_observer_reach(packet_hash)


@router.post("/packets/reach-counts", response_model=PacketObserverReachCountsResponse)
async def post_packet_reach_counts(
    request: PacketObserverReachCountsRequest,
) -> PacketObserverReachCountsResponse:
    """Batch observer counts for visible flood messages. Max 20 hashes."""
    return await get_packet_observer_reach_counts(request.hashes)


@router.get("/nodes/{pubkey}/reach", response_model=DirectoryReachResponse)
async def get_node_reach(pubkey: str) -> DirectoryReachResponse:
    """Community 0-hop observers. HTTP 500 is a failure, not empty data."""
    return await get_directory_node_reach(pubkey)


@router.get("/nodes/{pubkey}/neighbors", response_model=DirectoryNeighborsResponse)
async def get_node_neighbors(pubkey: str) -> DirectoryNeighborsResponse:
    """Community neighbor affinity for optional repeater-disk calibration."""
    return await get_directory_node_neighbors(pubkey)


@router.post("/cache/reset", response_model=DirectoryCacheResetResponse)
async def post_reset_directory_cache() -> DirectoryCacheResetResponse:
    """Wipe the directory hop cache only. RF contacts are untouched."""
    deleted = await reset_directory_cache()
    logger.info("Wiped directory hop cache (%d rows)", deleted)
    return DirectoryCacheResetResponse(deleted=deleted)
