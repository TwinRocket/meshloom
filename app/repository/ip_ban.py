"""``ip_bans`` and ``ip_ban_whitelist`` (migrations 135, 137): the built-in ban's state.

Rules in ``app/auth/ip_ban.py``. The service and ``meshloom-admin`` share these
tables, so a ban or whitelist change made by the CLI while the service runs is
seen by the service on its next check of that address.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from pydantic import BaseModel

from app.database import db

# Bounds: a distributed attack cannot grow the tables without limit. Past
# MAX_BANS, the ban that ends first makes room; past MAX_WHITELIST, an add is refused.
MAX_BANS = 10000
# An ended ban is kept this long, so a repeat offender's next ban is longer
# (``IpBan.strikes``, MSHL-434). ``unban`` forgets it at once.
STRIKE_MEMORY_S = 7 * 86400
MAX_WHITELIST = 256
NOTE_MAX_LENGTH = 200

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class IpBan(BaseModel):
    address: str
    banned_at: int
    expires_at: int
    failures: int
    route: str
    # 1 for a first ban, one more for each ban that follows one that ended
    # less than ``STRIKE_MEMORY_S`` before (MSHL-434).
    strikes: int = 1


class WhitelistEntry(BaseModel):
    network: str
    note: str
    created_at: int


class WhitelistFullError(Exception):
    """``MAX_WHITELIST`` entries already."""


def canonical_address(raw: str | None) -> IPAddress | None:
    """The address, IPv4-mapped IPv6 as IPv4, or None when ``raw`` is not one."""
    if not raw or "%" in raw:
        # An IPv6 zone id is free text after the ``%``, and not a client address.
        return None
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def ban_key(address: IPAddress) -> str:
    """The key a ban is stored under: IPv4 by address, IPv6 by its /64 (as the
    login limiter, ``app/auth/ratelimit.py``: one host gets a whole /64)."""
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def parse_ban_key(raw: str) -> str | None:
    """The key ``raw`` names: an address (its key), or a key as listed."""
    address = canonical_address(raw)
    if address is not None:
        return ban_key(address)
    try:
        network = ipaddress.ip_network(raw.strip(), strict=False)
    except ValueError:
        return None
    if network.version == 6 and network.prefixlen == 64:
        return str(network)
    if network.version == 4 and network.prefixlen == 32:
        return str(network.network_address)
    return None


def parse_network(raw: str) -> IPNetwork | None:
    """A whitelist entry: an address (a /32 or /128) or a network, host bits cleared."""
    address = canonical_address(raw)
    if address is not None:
        return ipaddress.ip_network(address)
    try:
        return ipaddress.ip_network(raw.strip(), strict=False)
    except ValueError:
        return None


def _ban(row: Any) -> IpBan:
    return IpBan(
        address=row["address"],
        banned_at=row["banned_at"],
        expires_at=row["expires_at"],
        failures=row["failures"],
        route=row["route"],
        strikes=row["strikes"],
    )


class IpBanRepository:
    @staticmethod
    async def active(now: float) -> list[IpBan]:
        """The bans still running, the one that ends last first."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM ip_bans WHERE expires_at > ? ORDER BY expires_at DESC, address",
                (int(now),),
            ) as cursor:
                return [_ban(row) for row in await cursor.fetchall()]

    @staticmethod
    async def get(address: str) -> IpBan | None:
        """The row of ``address``: a running ban, or one that ended less than
        ``STRIKE_MEMORY_S`` ago (check ``expires_at``)."""
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM ip_bans WHERE address = ?", (address,)) as c:
                row = await c.fetchone()
        return _ban(row) if row is not None else None

    @staticmethod
    async def ban(ban: IpBan) -> None:
        """Write ``ban`` (replacing one on the same key), dropping the bans that
        ended more than ``STRIKE_MEMORY_S`` ago and, past ``MAX_BANS``, the ones
        that end first."""
        async with db.tx() as conn:
            for sql, params in (
                (
                    "DELETE FROM ip_bans WHERE expires_at <= ?",
                    (ban.banned_at - STRIKE_MEMORY_S,),
                ),
                (
                    "INSERT OR REPLACE INTO ip_bans"
                    " (address, banned_at, expires_at, failures, route, strikes)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ban.address,
                        ban.banned_at,
                        ban.expires_at,
                        ban.failures,
                        ban.route,
                        ban.strikes,
                    ),
                ),
                (
                    "DELETE FROM ip_bans WHERE address NOT IN"
                    " (SELECT address FROM ip_bans ORDER BY expires_at DESC LIMIT ?)",
                    (MAX_BANS,),
                ),
            ):
                async with conn.execute(sql, params):
                    pass

    @staticmethod
    async def unban(address: str) -> bool:
        """Delete the row of ``address``, a running ban or the memory of an ended
        one (its next ban is a first one again). False when there is none."""
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM ip_bans WHERE address = ?", (address,)) as c:
                return c.rowcount > 0

    @staticmethod
    async def unban_all() -> int:
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM ip_bans") as cursor:
                return cursor.rowcount

    @staticmethod
    async def whitelist() -> list[WhitelistEntry]:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT network, note, created_at FROM ip_ban_whitelist"
                " ORDER BY created_at, network"
            ) as cursor:
                return [
                    WhitelistEntry(
                        network=row["network"], note=row["note"], created_at=row["created_at"]
                    )
                    for row in await cursor.fetchall()
                ]

    @staticmethod
    async def whitelisted(address: IPAddress) -> bool:
        """Whether a whitelist entry covers ``address``. Read on every check, never
        cached: an entry the CLI adds applies at once."""
        async with db.readonly() as conn:
            async with conn.execute("SELECT network FROM ip_ban_whitelist") as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            network = parse_network(row[0])
            if network is not None and network.version == address.version and address in network:
                return True
        return False

    @staticmethod
    async def add_to_whitelist(network: IPNetwork, note: str, now: float) -> bool:
        """False when the entry already exists. ``WhitelistFullError`` past ``MAX_WHITELIST``."""
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT 1 FROM ip_ban_whitelist WHERE network = ?", (str(network),)
            ) as cursor:
                if await cursor.fetchone() is not None:
                    return False
            async with conn.execute("SELECT COUNT(*) FROM ip_ban_whitelist") as cursor:
                row = await cursor.fetchone()
            if row is not None and row[0] >= MAX_WHITELIST:
                raise WhitelistFullError
            async with conn.execute(
                "INSERT INTO ip_ban_whitelist (network, note, created_at) VALUES (?, ?, ?)",
                (str(network), note.strip()[:NOTE_MAX_LENGTH], int(now)),
            ):
                pass
        return True

    @staticmethod
    async def remove_from_whitelist(network: IPNetwork) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM ip_ban_whitelist WHERE network = ?", (str(network),)
            ) as cursor:
                return cursor.rowcount > 0
