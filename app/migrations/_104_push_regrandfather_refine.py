import ipaddress
import logging
from urllib.parse import urlsplit

import aiosqlite

logger = logging.getLogger(__name__)

# Frozen copy of the rule in app/services/backup.py (``endpoint_looks_private``):
# a migration never imports app code that may change later. The networks are
# listed here, not read from ``ipaddress.is_global``, which differs between
# Python versions.
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)
_PRIVATE_NAME_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")
# The browser push services (same list as migration 097): public.
_BROWSER_PUSH_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
    "push.apple.com",
)


def _host(endpoint: str) -> str:
    try:
        return (urlsplit(endpoint).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _looks_private(endpoint: str) -> bool:
    host = _host(endpoint)
    if not host:
        return False
    ip = _parse_ip(host)
    if ip is not None:
        return any(ip in net for net in _PRIVATE_NETWORKS if net.version == ip.version)
    return "." not in host or host.endswith(_PRIVATE_NAME_SUFFIXES)


def _is_custom_hostname(endpoint: str) -> bool:
    """A name that is not a browser push service: it may resolve to a private
    address (split DNS, Tailscale ``*.ts.net``) with nothing to show it."""
    host = _host(endpoint)
    if not host or _parse_ip(host) is not None:
        return False
    return not any(host == h or host.endswith("." + h) for h in _BROWSER_PUSH_HOSTS)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Undo the over-wide grandfathering of migration 097, never silently.

    097 turned ``push_allow_private_endpoints`` on for any subscription that was
    not a browser push service, including a public UnifiedPush distributor such
    as ntfy.sh. While the one-time notice is still pending
    (``push_private_endpoints_grandfathered`` = 1, so the user never touched the
    box), switch both flags back off unless a stored endpoint visibly targets a
    private network (non-public IP literal, dotless or ``.local``/``.lan`` name;
    no DNS). A user's own choice (flag already cleared) is never changed.

    A hostname the migration cannot place (split DNS, Tailscale ``*.ts.net``)
    may still be private: withdrawing the opt-in then sets
    ``push_private_endpoints_review``, a persistent notice with a one-click
    re-enable. Sends the egress guard refuses never delete a subscription.
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await tables_cursor.fetchall()}
    if "app_settings" not in tables:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "push_private_endpoints_review" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN push_private_endpoints_review INTEGER DEFAULT 0"
        )
    if not {"push_allow_private_endpoints", "push_private_endpoints_grandfathered"} <= columns:
        await conn.commit()
        return
    endpoints: list[str] = []
    if "push_subscriptions" in tables:
        cursor = await conn.execute("SELECT endpoint FROM push_subscriptions")
        endpoints = [str(row[0] or "") for row in await cursor.fetchall()]
    if not any(_looks_private(e) for e in endpoints):
        review = 1 if any(_is_custom_hostname(e) for e in endpoints) else 0
        cursor = await conn.execute(
            "UPDATE app_settings SET push_allow_private_endpoints = 0,"
            " push_private_endpoints_grandfathered = 0,"
            " push_private_endpoints_review = ?"
            " WHERE push_private_endpoints_grandfathered = 1",
            (review,),
        )
        if cursor.rowcount:
            logger.info(
                "Push private-endpoint grandfathering withdrawn: no private endpoint (review=%d)",
                review,
            )
    await conn.commit()
