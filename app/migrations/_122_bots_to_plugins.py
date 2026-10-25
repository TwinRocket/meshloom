"""Migration 122: legacy bots become local plugins (MSHL-192, the 5.0.0 switch).

Each ``fanout_configs`` row of type ``bot`` becomes a ``plugins`` row (kind ``local``,
``plugin_id`` ``local.<uuid4>``, the bot's code as ``source``, its name as label, its
``enabled`` flag and message scope), built by ``app/plugins/bot_conversion.py``: a
manifest with the ``compat.py:Bot`` entry, the grant of a 4.18 bot (read every
message, reply in the same conversation), status ``needs_attention`` with attention
``review_permissions`` plus the static scan of the source. Nothing runs before an
admin accepts that grant.

When the retired bot lock (``legacy_env_settings.ENV_BOT_LOCK``) is true in the
environment (read here, once, never a setting), every converted instance is stored
disabled: the old lock is honoured.

The original rows are copied into ``fanout_configs_bots_archive`` (never exported,
never shown in ``/api/debug``; purged one release later), with the instance they
became and the notice flag behind ``/api/health`` ``converted_bots``, then deleted
from ``fanout_configs``. One transaction (the runner's): all or nothing.
"""

import json
import logging
import os
import time

import aiosqlite

from app.plugins import bot_conversion
from app.services.legacy_env_settings import ENV_BOT_LOCK

logger = logging.getLogger(__name__)

# Kept textually identical to SCHEMA_TABLES in app/database.py:
# tests/test_migrations/test_122_bots_to_plugins.py compares the two schemas.
_STATEMENT = """CREATE TABLE IF NOT EXISTS fanout_configs_bots_archive (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER DEFAULT 0,
    config TEXT NOT NULL DEFAULT '{}',
    scope TEXT NOT NULL DEFAULT '{}',
    sort_order INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    plugin_instance_id TEXT NOT NULL,
    archived_at INTEGER NOT NULL,
    notice_acknowledged INTEGER NOT NULL DEFAULT 0
)"""


async def _table_exists(conn: aiosqlite.Connection, name: str) -> bool:
    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ) as cursor:
        return await cursor.fetchone() is not None


def _json(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.execute(_STATEMENT)
    if not (await _table_exists(conn, "fanout_configs") and await _table_exists(conn, "plugins")):
        await conn.commit()
        return
    async with conn.execute(
        "SELECT id, name, enabled, config, scope, sort_order, created_at"
        " FROM fanout_configs WHERE type = 'bot' ORDER BY sort_order, created_at, id"
    ) as cursor:
        bots = await cursor.fetchall()
    locked = bot_conversion.env_true(os.environ.get(ENV_BOT_LOCK))
    now = int(time.time())
    for config_id, name, enabled, config, scope, sort_order, created_at in bots:
        code = _json(config).get("code")
        row = bot_conversion.converted_row(
            label=name or "Bot",
            source=code if isinstance(code, str) else "",
            enabled=bool(enabled) and not locked,
            fanout_scope=_json(scope),
        )
        await bot_conversion.insert_converted(conn, row)
        await conn.execute(
            "INSERT INTO fanout_configs_bots_archive (id, name, enabled, config, scope,"
            " sort_order, created_at, plugin_instance_id, archived_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                config_id,
                name or "",
                enabled or 0,
                config or "{}",
                scope or "{}",
                sort_order or 0,
                created_at or 0,
                row["instance_id"],
                now,
            ),
        )
    await conn.execute("DELETE FROM fanout_configs WHERE type = 'bot'")
    if bots:
        logger.warning(
            "%d bot(s) converted to extensions; each needs a review in Settings > Extensions"
            " before it starts%s",
            len(bots),
            f" ({ENV_BOT_LOCK} was set: all stored disabled)" if locked else "",
        )
    await conn.commit()
