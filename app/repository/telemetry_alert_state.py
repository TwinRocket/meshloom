import logging
from typing import Any

from app.database import db

logger = logging.getLogger(__name__)


class TelemetryAlertStateRepository:
    """Persisted per-node telemetry alert latch (survives restart)."""

    @staticmethod
    async def get(
        public_key: str, rule_id: str, radio_id: str = "default"
    ) -> dict[str, Any] | None:
        eff_radio = radio_id or "default"
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT radio_id, public_key, rule_id, consecutive_misses, last_fired_at, last_value
                FROM telemetry_alert_state
                WHERE radio_id = ? AND public_key = ? AND rule_id = ?
                """,
                (eff_radio, public_key, rule_id),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "radio_id": row["radio_id"],
            "public_key": row["public_key"],
            "rule_id": row["rule_id"],
            "consecutive_misses": int(row["consecutive_misses"] or 0),
            "last_fired_at": row["last_fired_at"],
            "last_value": row["last_value"],
        }

    @staticmethod
    async def upsert(
        public_key: str,
        rule_id: str,
        *,
        consecutive_misses: int = 0,
        last_fired_at: int | None = None,
        last_value: float | None = None,
        radio_id: str = "default",
    ) -> None:
        eff_radio = radio_id or "default"
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO telemetry_alert_state
                    (radio_id, public_key, rule_id, consecutive_misses, last_fired_at, last_value)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(radio_id, public_key, rule_id) DO UPDATE SET
                    consecutive_misses = excluded.consecutive_misses,
                    last_fired_at = excluded.last_fired_at,
                    last_value = excluded.last_value
                """,
                (eff_radio, public_key, rule_id, consecutive_misses, last_fired_at, last_value),
            ):
                pass

    @staticmethod
    async def list_latched(
        public_keys: list[str], radio_id: str = "default"
    ) -> list[dict[str, Any]]:
        """Current latches (last_fired_at set) for the given still-tracked keys."""
        if not public_keys:
            return []
        eff_radio = radio_id or "default"
        placeholders = ",".join("?" * len(public_keys))
        async with db.readonly() as conn:
            async with conn.execute(
                f"""
                SELECT radio_id, public_key, rule_id, consecutive_misses, last_fired_at, last_value
                FROM telemetry_alert_state
                WHERE radio_id = ? AND last_fired_at IS NOT NULL AND public_key IN ({placeholders})
                ORDER BY public_key, rule_id
                """,
                [eff_radio, *public_keys],
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "radio_id": row["radio_id"],
                "public_key": row["public_key"],
                "rule_id": row["rule_id"],
                "consecutive_misses": int(row["consecutive_misses"] or 0),
                "last_fired_at": row["last_fired_at"],
                "last_value": row["last_value"],
            }
            for row in rows
        ]
