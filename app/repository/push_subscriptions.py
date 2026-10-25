"""Repository for push_subscriptions table.

Every subscription belongs to an account (``user_id``, migration 116, MSHL-290): the routes
list and manage the caller's own devices, and a push reaches a device only while its owner
is enabled and still holds ``send`` (``deliverable``). An unowned row (subscribed before the
first account existed, or restored from a backup, which carries no owner) goes to the first
administrator as soon as one exists (``_adopt_unowned``).
"""

import logging
import time
import uuid
from typing import Any

from app.database import db
from app.migrations._116_push_subscription_owner import FIRST_ADMIN_SQL

logger = logging.getLogger(__name__)

# Auto-delete subscriptions that have failed this many times consecutively
# without any successful delivery in between.
MAX_CONSECUTIVE_FAILURES = 15

_PUSH_LANGUAGES = frozenset({"fr", "en"})


def _normalize_language(language: str | None) -> str:
    if language in _PUSH_LANGUAGES:
        return language
    return "fr"


def _row_to_dict(row: Any) -> dict[str, Any]:
    keys = row.keys() if hasattr(row, "keys") else []
    raw_language = row["language"] if "language" in keys else None
    return {
        "id": row["id"],
        "endpoint": row["endpoint"],
        "p256dh": row["p256dh"],
        "auth": row["auth"],
        "label": row["label"] or "",
        "language": _normalize_language(raw_language),
        "created_at": row["created_at"] or 0,
        "last_success_at": row["last_success_at"],
        "failure_count": row["failure_count"] or 0,
        "user_id": row["user_id"] if "user_id" in keys else None,
    }


# The owner's username and state come with the row: ``owner_username`` for the admin's
# listing, ``owner_role`` and ``owner_disabled`` for ``deliverable``.
_SELECT_WITH_OWNER = """
    SELECT ps.*, u.username AS owner_username, u.role AS owner_role,
           u.disabled AS owner_disabled
    FROM push_subscriptions ps LEFT JOIN users u ON u.id = ps.user_id
"""


def _owned_row_to_dict(row: Any) -> dict[str, Any]:
    out = _row_to_dict(row)
    out["owner_username"] = row["owner_username"]
    out["owner_role"] = row["owner_role"]
    out["owner_disabled"] = bool(row["owner_disabled"])
    return out


async def _adopt_unowned() -> int:
    """Give unowned subscriptions to the first administrator (none yet: left as they are)."""
    async with db.tx() as conn:
        async with conn.execute(FIRST_ADMIN_SQL) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return 0
        async with conn.execute(
            "UPDATE push_subscriptions SET user_id = ? WHERE user_id IS NULL", (row[0],)
        ) as cursor:
            adopted = cursor.rowcount
    if adopted:
        logger.info("%d unowned push subscription(s) given to the first administrator", adopted)
    return adopted


async def _fetch_with_owner(where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    async with db.readonly() as conn:
        async with conn.execute(
            f"{_SELECT_WITH_OWNER} {where} ORDER BY ps.created_at DESC", params
        ) as cursor:
            rows = await cursor.fetchall()
    return [_owned_row_to_dict(row) for row in rows]


class PushSubscriptionRepository:
    @staticmethod
    async def create(
        endpoint: str,
        p256dh: str,
        auth: str,
        label: str = "",
        language: str = "fr",
        user_id: int | None = None,
    ) -> dict[str, Any]:
        """Create or upsert a push subscription (keyed by endpoint).

        The endpoint is the browser: the account that subscribes it last owns it (a shared
        browser moves to the account signed in). ``user_id=None`` (a backup restore) keeps
        the current owner of an existing row, or goes to the first administrator.
        """
        sub_id = str(uuid.uuid4())
        now = int(time.time())
        language = _normalize_language(language)

        async with db.tx() as conn:
            if user_id is None:
                async with conn.execute(FIRST_ADMIN_SQL) as cursor:
                    admin = await cursor.fetchone()
                owner = admin[0] if admin else None
            else:
                owner = user_id
            await conn.execute(
                """
                INSERT INTO push_subscriptions
                    (id, endpoint, p256dh, auth, label, language, created_at, failure_count,
                     user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    p256dh = excluded.p256dh,
                    auth = excluded.auth,
                    label = CASE WHEN excluded.label != '' THEN excluded.label
                                 ELSE push_subscriptions.label END,
                    language = excluded.language,
                    failure_count = 0,
                    user_id = CASE WHEN ? THEN excluded.user_id
                                   ELSE COALESCE(push_subscriptions.user_id, excluded.user_id)
                              END
                """,
                (sub_id, endpoint, p256dh, auth, label, language, now, owner, user_id is not None),
            )
            async with conn.execute(
                "SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ) as cursor:
                row = await cursor.fetchone()

        return _row_to_dict(row) if row else {"id": sub_id}  # type: ignore[arg-type]

    @staticmethod
    async def get(subscription_id: str) -> dict[str, Any] | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM push_subscriptions WHERE id = ?", (subscription_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    async def list_for_user(user_id: int) -> list[dict[str, Any]]:
        """The devices of one account (unowned rows adopted first, see ``_adopt_unowned``)."""
        await _adopt_unowned()
        return await _fetch_with_owner("WHERE ps.user_id = ?", (user_id,))

    @staticmethod
    async def list_with_owner() -> list[dict[str, Any]]:
        """Every device with its owner's username (the administrator's listing)."""
        await _adopt_unowned()
        return await _fetch_with_owner()

    @staticmethod
    async def deliverable() -> list[dict[str, Any]]:
        """The devices a push may reach: the owner exists, is enabled and holds ``send``.

        A disabled or demoted owner's devices are kept (they come back with the account)
        but skipped. Every role holding ``send`` holds ``read_private`` too, so a direct
        message never reaches an account that could not read it in the app.
        """
        from app.auth.permissions import ROLE_PERMISSIONS

        subs = await _fetch_with_owner()
        if any(sub["user_id"] is None for sub in subs) and await _adopt_unowned():
            subs = await _fetch_with_owner()
        return [
            sub
            for sub in subs
            if sub["user_id"] is not None
            and not sub["owner_disabled"]
            and "send" in ROLE_PERMISSIONS.get(sub["owner_role"], frozenset())
        ]

    @staticmethod
    async def get_all() -> list[dict[str, Any]]:
        """Every subscription, whoever owns it (backup export, language vote)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM push_subscriptions ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_dict(row) for row in rows]

    @staticmethod
    async def update(subscription_id: str, **fields: Any) -> dict[str, Any] | None:
        updates: list[str] = []
        params: list[Any] = []

        if "label" in fields:
            updates.append("label = ?")
            params.append(fields["label"])

        if "language" in fields:
            updates.append("language = ?")
            params.append(_normalize_language(fields["language"]))

        if not updates:
            return await PushSubscriptionRepository.get(subscription_id)

        params.append(subscription_id)
        async with db.tx() as conn:
            await conn.execute(
                f"UPDATE push_subscriptions SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            async with conn.execute(
                "SELECT * FROM push_subscriptions WHERE id = ?", (subscription_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_dict(row) if row else None

    @staticmethod
    async def delete(subscription_id: str) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,)
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def batch_record_outcomes(
        success_ids: list[str], failure_ids: list[str], remove_ids: list[str]
    ) -> None:
        """Batch-update delivery outcomes in a single transaction."""
        now = int(time.time())
        async with db.tx() as conn:
            if remove_ids:
                placeholders = ",".join("?" for _ in remove_ids)
                await conn.execute(
                    f"DELETE FROM push_subscriptions WHERE id IN ({placeholders})",
                    remove_ids,
                )
            if success_ids:
                placeholders = ",".join("?" for _ in success_ids)
                await conn.execute(
                    f"UPDATE push_subscriptions SET last_success_at = ?, failure_count = 0 "
                    f"WHERE id IN ({placeholders})",
                    [now, *success_ids],
                )
            if failure_ids:
                placeholders = ",".join("?" for _ in failure_ids)
                await conn.execute(
                    f"UPDATE push_subscriptions SET failure_count = failure_count + 1 "
                    f"WHERE id IN ({placeholders})",
                    failure_ids,
                )
            # Evict subscriptions that have exceeded the failure threshold
            await conn.execute(
                "DELETE FROM push_subscriptions WHERE failure_count >= ?",
                (MAX_CONSECUTIVE_FAILURES,),
            )
