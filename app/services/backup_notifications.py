"""The ``notifications`` section of the full backup: channels without secrets, and the rules.

The section holds the channels of the notification engine (``notification_channels``) and
``app_settings.notification_rules`` (events, conversations, keywords, watched nodes). The block
lists are ``blocked_keys`` / ``blocked_names`` in the settings, which the v1 fields already carry.

- **No secret leaves.** A write-only field (``writeOnly`` in the type's schema) is never exported:
  ``secret_omitted`` names the ones that held a value. The delivery journal, the counters and the
  dedup state are runtime data and stay out. ``paused_until`` (channel and rules) is not exported:
  a restore must not bring back a stale silence.
- **A channel without its secret never sends.** It is recreated disabled, with
  ``last_error = "secret_missing"`` (the channel list shows it; saving the secret clears it, see
  ``NotificationChannelRepository.update``). A restore onto a channel that already exists with the
  same destination keeps the stored secrets, and its enabled flag follows the file.
- **The ``legacy:*`` channels belong to ``notification_destinations``** (the settings of the
  v1 part, restored first): the section only sets their own options, never their destination.
- Restoring merges the channels (a channel the file does not name stays) and replaces the rules,
  minus the ids of channels that exist nowhere.

A 4.18.x file has no such section: its ``push_defaults``, ``push_conversation_overrides`` and
destinations reach the rules and the ``legacy:*`` channels through the same import code as
migration 113 (``restore_json``, ``legacy_sync``), see ``docs/dev/backup.md``.
"""

import json
import logging
import time
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import BackupSection
from app.services.backup_sections import RestoreSource, register_backup_section, register_on_restore

logger = logging.getLogger(__name__)

NOTIFICATIONS_SECTION = "notifications"
NOTIFICATIONS_SECTION_VERSION = 1
SECRET_MISSING = "secret_missing"
MAX_BACKUP_CHANNELS = 200
MAX_RULES_BYTES = 1_000_000


class BackupNotificationChannel(BaseModel):
    """One channel as the file holds it: public config only."""

    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    id: str = Field(min_length=1, max_length=80)
    type: str = Field(min_length=1, max_length=40)
    preset: str | None = Field(default=None, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    language: Literal["auto", "fr", "en"] = "auto"
    config: dict[str, Any] = Field(default_factory=dict)
    # Names of the write-only fields that held a value; the values never leave.
    secret_omitted: dict[str, Literal[True]] = Field(default_factory=dict)
    allow_private_network: bool = False
    include_message_text: Literal["always", "public_only", "never"] = "always"
    verify_tls: bool = True
    quiet_hours_exempt: bool = False
    rate_per_min: int | None = Field(default=None, ge=1)
    rate_per_hour: int | None = Field(default=None, ge=1)
    rate_per_day: int | None = Field(default=None, ge=1)
    sort_order: int = 0
    created_at: int = Field(default=0, ge=0)


class BackupNotificationsData(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    channels: list[BackupNotificationChannel] = Field(max_length=MAX_BACKUP_CHANNELS)
    rules: dict[str, Any] = Field(default_factory=dict)


async def export_notifications() -> BackupSection | None:
    from app.repository.notification_channels import NotificationChannelRepository

    channels = await NotificationChannelRepository.list_all()
    rules = await NotificationChannelRepository.get_rules()
    if not channels and not rules:
        return None
    exported = [
        BackupNotificationChannel(
            id=channel.id,
            type=channel.type,
            preset=channel.preset,
            name=channel.name,
            enabled=channel.enabled,
            language=channel.language,
            config=dict(channel.config),
            secret_omitted=dict.fromkeys(channel.secret_fields_set, True),
            allow_private_network=channel.allow_private_network,
            include_message_text=channel.include_message_text,
            verify_tls=channel.verify_tls,
            quiet_hours_exempt=channel.quiet_hours_exempt,
            rate_per_min=channel.rate_per_min,
            rate_per_hour=channel.rate_per_hour,
            rate_per_day=channel.rate_per_day,
            sort_order=channel.sort_order,
            created_at=channel.created_at,
        ).model_dump(mode="json")
        for channel in channels
    ]
    exported_rules = {**rules, "paused_until": None} if rules else {}
    return BackupSection(
        data={"channels": exported, "rules": exported_rules},
        secrets_omitted=any(entry["secret_omitted"] for entry in exported),
    )


def _loads(raw: object) -> dict[str, Any]:
    try:
        loaded = json.loads(raw) if isinstance(raw, str) and raw else {}
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _public_config(entry: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """The config the type accepts, secret fields removed; None when it does not validate."""
    public = {k: v for k, v in config.items() if k not in entry.secret_fields}
    try:
        entry.config_model.model_validate(public)
    except ValidationError:
        return None
    return public


def _same_config(entry: Any, stored: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Whether two public configs are the same once the type's defaults are filled in."""
    try:
        return entry.config_model.model_validate(stored).model_dump(
            by_alias=True
        ) == entry.config_model.model_validate(incoming).model_dump(by_alias=True)
    except ValidationError:
        return False


async def _channel_ids(conn: aiosqlite.Connection) -> set[str]:
    async with conn.execute("SELECT id FROM notification_channels") as cursor:
        return {str(row[0]) for row in await cursor.fetchall()}


async def _restore_channel(
    conn: aiosqlite.Connection,
    item: BackupNotificationChannel,
    warnings: list[str],
) -> bool:
    """Write one channel; True when it ends up needing a secret."""
    from app.notifications.channels import ChannelError, get_type

    async with conn.execute("SELECT * FROM notification_channels WHERE id = ?", (item.id,)) as cur:
        row = await cur.fetchone()

    own = {
        "name": item.name.strip() or item.id,
        "enabled": 1 if item.enabled else 0,
        "language": item.language,
        "include_message_text": item.include_message_text,
        "verify_tls": 1 if item.verify_tls else 0,
        "quiet_hours_exempt": 1 if item.quiet_hours_exempt else 0,
        "rate_per_min": item.rate_per_min,
        "rate_per_hour": item.rate_per_hour,
        "rate_per_day": item.rate_per_day,
        "sort_order": item.sort_order,
    }

    if item.id.startswith("legacy:"):
        # Destination and secrets come from ``notification_destinations``.
        if row is None:
            warnings.append(f"notification_channel_dropped:{item.id}")
            return False
        await _update(conn, item.id, own)
        return False
    if item.id.startswith("builtin:"):
        if row is None:
            warnings.append(f"notification_channel_dropped:{item.id}")
            return False
        await _update(conn, item.id, {k: v for k, v in own.items() if k != "name"})
        return False

    try:
        entry = get_type(item.type, item.preset)
    except ChannelError:
        warnings.append(f"notification_channel_dropped:{item.id}")
        return False
    public = _public_config(entry, item.config)
    if public is None:
        warnings.append(f"notification_channel_dropped:{item.id}")
        return False

    kept: dict[str, str] = {}
    if row is not None and row["type"] == entry.row_type and row["preset"] == item.preset:
        same_destination = (
            _same_config(entry, _loads(row["config"]), public)
            and bool(row["allow_private_network"]) == item.allow_private_network
            and bool(row["verify_tls"]) == item.verify_tls
        )
        if same_destination:
            kept = {k: v for k, v in _loads(row["secrets"]).items() if isinstance(v, str) and v}

    wanted = set(item.secret_omitted) | {f for f in entry.required if f in entry.secret_fields}
    missing = sorted(name for name in wanted if name not in kept)
    columns = {
        **own,
        "type": entry.row_type,
        "preset": item.preset,
        "config": json.dumps(public, sort_keys=True),
        "secrets": json.dumps(kept, sort_keys=True),
        "allow_private_network": 1 if item.allow_private_network else 0,
    }
    if missing:
        columns["enabled"] = 0
        columns["last_error"] = SECRET_MISSING
    elif row is not None and row["last_error"] == SECRET_MISSING:
        columns["last_error"] = None
    if row is None:
        columns.update(id=item.id, created_at=item.created_at or int(time.time()))
        await conn.execute(
            f"INSERT INTO notification_channels ({', '.join(columns)})"  # noqa: S608
            f" VALUES ({', '.join('?' for _ in columns)})",
            tuple(columns.values()),
        )
    else:
        await _update(conn, item.id, columns)
    return bool(missing)


async def _update(conn: aiosqlite.Connection, channel_id: str, columns: dict[str, Any]) -> None:
    assignments = ", ".join(f"{name} = ?" for name in columns)
    await conn.execute(
        f"UPDATE notification_channels SET {assignments} WHERE id = ?",  # noqa: S608
        (*columns.values(), channel_id),
    )


async def restore_notifications(section: BackupSection, conn: aiosqlite.Connection) -> list[str]:
    from app.repository.notification_channels import strip_channel_from_rules

    data = BackupNotificationsData.model_validate(section.data)
    warnings: list[str] = []
    needing_secret = 0
    for item in data.channels:
        if await _restore_channel(conn, item, warnings):
            needing_secret += 1

    if data.rules and len(json.dumps(data.rules)) <= MAX_RULES_BYTES:
        rules = json.loads(json.dumps(data.rules))
        rules["paused_until"] = None
        known = await _channel_ids(conn)
        for channel_id in _referenced_ids(rules) - known:
            strip_channel_from_rules(rules, channel_id)
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
            (json.dumps(rules, sort_keys=True),),
        )
    elif data.rules:
        warnings.append("notification_rules_dropped")

    if needing_secret:
        warnings.append(f"notification_secrets_missing:{needing_secret}")
    logger.info(
        "Backup restore: %d notification channel(s), %d need a secret",
        len(data.channels),
        needing_secret,
    )
    return warnings


def _referenced_ids(rules: dict[str, Any]) -> set[str]:
    """Every channel id a rule names (events, contact types, conversations, keywords, nodes)."""
    ids: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, list):
            ids.update(v for v in value if isinstance(v, str))
        elif isinstance(value, dict):
            ids.update(k for k in value if isinstance(k, str))

    events = rules.get("events")
    for event in (events if isinstance(events, dict) else {}).values():
        if not isinstance(event, dict):
            continue
        add(event.get("channels"))
        types = event.get("contact_types")
        for listed in (types if isinstance(types, dict) else {}).values():
            add(listed)
    conversations = rules.get("conversations")
    for entry in (conversations if isinstance(conversations, dict) else {}).values():
        add(entry)
    for key in ("keywords", "watched_nodes"):
        listed = rules.get(key)
        for entry in listed if isinstance(listed, list) else []:
            if isinstance(entry, dict):
                add(entry.get("channels"))
    return ids


async def _after_restore(source: RestoreSource) -> None:
    """The channel cache and the deprecated preference columns follow the restored rows."""
    from app.notifications.legacy_sync import sync_columns
    from app.repository.notification_channels import invalidate_cache

    invalidate_cache()
    await sync_columns()


register_backup_section(
    NOTIFICATIONS_SECTION,
    export_notifications,
    restore_notifications,
    description={
        "en": "Notifications: channels (without their secrets) and rules",
        "fr": "Notifications : canaux (sans leurs secrets) et règles",
    },
    sensitive=False,
    version=NOTIFICATIONS_SECTION_VERSION,
)
register_on_restore("notifications", _after_restore)
