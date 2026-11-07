"""Legacy bots as local plugins (MSHL-192, the 5.0.0 switch). To remove in 5.2.0.

4.18.x ran each ``fanout_configs`` row of type ``bot`` with ``exec()`` inside the
app. 5.0.0 retires that runtime: migration 122 (``app/migrations/_122_bots_to_plugins.py``)
turns every bot into a ``local`` plugin, and a bot entry in a backup file is turned
into one the same way (``app/services/backup.py``). A converted bot:

- keeps its code as is in ``plugins.source``; the host writes it out as ``bot.py``
  next to a fixed ``compat.py`` wrapper (``meshloom_plugin.compat.legacy_bot``), the
  manifest entry being ``compat.py:Bot`` (``PluginManager.code_dir``). It runs in the
  plugin sandbox like any plugin: own process, permissions, quotas, global pause
  and ``MESHCORE_DISABLE_PLUGINS`` lock;
- is granted what a bot could do: read every message (channels, direct, outgoing)
  and reply where the message came from;
- never starts before an admin accepts that grant: status ``needs_attention``,
  attention ``review_permissions`` (``restored`` for a backup entry), acknowledged
  like any other attention (``_status_block`` in ``app/routers/plugins.py``). The
  attention also carries the static scan of the source (``app/plugins/attention.py``).

The originals stay in ``fanout_configs_bots_archive`` (never exported, never shown in
``/api/debug``) until its purge one release later. The archive rows also drive the
"N bots converted" notice of ``/api/health`` (``converted_bots``) until an admin
acknowledges it (``POST /api/plugins/converted-bots/acknowledge``).
"""

from __future__ import annotations

import ast
import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

ARCHIVE_TABLE = "fanout_configs_bots_archive"

COMPAT_ENTRY = "compat.py:Bot"
COMPAT_SOURCE_FILE = "bot.py"
# No pathlib/os here: the plugin sandbox only allows its import allowlist, and reads
# need an absolute path (``__file__`` is one, the cwd is the private work dir).
COMPAT_WRAPPER = '''"""Runs a 4.18.x bot (bot.py) as a plugin. Written by Meshloom: edit bot.py instead."""

from meshloom_plugin.compat import legacy_bot

with open(__file__.rsplit("/", 1)[0] + "/bot.py", encoding="utf-8") as _source:
    Bot = legacy_bot(_source.read())
'''

CONVERTED_VERSION = "4.18.0"
CONVERTED_HOOKS = ["message"]
# What a 4.18.x bot could do: see every message and answer in the same conversation.
CONVERTED_PERMISSIONS: dict[str, Any] = {
    "messages.read": {"channels": "all", "direct": True, "outgoing": True},
    "messages.send": {"targets": "reply"},
}

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})


def env_true(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


def is_compat_manifest(manifest: dict[str, Any] | None) -> bool:
    return isinstance(manifest, dict) and manifest.get("entry") == COMPAT_ENTRY


def _permissions() -> dict[str, Any]:
    return json.loads(json.dumps(CONVERTED_PERMISSIONS))


def converted_manifest(plugin_id: str, label: str) -> dict[str, Any]:
    """The manifest of a converted bot, as stored (``parse_manifest(origin="local")``
    accepts it; the tests check that)."""
    name = (label or "Bot").strip()[:60] or "Bot"
    return {
        "manifest": 1,
        "id": plugin_id,
        "version": CONVERTED_VERSION,
        "name": {"en": name},
        "summary": {"en": "Bot converted from Meshloom 4.18"},
        "authors": [{"name": "Local"}],
        "license": "MIT",
        "categories": ["utility"],
        "runtime": {"type": "python", "api": 1, "python": ">=3.11"},
        "compat": {"meshloom": ">=5.0.0"},
        "entry": COMPAT_ENTRY,
        "hooks": list(CONVERTED_HOOKS),
        "permissions": _permissions(),
        "privacy": {"sends_to_third_parties": [], "stores_message_content": False},
        "readme": {"en": "README.md"},
    }


def converted_scope(fanout_scope: Any) -> dict[str, Any]:
    """The bot's message filter as a plugin scope (a 4.18 bot always had ``messages: all``)."""
    messages = fanout_scope.get("messages") if isinstance(fanout_scope, dict) else None
    if messages in (None, "all"):
        return {}
    if messages == "none":
        return {"messages": "none"}
    if isinstance(messages, dict) and messages and set(messages) <= {"channels", "contacts"}:
        return {"messages": messages}
    return {}


def scan(source: str) -> dict[str, Any]:
    """The static scan of the bot's code, in the attention JSON shape. Never raises."""
    from app.plugins.attention import scan_source

    try:
        return scan_source(source).to_json()
    except Exception:  # noqa: BLE001 - a scan bug must never block the conversion
        logger.exception("Attention scan of a converted bot failed")
        return {"reasons": [], "http_hosts": [], "truncated": False}


def valid_source(source: Any) -> bool:
    """A non-empty body that parses (a backup entry failing this is dropped, like any
    local plugin with a broken body; the migration keeps it for the user to fix)."""
    if not isinstance(source, str) or not source.strip():
        return False
    try:
        ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    return True


def converted_row(
    *,
    label: str,
    source: str,
    enabled: bool,
    fanout_scope: Any,
    restored: bool = False,
) -> dict[str, Any]:
    """The ``plugins`` row values of one converted bot (shared by migration 122 and the
    backup restore). It never runs without review: ``needs_attention``."""
    plugin_id = f"local.{uuid.uuid4()}"
    attention: dict[str, Any] = {
        "type": "restored" if restored else "review_permissions",
        "converted_bot": True,
        **scan(source),
    }
    if restored:
        attention.update(review_permissions=True, was_enabled=bool(enabled), secrets_missing=[])
    return {
        "instance_id": str(uuid.uuid4()),
        "plugin_id": plugin_id,
        "label": (label or "Bot")[:100],
        "kind": "local",
        "version": CONVERTED_VERSION,
        "enabled": bool(enabled) and not restored,
        "manifest": converted_manifest(plugin_id, label),
        "scope": converted_scope(fanout_scope),
        "granted_permissions": _permissions(),
        "source": source,
        "status": "needs_attention",
        "attention": attention,
    }


async def insert_converted(conn: Any, row: dict[str, Any]) -> None:
    """Insert one converted row with a raw connection (inside the caller's transaction)."""
    now = int(time.time())
    async with conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM plugins") as cursor:
        found = await cursor.fetchone()
    await conn.execute(
        """
        INSERT INTO plugins (
            instance_id, plugin_id, label, kind, version, enabled, manifest,
            config_schema, config, scope, granted_permissions, quotas, auto_update,
            source, archive_sha256, repository, installed_at, updated_at, status,
            last_error, attention, sort_order
        ) VALUES (?, ?, ?, 'local', ?, ?, ?, '{}', '{}', ?, ?, '{}', 0, ?, NULL, NULL, ?, ?,
                  ?, NULL, ?, ?)
        """,
        (
            row["instance_id"],
            row["plugin_id"],
            row["label"],
            row["version"],
            1 if row["enabled"] else 0,
            json.dumps(row["manifest"]),
            json.dumps(row["scope"]),
            json.dumps(row["granted_permissions"]),
            row["source"],
            now,
            now,
            row["status"],
            json.dumps(row["attention"]),
            found[0] if found else 0,
        ),
    )


# --- the notice ------------------------------------------------------------------


async def notice() -> dict[str, int] | None:
    """``{"converted": N, "attention": X}`` while the conversion notice is not
    acknowledged, else None. X counts the converted instances still waiting for review."""
    from app.database import db

    try:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT plugin_instance_id FROM {ARCHIVE_TABLE} WHERE notice_acknowledged = 0"
            ) as cursor:
                ids = [row[0] for row in await cursor.fetchall()]
            if not ids:
                return None
            marks = ",".join("?" for _ in ids)
            async with conn.execute(
                f"SELECT COUNT(*) FROM plugins WHERE instance_id IN ({marks})"
                " AND status = 'needs_attention'",
                ids,
            ) as cursor:
                row = await cursor.fetchone()
    except Exception:  # noqa: BLE001 - health must answer even on a damaged table
        logger.debug("Converted bots notice unreadable", exc_info=True)
        return None
    return {"converted": len(ids), "attention": int(row[0]) if row else 0}


async def acknowledge() -> int:
    """Clear the notice; returns how many conversions it covered."""
    from app.database import db

    async with db.tx() as conn:
        cursor = await conn.execute(
            f"UPDATE {ARCHIVE_TABLE} SET notice_acknowledged = 1 WHERE notice_acknowledged = 0"
        )
        return cursor.rowcount or 0


async def is_archived_bot(config_id: str) -> bool:
    """Whether ``config_id`` was a bot converted by migration 122 (PATCH answers 410)."""
    from app.database import db

    try:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT 1 FROM {ARCHIVE_TABLE} WHERE id = ?", (config_id,)
            ) as cursor:
                return await cursor.fetchone() is not None
    except Exception:  # noqa: BLE001 - a missing table means nothing was archived
        return False
