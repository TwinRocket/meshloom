import time
from typing import Any

from app.database import db
from app.models import Channel
from app.services.channel_membership import (
    MEMBERSHIP_ADOPTED,
    ChannelMembership,
    coerce_membership,
    normalize_channel_key,
)


def effective_channel_muted(muted: bool, muted_until: int | None, now: int | None = None) -> bool:
    if not muted:
        return False
    if muted_until is None:
        return True
    return muted_until > (now if now is not None else int(time.time()))


def _row_muted_until(row: Any) -> int | None:
    try:
        raw = row["muted_until"]
    except (KeyError, IndexError, TypeError):
        return None
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _channel_from_row(row: Any) -> Channel:
    membership = MEMBERSHIP_ADOPTED
    try:
        membership = coerce_membership(row["membership"])
    except (KeyError, IndexError, TypeError):
        membership = MEMBERSHIP_ADOPTED
    muted_until = _row_muted_until(row)
    stored_muted = bool(row["muted"])
    muted = effective_channel_muted(stored_muted, muted_until)
    return Channel(
        key=row["key"],
        name=row["name"],
        is_hashtag=bool(row["is_hashtag"]),
        on_radio=bool(row["on_radio"]),
        flood_scope_override=row["flood_scope_override"],
        path_hash_mode_override=row["path_hash_mode_override"],
        last_read_at=row["last_read_at"],
        favorite=bool(row["favorite"]),
        muted=muted,
        muted_until=muted_until if muted else None,
        membership=membership,
    )


_CHANNEL_SELECT = (
    "SELECT key, name, is_hashtag, on_radio, flood_scope_override, "
    "path_hash_mode_override, last_read_at, favorite, muted, muted_until, membership "
    "FROM channels"
)


class ChannelRepository:
    @staticmethod
    async def upsert(
        key: str,
        name: str,
        is_hashtag: bool = False,
        on_radio: bool = False,
        membership: ChannelMembership = MEMBERSHIP_ADOPTED,
    ) -> None:
        """Upsert a channel. Key is 32-char hex string."""
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO channels (key, name, is_hashtag, on_radio, flood_scope_override, membership)
                VALUES (?, ?, ?, ?, NULL, ?)
                ON CONFLICT(key) DO UPDATE SET
                    name = excluded.name,
                    is_hashtag = excluded.is_hashtag,
                    on_radio = excluded.on_radio,
                    membership = excluded.membership
                """,
                (
                    normalize_channel_key(key),
                    name,
                    is_hashtag,
                    on_radio,
                    coerce_membership(membership),
                ),
            ):
                pass

    @staticmethod
    async def upsert_name(
        key: str,
        name: str,
        *,
        is_hashtag: bool = False,
        membership: ChannelMembership = MEMBERSHIP_ADOPTED,
    ) -> None:
        """Create or rename a channel without touching radio/read/override state."""
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO channels (key, name, is_hashtag, on_radio, flood_scope_override, membership)
                VALUES (?, ?, ?, 0, NULL, ?)
                ON CONFLICT(key) DO UPDATE SET
                    name = excluded.name,
                    is_hashtag = excluded.is_hashtag,
                    membership = excluded.membership
                """,
                (normalize_channel_key(key), name, is_hashtag, coerce_membership(membership)),
            ):
                pass

    @staticmethod
    async def insert_if_absent(
        key: str,
        name: str,
        *,
        is_hashtag: bool = False,
        membership: ChannelMembership = MEMBERSHIP_ADOPTED,
    ) -> bool:
        """Insert a channel only when the key is new. Existing rows are left alone."""
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO channels (key, name, is_hashtag, on_radio, flood_scope_override, membership)
                VALUES (?, ?, ?, 0, NULL, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (normalize_channel_key(key), name, is_hashtag, coerce_membership(membership)),
            ) as cursor:
                return (cursor.rowcount or 0) > 0

    @staticmethod
    async def get_by_key(key: str) -> Channel | None:
        """Get a channel by its key (32-char hex string)."""
        async with db.readonly() as conn:
            async with conn.execute(
                f"{_CHANNEL_SELECT} WHERE key = ?",
                (normalize_channel_key(key),),
            ) as cursor:
                row = await cursor.fetchone()
        if row:
            return _channel_from_row(row)
        return None

    @staticmethod
    async def get_all() -> list[Channel]:
        async with db.readonly() as conn:
            async with conn.execute(f"{_CHANNEL_SELECT} ORDER BY name") as cursor:
                rows = await cursor.fetchall()
        return [_channel_from_row(row) for row in rows]

    @staticmethod
    async def set_favorite(key: str, value: bool) -> bool:
        """Set or clear the favorite flag for a channel. Returns True if row was found."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE channels SET favorite = ? WHERE key = ?",
                (1 if value else 0, normalize_channel_key(key)),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount > 0

    @staticmethod
    async def set_muted(key: str, value: bool, muted_until: int | None = None) -> bool:
        """Set or clear mute. ``muted_until`` is unix time; None means indefinite."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE channels SET muted = ?, muted_until = ? WHERE key = ?",
                (
                    1 if value else 0,
                    muted_until if value else None,
                    normalize_channel_key(key),
                ),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount > 0

    @staticmethod
    async def delete(key: str) -> None:
        """Delete a channel by key."""
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM channels WHERE key = ?",
                (normalize_channel_key(key),),
            ):
                pass

    @staticmethod
    async def update_last_read_at(key: str, timestamp: int | None = None) -> bool:
        """Update the last_read_at timestamp for a channel.

        Returns True if a row was updated, False if channel not found.
        """
        ts = timestamp if timestamp is not None else int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE channels SET last_read_at = ? WHERE key = ?",
                (ts, normalize_channel_key(key)),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount > 0

    @staticmethod
    async def update_flood_scope_override(key: str, flood_scope_override: str | None) -> bool:
        """Set or clear a channel's flood-scope override."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE channels SET flood_scope_override = ? WHERE key = ?",
                (flood_scope_override, normalize_channel_key(key)),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount > 0

    @staticmethod
    async def update_path_hash_mode_override(key: str, path_hash_mode_override: int | None) -> bool:
        """Set or clear a channel's path hash mode override."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE channels SET path_hash_mode_override = ? WHERE key = ?",
                (path_hash_mode_override, normalize_channel_key(key)),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount > 0

    @staticmethod
    async def mark_all_read(timestamp: int) -> None:
        """Mark adopted channels as read at the given timestamp."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE channels SET last_read_at = ? WHERE membership = ?",
                (timestamp, MEMBERSHIP_ADOPTED),
            ):
                pass
