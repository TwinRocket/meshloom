"""Repository for ``user_notification_rules``: what each account changed for its own devices.

A row is one account's override document (``app/notifications/user_rules.py``): events and
contact types forced on or off for its Web Push devices. No row means "follow the admin
defaults" (``app_settings.notification_rules``). The rows are read on every notification, so
``all_overrides()`` keeps them in memory (one process owns the database, as for the channel
cache); every write and ``invalidate_cache()`` drop that copy.
"""

import json
import logging
import time
from typing import Any

from app.database import db
from app.notifications.user_rules import sanitize_override

logger = logging.getLogger(__name__)

_cache: dict[int, dict[str, Any]] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


class UserNotificationRulesRepository:
    @staticmethod
    async def get(user_id: int) -> dict[str, Any]:
        """One account's override (``{}`` when it follows the defaults)."""
        return (await UserNotificationRulesRepository.all_overrides()).get(user_id, {})

    @staticmethod
    async def all_overrides() -> dict[int, dict[str, Any]]:
        """Every non-empty override by account id (cached; never raises on a bad row)."""
        global _cache
        if _cache is not None:
            return _cache
        async with db.readonly() as conn:
            async with conn.execute("SELECT user_id, rules FROM user_notification_rules") as cur:
                rows = await cur.fetchall()
        loaded: dict[int, dict[str, Any]] = {}
        for row in rows:
            try:
                raw = json.loads(row[1]) if row[1] else {}
            except json.JSONDecodeError:
                continue
            override = sanitize_override(raw)
            if override:
                loaded[int(row[0])] = override
        _cache = loaded
        return loaded

    @staticmethod
    async def set(user_id: int, override: dict[str, Any]) -> None:
        """Replace an account's override; an empty one deletes the row."""
        clean = sanitize_override(override)
        async with db.tx() as conn:
            await UserNotificationRulesRepository.write(conn, user_id, clean)
        invalidate_cache()

    @staticmethod
    async def write(conn: Any, user_id: int, override: dict[str, Any]) -> None:
        """``set`` inside the caller's transaction (the backup restore)."""
        if not override:
            await conn.execute("DELETE FROM user_notification_rules WHERE user_id = ?", (user_id,))
            return
        await conn.execute(
            "INSERT INTO user_notification_rules (user_id, rules, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET rules = excluded.rules,"
            " updated_at = excluded.updated_at",
            (user_id, json.dumps(override, sort_keys=True), int(time.time())),
        )
