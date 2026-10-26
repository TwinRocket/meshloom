"""The ``notifications`` section of the full backup: channels without secrets, and the rules.

The section holds the channels of the notification engine (``notification_channels``) and
``app_settings.notification_rules`` (events, conversations, keywords, watched nodes). The block
lists are ``blocked_keys`` / ``blocked_names`` in the settings, which the v1 fields already carry.

- **No secret leaves.** A write-only field (``writeOnly`` in the type's schema) is never exported:
  ``secret_omitted`` names the ones that held a value. The delivery journal, the counters and the
  dedup state, and the quiet-hours counters (``quiet:*`` rows of ``notification_state``) are runtime
  data and stay out; the ``quiet_hours`` setting itself travels in the rules. ``paused_until`` (channel and rules) is not exported:
  a restore must not bring back a stale silence.
- **A channel without its secret never sends.** It is recreated disabled, with
  ``last_error = "secret_missing"`` (the channel list shows it; saving the secret clears it, see
  ``NotificationChannelRepository.update``). A restore onto a channel that already exists with the
  same destination keeps the stored secrets, and its enabled flag follows the file. The heartbeat
  ping URL (MSHL-131) is one of these secrets (``heartbeat_url``): left out like the others, kept
  only when the file says the channel had one.
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
import re
import time
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import BackupSection
from app.notifications.keywords import MAX_KEYWORDS
from app.notifications.keywords import clean_rule as clean_keyword_rule
from app.notifications.watched_nodes import MAX_WATCHED, normalize_stored
from app.notifications.watched_nodes import clean_entry as clean_watched_entry
from app.repository.notification_channels import PENDING_SECRETS, decode_secrets, encode_secrets
from app.services.backup_accounts import USER_NOTIFICATION_RULES_SECTION, USERS_SECTION
from app.services.backup_sections import RestoreSource, register_backup_section, register_on_restore

logger = logging.getLogger(__name__)

NOTIFICATIONS_SECTION = "notifications"
NOTIFICATIONS_SECTION_VERSION = 1
SECRET_MISSING = "secret_missing"
MAX_BACKUP_CHANNELS = 200
MAX_RULES_BYTES = 1_000_000
MAX_RULE_ENTRIES = 500
MAX_RULE_CHANNELS = 200
# The characters ``create()`` can give an id (no ``:``, which is reserved for the fixed ids).
_CHANNEL_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
FIXED_IDS = frozenset({"builtin:push", "legacy:email", "legacy:webhook", "legacy:apprise"})


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
    template_dm: str = ""
    template_channel: str = ""
    quiet_hours_exempt: bool = False
    rate_per_min: int | None = Field(default=None, ge=1)
    rate_per_hour: int | None = Field(default=None, ge=1)
    rate_per_day: int | None = Field(default=None, ge=1)
    # MSHL-131: 0 = off. A ping URL is a secret (``heartbeat_url`` in ``secret_omitted``).
    heartbeat_interval_s: int = Field(default=0, ge=0, le=86400)
    heartbeat_method: Literal["GET", "POST"] = "GET"
    # MSHL-136: 0 = off, else 300..86400 (an interval outside restores as off, with a warning).
    digest_interval_s: int = Field(default=0, ge=0, le=86400)
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
            template_dm=channel.template_dm,
            template_channel=channel.template_channel,
            quiet_hours_exempt=channel.quiet_hours_exempt,
            rate_per_min=channel.rate_per_min,
            rate_per_hour=channel.rate_per_hour,
            rate_per_day=channel.rate_per_day,
            heartbeat_interval_s=channel.heartbeat_interval_s,
            heartbeat_method=channel.heartbeat_method,
            digest_interval_s=channel.digest_interval_s,
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
    # A backup written before a key existed reads with that key's stored default (an older
    # webhook restores as ``v1``), written explicitly so the row no longer depends on it.
    public = entry.read_config({k: v for k, v in config.items() if k not in entry.secret_fields})
    try:
        entry.config_model.model_validate(public)
    except ValidationError:
        return None
    return public


def _same_config(entry: Any, stored: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Whether two public configs are the same once the type's defaults are filled in."""
    try:
        return entry.config_model.model_validate(entry.read_config(stored)).model_dump(
            by_alias=True
        ) == entry.config_model.model_validate(entry.read_config(incoming)).model_dump(
            by_alias=True
        )
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
    from app.notifications.channels import (
        DIGEST_MIN_S,
        HEARTBEAT_MIN_S,
        HEARTBEAT_URL_KEY,
        ChannelError,
        get_type,
        heartbeat_supported,
    )
    from app.notifications.templates import check_templates

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
        "heartbeat_interval_s": item.heartbeat_interval_s,
        "heartbeat_method": item.heartbeat_method,
        "digest_interval_s": item.digest_interval_s,
        "sort_order": item.sort_order,
    }

    if item.id.startswith("legacy:") or item.id.startswith("builtin:"):
        if item.id not in FIXED_IDS:
            warnings.append(f"notification_channel_dropped:{item.id}")
            return False
        legacy = item.id.startswith("legacy:")
        if row is None:
            if legacy and item.secret_omitted:
                # The destination itself (its address or password) was left out of the file.
                warnings.append("notification_destination_secrets_missing")
            elif not legacy:
                warnings.append(f"notification_channel_dropped:{item.id}")
            return False
        # Destination and secrets of a ``legacy:*`` channel come from
        # ``notification_destinations``; ``builtin:push`` keeps its name.
        await _update(
            conn, item.id, own if legacy else {k: v for k, v in own.items() if k != "name"}
        )
        if legacy:
            stored, _unreadable = decode_secrets(row["secrets"], item.id)
            if any(not stored.get(name) for name in item.secret_omitted):
                warnings.append("notification_destination_secrets_missing")
        return False

    # The invariants ``create()`` enforces: a free id, a known type that is not a singleton, the
    # required fields.
    if not _CHANNEL_ID.match(item.id):
        warnings.append("notification_channel_dropped:invalid_id")
        return False
    try:
        entry = get_type(item.type, item.preset)
    except ChannelError:
        warnings.append(f"notification_channel_dropped:{item.id}")
        return False
    if entry.text_policy_locked and item.include_message_text != entry.include_message_text_default:
        # A service that cuts a long text (SMS) keeps its policy: a file cannot loosen it.
        own["include_message_text"] = entry.include_message_text_default
        warnings.append(f"notification_text_policy_forced:{item.id}")
        logger.warning("Restore: text policy of channel %s forced to its fixed value", item.id)
    try:
        own["template_dm"], own["template_channel"] = check_templates(
            entry.row_type, item.template_dm, item.template_channel
        )
    except ChannelError:
        # A file edited by hand: a refused template restores as the default body.
        own["template_dm"] = own["template_channel"] = ""
        warnings.append(f"notification_templates_dropped:{item.id}")
    if item.heartbeat_interval_s and not (
        item.heartbeat_interval_s >= HEARTBEAT_MIN_S
        and heartbeat_supported(entry.row_type, item.preset)
    ):
        # A file edited by hand: an interval the API would refuse restores as off.
        own["heartbeat_interval_s"] = 0
        warnings.append(f"notification_heartbeat_dropped:{item.id}")
    if item.digest_interval_s and item.digest_interval_s < DIGEST_MIN_S:
        # A file edited by hand: an interval the API would refuse restores as off.
        own["digest_interval_s"] = 0
        warnings.append(f"notification_digest_dropped:{item.id}")
    public = _public_config(entry, item.config)
    if (
        entry.singleton_id is not None
        or public is None
        or any(not public.get(f) for f in entry.required if f not in entry.secret_fields)
    ):
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
            readable, _unreadable = decode_secrets(row["secrets"], item.id)
            kept = {k: v for k, v in readable.items() if isinstance(v, str) and v}
            if HEARTBEAT_URL_KEY not in item.secret_omitted:
                # The file has no ping URL: the channel restores in message mode.
                kept.pop(HEARTBEAT_URL_KEY, None)

    wanted = set(item.secret_omitted) | {f for f in entry.required if f in entry.secret_fields}
    missing = sorted(name for name in wanted if name not in kept)
    columns = {
        **own,
        "type": entry.row_type,
        "preset": item.preset,
        "config": json.dumps(public, sort_keys=True),
        "secrets": encode_secrets(kept, item.id),
        "allow_private_network": 1 if item.allow_private_network else 0,
    }
    if missing:
        columns["enabled"] = 0
        columns["last_error"] = SECRET_MISSING
    elif row is not None and row["last_error"] == SECRET_MISSING:
        columns["last_error"] = None
    # The names the channel still waits for: it cannot be enabled until each one is typed again
    # or cleared on purpose (``NotificationChannelRepository.update``).
    if missing:
        await conn.execute(
            "INSERT OR REPLACE INTO notification_state (key, value, expires_at)"
            " VALUES (?, ?, NULL)",
            (PENDING_SECRETS + item.id, json.dumps(missing)),
        )
    else:
        await conn.execute(
            "DELETE FROM notification_state WHERE key = ?", (PENDING_SECRETS + item.id,)
        )
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
    waiting = 0
    seen: set[str] = set()
    for item in data.channels:
        if item.id in seen:
            warnings.append("notification_channel_dropped:duplicate")
            continue
        seen.add(item.id)
        if await _restore_channel(conn, item, warnings):
            waiting += 1

    if data.rules:
        rules = sanitize_rules(data.rules, warnings)
        known = await _channel_ids(conn)
        for channel_id in _referenced_ids(rules) - known:
            strip_channel_from_rules(rules, channel_id)
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
            (json.dumps(rules, sort_keys=True),),
        )

    if waiting:
        warnings.append(f"notification_secrets_missing:{waiting}")
    logger.info(
        "Backup restore: %d notification channel(s) read, %d restored switched off",
        len(data.channels),
        waiting,
    )
    return warnings


class _EventRule(BaseModel):
    """What the rules API accepts for one event (``EventRulePatch``)."""

    model_config = ConfigDict(extra="ignore")

    channels: list[str] = Field(default_factory=list, max_length=MAX_RULE_CHANNELS)
    always_deliver: bool = False
    contact_types: dict[str, list[str]] | None = None


class _ListRule(BaseModel):
    """A keyword or watched-node rule: free fields, but its channels are a list of ids."""

    model_config = ConfigDict(extra="allow")

    channels: list[str] = Field(default_factory=list, max_length=MAX_RULE_CHANNELS)


CONTACT_TYPES = ("companion", "repeater", "sensor")


def sanitize_rules(raw: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """The rules a file may carry, validated entry by entry like the rules API does.

    A malformed entry is dropped (one ``notification_rules_sanitized`` warning in all); the rest is
    kept, so the settings page never reads a shape it cannot render. ``paused_until`` is emptied.
    """
    from app.notifications.events import EVENT_IDS

    if len(json.dumps(raw)) > MAX_RULES_BYTES:
        warnings.append("notification_rules_dropped")
        return {"version": 1, "paused_until": None}
    dropped = 0
    rules: dict[str, Any] = {"version": 1, "paused_until": None}

    events: dict[str, Any] = {}
    raw_events = raw.get("events")
    for event_id, entry in raw_events.items() if isinstance(raw_events, dict) else []:
        try:
            if event_id not in EVENT_IDS:
                raise ValueError(event_id)
            rule = _EventRule.model_validate(entry)
            types = rule.contact_types
            if types is not None and (
                set(types) - set(CONTACT_TYPES)
                or any(len(ids) > MAX_RULE_CHANNELS for ids in types.values())
            ):
                raise ValueError("contact_types")
            events[event_id] = rule.model_dump(exclude_none=True)
        except (ValueError, ValidationError):
            dropped += 1
    if isinstance(raw_events, dict):
        rules["events"] = events
    elif raw_events is not None:
        dropped += 1

    raw_conversations = raw.get("conversations")
    conversations: dict[str, dict[str, bool]] = {}
    for key, entry in raw_conversations.items() if isinstance(raw_conversations, dict) else []:
        if (
            isinstance(key, str)
            and isinstance(entry, dict)
            and len(conversations) < MAX_RULE_ENTRIES
            and all(isinstance(k, str) and isinstance(v, bool) for k, v in entry.items())
        ):
            conversations[key] = dict(entry)
        else:
            dropped += 1
    rules["conversations"] = conversations
    if raw_conversations is not None and not isinstance(raw_conversations, dict):
        dropped += 1

    for name in ("keywords", "watched_nodes"):
        raw_list = raw.get(name)
        kept: list[dict[str, Any]] = []
        for entry in raw_list if isinstance(raw_list, list) else []:
            try:
                if len(kept) >= (MAX_KEYWORDS if name == "keywords" else MAX_WATCHED):
                    raise ValueError(name)
                if name == "keywords":
                    # The same checks as the API (``keywords.py``): a rule that would be
                    # refused there is not restored; ids stay unique.
                    rule = clean_keyword_rule(entry)
                    if any(rule["id"] == k["id"] for k in kept):
                        raise ValueError("id")
                    _ListRule.model_validate(rule)
                    kept.append(rule)
                else:
                    # The same checks as the API (``watched_nodes.py``), at most 50 keys.
                    node = clean_watched_entry(normalize_watched_entry(entry))
                    if any(node["public_key"] == k["public_key"] for k in kept):
                        raise ValueError("public_key")
                    kept.append(node)
            except (ValueError, ValidationError):
                dropped += 1
        if raw_list is not None and not isinstance(raw_list, list):
            dropped += 1
        rules[name] = kept

    if "quiet_hours" in raw:
        from app.notifications.quiet_hours import validate_quiet_hours

        try:
            if not isinstance(raw["quiet_hours"], dict):
                raise ValueError("quiet_hours")
            rules["quiet_hours"] = validate_quiet_hours(raw["quiet_hours"])
        except ValueError:
            dropped += 1

    if isinstance(raw.get("mention_whole_word"), bool):
        rules["mention_whole_word"] = raw["mention_whole_word"]
    if "emit_ws_events" in raw:
        # The API's check: a boolean, else it is dropped (the default, on, applies).
        if isinstance(raw["emit_ws_events"], bool):
            rules["emit_ws_events"] = raw["emit_ws_events"]
        else:
            dropped += 1
    if dropped:
        warnings.append("notification_rules_sanitized")
    return rules


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


def normalize_watched_entry(entry: object) -> object:
    """A row saved before the 60 s minimum cooldown restores with the minimum, not dropped."""
    return normalize_stored([entry])[0] if isinstance(entry, dict) else entry


async def _after_restore(source: RestoreSource) -> None:
    """The channel cache and the deprecated preference columns follow the restored rows."""
    from app.notifications.legacy_sync import sync_columns
    from app.repository.notification_channels import invalidate_cache

    invalidate_cache()  # the account choices too
    await sync_columns()
    # The heartbeat loops (MSHL-131) follow the restored channels now, not at the next tick.
    from app.notifications.dispatcher import notification_dispatcher

    await notification_dispatcher().heartbeats.reconcile()


MAX_BACKUP_USER_RULES = 10_000


class BackupUserNotificationRules(BaseModel):
    """One account's choices as the file holds them (``user_rules.sanitize_override``)."""

    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    user_id: int = Field(ge=1)
    rules: dict[str, Any] = Field(default_factory=dict)


class BackupUserNotificationRulesData(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    accounts: list[BackupUserNotificationRules] = Field(max_length=MAX_BACKUP_USER_RULES)


async def export_user_notification_rules() -> BackupSection | None:
    from app.repository.user_notification_rules import UserNotificationRulesRepository

    overrides = await UserNotificationRulesRepository.all_overrides()
    if not overrides:
        return None
    return BackupSection(
        data={
            "accounts": [
                {"user_id": user_id, "rules": rules} for user_id, rules in sorted(overrides.items())
            ]
        }
    )


async def restore_user_notification_rules(
    section: BackupSection, conn: aiosqlite.Connection
) -> list[str]:
    """Replace every account's choices with the file's. Runs in the accounts' transaction, right
    after them, so an id names the restored account; a row whose account does not exist is
    dropped. A ``users`` restore without this section leaves no choices (the replaced
    accounts' rows cascade): the accounts follow the admin defaults."""
    from app.notifications.user_rules import sanitize_override
    from app.repository.user_notification_rules import UserNotificationRulesRepository

    data = BackupUserNotificationRulesData.model_validate(section.data)
    async with conn.execute("SELECT id FROM users") as cursor:
        known = {int(row[0]) for row in await cursor.fetchall()}
    await conn.execute("DELETE FROM user_notification_rules")
    warnings: list[str] = []
    for item in data.accounts:
        override = sanitize_override(item.rules)
        if item.user_id not in known:
            warnings.append(f"notification_user_rules_dropped:{item.user_id}")
            continue
        await UserNotificationRulesRepository.write(conn, item.user_id, override)
    return warnings


register_backup_section(
    USER_NOTIFICATION_RULES_SECTION,
    export_user_notification_rules,
    restore_user_notification_rules,
    description={
        "en": "Each account's own notification choices for its devices",
        "fr": "Choix de notification propres à chaque compte pour ses appareils",
    },
    sensitive=False,
    restore_with=USERS_SECTION,
)
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
