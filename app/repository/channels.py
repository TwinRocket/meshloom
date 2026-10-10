import functools
import hashlib
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


def _row_pinned(row: Any) -> bool:
    try:
        return bool(row["pinned"])
    except (KeyError, IndexError, TypeError):
        return False


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
        pinned=_row_pinned(row),
        muted=muted,
        muted_until=muted_until if muted else None,
        membership=membership,
    )


_CHANNEL_SELECT = (
    "SELECT key, name, is_hashtag, on_radio, flood_scope_override, "
    "path_hash_mode_override, last_read_at, favorite, pinned, muted, muted_until, membership "
    "FROM channels"
)


# GroupText trial-decrypt index: first byte of SHA256(key) -> [(key, name,
# key bytes)], in ``get_all()`` order (first match wins, as before). Built
# lazily, dropped by every write that can add, remove or rename a channel, and
# tied to the connected Database object so a swapped DB never sees stale keys.
_DecryptCandidate = tuple[str, str, bytes]
_decrypt_index: dict[int, list[_DecryptCandidate]] | None = None
_decrypt_index_db: object | None = None
_decrypt_index_generation = 0


def invalidate_channel_decrypt_index() -> None:
    global _decrypt_index, _decrypt_index_generation
    _decrypt_index = None
    _decrypt_index_generation += 1


def _invalidates_decrypt_index(fn):
    """Drop the decrypt index once the write has committed (or failed)."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        finally:
            invalidate_channel_decrypt_index()

    return wrapper


class ChannelRepository:
    @staticmethod
    async def get_decrypt_candidates(channel_hash: int) -> list[_DecryptCandidate]:
        """Channels whose key hash byte equals ``channel_hash`` (usually 0 or 1)."""
        global _decrypt_index, _decrypt_index_db
        index = _decrypt_index
        if index is None or _decrypt_index_db is not db:
            generation = _decrypt_index_generation
            source_db = db
            index = {}
            for channel in await ChannelRepository.get_all():
                try:
                    key_bytes = bytes.fromhex(channel.key)
                except ValueError:
                    continue
                bucket = index.setdefault(hashlib.sha256(key_bytes).digest()[0], [])
                bucket.append((channel.key, channel.name, key_bytes))
            # Only publish if no write invalidated the index while we read.
            if generation == _decrypt_index_generation and source_db is db:
                _decrypt_index, _decrypt_index_db = index, source_db
        return index.get(channel_hash, [])

    @staticmethod
    @_invalidates_decrypt_index
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
    @_invalidates_decrypt_index
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
    @_invalidates_decrypt_index
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
    async def set_pinned(key: str, value: bool) -> bool:
        """Set or clear the pinned flag for a channel. Returns True if row was found."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE channels SET pinned = ? WHERE key = ?",
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
    @_invalidates_decrypt_index
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
