"""Migration 115: the ``message.mention`` rule, copied from ``message.dm``.

Mentions notified nobody until the mention producer (MSHL-121). The default rule routes them to
the channels of ``message.dm``, the way a direct message is. Installs whose rules were imported
by migration 113 have no ``message.mention`` entry; this adds one. Idempotent: an existing entry
(even an empty one the user chose) is left alone, and nothing happens without stored rules.
"""

import json
import logging
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA table_info(app_settings)")
    if "notification_rules" not in {row[1] for row in await cursor.fetchall()}:
        return
    cursor = await conn.execute("SELECT notification_rules FROM app_settings WHERE id = 1")
    row = await cursor.fetchone()
    if row is None or not row[0]:
        return
    try:
        rules: Any = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return
    events = rules.get("events") if isinstance(rules, dict) else None
    if not isinstance(events, dict) or "message.mention" in events:
        return
    dm = events.get("message.dm")
    channels = dm.get("channels") if isinstance(dm, dict) else None
    events["message.mention"] = {
        "channels": [str(c) for c in channels] if isinstance(channels, list) else [],
        "always_deliver": False,
    }
    await conn.execute(
        "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
        (json.dumps(rules, sort_keys=True),),
    )
    logger.info("message.mention rule added from message.dm")
