import json
import logging
import time

from app.database import db

logger = logging.getLogger(__name__)

# Panes whose last answer is worth keeping. Console output is excluded on purpose:
# it is a transcript, not state.
CACHEABLE_PANES = frozenset(
    {
        "status",
        "node_info",
        "neighbors",
        "acl",
        "radio_settings",
        "advert_intervals",
        "owner_info",
        "lpp_telemetry",
        "regions",
    }
)

# Beyond this the values describe a mesh that has moved on; serving them would be
# presenting history as the current state.
_MAX_AGE_SECONDS = 7 * 86400


class RepeaterPaneCacheRepository:
    """Last known answer per repeater pane.

    A cache, not a history: one row per (public_key, pane), overwritten on each
    successful fetch. Time series live in `repeater_telemetry_history`.
    """

    @staticmethod
    async def put(public_key: str, pane: str, data: dict | list) -> None:
        if pane not in CACHEABLE_PANES:
            return
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO repeater_pane_cache (public_key, pane, data, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(public_key, pane) DO UPDATE SET
                    data = excluded.data,
                    fetched_at = excluded.fetched_at
                """,
                (public_key, pane, json.dumps(data), int(time.time())),
            ):
                pass

    @staticmethod
    async def get_all(public_key: str) -> dict[str, dict]:
        """Every cached pane for one repeater, keyed by pane name.

        Each entry carries its own `fetched_at` so callers can show how old the
        values are instead of passing them off as current.
        """
        cutoff = int(time.time()) - _MAX_AGE_SECONDS
        out: dict[str, dict] = {}
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT pane, data, fetched_at
                FROM repeater_pane_cache
                WHERE public_key = ? AND fetched_at >= ?
                """,
                (public_key, cutoff),
            ) as cursor:
                async for row in cursor:
                    try:
                        payload = json.loads(row["data"])
                    except (TypeError, ValueError):
                        logger.debug("Dropping unreadable cache row for pane %s", row["pane"])
                        continue
                    out[row["pane"]] = {"data": payload, "fetched_at": row["fetched_at"]}
        return out

    @staticmethod
    async def clear(public_key: str) -> None:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM repeater_pane_cache WHERE public_key = ?",
                (public_key,),
            ):
                pass
