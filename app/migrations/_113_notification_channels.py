import json
import logging
import time
from collections.abc import Mapping
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# DDL kept textually identical to ``SCHEMA_TABLES``/``SCHEMA_INDEXES`` in
# app/database.py (tests/test_migrations/test_migration_113.py compares them).
CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS notification_channels (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    preset TEXT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    language TEXT NOT NULL DEFAULT 'auto',
    config TEXT NOT NULL DEFAULT '{}',
    secrets TEXT NOT NULL DEFAULT '{}',
    allow_private_network INTEGER NOT NULL DEFAULT 0,
    include_message_text TEXT NOT NULL DEFAULT 'always',
    verify_tls INTEGER NOT NULL DEFAULT 1,
    quiet_hours_exempt INTEGER NOT NULL DEFAULT 0,
    rate_per_min INTEGER,
    rate_per_hour INTEGER,
    rate_per_day INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    last_ok_at INTEGER,
    last_error TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    paused_until INTEGER
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    event TEXT NOT NULL,
    dedup_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_counters (
    channel_id TEXT NOT NULL,
    hour INTEGER NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (channel_id, hour)
);

CREATE TABLE IF NOT EXISTS notification_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at INTEGER
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_channel
    ON notification_deliveries(channel_id, created_at);
"""

# Legacy medium key -> the channel it becomes.
MEDIUM_CHANNEL = {
    "push": "builtin:push",
    "email": "legacy:email",
    "webhook": "legacy:webhook",
    "apprise": "legacy:apprise",
}
MEDIA = tuple(MEDIUM_CHANNEL)

# Legacy ``push_defaults`` key -> new event id (``contact.first_seen`` is built apart).
EVENT_OF_KEY = {
    "new_dm": "message.dm",
    "channel_found": "channel.found",
    "telemetry_alert": "telemetry.alert",
    "oss_update": "app.update",
    "radio_offline": "radio.offline",
    "radio_online": "radio.online",
    "app_started": "app.started",
}
ALL_DEFAULT_KEYS = (
    "new_contact",
    "new_dm",
    "advert_repeater",
    "advert_companion",
    "advert_sensor",
    "channel_found",
    "telemetry_alert",
    "oss_update",
    "radio_offline",
    "radio_online",
    "app_started",
)
ALWAYS_DELIVER = {"radio.offline"}


def _object(raw: object) -> dict[str, Any]:
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _flags(raw: object, *, base_push: bool) -> dict[str, bool]:
    """A legacy bool (push only) or a ``{push,email,webhook,apprise}`` mapping."""
    flags = {"push": base_push, "email": False, "webhook": False, "apprise": False}
    if isinstance(raw, bool):
        flags["push"] = raw
    elif isinstance(raw, Mapping):
        for medium in MEDIA:
            if raw.get(medium) is not None:
                flags[medium] = bool(raw[medium])
    return flags


def _text(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def build_channels(destinations: Mapping[str, Any], now: int) -> list[dict[str, Any]]:
    """The channel rows a 4.18.x ``notification_destinations`` document becomes.

    ``builtin:push`` always; ``legacy:email`` when the host or the recipient is set;
    ``legacy:webhook`` when the URL is set; ``legacy:apprise`` when URLs are set.
    Every pre-existing destination keeps its LAN reach (Access grandfathering): a
    webhook its own stored flag, a missing one reads as allowed.
    """
    channels: list[dict[str, Any]] = [
        {
            "id": "builtin:push",
            "type": "web_push",
            "name": "Web Push",
            "config": {},
            "secrets": {},
            "allow_private_network": 0,
            "quiet_hours_exempt": 0,
            "sort_order": 0,
        }
    ]
    email = _object(destinations.get("email"))
    if _text(email.get("host")) or _text(email.get("to")):
        config = {
            "host": _text(email.get("host")),
            "port": email.get("port") if isinstance(email.get("port"), int) else 587,
            "mode": email.get("mode")
            if email.get("mode") in ("none", "starttls", "ssl")
            else "starttls",
            "user": _text(email.get("user")),
            "from": _text(email.get("from")),
            "to": _text(email.get("to")),
        }
        password = email.get("password")
        channels.append(
            {
                "id": "legacy:email",
                "type": "email",
                "name": "Email",
                "config": config,
                "secrets": {"password": password} if isinstance(password, str) and password else {},
                # The SMTP host never went through the egress guard: keep it reachable.
                "allow_private_network": 1,
                "quiet_hours_exempt": 0,
                "sort_order": 1,
            }
        )
    webhook = _object(destinations.get("webhook"))
    url = webhook.get("url")
    if isinstance(url, str) and url.strip():
        secrets: dict[str, str] = {"url": url}
        hmac_secret = webhook.get("hmac_secret")
        if isinstance(hmac_secret, str) and hmac_secret:
            secrets["hmac_secret"] = hmac_secret
        stored_flag = webhook.get("allow_private_network")
        grandfathered = "allow_private_network" not in webhook
        channels.append(
            {
                "id": "legacy:webhook",
                "type": "webhook",
                "name": "Webhook",
                "config": {"private_network_grandfathered": True}
                if grandfathered or webhook.get("private_network_grandfathered") is True
                else {},
                "secrets": secrets,
                "allow_private_network": 1 if grandfathered or stored_flag else 0,
                # 4.18.x had no quiet hours: nothing to exempt. Deliberate (MSHL-368): the
                # registry default of a new webhook channel (exempt) is separate, and the
                # channel card's box flips this one once quiet hours are switched on.
                "quiet_hours_exempt": 0,
                "sort_order": 2,
            }
        )
    apprise = _object(destinations.get("apprise"))
    urls = apprise.get("urls")
    if isinstance(urls, str) and urls.strip():
        text_policy = apprise.get("include_message_text")
        channels.append(
            {
                "id": "legacy:apprise",
                "type": "apprise",
                "name": "Apprise",
                "config": {"format": "markdown" if apprise.get("format") == "markdown" else "text"},
                "secrets": {"urls": urls},
                "allow_private_network": 1 if apprise.get("allow_private_network") else 0,
                "include_message_text": text_policy
                if text_policy in ("always", "public_only", "never")
                else "public_only",
                "quiet_hours_exempt": 0,
                "sort_order": 3,
            }
        )
    for channel in channels:
        channel.setdefault("include_message_text", "always")
        channel["created_at"] = now
    return channels


def build_rules(
    push_defaults: object,
    conversation_overrides: object,
    channel_ids: set[str],
) -> dict[str, Any]:
    """``push_defaults`` and ``push_conversation_overrides`` as ``notification_rules``.

    A medium whose channel was not created (no SMTP, no URL) is dropped: it could
    not deliver, and a reference to a channel that does not exist would only be an
    orphan. ``contact.first_seen`` keeps the legacy per-type reading
    (``contact_types``): a companion follows ``new_contact`` OR ``advert_companion``,
    a repeater ``advert_repeater``, a sensor ``advert_sensor``.
    """
    defaults = _object(push_defaults)

    def channels_of(flags: Mapping[str, bool]) -> list[str]:
        return [
            MEDIUM_CHANNEL[m] for m in MEDIA if flags.get(m) and MEDIUM_CHANNEL[m] in channel_ids
        ]

    def flags_for(key: str) -> dict[str, bool]:
        return _flags(defaults.get(key), base_push=True)

    events: dict[str, Any] = {}
    for key, event in EVENT_OF_KEY.items():
        events[event] = {
            "channels": channels_of(flags_for(key)),
            "always_deliver": event in ALWAYS_DELIVER,
        }
    new_contact = flags_for("new_contact")
    companion = {m: new_contact[m] or flags_for("advert_companion")[m] for m in MEDIA}
    contact_types = {
        "companion": channels_of(companion),
        "repeater": channels_of(flags_for("advert_repeater")),
        "sensor": channels_of(flags_for("advert_sensor")),
    }
    union = [
        cid for cid in MEDIUM_CHANNEL.values() if any(cid in ids for ids in contact_types.values())
    ]
    events["contact.first_seen"] = {
        "channels": union,
        "contact_types": contact_types,
        "always_deliver": False,
    }
    # A public or # channel message reached Web Push only; keep that reach.
    events["message.channel"] = {
        "channels": ["builtin:push"],
        "always_deliver": False,
    }

    conversations: dict[str, dict[str, bool]] = {}
    for key, raw in _object(conversation_overrides).items():
        stored = _flags(raw, base_push=False)
        present = (
            {"push": raw}
            if isinstance(raw, bool)
            else {
                m: bool(raw[m])
                for m in MEDIA
                if isinstance(raw, Mapping) and raw.get(m) is not None
            }
        )
        entry = {MEDIUM_CHANNEL[m]: stored[m] for m in present if MEDIUM_CHANNEL[m] in channel_ids}
        if entry:
            conversations[str(key)] = entry
    return {
        "version": 1,
        "paused_until": None,
        "events": events,
        "conversations": conversations,
        "keywords": [],
        "watched_nodes": [],
    }


async def migrate(conn: aiosqlite.Connection) -> None:
    """Notification channels, deliveries journal, counters, state, and the rules column.

    Creates the four tables and ``app_settings.notification_rules``, then imports the
    4.18.x preferences once: ``builtin:push``, ``legacy:email|webhook|apprise`` from
    ``notification_destinations``, ``push_defaults`` and ``push_conversation_overrides``
    as ``notification_rules``. The legacy columns stay in place and are no longer
    written. Idempotent: existing channels are kept and non-empty rules are never
    overwritten. Delivery is untouched here (the dispatcher reads the rules later).
    """
    # One statement at a time: ``executescript`` would commit the runner's transaction.
    for statement in (*CREATE_TABLES.split(";\n\n"), CREATE_INDEXES):
        if statement.strip().strip(";"):
            await conn.execute(statement.strip().rstrip(";"))

    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return
    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    if "notification_rules" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN notification_rules TEXT DEFAULT '{}'"
        )
        columns.add("notification_rules")

    wanted = [
        c
        for c in ("notification_destinations", "push_defaults", "push_conversation_overrides")
        if c in columns
    ]
    values: dict[str, Any] = {}
    row = None
    if wanted:
        cursor = await conn.execute(
            f"SELECT {', '.join(wanted)}, notification_rules FROM app_settings WHERE id = 1"
        )
        row = await cursor.fetchone()
    if row is None:
        # No settings row (or no legacy column): a fresh install still gets the push channel.
        cursor = await conn.execute("SELECT 1 FROM app_settings WHERE id = 1")
        if await cursor.fetchone() is None:
            await conn.commit()
            return
        current_rules = ""
    else:
        values = dict(zip(wanted, row[: len(wanted)], strict=True))
        current_rules = row[len(wanted)] or ""

    destinations = _object(values.get("notification_destinations"))
    now = int(time.time())
    channels = build_channels(destinations, now)
    for channel in channels:
        await conn.execute(
            """
            INSERT OR IGNORE INTO notification_channels
                (id, type, preset, name, enabled, language, config, secrets,
                 allow_private_network, include_message_text, verify_tls,
                 quiet_hours_exempt, sort_order, created_at)
            VALUES (?, ?, NULL, ?, 1, 'auto', ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                channel["id"],
                channel["type"],
                channel["name"],
                json.dumps(channel["config"], sort_keys=True),
                json.dumps(channel["secrets"], sort_keys=True),
                channel["allow_private_network"],
                channel["include_message_text"],
                channel["quiet_hours_exempt"],
                channel["sort_order"],
                channel["created_at"],
            ),
        )

    if _object(current_rules):
        logger.info("notification_rules already set, left untouched")
    else:
        rules = build_rules(
            values.get("push_defaults"),
            values.get("push_conversation_overrides"),
            {c["id"] for c in channels},
        )
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
            (json.dumps(rules, sort_keys=True),),
        )
    logger.info("Imported %d notification channel(s) from the legacy preferences", len(channels))
    await conn.commit()
