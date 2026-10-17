import logging
from urllib.parse import urlsplit

import aiosqlite

logger = logging.getLogger(__name__)

# The browser push services (Chrome, Firefox, Edge, Safari): all public.
_BROWSER_PUSH_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
    "push.apple.com",
)


def _is_browser_push_service(endpoint: str) -> bool:
    try:
        host = (urlsplit(endpoint).hostname or "").rstrip(".")
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _BROWSER_PUSH_HOSTS)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Add ``push_allow_private_endpoints`` (Settings → Notifications) and
    ``push_private_endpoints_grandfathered``.

    The egress guard (``app/net/egress_guard.py``) refuses a Web Push endpoint
    on a private network (LAN, loopback, CGNAT...) unless this setting is on: a
    self-hosted UnifiedPush distributor needs it. It starts off, except when a
    stored subscription points somewhere other than a browser push service (a
    UnifiedPush distributor, possibly on the LAN): then it starts on and
    grandfathered, like a webhook saved before the guard, and the UI shows a
    one-time notice. Cloud metadata addresses stay refused either way.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "app_settings" not in tables:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "push_private_endpoints_grandfathered" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN push_private_endpoints_grandfathered"
            " INTEGER DEFAULT 0"
        )
    if "push_allow_private_endpoints" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN push_allow_private_endpoints INTEGER DEFAULT 0"
        )
        logger.info("Added push_allow_private_endpoints setting")
        if "push_subscriptions" in tables:
            cursor = await conn.execute("SELECT endpoint FROM push_subscriptions")
            endpoints = [str(row[0] or "") for row in await cursor.fetchall()]
            if any(not _is_browser_push_service(e) for e in endpoints):
                await conn.execute(
                    "UPDATE app_settings SET push_allow_private_endpoints = 1,"
                    " push_private_endpoints_grandfathered = 1"
                )
                logger.info(
                    "Push endpoints on a private network allowed (grandfathered): "
                    "a stored subscription is not a browser push service"
                )
    await conn.commit()
