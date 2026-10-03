import logging
import time
from collections.abc import Mapping
from typing import Any

from app.database import db
from app.models import (
    Contact,
    ContactAdvertPath,
    ContactAdvertPathSummary,
    ContactNameHistory,
    ContactUpsert,
)
from app.path_utils import first_hop_hex, normalize_contact_route, normalize_route_override

logger = logging.getLogger(__name__)


class AmbiguousPublicKeyPrefixError(ValueError):
    """Raised when a public key prefix matches multiple contacts."""

    def __init__(self, prefix: str, matches: list[str]):
        self.prefix = prefix.lower()
        self.matches = matches
        super().__init__(f"Ambiguous public key prefix '{self.prefix}'")


class ContactRepository:
    @staticmethod
    def _coerce_contact_upsert(
        contact: ContactUpsert | Contact | Mapping[str, Any],
    ) -> ContactUpsert:
        if isinstance(contact, ContactUpsert):
            return contact
        if isinstance(contact, Contact):
            return contact.to_upsert()
        return ContactUpsert.model_validate(contact)

    _UPSERT_SQL = """
                INSERT INTO contacts (radio_id, public_key, name, type, flags, direct_path, direct_path_len,
                                      direct_path_hash_mode, direct_path_updated_at,
                                      route_override_path, route_override_len,
                                      route_override_hash_mode,
                                      last_advert, lat, lon, last_seen,
                                      on_radio, last_contacted, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(radio_id, public_key) DO UPDATE SET
                    name = COALESCE(excluded.name, contacts.name),
                    type = CASE WHEN excluded.type = 0 THEN contacts.type ELSE excluded.type END,
                    flags = excluded.flags,
                    direct_path = COALESCE(excluded.direct_path, contacts.direct_path),
                    direct_path_len = COALESCE(excluded.direct_path_len, contacts.direct_path_len),
                    direct_path_hash_mode = COALESCE(
                        excluded.direct_path_hash_mode, contacts.direct_path_hash_mode
                    ),
                    direct_path_updated_at = COALESCE(
                        excluded.direct_path_updated_at, contacts.direct_path_updated_at
                    ),
                    route_override_path = COALESCE(
                        excluded.route_override_path, contacts.route_override_path
                    ),
                    route_override_len = COALESCE(
                        excluded.route_override_len, contacts.route_override_len
                    ),
                    route_override_hash_mode = COALESCE(
                        excluded.route_override_hash_mode, contacts.route_override_hash_mode
                    ),
                    last_advert = COALESCE(excluded.last_advert, contacts.last_advert),
                    lat = COALESCE(excluded.lat, contacts.lat),
                    lon = COALESCE(excluded.lon, contacts.lon),
                    last_seen = CASE
                        WHEN excluded.last_seen IS NULL THEN contacts.last_seen
                        WHEN contacts.last_seen IS NULL THEN excluded.last_seen
                        WHEN excluded.last_seen > contacts.last_seen THEN excluded.last_seen
                        ELSE contacts.last_seen
                    END,
                    on_radio = COALESCE(excluded.on_radio, contacts.on_radio),
                    last_contacted = COALESCE(excluded.last_contacted, contacts.last_contacted),
                    first_seen = COALESCE(contacts.first_seen, excluded.first_seen)
                """

    @staticmethod
    def _upsert_params(
        contact: ContactUpsert | Contact | Mapping[str, Any],
        radio_id: str = "default",
    ) -> tuple[str, str, tuple[Any, ...]]:
        contact_row = ContactRepository._coerce_contact_upsert(contact)
        effective_radio_id = (
            radio_id or getattr(contact_row, "radio_id", None) or "default"
        )
        if (
            contact_row.direct_path is None
            and contact_row.direct_path_len is None
            and contact_row.direct_path_hash_mode is None
        ):
            direct_path = None
            direct_path_len = None
            direct_path_hash_mode = None
        else:
            direct_path, direct_path_len, direct_path_hash_mode = normalize_contact_route(
                contact_row.direct_path,
                contact_row.direct_path_len,
                contact_row.direct_path_hash_mode,
            )
        route_override_path, route_override_len, route_override_hash_mode = (
            normalize_route_override(
                contact_row.route_override_path,
                contact_row.route_override_len,
                contact_row.route_override_hash_mode,
            )
        )
        public_key = contact_row.public_key.lower()
        return (
            effective_radio_id,
            public_key,
            (
                effective_radio_id,
                public_key,
                contact_row.name,
                contact_row.type,
                contact_row.flags,
                direct_path,
                direct_path_len,
                direct_path_hash_mode,
                contact_row.direct_path_updated_at,
                route_override_path,
                route_override_len,
                route_override_hash_mode,
                contact_row.last_advert,
                contact_row.lat,
                contact_row.lon,
                contact_row.last_seen,
                contact_row.on_radio,
                contact_row.last_contacted,
                contact_row.first_seen,
            ),
        )

    @staticmethod
    async def upsert(
        contact: ContactUpsert | Contact | Mapping[str, Any],
        radio_id: str = "default",
    ) -> None:
        _radio_id, _public_key, params = ContactRepository._upsert_params(
            contact, radio_id=radio_id
        )
        async with db.tx() as conn:
            async with conn.execute(ContactRepository._UPSERT_SQL, params):
                pass

    @staticmethod
    async def upsert_reporting_insert(
        contact: ContactUpsert | Contact | Mapping[str, Any],
        radio_id: str = "default",
    ) -> bool:
        """Upsert a contact and report whether the row was newly inserted.

        SELECT + INSERT share one ``db.tx()``. Do not call ``get_by_key()``
        here — the DB lock is not re-entrant. Returns True only when the
        public key did not already exist for this radio.
        """
        eff_radio_id, public_key, params = ContactRepository._upsert_params(
            contact, radio_id=radio_id
        )
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT 1 FROM contacts WHERE radio_id = ? AND public_key = ?",
                (eff_radio_id, public_key),
            ) as cursor:
                existed = await cursor.fetchone() is not None
            async with conn.execute(ContactRepository._UPSERT_SQL, params):
                pass
            return not existed

    @staticmethod
    def _row_to_contact(row) -> Contact:
        """Convert a database row to a Contact model."""
        available_columns = set(row.keys())
        direct_path, direct_path_len, direct_path_hash_mode = normalize_contact_route(
            row["direct_path"] if "direct_path" in available_columns else None,
            row["direct_path_len"] if "direct_path_len" in available_columns else None,
            row["direct_path_hash_mode"] if "direct_path_hash_mode" in available_columns else None,
        )
        route_override_path = (
            row["route_override_path"] if "route_override_path" in available_columns else None
        )
        route_override_len = (
            row["route_override_len"] if "route_override_len" in available_columns else None
        )
        route_override_hash_mode = (
            row["route_override_hash_mode"]
            if "route_override_hash_mode" in available_columns
            else None
        )
        route_override_path, route_override_len, route_override_hash_mode = (
            normalize_route_override(
                route_override_path,
                route_override_len,
                route_override_hash_mode,
            )
        )
        return Contact(
            radio_id=row["radio_id"] if "radio_id" in available_columns else "default",
            public_key=row["public_key"],
            name=row["name"],
            type=row["type"],
            flags=row["flags"],
            direct_path=direct_path,
            direct_path_len=direct_path_len,
            direct_path_hash_mode=direct_path_hash_mode,
            direct_path_updated_at=(
                row["direct_path_updated_at"]
                if "direct_path_updated_at" in available_columns
                else None
            ),
            route_override_path=route_override_path,
            route_override_len=route_override_len,
            route_override_hash_mode=route_override_hash_mode,
            last_advert=row["last_advert"],
            lat=row["lat"],
            lon=row["lon"],
            last_seen=row["last_seen"],
            on_radio=bool(row["on_radio"]),
            favorite=bool(row["favorite"]) if "favorite" in available_columns else False,
            pinned=bool(row["pinned"]) if "pinned" in available_columns else False,
            last_contacted=row["last_contacted"],
            last_read_at=row["last_read_at"],
            first_seen=row["first_seen"],
        )

    @staticmethod
    async def get_by_key(public_key: str, radio_id: str = "default") -> Contact | None:
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM contacts WHERE radio_id = ? AND public_key = ?",
                (eff_radio_id, public_key.lower()),
            ) as cursor:
                row = await cursor.fetchone()
        return ContactRepository._row_to_contact(row) if row else None

    # Alias for get_by_key
    get = get_by_key

    @staticmethod
    async def get_by_key_prefix(prefix: str, radio_id: str = "default") -> Contact | None:
        """Get a contact by key prefix only if it resolves uniquely.

        Returns None when no contacts match OR when multiple contacts match
        the prefix (to avoid silently selecting the wrong contact).
        """
        eff_radio_id = radio_id or "default"
        normalized_prefix = prefix.lower()
        exact = await ContactRepository.get_by_key(normalized_prefix, radio_id=eff_radio_id)
        if exact:
            return exact
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM contacts WHERE radio_id = ? AND public_key LIKE ? ORDER BY public_key LIMIT 2",
                (eff_radio_id, f"{normalized_prefix}%"),
            ) as cursor:
                rows = list(await cursor.fetchall())
        if len(rows) != 1:
            return None
        return ContactRepository._row_to_contact(rows[0])

    @staticmethod
    async def _get_prefix_matches(
        prefix: str, limit: int = 2, radio_id: str = "default"
    ) -> list[Contact]:
        """Get contacts matching a key prefix, up to limit."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM contacts WHERE radio_id = ? AND public_key LIKE ? ORDER BY public_key LIMIT ?",
                (eff_radio_id, f"{prefix.lower()}%", limit),
            ) as cursor:
                rows = list(await cursor.fetchall())
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_by_key_or_prefix(
        key_or_prefix: str, radio_id: str = "default"
    ) -> Contact | None:
        """Get a contact by exact key match, falling back to prefix match.

        Useful when the input might be a full 64-char public key or a shorter prefix.
        """
        eff_radio_id = radio_id or "default"
        contact = await ContactRepository.get_by_key(key_or_prefix, radio_id=eff_radio_id)
        if contact:
            return contact

        matches = await ContactRepository._get_prefix_matches(
            key_or_prefix, limit=2, radio_id=eff_radio_id
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AmbiguousPublicKeyPrefixError(
                key_or_prefix,
                [m.public_key for m in matches],
            )
        return None

    @staticmethod
    async def list_by_key_prefix(
        prefix: str, limit: int = 20, radio_id: str = "default"
    ) -> list[Contact]:
        """List contacts matching a public-key prefix (not hop prefixes)."""
        return await ContactRepository._get_prefix_matches(
            prefix, limit=limit, radio_id=radio_id
        )

    @staticmethod
    async def get_by_name(name: str, radio_id: str = "default") -> list[Contact]:
        """Get all contacts with the given exact name."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM contacts WHERE radio_id = ? AND name = ?",
                (eff_radio_id, name),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def resolve_prefixes(
        prefixes: list[str], radio_id: str = "default"
    ) -> dict[str, Contact]:
        """Resolve multiple key prefixes to contacts in a single query.

        Returns a dict mapping each prefix to its Contact, only for prefixes
        that resolve uniquely (exactly one match). Ambiguous or unmatched
        prefixes are omitted.
        """
        if not prefixes:
            return {}
        eff_radio_id = radio_id or "default"
        normalized = [p.lower() for p in prefixes]
        conditions = " OR ".join(["public_key LIKE ?"] * len(normalized))
        params = [eff_radio_id] + [f"{p}%" for p in normalized]
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT * FROM contacts WHERE radio_id = ? AND ({conditions})",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        # Group by which prefix each row matches
        prefix_to_rows: dict[str, list] = {p: [] for p in normalized}
        for row in rows:
            pk = row["public_key"]
            for p in normalized:
                if pk.startswith(p):
                    prefix_to_rows[p].append(row)
        # Only include uniquely-resolved prefixes
        result: dict[str, Contact] = {}
        for p in normalized:
            if len(prefix_to_rows[p]) == 1:
                result[p] = ContactRepository._row_to_contact(prefix_to_rows[p][0])
        return result

    @staticmethod
    async def list_stale_public_keys(cutoff: int, radio_id: str = "default") -> list[str]:
        """Public keys matching the bulk-delete last-heard-before filter.

        A contact is stale when ``COALESCE(last_seen, 0)`` and ``first_seen``
        are both at or before ``cutoff``. NULL ``first_seen`` excludes the row
        (NEW_CONTACT / radio sync / POST contact leave RF timestamps unset).
        Favorites are never returned — automatic purge must not remove them.
        """
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT public_key FROM contacts
                WHERE radio_id = ?
                  AND favorite = 0
                  AND COALESCE(last_seen, 0) <= ?
                  AND first_seen <= ?
                """,
                (eff_radio_id, cutoff, cutoff),
            ) as cursor:
                rows = await cursor.fetchall()
        return [row["public_key"] for row in rows]

    @staticmethod
    async def get_all(
        limit: int = 100, offset: int = 0, radio_id: str = "default"
    ) -> list[Contact]:
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM contacts WHERE radio_id = ? ORDER BY COALESCE(name, public_key) LIMIT ? OFFSET ?",
                (eff_radio_id, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def list_with_map_location(radio_id: str = "default") -> list[Contact]:
        """Contacts with a usable map pin (non-null, not the 0,0 sentinel)."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT * FROM contacts
                WHERE radio_id = ?
                  AND lat IS NOT NULL
                  AND lon IS NOT NULL
                  AND NOT (lat = 0 AND lon = 0)
                  AND length(public_key) = 64
                """,
                (eff_radio_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_repeaters_by_recent(
        limit: int = 8, radio_id: str = "default"
    ) -> list[Contact]:
        """Get repeater contacts ordered by most recently seen."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT * FROM contacts
                WHERE radio_id = ? AND type = 2 AND length(public_key) = 64
                ORDER BY COALESCE(last_seen, 0) DESC
                LIMIT ?
                """,
                (eff_radio_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_recently_contacted_non_repeaters(
        limit: int = 200, radio_id: str = "default"
    ) -> list[Contact]:
        """Get recently interacted-with non-repeater contacts."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT * FROM contacts
                WHERE radio_id = ? AND type != 2 AND last_contacted IS NOT NULL AND length(public_key) = 64
                ORDER BY last_contacted DESC
                LIMIT ?
                """,
                (eff_radio_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_recently_dm_active_non_repeaters(
        limit: int = 200, radio_id: str = "default"
    ) -> list[Contact]:
        """Get non-repeater contacts with the most recent DM activity (sent or received)."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT c.*
                FROM contacts c
                INNER JOIN (
                    SELECT radio_id, conversation_key, MAX(received_at) AS last_dm
                    FROM messages
                    WHERE radio_id = ? AND type = 'PRIV'
                    GROUP BY radio_id, conversation_key
                ) m ON c.radio_id = m.radio_id AND c.public_key = m.conversation_key
                WHERE c.radio_id = ? AND c.type != 2 AND length(c.public_key) = 64
                ORDER BY m.last_dm DESC
                LIMIT ?
                """,
                (eff_radio_id, eff_radio_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def get_recently_advertised_non_repeaters(
        limit: int = 200, radio_id: str = "default"
    ) -> list[Contact]:
        """Get recently advert-heard non-repeater contacts."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT * FROM contacts
                WHERE radio_id = ? AND type != 2 AND last_advert IS NOT NULL AND length(public_key) = 64
                ORDER BY last_advert DESC
                LIMIT ?
                """,
                (eff_radio_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def update_direct_path(
        public_key: str,
        path: str,
        path_len: int,
        path_hash_mode: int | None = None,
        updated_at: int | None = None,
        radio_id: str = "default",
    ) -> None:
        """Persist a learned direct route for a contact."""
        eff_radio_id = radio_id or "default"
        normalized_path, normalized_path_len, normalized_hash_mode = normalize_contact_route(
            path,
            path_len,
            path_hash_mode,
        )
        ts = updated_at if updated_at is not None else int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                """UPDATE contacts SET direct_path = ?, direct_path_len = ?,
                   direct_path_hash_mode = COALESCE(?, direct_path_hash_mode),
                   direct_path_updated_at = ?,
                   last_seen = CASE
                       WHEN last_seen IS NULL THEN ?
                       WHEN ? > last_seen THEN ?
                       ELSE last_seen
                   END
                   WHERE radio_id = ? AND public_key = ?""",
                (
                    normalized_path,
                    normalized_path_len,
                    normalized_hash_mode,
                    ts,
                    ts,
                    ts,
                    ts,
                    eff_radio_id,
                    public_key.lower(),
                ),
            ):
                pass

    @staticmethod
    async def set_routing_override(
        public_key: str,
        path: str | None,
        path_len: int | None,
        path_hash_mode: int | None = None,
        radio_id: str = "default",
    ) -> None:
        eff_radio_id = radio_id or "default"
        normalized_path, normalized_len, normalized_hash_mode = normalize_route_override(
            path,
            path_len,
            path_hash_mode,
        )
        async with db.tx() as conn:
            async with conn.execute(
                """
                UPDATE contacts
                SET route_override_path = ?, route_override_len = ?, route_override_hash_mode = ?
                WHERE radio_id = ? AND public_key = ?
                """,
                (
                    normalized_path,
                    normalized_len,
                    normalized_hash_mode,
                    eff_radio_id,
                    public_key.lower(),
                ),
            ):
                pass

    @staticmethod
    async def clear_routing_override(public_key: str, radio_id: str = "default") -> None:
        eff_radio_id = radio_id or "default"
        async with db.tx() as conn:
            async with conn.execute(
                """
                UPDATE contacts
                SET route_override_path = NULL,
                    route_override_len = NULL,
                    route_override_hash_mode = NULL
                WHERE radio_id = ? AND public_key = ?
                """,
                (eff_radio_id, public_key.lower()),
            ):
                pass

    @staticmethod
    async def clear_on_radio_except(keep_keys: list[str], radio_id: str = "default") -> None:
        """Set on_radio=False for all contacts NOT in keep_keys."""
        eff_radio_id = radio_id or "default"
        async with db.tx() as conn:
            if not keep_keys:
                async with conn.execute(
                    "UPDATE contacts SET on_radio = 0 WHERE radio_id = ? AND on_radio = 1",
                    (eff_radio_id,),
                ):
                    pass
            else:
                placeholders = ",".join("?" * len(keep_keys))
                async with conn.execute(
                    f"UPDATE contacts SET on_radio = 0 WHERE radio_id = ? AND on_radio = 1 AND public_key NOT IN ({placeholders})",
                    [eff_radio_id, *keep_keys],
                ):
                    pass

    @staticmethod
    async def get_favorites(radio_id: str = "default") -> list[Contact]:
        """Return all contacts marked as favorite."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM contacts WHERE radio_id = ? AND favorite = 1 AND LENGTH(public_key) = 64",
                (eff_radio_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]

    @staticmethod
    async def set_favorite(public_key: str, value: bool, radio_id: str = "default") -> None:
        """Set or clear the favorite flag for a contact."""
        eff_radio_id = radio_id or "default"
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE contacts SET favorite = ? WHERE radio_id = ? AND public_key = ?",
                (1 if value else 0, eff_radio_id, public_key.lower()),
            ):
                pass

    @staticmethod
    async def set_pinned(public_key: str, value: bool, radio_id: str = "default") -> None:
        """Set or clear the pinned flag for a contact."""
        eff_radio_id = radio_id or "default"
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE contacts SET pinned = ? WHERE radio_id = ? AND public_key = ?",
                (1 if value else 0, eff_radio_id, public_key.lower()),
            ):
                pass

    @staticmethod
    async def delete(public_key: str, radio_id: str = "default") -> None:
        eff_radio_id = radio_id or "default"
        normalized = public_key.lower()
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM contacts WHERE radio_id = ? AND public_key = ?",
                (eff_radio_id, normalized),
            ):
                pass

    @staticmethod
    async def update_last_contacted(
        public_key: str, timestamp: int | None = None, radio_id: str = "default"
    ) -> None:
        eff_radio_id = radio_id or "default"
        ts = timestamp if timestamp is not None else int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE contacts SET last_contacted = ? WHERE radio_id = ? AND public_key = ?",
                (ts, eff_radio_id, public_key.lower()),
            ):
                pass

    @staticmethod
    async def touch_last_seen(
        public_key: str, timestamp: int, radio_id: str = "default"
    ) -> None:
        eff_radio_id = radio_id or "default"
        async with db.tx() as conn:
            async with conn.execute(
                """
                UPDATE contacts
                SET last_seen = CASE
                    WHEN last_seen IS NULL THEN ?
                    WHEN ? > last_seen THEN ?
                    ELSE last_seen
                END
                WHERE radio_id = ? AND public_key = ?
                """,
                (timestamp, timestamp, timestamp, eff_radio_id, public_key.lower()),
            ):
                pass

    @staticmethod
    async def update_last_read_at(
        public_key: str, timestamp: int | None = None, radio_id: str = "default"
    ) -> bool:
        eff_radio_id = radio_id or "default"
        ts = timestamp if timestamp is not None else int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE contacts SET last_read_at = ? WHERE radio_id = ? AND public_key = ?",
                (ts, eff_radio_id, public_key.lower()),
            ) as cursor:
                rowcount = cursor.rowcount
        return rowcount > 0

    @staticmethod
    async def list_prefix_placeholder_keys(
        full_key: str, radio_id: str = "default"
    ) -> list[str]:
        eff_radio_id = radio_id or "default"
        normalized = full_key.lower()
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT public_key
                FROM contacts
                WHERE radio_id = ?
                  AND length(public_key) < 64
                  AND ? LIKE public_key || '%'
                ORDER BY length(public_key) DESC, public_key
                """,
                (eff_radio_id, normalized),
            ) as cursor:
                rows = list(await cursor.fetchall())
        return [row["public_key"] for row in rows]

    @staticmethod
    async def promote_prefix_placeholders(
        full_key: str, radio_id: str = "default"
    ) -> list[str]:
        eff_radio_id = radio_id or "default"

        async def migrate_child_rows(conn, old_key: str, new_key: str) -> None:
            async with conn.execute(
                """
                INSERT INTO contact_name_history (radio_id, public_key, name, first_seen, last_seen)
                SELECT radio_id, ?, name, first_seen, last_seen
                FROM contact_name_history
                WHERE radio_id = ? AND public_key = ?
                ON CONFLICT(radio_id, public_key, name) DO UPDATE SET
                    first_seen = MIN(contact_name_history.first_seen, excluded.first_seen),
                    last_seen = MAX(contact_name_history.last_seen, excluded.last_seen)
                """,
                (new_key, eff_radio_id, old_key),
            ):
                pass
            async with conn.execute(
                """
                INSERT INTO contact_advert_paths
                    (radio_id, public_key, path_hex, path_len, first_seen, last_seen, heard_count)
                SELECT radio_id, ?, path_hex, path_len, first_seen, last_seen, heard_count
                FROM contact_advert_paths
                WHERE radio_id = ? AND public_key = ?
                ON CONFLICT(radio_id, public_key, path_hex, path_len) DO UPDATE SET
                    first_seen = MIN(contact_advert_paths.first_seen, excluded.first_seen),
                    last_seen = MAX(contact_advert_paths.last_seen, excluded.last_seen),
                    heard_count = contact_advert_paths.heard_count + excluded.heard_count
                """,
                (new_key, eff_radio_id, old_key),
            ):
                pass
            async with conn.execute(
                "DELETE FROM contact_name_history WHERE radio_id = ? AND public_key = ?",
                (eff_radio_id, old_key),
            ):
                pass
            async with conn.execute(
                "DELETE FROM contact_advert_paths WHERE radio_id = ? AND public_key = ?",
                (eff_radio_id, old_key),
            ):
                pass

        normalized_full_key = full_key.lower()
        promoted_keys: list[str] = []
        async with db.tx() as conn:
            async with conn.execute(
                """
                SELECT public_key, last_seen, last_contacted, first_seen, last_read_at
                FROM contacts
                WHERE radio_id = ?
                  AND length(public_key) < 64
                  AND ? LIKE public_key || '%'
                ORDER BY length(public_key) DESC, public_key
                """,
                (eff_radio_id, normalized_full_key),
            ) as cursor:
                rows = list(await cursor.fetchall())
            if not rows:
                return []

            for row in rows:
                old_key = row["public_key"]
                if old_key == normalized_full_key:
                    continue

                async with conn.execute(
                    """
                    SELECT COUNT(*) AS match_count
                    FROM contacts
                    WHERE radio_id = ?
                      AND length(public_key) = 64
                      AND public_key LIKE ? || '%'
                    """,
                    (eff_radio_id, old_key),
                ) as match_cursor:
                    match_row = await match_cursor.fetchone()
                match_count = match_row["match_count"] if match_row is not None else 0
                if match_count != 1:
                    logger.warning(
                        "Skipping prefix promotion for %s: %d full-key contacts match (expected 1)",
                        old_key,
                        match_count,
                    )
                    continue

                await migrate_child_rows(conn, old_key, normalized_full_key)

                async with conn.execute(
                    """
                    UPDATE contacts
                    SET last_seen = CASE
                            WHEN contacts.last_seen IS NULL THEN ?
                            WHEN ? IS NULL THEN contacts.last_seen
                            WHEN ? > contacts.last_seen THEN ?
                            ELSE contacts.last_seen
                        END,
                        last_contacted = CASE
                            WHEN contacts.last_contacted IS NULL THEN ?
                            WHEN ? IS NULL THEN contacts.last_contacted
                            WHEN ? > contacts.last_contacted THEN ?
                            ELSE contacts.last_contacted
                        END,
                        first_seen = CASE
                            WHEN contacts.first_seen IS NULL THEN ?
                            WHEN ? IS NULL THEN contacts.first_seen
                            WHEN ? < contacts.first_seen THEN ?
                            ELSE contacts.first_seen
                        END,
                        last_read_at = CASE
                            WHEN contacts.last_read_at IS NULL THEN ?
                            WHEN ? IS NULL THEN contacts.last_read_at
                            WHEN ? > contacts.last_read_at THEN ?
                            ELSE contacts.last_read_at
                        END
                    WHERE radio_id = ? AND public_key = ?
                    """,
                    (
                        row["last_seen"],
                        row["last_seen"],
                        row["last_seen"],
                        row["last_seen"],
                        row["last_contacted"],
                        row["last_contacted"],
                        row["last_contacted"],
                        row["last_contacted"],
                        row["first_seen"],
                        row["first_seen"],
                        row["first_seen"],
                        row["first_seen"],
                        row["last_read_at"],
                        row["last_read_at"],
                        row["last_read_at"],
                        row["last_read_at"],
                        eff_radio_id,
                        normalized_full_key,
                    ),
                ):
                    pass
                async with conn.execute(
                    "DELETE FROM contacts WHERE radio_id = ? AND public_key = ?",
                    (eff_radio_id, old_key),
                ):
                    pass

                promoted_keys.append(old_key)

        return promoted_keys

    @staticmethod
    async def mark_all_read(timestamp: int, radio_id: str = "default") -> None:
        """Mark all contacts as read at the given timestamp."""
        eff_radio_id = radio_id or "default"
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE contacts SET last_read_at = ? WHERE radio_id = ?",
                (timestamp, eff_radio_id),
            ):
                pass

    @staticmethod
    async def get_by_pubkey_first_byte(
        hex_byte: str, radio_id: str = "default"
    ) -> list[Contact]:
        """Get contacts whose public key starts with the given hex byte (2 chars)."""
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM contacts WHERE radio_id = ? AND substr(public_key, 1, 2) = ?",
                (eff_radio_id, hex_byte.lower()),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactRepository._row_to_contact(row) for row in rows]


class ContactAdvertPathRepository:
    """Repository for recent unique advertisement paths per contact."""

    @staticmethod
    def _row_to_path(row) -> ContactAdvertPath:
        path = row["path_hex"] or ""
        path_len = row["path_len"]
        next_hop = first_hop_hex(path, path_len)
        return ContactAdvertPath(
            path=path,
            path_len=path_len,
            next_hop=next_hop,
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            heard_count=row["heard_count"],
        )

    @staticmethod
    async def record_observation(
        public_key: str,
        path_hex: str,
        timestamp: int,
        max_paths: int = 10,
        hop_count: int | None = None,
        radio_id: str = "default",
    ) -> None:
        """Upsert a unique advert path observation for a contact and prune to N most recent."""
        eff_radio_id = radio_id or "default"
        if max_paths < 1:
            max_paths = 1

        normalized_key = public_key.lower()
        normalized_path = path_hex.lower()
        path_len = hop_count if hop_count is not None else len(normalized_path) // 2

        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO contact_advert_paths
                    (radio_id, public_key, path_hex, path_len, first_seen, last_seen, heard_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(radio_id, public_key, path_hex, path_len) DO UPDATE SET
                    last_seen = MAX(contact_advert_paths.last_seen, excluded.last_seen),
                    heard_count = contact_advert_paths.heard_count + 1
                """,
                (
                    eff_radio_id,
                    normalized_key,
                    normalized_path,
                    path_len,
                    timestamp,
                    timestamp,
                ),
            ):
                pass

            # Keep only the N most recent unique paths per contact for this radio.
            async with conn.execute(
                """
                DELETE FROM contact_advert_paths
                WHERE radio_id = ? AND public_key = ?
                  AND id NOT IN (
                      SELECT id
                      FROM contact_advert_paths
                      WHERE radio_id = ? AND public_key = ?
                      ORDER BY last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
                      LIMIT ?
                  )
                """,
                (eff_radio_id, normalized_key, eff_radio_id, normalized_key, max_paths),
            ):
                pass

    @staticmethod
    async def get_recent_for_contact(
        public_key: str, limit: int = 10, radio_id: str = "default"
    ) -> list[ContactAdvertPath]:
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT path_hex, path_len, first_seen, last_seen, heard_count
                FROM contact_advert_paths
                WHERE radio_id = ? AND public_key = ?
                ORDER BY last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
                LIMIT ?
                """,
                (eff_radio_id, public_key.lower(), limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [ContactAdvertPathRepository._row_to_path(row) for row in rows]

    @staticmethod
    async def get_recent_for_all_contacts(
        limit_per_contact: int = 10, radio_id: str = "default"
    ) -> list[ContactAdvertPathSummary]:
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT public_key, path_hex, path_len, first_seen, last_seen, heard_count
                FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY radio_id, public_key
                               ORDER BY last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
                           ) AS rn
                    FROM contact_advert_paths
                    WHERE radio_id = ?
                )
                WHERE rn <= ?
                ORDER BY public_key ASC, last_seen DESC, heard_count DESC, path_len ASC, path_hex ASC
                """,
                (eff_radio_id, limit_per_contact),
            ) as cursor:
                rows = await cursor.fetchall()

        grouped: dict[str, list[ContactAdvertPath]] = {}
        for row in rows:
            key = row["public_key"]
            paths = grouped.get(key)
            if paths is None:
                paths = []
                grouped[key] = paths
            paths.append(ContactAdvertPathRepository._row_to_path(row))

        return [
            ContactAdvertPathSummary(public_key=key, paths=paths) for key, paths in grouped.items()
        ]


class ContactNameHistoryRepository:
    """Repository for contact name change history."""

    @staticmethod
    async def record_name(
        public_key: str, name: str, timestamp: int, radio_id: str = "default"
    ) -> None:
        """Record a name observation. Upserts: updates last_seen if name already known."""
        eff_radio_id = radio_id or "default"
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO contact_name_history (radio_id, public_key, name, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(radio_id, public_key, name) DO UPDATE SET
                    last_seen = MAX(contact_name_history.last_seen, excluded.last_seen)
                """,
                (eff_radio_id, public_key.lower(), name, timestamp, timestamp),
            ):
                pass

    @staticmethod
    async def get_history(
        public_key: str, radio_id: str = "default"
    ) -> list[ContactNameHistory]:
        eff_radio_id = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT name, first_seen, last_seen
                FROM contact_name_history
                WHERE radio_id = ? AND public_key = ?
                ORDER BY last_seen DESC
                """,
                (eff_radio_id, public_key.lower()),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            ContactNameHistory(
                name=row["name"], first_seen=row["first_seen"], last_seen=row["last_seen"]
            )
            for row in rows
        ]
