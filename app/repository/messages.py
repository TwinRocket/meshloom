import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.database import db
from app.models import (
    ContactAnalyticsHourlyBucket,
    ContactAnalyticsWeeklyBucket,
    Message,
    MessagePath,
)


class MessageRepository:
    @dataclass
    class _SearchQuery:
        free_text: str
        user_terms: list[str]
        channel_terms: list[str]

    _SEARCH_OPERATOR_RE = re.compile(
        r'(?<!\S)(user|channel):(?:"((?:[^"\\]|\\.)*)"|(\S+))',
        re.IGNORECASE,
    )

    @staticmethod
    def _contact_activity_filter(public_key: str) -> tuple[str, list[Any]]:
        lower_key = public_key.lower()
        return (
            "((type = 'PRIV' AND conversation_key = ?) OR (type = 'CHAN' AND sender_key = ?))",
            [lower_key, lower_key],
        )

    @staticmethod
    def _name_activity_filter(sender_name: str) -> tuple[str, list[Any]]:
        return "type = 'CHAN' AND sender_name = ?", [sender_name]

    @staticmethod
    def _parse_paths(paths_json: str | None) -> list[MessagePath] | None:
        """Parse paths JSON string to list of MessagePath objects."""
        if not paths_json:
            return None
        try:
            paths_data = json.loads(paths_json)
            return [MessagePath(**p) for p in paths_data]
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            return None

    @staticmethod
    async def create(
        msg_type: str,
        text: str,
        received_at: int,
        conversation_key: str,
        sender_timestamp: int | None = None,
        path: str | None = None,
        path_len: int | None = None,
        rssi: int | None = None,
        snr: float | None = None,
        txt_type: int = 0,
        signature: str | None = None,
        outgoing: bool = False,
        sender_name: str | None = None,
        sender_key: str | None = None,
        transport_code: int | None = None,
        region: str | None = None,
        packet_hash: str | None = None,
        observer_reach_eligible: bool | None = None,
    ) -> int | None:
        """Create a message, returning the ID or None if duplicate.

        Uses INSERT OR IGNORE to handle the message dedup indexes:
        - channel messages dedupe by content/timestamp for echo reconciliation
        - incoming direct messages dedupe by conversation/text/timestamp so
          raw-packet and fallback observations merge onto one row

        The path parameter is converted to the paths JSON array format.
        """
        # Convert single path to paths array format
        paths_json = None
        if path is not None:
            entry: dict = {"path": path, "received_at": received_at}
            if path_len is not None:
                entry["path_len"] = path_len
            if rssi is not None:
                entry["rssi"] = rssi
            if snr is not None:
                entry["snr"] = snr
            paths_json = json.dumps([entry])

        # Normalize sender_key to lowercase so queries can match without LOWER().
        normalized_sender_key = sender_key.lower() if sender_key else sender_key
        from app.path_utils import canonical_packet_hash

        stored_hash = canonical_packet_hash(packet_hash)
        eligible_int = (
            None if observer_reach_eligible is None else int(bool(observer_reach_eligible))
        )

        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT OR IGNORE INTO messages (type, conversation_key, text, sender_timestamp,
                                                received_at, paths, txt_type, signature, outgoing,
                                                sender_name, sender_key, transport_code, region,
                                                packet_hash, observer_reach_eligible)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg_type,
                    conversation_key,
                    text,
                    sender_timestamp,
                    received_at,
                    paths_json,
                    txt_type,
                    signature,
                    outgoing,
                    sender_name,
                    normalized_sender_key,
                    transport_code,
                    region,
                    stored_hash,
                    eligible_int,
                ),
            ) as cursor:
                rowcount = cursor.rowcount
                lastrowid = cursor.lastrowid
        # rowcount is 0 if INSERT was ignored due to UNIQUE constraint violation
        if rowcount == 0:
            return None
        return lastrowid

    @staticmethod
    async def add_path(
        message_id: int,
        path: str,
        received_at: int | None = None,
        path_len: int | None = None,
        rssi: int | None = None,
        snr: float | None = None,
    ) -> list[MessagePath]:
        """Add a new path to an existing message.

        This is used when a repeat/echo of a message arrives via a different route.
        Returns the updated list of paths.
        """
        ts = received_at if received_at is not None else int(time.time())

        # Atomic append: use json_insert to avoid read-modify-write race when
        # multiple duplicate packets arrive concurrently for the same message.
        entry: dict = {"path": path, "received_at": ts}
        if path_len is not None:
            entry["path_len"] = path_len
        if rssi is not None:
            entry["rssi"] = rssi
        if snr is not None:
            entry["snr"] = snr
        new_entry = json.dumps(entry)
        async with db.tx() as conn:
            async with conn.execute(
                """UPDATE messages SET paths = json_insert(
                    COALESCE(paths, '[]'), '$[#]', json(?)
                ) WHERE id = ?""",
                (new_entry, message_id),
            ):
                pass

            # Read back the full list for the return value, same transaction.
            async with conn.execute(
                "SELECT paths FROM messages WHERE id = ?", (message_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if not row or not row["paths"]:
            return []

        try:
            all_paths = json.loads(row["paths"])
        except json.JSONDecodeError:
            return []

        return [MessagePath(**p) for p in all_paths]

    @staticmethod
    async def claim_prefix_messages(full_key: str) -> int:
        """Promote prefix-stored messages to the full conversation key.

        When a full key becomes known for a contact, any messages stored with
        only a prefix as conversation_key are updated to use the full key.
        """
        lower_key = full_key.lower()
        # INDEXED BY: the planner otherwise prefers a type-prefixed index and
        # walks every PRIV row; the partial index only holds prefix-keyed DMs.
        async with db.tx() as conn:
            async with conn.execute(
                """UPDATE messages INDEXED BY idx_messages_priv_prefix_key
                   SET conversation_key = ?,
                       sender_key = CASE
                           WHEN sender_key IS NOT NULL AND length(sender_key) < 64
                                AND ? LIKE sender_key || '%'
                           THEN ? ELSE sender_key END
                   WHERE type = 'PRIV' AND length(conversation_key) < 64
                   AND ? LIKE conversation_key || '%'
                   AND (
                       SELECT COUNT(*) FROM contacts
                       WHERE length(public_key) = 64
                         AND public_key LIKE messages.conversation_key || '%'
                   ) = 1""",
                (lower_key, lower_key, lower_key, lower_key),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount

    @staticmethod
    async def backfill_channel_sender_key(public_key: str, name: str) -> int:
        """Backfill sender_key on channel messages that match a contact's name.

        When a contact becomes known (via advert, sync, or manual creation),
        any channel messages with a matching sender_name but no sender_key
        are updated to associate them with this contact's public key.
        """
        async with db.tx() as conn:
            async with conn.execute(
                """UPDATE messages SET sender_key = ?
                   WHERE type = 'CHAN' AND sender_name = ? AND sender_key IS NULL
                   AND (
                       SELECT COUNT(*) FROM contacts
                       WHERE name = ?
                   ) = 1
                   AND EXISTS (
                       SELECT 1 FROM contacts
                       WHERE public_key = ? AND name = ?
                   )""",
                (public_key.lower(), name, name, public_key.lower(), name),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount

    @staticmethod
    def _normalize_conversation_key(conversation_key: str) -> tuple[str, str]:
        """Normalize a conversation key and return (sql_clause, normalized_key).

        Returns the WHERE clause fragment and the normalized key value.
        """
        if len(conversation_key) == 64:
            return "AND conversation_key = ?", conversation_key.lower()
        elif len(conversation_key) == 32:
            return "AND conversation_key = ?", conversation_key.upper()
        else:
            return "AND conversation_key LIKE ?", f"{conversation_key}%"

    @staticmethod
    def _unescape_search_quoted_value(value: str) -> str:
        return value.replace('\\"', '"').replace("\\\\", "\\")

    @staticmethod
    def _parse_search_query(q: str) -> _SearchQuery:
        user_terms: list[str] = []
        channel_terms: list[str] = []
        fragments: list[str] = []
        last_end = 0

        for match in MessageRepository._SEARCH_OPERATOR_RE.finditer(q):
            fragments.append(q[last_end : match.start()])
            raw_value = match.group(2) if match.group(2) is not None else match.group(3) or ""
            value = MessageRepository._unescape_search_quoted_value(raw_value)
            if match.group(1).lower() == "user":
                user_terms.append(value)
            else:
                channel_terms.append(value)
            last_end = match.end()

        if not user_terms and not channel_terms:
            return MessageRepository._SearchQuery(free_text=q, user_terms=[], channel_terms=[])

        fragments.append(q[last_end:])
        free_text = " ".join(fragment.strip() for fragment in fragments if fragment.strip())
        return MessageRepository._SearchQuery(
            free_text=free_text,
            user_terms=user_terms,
            channel_terms=channel_terms,
        )

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _looks_like_hex_prefix(value: str) -> bool:
        return bool(value) and all(ch in "0123456789abcdefABCDEF" for ch in value)

    @staticmethod
    def _build_channel_scope_clause(value: str) -> tuple[str, list[Any]]:
        params: list[Any] = [value]
        clause = "(messages.type = 'CHAN' AND (channels.name = ? COLLATE NOCASE"

        if MessageRepository._looks_like_hex_prefix(value):
            if len(value) == 32:
                clause += " OR messages.conversation_key = ?"
                params.append(value.upper())
            else:
                clause += " OR messages.conversation_key LIKE ? ESCAPE '\\'"
                params.append(f"{MessageRepository._escape_like(value.upper())}%")

        clause += "))"
        return clause, params

    @staticmethod
    def _build_user_scope_clause(value: str) -> tuple[str, list[Any]]:
        params: list[Any] = [value, value]
        clause = (
            "((messages.type = 'PRIV' AND contacts.name = ? COLLATE NOCASE)"
            " OR (messages.type = 'CHAN' AND sender_name = ? COLLATE NOCASE)"
        )

        if MessageRepository._looks_like_hex_prefix(value):
            lower_value = value.lower()
            priv_key_clause: str
            chan_key_clause: str
            if len(value) == 64:
                priv_key_clause = "messages.conversation_key = ?"
                chan_key_clause = "sender_key = ?"
                params.extend([lower_value, lower_value])
            else:
                escaped_prefix = f"{MessageRepository._escape_like(lower_value)}%"
                priv_key_clause = "messages.conversation_key LIKE ? ESCAPE '\\'"
                chan_key_clause = "sender_key LIKE ? ESCAPE '\\'"
                params.extend([escaped_prefix, escaped_prefix])

            clause += (
                f" OR (messages.type = 'PRIV' AND {priv_key_clause})"
                f" OR (messages.type = 'CHAN' AND sender_key IS NOT NULL AND {chan_key_clause})"
            )

        clause += ")"
        return clause, params

    @staticmethod
    def _build_blocked_incoming_clause(
        message_alias: str = "",
        blocked_keys: list[str] | None = None,
        blocked_names: list[str] | None = None,
    ) -> tuple[str, list[Any]]:
        prefix = f"{message_alias}." if message_alias else ""
        blocked_matchers: list[str] = []
        params: list[Any] = []

        if blocked_keys:
            placeholders = ",".join("?" for _ in blocked_keys)
            blocked_matchers.append(
                f"({prefix}type = 'PRIV' AND {prefix}conversation_key IN ({placeholders}))"
            )
            params.extend(blocked_keys)
            blocked_matchers.append(
                f"({prefix}type = 'CHAN' AND {prefix}sender_key IS NOT NULL"
                f" AND {prefix}sender_key IN ({placeholders}))"
            )
            params.extend(blocked_keys)

        if blocked_names:
            placeholders = ",".join("?" for _ in blocked_names)
            blocked_matchers.append(
                f"({prefix}sender_name IS NOT NULL AND {prefix}sender_name IN ({placeholders}))"
            )
            params.extend(blocked_names)

        if not blocked_matchers:
            return "", []

        return f"NOT ({prefix}outgoing = 0 AND ({' OR '.join(blocked_matchers)}))", params

    @staticmethod
    def _row_to_message(row: Any) -> Message:
        """Convert a database row to a Message model."""
        packet_id = None
        transport_code = None
        region = None
        packet_hash = None
        observer_reach_eligible = None
        if hasattr(row, "keys"):
            row_keys = row.keys()
            if "packet_id" in row_keys:
                packet_id = row["packet_id"]
            if "transport_code" in row_keys:
                transport_code = row["transport_code"]
            if "region" in row_keys:
                region = row["region"]
            if "packet_hash" in row_keys:
                packet_hash = row["packet_hash"]
            if "observer_reach_eligible" in row_keys:
                raw_eligible = row["observer_reach_eligible"]
                if raw_eligible is not None:
                    observer_reach_eligible = bool(raw_eligible)

        return Message(
            id=row["id"],
            type=row["type"],
            conversation_key=row["conversation_key"],
            text=row["text"],
            sender_timestamp=row["sender_timestamp"],
            received_at=row["received_at"],
            paths=MessageRepository._parse_paths(row["paths"]),
            txt_type=row["txt_type"],
            signature=row["signature"],
            sender_key=row["sender_key"],
            outgoing=bool(row["outgoing"]),
            acked=row["acked"],
            sender_name=row["sender_name"],
            packet_id=packet_id,
            transport_code=transport_code,
            region=region,
            packet_hash=packet_hash,
            observer_reach_eligible=observer_reach_eligible,
        )

    @staticmethod
    def _message_select(message_alias: str = "messages") -> str:
        return (
            f"{message_alias}.*, "
            f"(SELECT MIN(id) FROM raw_packets WHERE message_id = {message_alias}.id) AS packet_id"
        )

    @staticmethod
    async def list_recent_paths_for_contact(public_key: str, limit: int = 200) -> list[MessagePath]:
        """Recent stored paths for DMs to/from a contact and channel posts they sent."""
        clause, params = MessageRepository._contact_activity_filter(public_key)
        query = (
            f"SELECT paths FROM messages WHERE {clause} AND paths IS NOT NULL "
            "ORDER BY received_at DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 500)))
        async with db.readonly() as conn:
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        paths: list[MessagePath] = []
        for row in rows:
            parsed = MessageRepository._parse_paths(row["paths"])
            if parsed:
                paths.extend(parsed)
        return paths

    @staticmethod
    async def get_all(
        limit: int = 100,
        offset: int = 0,
        msg_type: str | None = None,
        conversation_key: str | None = None,
        before: int | None = None,
        before_id: int | None = None,
        after: int | None = None,
        after_id: int | None = None,
        q: str | None = None,
        blocked_keys: list[str] | None = None,
        blocked_names: list[str] | None = None,
    ) -> list[Message]:
        search_query = MessageRepository._parse_search_query(q) if q else None
        query = (
            f"SELECT {MessageRepository._message_select('messages')} FROM messages "
            "LEFT JOIN contacts ON messages.type = 'PRIV' "
            "AND messages.conversation_key = contacts.public_key "
            "LEFT JOIN channels ON messages.type = 'CHAN' "
            "AND messages.conversation_key = channels.key "
            "WHERE 1=1"
        )
        params: list[Any] = []

        blocked_clause, blocked_params = MessageRepository._build_blocked_incoming_clause(
            "messages", blocked_keys, blocked_names
        )
        if blocked_clause:
            query += f" AND {blocked_clause}"
            params.extend(blocked_params)

        if msg_type:
            query += " AND messages.type = ?"
            params.append(msg_type)
        if conversation_key:
            clause, norm_key = MessageRepository._normalize_conversation_key(conversation_key)
            query += f" {clause.replace('conversation_key', 'messages.conversation_key')}"
            params.append(norm_key)

        if search_query and search_query.user_terms:
            scope_clauses: list[str] = []
            for term in search_query.user_terms:
                clause, clause_params = MessageRepository._build_user_scope_clause(term)
                scope_clauses.append(clause)
                params.extend(clause_params)
            query += f" AND ({' OR '.join(scope_clauses)})"

        if search_query and search_query.channel_terms:
            scope_clauses = []
            for term in search_query.channel_terms:
                clause, clause_params = MessageRepository._build_channel_scope_clause(term)
                scope_clauses.append(clause)
                params.extend(clause_params)
            query += f" AND ({' OR '.join(scope_clauses)})"

        if search_query and search_query.free_text:
            escaped_q = MessageRepository._escape_like(search_query.free_text)
            query += " AND messages.text LIKE ? ESCAPE '\\' COLLATE NOCASE"
            params.append(f"%{escaped_q}%")

        # Forward cursor (after/after_id) — mutually exclusive with before/before_id
        if after is not None and after_id is not None:
            query += (
                " AND (messages.received_at > ? OR (messages.received_at = ? AND messages.id > ?))"
            )
            params.extend([after, after, after_id])
            query += " ORDER BY messages.received_at ASC, messages.id ASC LIMIT ?"
            params.append(limit)
        else:
            if before is not None and before_id is not None:
                query += (
                    " AND (messages.received_at < ?"
                    " OR (messages.received_at = ? AND messages.id < ?))"
                )
                params.extend([before, before, before_id])

            query += " ORDER BY messages.received_at DESC, messages.id DESC LIMIT ?"
            params.append(limit)
            if before is None or before_id is None:
                query += " OFFSET ?"
                params.append(offset)

        async with db.readonly() as conn:
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [MessageRepository._row_to_message(row) for row in rows]

    @staticmethod
    async def get_around(
        message_id: int,
        msg_type: str | None = None,
        conversation_key: str | None = None,
        context_size: int = 100,
        blocked_keys: list[str] | None = None,
        blocked_names: list[str] | None = None,
    ) -> tuple[list[Message], bool, bool]:
        """Get messages around a target message.

        Returns (messages, has_older, has_newer).
        """
        # Build common WHERE clause for optional conversation/type filtering.
        # If the target message doesn't match filters, return an empty result.
        where_parts: list[str] = []
        base_params: list[Any] = []
        if msg_type:
            where_parts.append("type = ?")
            base_params.append(msg_type)
        if conversation_key:
            clause, norm_key = MessageRepository._normalize_conversation_key(conversation_key)
            where_parts.append(clause.removeprefix("AND "))
            base_params.append(norm_key)

        blocked_clause, blocked_params = MessageRepository._build_blocked_incoming_clause(
            blocked_keys=blocked_keys, blocked_names=blocked_names
        )
        if blocked_clause:
            where_parts.append(blocked_clause)
            base_params.extend(blocked_params)

        where_sql = " AND ".join(["1=1", *where_parts])

        # 1. Get the target message (must satisfy filters if provided)
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {MessageRepository._message_select('messages')} "
                f"FROM messages WHERE id = ? AND {where_sql}",
                (message_id, *base_params),
            ) as target_cursor:
                target_row = await target_cursor.fetchone()
            if not target_row:
                return [], False, False

            target = MessageRepository._row_to_message(target_row)

            # 2. Get context_size+1 messages before target (DESC)
            before_query = f"""
                SELECT {MessageRepository._message_select("messages")} FROM messages WHERE {where_sql}
                AND (received_at < ? OR (received_at = ? AND id < ?))
                ORDER BY received_at DESC, id DESC LIMIT ?
            """
            before_params = [
                *base_params,
                target.received_at,
                target.received_at,
                target.id,
                context_size + 1,
            ]
            async with conn.execute(before_query, before_params) as before_cursor:
                before_rows = list(await before_cursor.fetchall())

            has_older = len(before_rows) > context_size
            before_messages = [
                MessageRepository._row_to_message(r) for r in before_rows[:context_size]
            ]

            # 3. Get context_size+1 messages after target (ASC)
            after_query = f"""
                SELECT {MessageRepository._message_select("messages")} FROM messages WHERE {where_sql}
                AND (received_at > ? OR (received_at = ? AND id > ?))
                ORDER BY received_at ASC, id ASC LIMIT ?
            """
            after_params = [
                *base_params,
                target.received_at,
                target.received_at,
                target.id,
                context_size + 1,
            ]
            async with conn.execute(after_query, after_params) as after_cursor:
                after_rows = list(await after_cursor.fetchall())

        has_newer = len(after_rows) > context_size
        after_messages = [MessageRepository._row_to_message(r) for r in after_rows[:context_size]]

        # Combine: before (reversed to ASC) + target + after
        all_messages = list(reversed(before_messages)) + [target] + after_messages
        return all_messages, has_older, has_newer

    @staticmethod
    async def increment_ack_count(message_id: int) -> int:
        """Increment ack count and return the new value.

        NOTE: ``RETURNING`` leaves the prepared statement active until the
        row is fetched, so we MUST consume it inside the ``async with``
        block. Without that, the commit at the end of ``db.tx()`` fails
        with ``cannot commit transaction - SQL statements in progress``.
        """
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE messages SET acked = acked + 1 WHERE id = ? RETURNING acked",
                (message_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return row["acked"] if row else 1

    @staticmethod
    async def get_ack_and_paths(message_id: int) -> tuple[int, list[MessagePath] | None]:
        """Get the current ack count and paths for a message."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT acked, paths FROM messages WHERE id = ?", (message_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return 0, None
        return row["acked"], MessageRepository._parse_paths(row["paths"])

    @staticmethod
    async def get_by_id(message_id: int) -> "Message | None":
        """Look up a message by its ID."""
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {MessageRepository._message_select('messages')} FROM messages WHERE id = ?",
                (message_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None

        return MessageRepository._row_to_message(row)

    @staticmethod
    async def get_by_packet_hash(packet_hash: str) -> "Message | None":
        """Most recent message with this firmware hash. Prefer outgoing (origin GPS)."""
        from app.path_utils import canonical_packet_hash

        stored = canonical_packet_hash(packet_hash)
        if stored is None:
            return None
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {MessageRepository._message_select('messages')} FROM messages "
                "WHERE packet_hash = ? ORDER BY outgoing DESC, id DESC LIMIT 1",
                (stored,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        return MessageRepository._row_to_message(row)

    @staticmethod
    async def apply_observer_reach(
        message_id: int,
        *,
        packet_hash: str | None = None,
        observer_reach_eligible: bool | None = None,
        hash_is_flood: bool | None = None,
    ) -> tuple[str | None, bool | None]:
        """Persist observer-reach fields.

        Eligible is sticky-true. Hash fills if empty; a flood echo may replace
        a directed hash, but a directed echo never replaces a flood hash.
        """
        from app.path_utils import canonical_packet_hash

        stored = canonical_packet_hash(packet_hash)
        eligible_int = (
            None if observer_reach_eligible is None else int(bool(observer_reach_eligible))
        )
        async with db.tx() as conn:
            if stored is not None:
                if hash_is_flood:
                    await conn.execute(
                        "UPDATE messages SET packet_hash = ? WHERE id = ?",
                        (stored, message_id),
                    )
                else:
                    await conn.execute(
                        "UPDATE messages SET packet_hash = COALESCE(packet_hash, ?) WHERE id = ?",
                        (stored, message_id),
                    )
            if eligible_int == 1:
                await conn.execute(
                    "UPDATE messages SET observer_reach_eligible = 1 WHERE id = ?",
                    (message_id,),
                )
            elif eligible_int == 0:
                await conn.execute(
                    """
                    UPDATE messages
                    SET observer_reach_eligible = COALESCE(observer_reach_eligible, 0)
                    WHERE id = ?
                    """,
                    (message_id,),
                )
            async with conn.execute(
                "SELECT packet_hash, observer_reach_eligible FROM messages WHERE id = ?",
                (message_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return stored, observer_reach_eligible
        raw_eligible = row["observer_reach_eligible"]
        return row["packet_hash"], None if raw_eligible is None else bool(raw_eligible)

    @staticmethod
    async def delete_by_id(message_id: int) -> None:
        """Delete a message row by ID."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE raw_packets SET message_id = NULL WHERE message_id = ?",
                (message_id,),
            ):
                pass
            async with conn.execute("DELETE FROM messages WHERE id = ?", (message_id,)):
                pass

    @staticmethod
    async def stream_chan_messages_with_raw(
        batch_size: int = 500,
    ) -> "AsyncIterator[tuple[int, bytes]]":
        """Yield (message_id, raw_packet_bytes) for CHAN messages that still have a
        retained raw packet, in ascending id batches.

        Used by the region backfill: region is a property of the on-air payload, so
        any retained raw packet for the message yields the same transport code.
        """
        last_id = 0
        while True:
            async with db.readonly() as conn:
                async with conn.execute(
                    """
                    SELECT m.id AS mid, rp.data AS data
                    FROM messages m
                    JOIN raw_packets rp ON rp.message_id = m.id
                    WHERE m.type = 'CHAN' AND m.id > ?
                    GROUP BY m.id
                    ORDER BY m.id ASC
                    LIMIT ?
                    """,
                    (last_id, batch_size),
                ) as cursor:
                    rows = await cursor.fetchall()
            if not rows:
                return
            for row in rows:
                yield row["mid"], bytes(row["data"])
                last_id = row["mid"]

    @staticmethod
    async def set_transport_scope(
        message_id: int, transport_code: int | None, region: str | None
    ) -> None:
        """Set the resolved transport code / region on a stored message."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE messages SET transport_code = ?, region = ? WHERE id = ?",
                (transport_code, region, message_id),
            ):
                pass

    @staticmethod
    async def get_by_content(
        msg_type: str,
        conversation_key: str,
        text: str,
        sender_timestamp: int | None,
        outgoing: bool | None = None,
    ) -> "Message | None":
        """Look up a message by its unique content fields."""
        query = """
            SELECT messages.*,
                   (SELECT MIN(id) FROM raw_packets WHERE message_id = messages.id) AS packet_id
            FROM messages
            WHERE type = ? AND conversation_key = ? AND text = ?
              AND (sender_timestamp = ? OR (sender_timestamp IS NULL AND ? IS NULL))
        """
        params: list[Any] = [msg_type, conversation_key, text, sender_timestamp, sender_timestamp]
        if outgoing is not None:
            query += " AND outgoing = ?"
            params.append(1 if outgoing else 0)
        query += " ORDER BY id ASC"
        async with db.readonly() as conn:
            async with conn.execute(query, params) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None

        return MessageRepository._row_to_message(row)

    @staticmethod
    async def get_unread_counts(
        name: str | None = None,
        blocked_keys: list[str] | None = None,
        blocked_names: list[str] | None = None,
    ) -> dict:
        """Get unread counts, mention flags, last-message times/previews, and read boundaries.

        Args:
            name: User's display name for @[name] mention detection. If None, mentions are skipped.
            blocked_keys: Public keys whose messages should be excluded from counts.
            blocked_names: Display names whose messages should be excluded from counts.

        Returns:
            Dict with 'counts', 'mentions', 'last_message_times',
            'last_message_previews', 'last_read_ats', and 'first_unread_ids' keys.
        """
        counts: dict[str, int] = {}
        mention_flags: dict[str, bool] = {}
        last_message_times: dict[str, int] = {}
        last_message_previews: dict[str, str] = {}
        last_message_outgoing: dict[str, bool] = {}
        last_read_ats: dict[str, int | None] = {}
        # id of the oldest unread message per conversation.
        first_unread_ids: dict[str, int | None] = {}

        mention_token = f"@[{name}]" if name else None

        blocked_clause, blocked_params = MessageRepository._build_blocked_incoming_clause(
            "m", blocked_keys, blocked_names
        )
        blocked_sql = f" AND {blocked_clause}" if blocked_clause else ""

        # Every query below starts from the list of conversations, found by a
        # skip-scan (one index seek per distinct type / conversation_key), then
        # touches only each conversation's newest row and its unread rows
        # through idx_messages_pagination / idx_messages_unread_covering. The
        # previous ROW_NUMBER() windows sorted the whole messages table while
        # holding the DB lock (~0.5 s at 300k messages).
        conversations_cte = """
            WITH RECURSIVE
            types(t) AS (
                SELECT MIN(type) FROM messages
                UNION ALL
                SELECT (SELECT MIN(type) FROM messages WHERE type > types.t)
                FROM types WHERE types.t IS NOT NULL
            ),
            conv(t, ck) AS (
                SELECT t, (SELECT MIN(conversation_key) FROM messages WHERE type = types.t)
                FROM types WHERE types.t IS NOT NULL
                UNION ALL
                SELECT conv.t, (
                    SELECT MIN(conversation_key) FROM messages
                    WHERE type = conv.t AND conversation_key > conv.ck
                )
                FROM conv WHERE conv.ck IS NOT NULL
            ),
            convs AS (
                SELECT conv.t, conv.ck,
                       ch.key IS NOT NULL AS has_channel,
                       COALESCE(ch.muted, 0) AS muted,
                       COALESCE(
                           CASE WHEN conv.t = 'CHAN' THEN ch.last_read_at
                                WHEN conv.t = 'PRIV' THEN ct.last_read_at END,
                           0
                       ) AS read_at
                FROM conv
                LEFT JOIN channels ch ON conv.t = 'CHAN' AND ch.key = conv.ck
                LEFT JOIN contacts ct ON conv.t = 'PRIV' AND ct.public_key = conv.ck
                WHERE conv.ck IS NOT NULL
            )
        """
        if mention_token:
            mention_sql = "SUM(INSTR(LOWER(m.text), LOWER(?)) > 0) > 0"
            mention_params: list[Any] = [mention_token]
        else:
            mention_sql = "0"
            mention_params = []

        async with db.readonly() as conn:
            # Unread counts, mention flags and oldest unread id. Muted channels
            # are skipped. Channel *counts* need a channels row (orphan channel
            # conversations only get a first-unread id, as before); every
            # non-channel conversation counts against its contact's read
            # boundary (0 when there is no contact row).
            async with conn.execute(
                f"""
                {conversations_cte},
                unread AS (
                    SELECT c.t, c.ck, c.has_channel, c.read_at,
                           COUNT(*) AS unread_count,
                           {mention_sql} AS has_mention
                    FROM convs c
                    JOIN messages m
                      ON m.type = c.t AND m.conversation_key = c.ck
                     AND m.outgoing = 0 AND m.received_at > c.read_at
                    WHERE (c.t <> 'CHAN' OR c.muted = 0)
                      {blocked_sql}
                    GROUP BY c.t, c.ck
                )
                SELECT u.t, u.ck, u.has_channel, u.unread_count, u.has_mention,
                       (
                           SELECT m.id FROM messages m
                           WHERE m.type = u.t AND m.conversation_key = u.ck
                             AND m.outgoing = 0 AND m.received_at > u.read_at
                             {blocked_sql}
                           ORDER BY m.received_at ASC, m.id ASC
                           LIMIT 1
                       ) AS first_unread_id
                FROM unread u
                """,
                (*mention_params, *blocked_params, *blocked_params),
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                is_channel = row["t"] == "CHAN"
                state_key = f"{'channel' if is_channel else 'contact'}-{row['ck']}"
                # Oldest unread by (received_at, id): sender timestamps are whole
                # seconds, so several unread messages routinely share a second;
                # this matches the client's own ordering.
                first_unread_ids[state_key] = row["first_unread_id"]
                if (is_channel and row["has_channel"]) or row["t"] == "PRIV":
                    counts[state_key] = row["unread_count"]
                    if mention_token and row["has_mention"]:
                        mention_flags[state_key] = True

            async with conn.execute(
                """
                SELECT key, last_read_at
                FROM channels
                """
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                last_read_ats[f"channel-{row['key']}"] = row["last_read_at"]

            async with conn.execute(
                """
                SELECT public_key, last_read_at
                FROM contacts
                """
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                last_read_ats[f"contact-{row['public_key']}"] = row["last_read_at"]

            # Newest message per conversation (including read ones), ordered by
            # (received_at DESC, id DESC) like the client, excluding blocked
            # incoming traffic so refresh matches live WS behavior.
            async with conn.execute(
                f"""
                {conversations_cte},
                last AS (
                    SELECT c.t, c.ck, (
                        SELECT m.id FROM messages m
                        WHERE m.type = c.t AND m.conversation_key = c.ck
                          {blocked_sql}
                        ORDER BY m.received_at DESC, m.id DESC
                        LIMIT 1
                    ) AS last_id
                    FROM convs c
                )
                SELECT l.t, l.ck, lm.received_at AS last_message_time,
                       SUBSTR(COALESCE(lm.text, ''), 1, 120) AS last_message_preview,
                       lm.outgoing
                FROM last l
                JOIN messages lm ON lm.id = l.last_id
                """,
                blocked_params,
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                prefix = "channel" if row["t"] == "CHAN" else "contact"
                state_key = f"{prefix}-{row['ck']}"
                last_message_times[state_key] = row["last_message_time"]
                last_message_previews[state_key] = row["last_message_preview"] or ""
                last_message_outgoing[state_key] = bool(row["outgoing"])

        # Only include last_read_ats for conversations that actually have messages.
        # Without this filter, every contact heard via advertisement (even without
        # any DMs) bloats the payload — 391KB down to ~46KB on a typical database.
        last_read_ats = {k: v for k, v in last_read_ats.items() if k in last_message_times}

        return {
            "counts": counts,
            "mentions": mention_flags,
            "last_message_times": last_message_times,
            "last_message_previews": last_message_previews,
            "last_message_outgoing": last_message_outgoing,
            "last_read_ats": last_read_ats,
            "first_unread_ids": first_unread_ids,
        }

    @staticmethod
    async def count_dm_messages(contact_key: str) -> int:
        """Count total DM messages for a contact."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE type = 'PRIV' AND conversation_key = ?",
                (contact_key.lower(),),
            ) as cursor:
                row = await cursor.fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    async def count_channel_messages_by_sender(sender_key: str) -> int:
        """Count channel messages sent by a specific contact."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE type = 'CHAN' AND sender_key = ?",
                (sender_key.lower(),),
            ) as cursor:
                row = await cursor.fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    async def count_channel_messages_by_sender_name(sender_name: str) -> int:
        """Count channel messages attributed to a display name."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE type = 'CHAN' AND sender_name = ?",
                (sender_name,),
            ) as cursor:
                row = await cursor.fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    async def get_first_channel_message_by_sender_name(sender_name: str) -> int | None:
        """Get the earliest stored channel message timestamp for a display name."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT MIN(received_at) AS first_seen FROM messages WHERE type = 'CHAN' AND sender_name = ?",
                (sender_name,),
            ) as cursor:
                row = await cursor.fetchone()
        return row["first_seen"] if row and row["first_seen"] is not None else None

    @staticmethod
    async def get_channel_stats(conversation_key: str) -> dict:
        """Get channel message statistics: time-windowed counts, first message, unique senders, top senders, path hash widths.

        Returns a dict with message_counts, first_message_at, unique_sender_count, top_senders_24h, path_hash_width_24h.
        """
        import time as _time

        from app.path_utils import bucket_path_hash_widths

        now = int(_time.time())
        t_1h = now - 3600
        t_24h = now - 86400
        t_48h = now - 172800
        t_7d = now - 604800

        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT COUNT(*) AS all_time,
                    SUM(CASE WHEN received_at >= ? THEN 1 ELSE 0 END) AS last_1h,
                    SUM(CASE WHEN received_at >= ? THEN 1 ELSE 0 END) AS last_24h,
                    SUM(CASE WHEN received_at >= ? THEN 1 ELSE 0 END) AS last_48h,
                    SUM(CASE WHEN received_at >= ? THEN 1 ELSE 0 END) AS last_7d,
                    MIN(received_at) AS first_message_at,
                    COUNT(DISTINCT sender_key) AS unique_sender_count
                FROM messages WHERE type = 'CHAN' AND conversation_key = ?
                """,
                (t_1h, t_24h, t_48h, t_7d, conversation_key),
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None  # Aggregate query always returns a row

            message_counts = {
                "last_1h": row["last_1h"] or 0,
                "last_24h": row["last_24h"] or 0,
                "last_48h": row["last_48h"] or 0,
                "last_7d": row["last_7d"] or 0,
                "all_time": row["all_time"] or 0,
            }

            async with conn.execute(
                """
                SELECT COALESCE(sender_name, sender_key, 'Unknown') AS display_name,
                    sender_key, COUNT(*) AS cnt
                FROM messages
                WHERE type = 'CHAN' AND conversation_key = ?
                    AND received_at >= ? AND sender_key IS NOT NULL
                GROUP BY sender_key ORDER BY cnt DESC LIMIT 5
                """,
                (conversation_key, t_24h),
            ) as cursor:
                top_rows = await cursor.fetchall()
            top_senders = [
                {
                    "sender_name": r["display_name"],
                    "sender_key": r["sender_key"],
                    "message_count": r["cnt"],
                }
                for r in top_rows
            ]

            # Path hash width distribution for last 24h: fetch raw rows under
            # the lock, then release BEFORE the CPU-bound in-Python envelope
            # parse. Parsing can iterate thousands of rows and previously held
            # the DB lock for the whole traversal — blocking every other repo
            # caller on a Pi. Keep the lock only for the fetch.
            async with conn.execute(
                """
                SELECT rp.data FROM raw_packets rp
                JOIN messages m ON rp.message_id = m.id
                WHERE m.type = 'CHAN' AND m.conversation_key = ?
                  AND rp.timestamp >= ?
                """,
                (conversation_key, t_24h),
            ) as cursor:
                rows3 = await cursor.fetchall()
            first_message_at = row["first_message_at"]
            unique_sender_count = row["unique_sender_count"] or 0

        path_hash_width_24h = bucket_path_hash_widths(rows3)

        return {
            "message_counts": message_counts,
            "first_message_at": first_message_at,
            "unique_sender_count": unique_sender_count,
            "top_senders_24h": top_senders,
            "path_hash_width_24h": path_hash_width_24h,
        }

    @staticmethod
    async def count_channels_with_incoming_messages() -> int:
        """Count distinct channel conversations with at least one incoming message."""
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT COUNT(DISTINCT conversation_key) AS cnt
                FROM messages
                WHERE type = 'CHAN' AND outgoing = 0
                """
            ) as cursor:
                row = await cursor.fetchone()
        return int(row["cnt"]) if row and row["cnt"] is not None else 0

    @staticmethod
    async def get_most_active_rooms(sender_key: str, limit: int = 5) -> list[tuple[str, str, int]]:
        """Get channels where a contact has sent the most messages.

        Returns list of (channel_key, channel_name, message_count) tuples.
        """
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT m.conversation_key, COALESCE(c.name, m.conversation_key) AS channel_name,
                       COUNT(*) AS cnt
                FROM messages m
                LEFT JOIN channels c ON m.conversation_key = c.key
                WHERE m.type = 'CHAN' AND m.sender_key = ?
                GROUP BY m.conversation_key
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (sender_key.lower(), limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [(row["conversation_key"], row["channel_name"], row["cnt"]) for row in rows]

    @staticmethod
    async def get_most_active_rooms_by_sender_name(
        sender_name: str, limit: int = 5
    ) -> list[tuple[str, str, int]]:
        """Get channels where a display name has sent the most messages."""
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT m.conversation_key, COALESCE(c.name, m.conversation_key) AS channel_name,
                       COUNT(*) AS cnt
                FROM messages m
                LEFT JOIN channels c ON m.conversation_key = c.key
                WHERE m.type = 'CHAN' AND m.sender_name = ?
                GROUP BY m.conversation_key
                ORDER BY cnt DESC
                LIMIT ?
                """,
                (sender_name, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [(row["conversation_key"], row["channel_name"], row["cnt"]) for row in rows]

    @staticmethod
    async def _get_activity_hour_buckets(where_sql: str, params: list[Any]) -> dict[int, int]:
        async with db.readonly() as conn:
            async with conn.execute(
                f"""
                SELECT received_at / 3600 AS hour_bucket, COUNT(*) AS cnt
                FROM messages
                WHERE {where_sql}
                GROUP BY hour_bucket
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        return {int(row["hour_bucket"]): row["cnt"] for row in rows}

    @staticmethod
    def _build_hourly_activity(
        hour_counts: dict[int, int], now: int
    ) -> list[ContactAnalyticsHourlyBucket]:
        current_hour = now // 3600
        if hour_counts:
            min_hour = min(hour_counts)
        else:
            min_hour = current_hour

        buckets: list[ContactAnalyticsHourlyBucket] = []
        for hour_bucket in range(current_hour - 23, current_hour + 1):
            last_24h_count = hour_counts.get(hour_bucket, 0)

            week_total = 0
            week_samples = 0
            all_time_total = 0
            all_time_samples = 0
            compare_hour = hour_bucket
            while compare_hour >= min_hour:
                count = hour_counts.get(compare_hour, 0)
                all_time_total += count
                all_time_samples += 1
                if week_samples < 7:
                    week_total += count
                    week_samples += 1
                compare_hour -= 24

            buckets.append(
                ContactAnalyticsHourlyBucket(
                    bucket_start=hour_bucket * 3600,
                    last_24h_count=last_24h_count,
                    last_week_average=round(week_total / week_samples, 2) if week_samples else 0,
                    all_time_average=round(all_time_total / all_time_samples, 2)
                    if all_time_samples
                    else 0,
                )
            )
        return buckets

    @staticmethod
    async def _get_weekly_activity(
        where_sql: str,
        params: list[Any],
        now: int,
        weeks: int = 26,
    ) -> list[ContactAnalyticsWeeklyBucket]:
        bucket_seconds = 7 * 24 * 3600
        current_day_start = (now // 86400) * 86400
        start = current_day_start - (weeks - 1) * bucket_seconds

        async with db.readonly() as conn:
            async with conn.execute(
                f"""
                SELECT (received_at - ?) / ? AS bucket_idx, COUNT(*) AS cnt
                FROM messages
                WHERE {where_sql} AND received_at >= ?
                GROUP BY bucket_idx
                """,
                [start, bucket_seconds, *params, start],
            ) as cursor:
                rows = await cursor.fetchall()
        counts = {int(row["bucket_idx"]): row["cnt"] for row in rows}

        return [
            ContactAnalyticsWeeklyBucket(
                bucket_start=start + bucket_idx * bucket_seconds,
                message_count=counts.get(bucket_idx, 0),
            )
            for bucket_idx in range(weeks)
        ]

    @staticmethod
    async def get_contact_activity_series(
        public_key: str,
        now: int | None = None,
    ) -> tuple[list[ContactAnalyticsHourlyBucket], list[ContactAnalyticsWeeklyBucket]]:
        """Get combined DM + channel activity series for a keyed contact."""
        ts = now if now is not None else int(time.time())
        where_sql, params = MessageRepository._contact_activity_filter(public_key)
        hour_counts = await MessageRepository._get_activity_hour_buckets(where_sql, params)
        hourly = MessageRepository._build_hourly_activity(hour_counts, ts)
        weekly = await MessageRepository._get_weekly_activity(where_sql, params, ts)
        return hourly, weekly

    @staticmethod
    async def get_sender_name_activity_series(
        sender_name: str,
        now: int | None = None,
    ) -> tuple[list[ContactAnalyticsHourlyBucket], list[ContactAnalyticsWeeklyBucket]]:
        """Get channel-only activity series for a sender name."""
        ts = now if now is not None else int(time.time())
        where_sql, params = MessageRepository._name_activity_filter(sender_name)
        hour_counts = await MessageRepository._get_activity_hour_buckets(where_sql, params)
        hourly = MessageRepository._build_hourly_activity(hour_counts, ts)
        weekly = await MessageRepository._get_weekly_activity(where_sql, params, ts)
        return hourly, weekly
