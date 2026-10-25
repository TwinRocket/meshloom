"""Migration 124: Slack presets keep one ``webhook`` secret instead of three parts (MSHL-365).

Builds of 5.0.0 before MSHL-365 stored a Slack channel as ``token_a``/``token_b`` (public
config) and ``token_c`` (secret). The preset now has a single write-only ``webhook`` field
(``https://hooks.slack.com/services/A/B/C``). A channel with all three valid parts is joined
into it; any other Slack row is left without the old keys, disabled and flagged
``secret_pending`` so the user types the webhook again (the old keys would be refused by the
new model).
"""

import json
import logging
import re
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_PART = re.compile(r"[A-Za-z0-9]{6,64}")
_OLD_KEYS = ("token_a", "token_b", "token_c")


def _loads(raw: object) -> dict[str, Any]:
    try:
        loaded = json.loads(raw) if isinstance(raw, str) and raw else {}
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


async def migrate(conn: aiosqlite.Connection) -> None:
    try:
        async with conn.execute(
            "SELECT id, config, secrets FROM notification_channels WHERE preset = 'slack'"
        ) as cursor:
            rows = await cursor.fetchall()
    except aiosqlite.OperationalError:  # no table yet (a database older than migration 113)
        return
    for channel_id, raw_config, raw_secrets in rows:
        config, secrets = _loads(raw_config), _loads(raw_secrets)
        if not any(key in config or key in secrets for key in _OLD_KEYS):
            continue
        parts = [
            str(config.get("token_a", "")),
            str(config.get("token_b", "")),
            str(secrets.get("token_c", "")),
        ]
        for key in _OLD_KEYS:
            config.pop(key, None)
            secrets.pop(key, None)
        if all(_PART.fullmatch(part) for part in parts):
            secrets["webhook"] = "https://hooks.slack.com/services/" + "/".join(parts)
            await conn.execute(
                "UPDATE notification_channels SET config = ?, secrets = ? WHERE id = ?",
                (
                    json.dumps(config, sort_keys=True),
                    json.dumps(secrets, sort_keys=True),
                    channel_id,
                ),
            )
            continue
        logger.warning("Slack channel %s had incomplete webhook parts: disabled", channel_id)
        await conn.execute(
            "UPDATE notification_channels SET config = ?, secrets = ?, enabled = 0 WHERE id = ?",
            (json.dumps(config, sort_keys=True), json.dumps(secrets, sort_keys=True), channel_id),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO notification_state (key, value, expires_at) VALUES (?, ?, NULL)",
            (f"secret_pending:{channel_id}", json.dumps(["webhook"])),
        )
