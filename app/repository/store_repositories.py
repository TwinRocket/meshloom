"""Repository for the custom Store repositories (``store_repositories``, migration 126,
MSHL-196). Rules and the GitHub lookup: ``app/plugins/custom_repos.py``.

One row per GitHub repository (PK: lowercase ``owner/name``) with the last good release
read from it. A failed check sets ``last_error`` and keeps that release. Removing a row
never touches installed instances.
"""

from __future__ import annotations

import json
from typing import Any

from app.database import db


def _view(row: Any) -> dict[str, Any]:
    return {
        "repository": row["repository"],
        "display_name": row["display_name"],
        "added_at": row["added_at"],
        "checked_at": row["checked_at"],
        "last_error": row["last_error"],
        "tag": row["tag"],
        "plugin_id": row["plugin_id"],
        "version": row["version"],
        "manifest": json.loads(row["manifest"]) if row["manifest"] else None,
        "archive_sha256": row["archive_sha256"],
        "archive_size": row["archive_size"],
    }


class StoreRepositoriesRepository:
    @staticmethod
    async def list() -> list[dict[str, Any]]:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM store_repositories ORDER BY repository"
            ) as cursor:
                return [_view(r) for r in await cursor.fetchall()]

    @staticmethod
    async def get(key: str) -> dict[str, Any] | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM store_repositories WHERE repository = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
        return _view(row) if row is not None else None

    @staticmethod
    async def insert(key: str, display_name: str, added_at: int) -> bool:
        """False when the repository is already there."""
        async with db.tx() as conn:
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO store_repositories (repository, display_name, added_at)"
                " VALUES (?, ?, ?)",
                (key, display_name, added_at),
            )
            return cursor.rowcount > 0

    @staticmethod
    async def save_release(
        key: str,
        *,
        display_name: str,
        checked_at: int,
        tag: str,
        entry: dict[str, Any],
        archive_sha256: str,
        archive_size: int | None,
    ) -> None:
        """Record a good read. ``archive_size`` is only known when the archive was
        downloaded; None keeps the old size when the sha256 is unchanged."""
        async with db.tx() as conn:
            await conn.execute(
                """
                UPDATE store_repositories SET display_name = ?, checked_at = ?,
                    last_error = NULL, tag = ?, plugin_id = ?, version = ?, manifest = ?,
                    archive_size = COALESCE(?, CASE WHEN archive_sha256 = ? THEN archive_size END),
                    archive_sha256 = ?
                WHERE repository = ?
                """,
                (
                    display_name,
                    checked_at,
                    tag,
                    entry["id"],
                    entry["version"],
                    json.dumps(entry, sort_keys=True),
                    archive_size,
                    archive_sha256,
                    archive_sha256,
                    key,
                ),
            )

    @staticmethod
    async def save_check(key: str, checked_at: int, error: str | None) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE store_repositories SET checked_at = ?, last_error = ? WHERE repository = ?",
                (checked_at, error, key),
            )

    @staticmethod
    async def delete(key: str) -> bool:
        async with db.tx() as conn:
            cursor = await conn.execute(
                "DELETE FROM store_repositories WHERE repository = ?", (key,)
            )
            return cursor.rowcount > 0
