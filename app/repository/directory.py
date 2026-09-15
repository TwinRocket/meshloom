import time

import aiosqlite

from app.database import db

DIRECTORY_SOURCE_CORESCOPE = "corescope"


class DirectoryCacheRow:
    __slots__ = ("prefix", "hash_width", "name", "source", "expires_at", "public_key", "lat", "lon")

    def __init__(
        self,
        prefix: str,
        hash_width: int,
        name: str | None,
        source: str,
        expires_at: int,
        public_key: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> None:
        self.prefix = prefix
        self.hash_width = hash_width
        self.name = name
        self.source = source
        self.expires_at = expires_at
        self.public_key = public_key
        self.lat = lat
        self.lon = lon

    @property
    def expired(self) -> bool:
        return self.expires_at <= int(time.time())


def _row_from_sql(row: aiosqlite.Row) -> DirectoryCacheRow:
    keys = row.keys()
    return DirectoryCacheRow(
        prefix=row["prefix"],
        hash_width=int(row["hash_width"]),
        name=row["name"],
        source=row["source"],
        expires_at=int(row["expires_at"]),
        public_key=row["public_key"] if "public_key" in keys else None,
        lat=row["lat"] if "lat" in keys else None,
        lon=row["lon"] if "lon" in keys else None,
    )


class DirectoryHopCacheRepository:
    """SQLite cache for directory hop names. Does not touch RF contacts."""

    @staticmethod
    async def get_many(keys: list[tuple[str, int]]) -> dict[tuple[str, int], DirectoryCacheRow]:
        if not keys:
            return {}
        placeholders = ",".join("(?, ?)" for _ in keys)
        params: list[object] = []
        for prefix, hash_width in keys:
            params.extend((prefix, hash_width))
        async with db.readonly() as conn:
            async with conn.execute(
                f"""
                SELECT prefix, hash_width, name, source, expires_at, public_key, lat, lon
                FROM directory_hop_cache
                WHERE (prefix, hash_width) IN ({placeholders})
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        return {(row["prefix"], int(row["hash_width"])): _row_from_sql(row) for row in rows}

    @staticmethod
    async def upsert(
        prefix: str,
        hash_width: int,
        name: str | None,
        source: str,
        expires_at: int,
        public_key: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> None:
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO directory_hop_cache (
                    prefix, hash_width, name, source, expires_at, public_key, lat, lon
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(prefix, hash_width) DO UPDATE SET
                    name = excluded.name,
                    source = excluded.source,
                    expires_at = excluded.expires_at,
                    public_key = excluded.public_key,
                    lat = excluded.lat,
                    lon = excluded.lon
                """,
                (prefix, hash_width, name, source, expires_at, public_key, lat, lon),
            )

    @staticmethod
    async def wipe() -> int:
        async with db.tx() as conn:
            cursor = await conn.execute("DELETE FROM directory_hop_cache")
            return cursor.rowcount if cursor.rowcount is not None else 0
