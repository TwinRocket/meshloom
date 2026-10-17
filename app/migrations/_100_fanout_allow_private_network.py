import json
import logging

import aiosqlite

logger = logging.getLogger(__name__)

# The fanout types whose destination is a user-chosen URL (egress guard).
_GUARDED_TYPES = ("webhook", "apprise", "sqs", "map_upload")


async def migrate(conn: aiosqlite.Connection) -> None:
    """Grandfather the private-network opt-in of existing fanout destinations.

    The egress guard (``app/net/egress_guard.py``) now refuses a fanout
    webhook, Apprise URL, SQS endpoint or map upload API on a private network
    (LAN, loopback, CGNAT...) unless the row sets ``allow_private_network``.
    Every existing row of those types gets ``allow_private_network: true`` and
    ``private_network_grandfathered: true`` (one-time banner in the UI), so
    nothing that worked stops working; new rows start with false. A row that
    already has the key is left as it is. Cloud metadata addresses stay refused.
    """
    tables_cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='fanout_configs'"
    )
    if await tables_cursor.fetchone() is None:
        await conn.commit()
        return

    placeholders = ",".join("?" for _ in _GUARDED_TYPES)
    cursor = await conn.execute(
        f"SELECT id, config FROM fanout_configs WHERE type IN ({placeholders})",
        _GUARDED_TYPES,
    )
    updated = 0
    for row_id, raw in await cursor.fetchall():
        try:
            config = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(config, dict) or "allow_private_network" in config:
            continue
        config["allow_private_network"] = True
        config["private_network_grandfathered"] = True
        await conn.execute(
            "UPDATE fanout_configs SET config = ? WHERE id = ?", (json.dumps(config), row_id)
        )
        updated += 1
    if updated:
        logger.info("Fanout destinations on a private network allowed (grandfathered): %d", updated)
    await conn.commit()
