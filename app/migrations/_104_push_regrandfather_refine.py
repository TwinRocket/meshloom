import ipaddress
import logging
from urllib.parse import urlsplit

import aiosqlite

logger = logging.getLogger(__name__)

# Frozen copy of the rule in app/services/backup.py (``endpoint_looks_private``):
# a migration never imports app code that may change later.
_PRIVATE_NAME_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")


def _looks_private(endpoint: str) -> bool:
    try:
        host = (urlsplit(endpoint).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if not host:
        return False
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    return "." not in host or host.endswith(_PRIVATE_NAME_SUFFIXES)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Undo the over-wide grandfathering of migration 097.

    097 turned ``push_allow_private_endpoints`` on for any subscription that was
    not a browser push service, including a public UnifiedPush distributor such
    as ntfy.sh. While the one-time notice is still pending
    (``push_private_endpoints_grandfathered`` = 1, so the user never touched the
    box), switch both flags back off unless a stored endpoint visibly targets a
    private network (IP literal that is not global, dotless or ``.local``/``.lan``
    name; no DNS). A user's own choice (flag already cleared) is never changed.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "app_settings" not in tables:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if not {"push_allow_private_endpoints", "push_private_endpoints_grandfathered"} <= columns:
        await conn.commit()
        return
    endpoints: list[str] = []
    if "push_subscriptions" in tables:
        cursor = await conn.execute("SELECT endpoint FROM push_subscriptions")
        endpoints = [str(row[0] or "") for row in await cursor.fetchall()]
    if not any(_looks_private(e) for e in endpoints):
        cursor = await conn.execute(
            "UPDATE app_settings SET push_allow_private_endpoints = 0,"
            " push_private_endpoints_grandfathered = 0"
            " WHERE push_private_endpoints_grandfathered = 1"
        )
        if cursor.rowcount:
            logger.info("Push private-endpoint grandfathering withdrawn: no private endpoint")
    await conn.commit()
