import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Any, Literal, TypedDict, TypeVar

import aiosqlite
from pydantic import ValidationError

from app import origin_policy, radio_workarounds
from app.database import db
from app.models import (
    AppSettings,
    NotificationDestinations,
    NotificationDestinationsUpdate,
    RejectedChannel,
    TelemetryAlertRules,
    UiPreferences,
    is_valid_start_view,
)
from app.path_utils import bucket_path_hash_widths, bucket_region_scope, parse_packet_envelope
from app.services.update_window import (
    DEFAULT_AUTO_UPDATE_WEEKDAYS,
    format_hhmm,
    normalize_weekdays,
    parse_hhmm,
)
from app.telemetry_interval import DEFAULT_TELEMETRY_INTERVAL_HOURS

logger = logging.getLogger(__name__)

SECONDS_1H = 3600
SECONDS_24H = 86400
SECONDS_72H = 259200
SECONDS_7D = 604800

PUSH_DEFAULT_KEYS = (
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

NOTIFICATION_MEDIA_KEYS = ("push", "email", "webhook", "apprise")


class NotificationMediaFlags(TypedDict):
    push: bool
    email: bool
    webhook: bool
    apprise: bool


class PushDefaults(TypedDict):
    new_contact: NotificationMediaFlags
    new_dm: NotificationMediaFlags
    advert_repeater: NotificationMediaFlags
    advert_companion: NotificationMediaFlags
    advert_sensor: NotificationMediaFlags
    channel_found: NotificationMediaFlags
    telemetry_alert: NotificationMediaFlags
    oss_update: NotificationMediaFlags
    radio_offline: NotificationMediaFlags
    radio_online: NotificationMediaFlags
    app_started: NotificationMediaFlags


def _default_media() -> NotificationMediaFlags:
    return NotificationMediaFlags(push=True, email=False, webhook=False, apprise=False)


DEFAULT_PUSH_DEFAULTS: PushDefaults = PushDefaults(
    **{key: _default_media() for key in PUSH_DEFAULT_KEYS}
)


def coerce_notification_media(
    raw: object,
    fallback: NotificationMediaFlags | None = None,
) -> NotificationMediaFlags:
    """Accept a legacy bool (push only) or ``{push,email,webhook,apprise}``."""
    base = NotificationMediaFlags(**(fallback or _default_media()))
    if isinstance(raw, bool):
        base["push"] = raw
        return base
    if isinstance(raw, Mapping):
        for key in NOTIFICATION_MEDIA_KEYS:
            if key in raw and raw[key] is not None:
                base[key] = bool(raw[key])
    return base


def media_enabled(
    defaults: Mapping[str, Any],
    key: str,
    channel: str = "push",
) -> bool:
    raw = defaults.get(key, True)
    return bool(coerce_notification_media(raw).get(channel, False))


def any_media_enabled(defaults: Mapping[str, Any], key: str) -> bool:
    flags = coerce_notification_media(defaults.get(key, True))
    return any(flags[key] for key in NOTIFICATION_MEDIA_KEYS)


def or_notification_media(*raws: object) -> NotificationMediaFlags:
    result = NotificationMediaFlags(push=False, email=False, webhook=False, apprise=False)
    for raw in raws:
        flags = coerce_notification_media(
            raw, NotificationMediaFlags(push=False, email=False, webhook=False, apprise=False)
        )
        for key in NOTIFICATION_MEDIA_KEYS:
            result[key] = result[key] or flags[key]
    return result


def _known_push_defaults(parsed: Mapping[str, Any]) -> PushDefaults:
    """Keep only known keys; missing ones take the built-in default."""
    return PushDefaults(
        **{
            key: (
                coerce_notification_media(parsed[key], _default_media())
                if key in parsed
                else _default_media()
            )
            for key in PUSH_DEFAULT_KEYS
        }
    )


def _load_json_object(raw: object) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_push_defaults(raw: object) -> PushDefaults:
    return _known_push_defaults(_load_json_object(raw))


def _coerce_push_defaults(
    defaults: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
) -> PushDefaults:
    base = _known_push_defaults(current or {})
    return PushDefaults(
        **{
            key: (
                coerce_notification_media(defaults[key], base[key])
                if key in defaults
                else NotificationMediaFlags(**base[key])
            )
            for key in PUSH_DEFAULT_KEYS
        }
    )


def _parse_auto_update_hhmm(raw: object, default: str = "00:00") -> str:
    if not isinstance(raw, str) or not raw.strip():
        return default
    try:
        return format_hhmm(*parse_hhmm(raw))
    except ValueError:
        return default


_ChoiceT = TypeVar("_ChoiceT", bound=str)
ShareShortenMode = Literal["auto", "always", "never"]
SharePreviewPolicy = Literal["always", "private", "never"]
SHARE_SHORTEN_MODES: tuple[ShareShortenMode, ...] = ("auto", "always", "never")
SHARE_PREVIEW_POLICIES: tuple[SharePreviewPolicy, ...] = ("always", "private", "never")
NotificationLanguage = Literal["auto", "fr", "en"]
NOTIFICATION_LANGUAGES: tuple[NotificationLanguage, ...] = ("auto", "fr", "en")

# Services with an instance identity, each its own ``app_settings.<service>_instance_seed``
# column (app/services/instance_identity.py). Access adds "remote" with its migration.
INSTANCE_SEED_SERVICES: tuple[str, ...] = ("share",)


def _share_choice(
    row: Any, column: str, allowed: tuple[_ChoiceT, ...], default: _ChoiceT
) -> _ChoiceT:
    """A stored share choice, or ``default`` when it is missing or not a known value."""
    try:
        value = row[column]
    except (KeyError, TypeError, IndexError):
        return default
    return value if value in allowed else default


def _parse_allowed_origins(raw: object) -> list[str]:
    """The stored CORS allowlist; anything unreadable is same-origin only."""
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(loaded, list):
        return []
    return [origin for origin in loaded if isinstance(origin, str) and origin]


def _parse_auto_update_weekdays(raw: object) -> list[int]:
    loaded: object = raw
    if isinstance(raw, str):
        try:
            loaded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return list(DEFAULT_AUTO_UPDATE_WEEKDAYS)
    if not isinstance(loaded, list):
        return list(DEFAULT_AUTO_UPDATE_WEEKDAYS)
    try:
        return normalize_weekdays(int(day) for day in loaded)
    except (TypeError, ValueError):
        return list(DEFAULT_AUTO_UPDATE_WEEKDAYS)


ConversationOverride = dict[str, bool]


def coerce_conversation_override(raw: object) -> ConversationOverride:
    """Accept a legacy bool (push only) or a partial ``{push,email,webhook,apprise}``."""
    if isinstance(raw, bool):
        return {"push": raw}
    if not isinstance(raw, Mapping):
        return {}
    out: ConversationOverride = {}
    for key in NOTIFICATION_MEDIA_KEYS:
        if key in raw and raw[key] is not None:
            out[key] = bool(raw[key])
    return out


def _parse_push_overrides(raw: object) -> dict[str, ConversationOverride]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    parsed: dict[str, ConversationOverride] = {}
    for key, value in loaded.items():
        if not isinstance(key, str):
            continue
        flags = coerce_conversation_override(value)
        if flags:
            parsed[key] = flags
    return parsed


def _store_push_overrides(overrides: Mapping[str, object]) -> dict[str, ConversationOverride]:
    stored: dict[str, ConversationOverride] = {}
    for key, value in overrides.items():
        flags = coerce_conversation_override(value)
        if flags:
            stored[str(key)] = flags
    return stored


async def _mirror_legacy_rules() -> None:
    """The notification rules are the delivery source, the 4.18.x preference columns the view
    the deprecated routes read: mirror each write of the columns into the rules."""
    from app.notifications.legacy_sync import sync_rules

    await sync_rules()


def _invalidate_block_lists() -> None:
    """After the commit of a block-list write, so a reader never caches the old lists."""
    from app.services.message_filters import invalidate_block_lists

    invalidate_block_lists()


class AppSettingsRepository:
    """Repository for app_settings table (single-row pattern).

    Public methods acquire the DB lock exactly once. ``toggle_*`` helpers that
    need a read-modify-write do so inside a single ``db.tx()`` — the internal
    ``_get_in_conn`` / ``_apply_updates`` helpers run under the caller's
    already-held lock and must NEVER call ``db.tx()`` or ``db.readonly()``.
    """

    @staticmethod
    async def _get_in_conn(conn: aiosqlite.Connection) -> AppSettings:
        """Load settings using an already-acquired connection.

        Used by the public ``get()`` and by multi-step operations
        (``toggle_blocked_key``, ``toggle_blocked_name``) to avoid re-entering
        the non-reentrant DB lock.
        """
        async with conn.execute(
            """
            SELECT max_radio_contacts, auto_decrypt_dm_on_advert,
                   last_message_times,
                   advert_interval, last_advert_time, flood_scope, known_regions,
                   blocked_keys, blocked_names, rejected_channels, discovery_blocked_types,
                   tracked_telemetry_repeaters, tracked_telemetry_contacts,
                   auto_resend_channel,
                   telemetry_interval_hours, telemetry_routed_hourly,
                   stale_contact_days, raw_packet_retention_days, telemetry_alert_rules,
                   notification_destinations,
                   ui_preferences, auto_update,
                   auto_update_window_start, auto_update_window_end,
                   auto_update_weekdays, enable_message_poll_fallback,
                   force_channel_slot_reconfigure, load_with_autoevict,
                   auto_clock_wraparound, auth_allowed_origins,
                   push_allow_private_endpoints, push_private_endpoints_grandfathered,
                   push_private_endpoints_review,
                   radio_offline_grace_s,
                   share_enabled, share_shorten_mode, share_preview_policy,
                   notification_language
            FROM app_settings WHERE id = 1
            """
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            # Should not happen after migration, but handle gracefully
            return AppSettings()

        # Parse last_message_times JSON
        last_message_times: dict[str, int] = {}
        if row["last_message_times"]:
            try:
                last_message_times = json.loads(row["last_message_times"])
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(
                    "Failed to parse last_message_times JSON, using empty dict: %s",
                    e,
                )
                last_message_times = {}

        # Parse ui_preferences JSON. A malformed blob falls back to defaults rather
        # than failing the whole settings read: chrome preferences must never be
        # able to stop the app from loading.
        ui_preferences = UiPreferences()
        try:
            raw_ui = row["ui_preferences"]
            if raw_ui:
                ui_preferences = UiPreferences.model_validate(json.loads(raw_ui))
        except (json.JSONDecodeError, TypeError, KeyError, ValidationError) as e:
            logger.warning("Failed to parse ui_preferences, using defaults: %s", e)
            ui_preferences = UiPreferences()
        # A start page that is no longer a known tool (restored backup, id removed later)
        # reads as the default, so saving other preferences never sends it back.
        if ui_preferences.start_view and not is_valid_start_view(ui_preferences.start_view):
            ui_preferences = ui_preferences.model_copy(update={"start_view": ""})

        # Parse blocked_keys JSON
        blocked_keys: list[str] = []
        if row["blocked_keys"]:
            try:
                blocked_keys = json.loads(row["blocked_keys"])
            except (json.JSONDecodeError, TypeError):
                blocked_keys = []

        # Parse blocked_names JSON
        blocked_names: list[str] = []
        if row["blocked_names"]:
            try:
                blocked_names = json.loads(row["blocked_names"])
            except (json.JSONDecodeError, TypeError):
                blocked_names = []

        from app.services.channel_membership import coerce_rejected_channels

        try:
            rejected_channels = coerce_rejected_channels(row["rejected_channels"])
        except (KeyError, TypeError):
            rejected_channels = []

        # Parse known_regions JSON
        known_regions: list[str] = []
        try:
            raw_regions = row["known_regions"]
            if raw_regions:
                known_regions = json.loads(raw_regions)
        except (json.JSONDecodeError, TypeError, KeyError):
            known_regions = []

        # Parse discovery_blocked_types JSON
        discovery_blocked_types: list[int] = []
        if row["discovery_blocked_types"]:
            try:
                discovery_blocked_types = json.loads(row["discovery_blocked_types"])
            except (json.JSONDecodeError, TypeError):
                discovery_blocked_types = []

        # Parse tracked_telemetry_repeaters JSON
        tracked_telemetry_repeaters: list[str] = []
        try:
            raw_tracked = row["tracked_telemetry_repeaters"]
            if raw_tracked:
                tracked_telemetry_repeaters = json.loads(raw_tracked)
        except (json.JSONDecodeError, TypeError, KeyError):
            tracked_telemetry_repeaters = []

        # Parse tracked_telemetry_contacts JSON
        tracked_telemetry_contacts: list[str] = []
        try:
            raw_tracked_contacts = row["tracked_telemetry_contacts"]
            if raw_tracked_contacts:
                tracked_telemetry_contacts = json.loads(raw_tracked_contacts)
        except (json.JSONDecodeError, TypeError, KeyError):
            tracked_telemetry_contacts = []

        # Parse auto_resend_channel boolean
        try:
            auto_resend_channel = bool(row["auto_resend_channel"])
        except (KeyError, TypeError):
            auto_resend_channel = False

        # Parse telemetry_interval_hours (migration adds the column with
        # default=8, but guard against older rows / partial migrations).
        try:
            raw_interval = row["telemetry_interval_hours"]
            telemetry_interval_hours = (
                int(raw_interval) if raw_interval is not None else DEFAULT_TELEMETRY_INTERVAL_HOURS
            )
        except (KeyError, TypeError, ValueError):
            telemetry_interval_hours = DEFAULT_TELEMETRY_INTERVAL_HOURS

        # Parse telemetry_routed_hourly boolean
        try:
            telemetry_routed_hourly = bool(row["telemetry_routed_hourly"])
        except (KeyError, TypeError):
            telemetry_routed_hourly = False

        try:
            raw_stale = row["stale_contact_days"]
            stale_contact_days = int(raw_stale) if raw_stale is not None else 0
        except (KeyError, TypeError, ValueError):
            stale_contact_days = 0

        try:
            raw_retention = row["raw_packet_retention_days"]
            raw_packet_retention_days = int(raw_retention) if raw_retention is not None else 0
        except (KeyError, TypeError, ValueError):
            raw_packet_retention_days = 0

        try:
            from app.telemetry_alerts import coerce_telemetry_alert_rules

            telemetry_alert_rules = coerce_telemetry_alert_rules(row["telemetry_alert_rules"])
        except (KeyError, TypeError):
            telemetry_alert_rules = TelemetryAlertRules()

        try:
            from app.telemetry_alerts import coerce_notification_destinations

            notification_destinations = coerce_notification_destinations(
                row["notification_destinations"]
            )
        except (KeyError, TypeError):
            notification_destinations = NotificationDestinations()

        try:
            auto_update = bool(row["auto_update"])
        except (KeyError, TypeError):
            auto_update = False

        try:
            auto_update_window_start = _parse_auto_update_hhmm(row["auto_update_window_start"])
        except (KeyError, TypeError):
            auto_update_window_start = "00:00"

        try:
            auto_update_window_end = _parse_auto_update_hhmm(row["auto_update_window_end"])
        except (KeyError, TypeError):
            auto_update_window_end = "00:00"

        try:
            auto_update_weekdays = _parse_auto_update_weekdays(row["auto_update_weekdays"])
        except (KeyError, TypeError):
            auto_update_weekdays = list(DEFAULT_AUTO_UPDATE_WEEKDAYS)

        try:
            auth_allowed_origins = _parse_allowed_origins(row["auth_allowed_origins"])
        except (KeyError, TypeError, IndexError):
            auth_allowed_origins = []

        try:
            _grace = int(row["radio_offline_grace_s"])
        except (KeyError, TypeError, ValueError, IndexError):
            _grace = 60
        _grace = min(max(_grace, 0), 3600)

        def _flag(column: str, default: bool = False) -> bool:
            try:
                value = row[column]
            except (KeyError, TypeError, IndexError):
                return default
            return default if value is None else bool(value)

        return AppSettings(
            max_radio_contacts=row["max_radio_contacts"],
            auto_decrypt_dm_on_advert=bool(row["auto_decrypt_dm_on_advert"]),
            last_message_times=last_message_times,
            ui_preferences=ui_preferences,
            advert_interval=row["advert_interval"] or 0,
            last_advert_time=row["last_advert_time"] or 0,
            flood_scope=row["flood_scope"] or "",
            known_regions=known_regions,
            blocked_keys=blocked_keys,
            blocked_names=blocked_names,
            rejected_channels=rejected_channels,
            discovery_blocked_types=discovery_blocked_types,
            tracked_telemetry_repeaters=tracked_telemetry_repeaters,
            tracked_telemetry_contacts=tracked_telemetry_contacts,
            auto_resend_channel=auto_resend_channel,
            telemetry_interval_hours=telemetry_interval_hours,
            telemetry_routed_hourly=telemetry_routed_hourly,
            stale_contact_days=stale_contact_days,
            raw_packet_retention_days=raw_packet_retention_days,
            telemetry_alert_rules=telemetry_alert_rules,
            notification_destinations=notification_destinations,
            auto_update=auto_update,
            auto_update_window_start=auto_update_window_start,
            auto_update_window_end=auto_update_window_end,
            auto_update_weekdays=auto_update_weekdays,
            enable_message_poll_fallback=_flag("enable_message_poll_fallback"),
            force_channel_slot_reconfigure=_flag("force_channel_slot_reconfigure"),
            load_with_autoevict=_flag("load_with_autoevict"),
            auto_clock_wraparound=_flag("auto_clock_wraparound", default=True),
            auth_allowed_origins=auth_allowed_origins,
            push_allow_private_endpoints=_flag("push_allow_private_endpoints"),
            push_private_endpoints_grandfathered=_flag("push_private_endpoints_grandfathered"),
            push_private_endpoints_review=_flag("push_private_endpoints_review"),
            radio_offline_grace_s=_grace,
            share_enabled=_flag("share_enabled"),
            share_shorten_mode=_share_choice(
                row, "share_shorten_mode", SHARE_SHORTEN_MODES, "auto"
            ),
            share_preview_policy=_share_choice(
                row, "share_preview_policy", SHARE_PREVIEW_POLICIES, "private"
            ),
            notification_language=_share_choice(
                row, "notification_language", NOTIFICATION_LANGUAGES, "auto"
            ),
        )

    @staticmethod
    async def _apply_updates(
        conn: aiosqlite.Connection,
        *,
        max_radio_contacts: int | None = None,
        auto_decrypt_dm_on_advert: bool | None = None,
        last_message_times: dict[str, int] | None = None,
        advert_interval: int | None = None,
        last_advert_time: int | None = None,
        flood_scope: str | None = None,
        known_regions: list[str] | None = None,
        blocked_keys: list[str] | None = None,
        blocked_names: list[str] | None = None,
        rejected_channels: list[RejectedChannel] | None = None,
        discovery_blocked_types: list[int] | None = None,
        tracked_telemetry_repeaters: list[str] | None = None,
        tracked_telemetry_contacts: list[str] | None = None,
        auto_resend_channel: bool | None = None,
        telemetry_interval_hours: int | None = None,
        telemetry_routed_hourly: bool | None = None,
        stale_contact_days: int | None = None,
        raw_packet_retention_days: int | None = None,
        telemetry_alert_rules: TelemetryAlertRules | None = None,
        notification_destinations: NotificationDestinations
        | NotificationDestinationsUpdate
        | None = None,
        ui_preferences: UiPreferences | None = None,
        auto_update: bool | None = None,
        auto_update_window_start: str | None = None,
        auto_update_window_end: str | None = None,
        auto_update_weekdays: list[int] | None = None,
        enable_message_poll_fallback: bool | None = None,
        force_channel_slot_reconfigure: bool | None = None,
        load_with_autoevict: bool | None = None,
        auto_clock_wraparound: bool | None = None,
        auth_allowed_origins: list[str] | None = None,
        push_allow_private_endpoints: bool | None = None,
        push_private_endpoints_grandfathered: bool | None = None,
        push_private_endpoints_review: bool | None = None,
        radio_offline_grace_s: int | None = None,
        share_enabled: bool | None = None,
        share_shorten_mode: str | None = None,
        share_preview_policy: str | None = None,
        notification_language: str | None = None,
    ) -> None:
        """Apply field updates using an already-acquired connection.

        Emits a single UPDATE statement inside the caller's transaction. Does
        NOT commit — the caller's ``db.tx()`` handles that.
        """
        updates: list[str] = []
        params: list[Any] = []

        if max_radio_contacts is not None:
            updates.append("max_radio_contacts = ?")
            params.append(max_radio_contacts)

        if auto_decrypt_dm_on_advert is not None:
            updates.append("auto_decrypt_dm_on_advert = ?")
            params.append(1 if auto_decrypt_dm_on_advert else 0)

        if last_message_times is not None:
            updates.append("last_message_times = ?")
            params.append(json.dumps(last_message_times))

        if ui_preferences is not None:
            updates.append("ui_preferences = ?")
            params.append(ui_preferences.model_dump_json())

        if advert_interval is not None:
            updates.append("advert_interval = ?")
            params.append(advert_interval)

        if last_advert_time is not None:
            updates.append("last_advert_time = ?")
            params.append(last_advert_time)

        if flood_scope is not None:
            updates.append("flood_scope = ?")
            params.append(flood_scope)

        if known_regions is not None:
            updates.append("known_regions = ?")
            params.append(json.dumps(known_regions))

        if blocked_keys is not None:
            updates.append("blocked_keys = ?")
            params.append(json.dumps(blocked_keys))

        if blocked_names is not None:
            updates.append("blocked_names = ?")
            params.append(json.dumps(blocked_names))

        if rejected_channels is not None:
            from app.services.channel_membership import rejected_channels_payload

            updates.append("rejected_channels = ?")
            params.append(json.dumps(rejected_channels_payload(rejected_channels)))

        if discovery_blocked_types is not None:
            updates.append("discovery_blocked_types = ?")
            params.append(json.dumps(discovery_blocked_types))

        if tracked_telemetry_repeaters is not None:
            updates.append("tracked_telemetry_repeaters = ?")
            params.append(json.dumps(tracked_telemetry_repeaters))

        if tracked_telemetry_contacts is not None:
            updates.append("tracked_telemetry_contacts = ?")
            params.append(json.dumps(tracked_telemetry_contacts))

        if auto_resend_channel is not None:
            updates.append("auto_resend_channel = ?")
            params.append(1 if auto_resend_channel else 0)

        if telemetry_interval_hours is not None:
            updates.append("telemetry_interval_hours = ?")
            params.append(telemetry_interval_hours)

        if telemetry_routed_hourly is not None:
            updates.append("telemetry_routed_hourly = ?")
            params.append(1 if telemetry_routed_hourly else 0)

        if stale_contact_days is not None:
            updates.append("stale_contact_days = ?")
            params.append(stale_contact_days)

        if raw_packet_retention_days is not None:
            updates.append("raw_packet_retention_days = ?")
            params.append(raw_packet_retention_days)

        if telemetry_alert_rules is not None:
            from app.telemetry_alerts import merge_telemetry_alert_rules, stored_rules_dict

            current = await AppSettingsRepository._get_in_conn(conn)
            merged = merge_telemetry_alert_rules(
                current.telemetry_alert_rules, telemetry_alert_rules
            )
            updates.append("telemetry_alert_rules = ?")
            params.append(json.dumps(stored_rules_dict(merged)))
            if "channels" in telemetry_alert_rules.model_fields_set:
                async with conn.execute(
                    "SELECT push_defaults FROM app_settings WHERE id = 1"
                ) as cursor:
                    row = await cursor.fetchone()
                current_defaults = _parse_push_defaults(row["push_defaults"] if row else None)
                current_defaults["telemetry_alert"] = coerce_notification_media(
                    merged.channels.model_dump(), current_defaults["telemetry_alert"]
                )
                updates.append("push_defaults = ?")
                params.append(json.dumps(current_defaults))

        if notification_destinations is not None:
            from app.telemetry_alerts import merge_notification_destinations

            current = await AppSettingsRepository._get_in_conn(conn)
            merged_dest = merge_notification_destinations(
                current.notification_destinations, notification_destinations
            )
            updates.append("notification_destinations = ?")
            params.append(merged_dest.model_dump_json(by_alias=True))

        if auto_update is not None:
            updates.append("auto_update = ?")
            params.append(1 if auto_update else 0)

        if auto_update_window_start is not None:
            updates.append("auto_update_window_start = ?")
            params.append(format_hhmm(*parse_hhmm(auto_update_window_start)))

        if auto_update_window_end is not None:
            updates.append("auto_update_window_end = ?")
            params.append(format_hhmm(*parse_hhmm(auto_update_window_end)))

        if auto_update_weekdays is not None:
            updates.append("auto_update_weekdays = ?")
            params.append(json.dumps(normalize_weekdays(auto_update_weekdays)))

        for column, flag in (
            ("enable_message_poll_fallback", enable_message_poll_fallback),
            ("force_channel_slot_reconfigure", force_channel_slot_reconfigure),
            ("load_with_autoevict", load_with_autoevict),
            ("auto_clock_wraparound", auto_clock_wraparound),
            ("push_allow_private_endpoints", push_allow_private_endpoints),
            ("push_private_endpoints_grandfathered", push_private_endpoints_grandfathered),
            ("push_private_endpoints_review", push_private_endpoints_review),
            ("share_enabled", share_enabled),
        ):
            if flag is not None:
                updates.append(f"{column} = ?")
                params.append(1 if flag else 0)

        if radio_offline_grace_s is not None:
            updates.append("radio_offline_grace_s = ?")
            params.append(min(max(int(radio_offline_grace_s), 0), 3600))

        for column, choice, allowed in (
            ("share_shorten_mode", share_shorten_mode, SHARE_SHORTEN_MODES),
            ("share_preview_policy", share_preview_policy, SHARE_PREVIEW_POLICIES),
            ("notification_language", notification_language, NOTIFICATION_LANGUAGES),
        ):
            if choice is not None:
                if choice not in allowed:
                    raise ValueError(f"{column} must be one of {', '.join(allowed)}")
                updates.append(f"{column} = ?")
                params.append(choice)

        if auth_allowed_origins is not None:
            updates.append("auth_allowed_origins = ?")
            params.append(json.dumps(auth_allowed_origins))

        if updates:
            query = f"UPDATE app_settings SET {', '.join(updates)} WHERE id = 1"
            async with conn.execute(query, params):
                pass

    @staticmethod
    async def get() -> AppSettings:
        """Get the current app settings.

        Always returns settings - creates default row if needed (migration handles initial row).
        """
        async with db.readonly() as conn:
            return await AppSettingsRepository._get_in_conn(conn)

    @staticmethod
    async def update(
        max_radio_contacts: int | None = None,
        auto_decrypt_dm_on_advert: bool | None = None,
        last_message_times: dict[str, int] | None = None,
        advert_interval: int | None = None,
        last_advert_time: int | None = None,
        flood_scope: str | None = None,
        known_regions: list[str] | None = None,
        blocked_keys: list[str] | None = None,
        blocked_names: list[str] | None = None,
        rejected_channels: list[RejectedChannel] | None = None,
        discovery_blocked_types: list[int] | None = None,
        tracked_telemetry_repeaters: list[str] | None = None,
        tracked_telemetry_contacts: list[str] | None = None,
        auto_resend_channel: bool | None = None,
        telemetry_interval_hours: int | None = None,
        telemetry_routed_hourly: bool | None = None,
        stale_contact_days: int | None = None,
        raw_packet_retention_days: int | None = None,
        telemetry_alert_rules: TelemetryAlertRules | None = None,
        notification_destinations: NotificationDestinations
        | NotificationDestinationsUpdate
        | None = None,
        ui_preferences: UiPreferences | None = None,
        auto_update: bool | None = None,
        auto_update_window_start: str | None = None,
        auto_update_window_end: str | None = None,
        auto_update_weekdays: list[int] | None = None,
        enable_message_poll_fallback: bool | None = None,
        force_channel_slot_reconfigure: bool | None = None,
        load_with_autoevict: bool | None = None,
        auto_clock_wraparound: bool | None = None,
        auth_allowed_origins: list[str] | None = None,
        push_allow_private_endpoints: bool | None = None,
        push_private_endpoints_grandfathered: bool | None = None,
        push_private_endpoints_review: bool | None = None,
        radio_offline_grace_s: int | None = None,
        share_enabled: bool | None = None,
        share_shorten_mode: str | None = None,
        share_preview_policy: str | None = None,
        notification_language: str | None = None,
    ) -> AppSettings:
        """Update app settings. Only provided fields are updated.

        A change of ``notification_destinations`` sends the push proof of
        modification (``notify_destination_changed``).
        """
        async with db.tx() as conn:
            destinations_before = (
                (await AppSettingsRepository._get_in_conn(conn)).notification_destinations
                if notification_destinations is not None
                else None
            )
            await AppSettingsRepository._apply_updates(
                conn,
                max_radio_contacts=max_radio_contacts,
                auto_decrypt_dm_on_advert=auto_decrypt_dm_on_advert,
                last_message_times=last_message_times,
                ui_preferences=ui_preferences,
                advert_interval=advert_interval,
                last_advert_time=last_advert_time,
                flood_scope=flood_scope,
                known_regions=known_regions,
                blocked_keys=blocked_keys,
                blocked_names=blocked_names,
                rejected_channels=rejected_channels,
                discovery_blocked_types=discovery_blocked_types,
                tracked_telemetry_repeaters=tracked_telemetry_repeaters,
                tracked_telemetry_contacts=tracked_telemetry_contacts,
                auto_resend_channel=auto_resend_channel,
                telemetry_interval_hours=telemetry_interval_hours,
                telemetry_routed_hourly=telemetry_routed_hourly,
                stale_contact_days=stale_contact_days,
                raw_packet_retention_days=raw_packet_retention_days,
                telemetry_alert_rules=telemetry_alert_rules,
                notification_destinations=notification_destinations,
                auto_update=auto_update,
                auto_update_window_start=auto_update_window_start,
                auto_update_window_end=auto_update_window_end,
                auto_update_weekdays=auto_update_weekdays,
                enable_message_poll_fallback=enable_message_poll_fallback,
                force_channel_slot_reconfigure=force_channel_slot_reconfigure,
                load_with_autoevict=load_with_autoevict,
                auto_clock_wraparound=auto_clock_wraparound,
                auth_allowed_origins=auth_allowed_origins,
                push_allow_private_endpoints=push_allow_private_endpoints,
                push_private_endpoints_grandfathered=push_private_endpoints_grandfathered,
                push_private_endpoints_review=push_private_endpoints_review,
                radio_offline_grace_s=radio_offline_grace_s,
                share_enabled=share_enabled,
                share_shorten_mode=share_shorten_mode,
                share_preview_policy=share_preview_policy,
                notification_language=notification_language,
            )
            result = await AppSettingsRepository._get_in_conn(conn)
        radio_workarounds.apply(result)
        origin_policy.apply(result)
        if blocked_keys is not None or blocked_names is not None:
            _invalidate_block_lists()
        if (
            share_enabled is not None
            or share_shorten_mode is not None
            or share_preview_policy is not None
        ):
            from app.services.share import note_share_settings_changed

            note_share_settings_changed(enabled=result.share_enabled)
        if destinations_before is not None:
            from app.notify import notify_destination_changed

            notify_destination_changed(destinations_before, result.notification_destinations)
            from app.notifications.legacy_sync import sync_destinations

            await sync_destinations()
        return result

    @staticmethod
    async def get_path_hash_one_byte_opt_in() -> bool:
        """True when the operator explicitly chose 1-byte path hashing."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT path_hash_one_byte_opt_in FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return False
        try:
            return bool(row["path_hash_one_byte_opt_in"])
        except (KeyError, TypeError, IndexError):
            return False

    @staticmethod
    async def set_path_hash_one_byte_opt_in(opt_in: bool) -> None:
        """Persist whether reconnects should leave a 1-byte radio setting alone."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET path_hash_one_byte_opt_in = ? WHERE id = 1",
                (1 if opt_in else 0,),
            )

    @staticmethod
    async def get_last_notified_update_version() -> str | None:
        """Last catalogue ``latest`` we already Web-Pushed. Not part of AppSettings."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT last_notified_update_version FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        try:
            value = row["last_notified_update_version"]
        except (KeyError, TypeError, IndexError):
            return None
        if not value:
            return None
        return str(value)

    @staticmethod
    async def set_last_notified_update_version(version: str | None) -> None:
        """Persist (or clear) the last Web-Pushed catalogue version."""
        stored = (version or "").strip() or None
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET last_notified_update_version = ? WHERE id = 1",
                (stored,),
            )

    @staticmethod
    async def toggle_blocked_key(key: str) -> AppSettings:
        """Toggle a public key in the blocked list. Keys are normalized to lowercase.

        Read-modify-write is atomic under a single ``db.tx()`` lock — two
        concurrent toggles for the same key cannot produce an inconsistent
        intermediate state.
        """
        normalized = key.lower()
        async with db.tx() as conn:
            settings = await AppSettingsRepository._get_in_conn(conn)
            if normalized in settings.blocked_keys:
                new_keys = [k for k in settings.blocked_keys if k != normalized]
            else:
                new_keys = settings.blocked_keys + [normalized]
            await AppSettingsRepository._apply_updates(conn, blocked_keys=new_keys)
            result = await AppSettingsRepository._get_in_conn(conn)
        _invalidate_block_lists()
        return result

    @staticmethod
    async def toggle_blocked_name(name: str) -> AppSettings:
        """Toggle a display name in the blocked list.

        Same atomicity guarantee as ``toggle_blocked_key``.
        """
        async with db.tx() as conn:
            settings = await AppSettingsRepository._get_in_conn(conn)
            if name in settings.blocked_names:
                new_names = [n for n in settings.blocked_names if n != name]
            else:
                new_names = settings.blocked_names + [name]
            await AppSettingsRepository._apply_updates(conn, blocked_names=new_names)
            result = await AppSettingsRepository._get_in_conn(conn)
        _invalidate_block_lists()
        return result

    @staticmethod
    async def get_rejected_channels() -> list[RejectedChannel]:
        settings = await AppSettingsRepository.get()
        return list(settings.rejected_channels)

    @staticmethod
    async def find_rejected_channel(key: str) -> RejectedChannel | None:
        from app.services.channel_membership import normalize_channel_key

        wanted = normalize_channel_key(key)
        for item in await AppSettingsRepository.get_rejected_channels():
            if item.key == wanted:
                return item
        return None

    @staticmethod
    async def is_rejected_channel(key: str) -> bool:
        return await AppSettingsRepository.find_rejected_channel(key) is not None

    @staticmethod
    async def add_rejected_channel(key: str, name: str) -> None:
        from app.services.channel_membership import normalize_channel_key

        wanted = normalize_channel_key(key)
        label = (name or "").strip() or wanted
        async with db.tx() as conn:
            settings = await AppSettingsRepository._get_in_conn(conn)
            items = [item for item in settings.rejected_channels if item.key != wanted]
            items.append(RejectedChannel(key=wanted, name=label))
            await AppSettingsRepository._apply_updates(conn, rejected_channels=items)

    @staticmethod
    async def remove_rejected_channel(key: str) -> None:
        from app.services.channel_membership import normalize_channel_key

        wanted = normalize_channel_key(key)
        async with db.tx() as conn:
            settings = await AppSettingsRepository._get_in_conn(conn)
            items = [item for item in settings.rejected_channels if item.key != wanted]
            if len(items) == len(settings.rejected_channels):
                return
            await AppSettingsRepository._apply_updates(conn, rejected_channels=items)

    @staticmethod
    async def get_identity_seed_values() -> list[str]:
        """Every non-empty ``app_settings`` column named ``*_seed`` (the
        per-service instance identity seeds), as text, bytes in hex.

        The backup export refuses a file holding any of them.
        """
        async with db.readonly() as conn:
            async with conn.execute("PRAGMA table_info(app_settings)") as cursor:
                columns = [row[1] for row in await cursor.fetchall() if row[1].endswith("_seed")]
            if not columns:
                return []
            quoted = ", ".join(f'"{column}"' for column in columns)
            async with conn.execute(f"SELECT {quoted} FROM app_settings WHERE id = 1") as cursor:
                row = await cursor.fetchone()
        values: list[str] = []
        for value in tuple(row) if row else ():
            if isinstance(value, bytes):
                value = value.hex()
            if value not in (None, ""):
                values.append(str(value))
        return values

    @staticmethod
    def _instance_seed_column(service: str) -> str:
        if service not in INSTANCE_SEED_SERVICES:
            raise ValueError(f"unknown instance identity service {service!r}")
        return f"{service}_instance_seed"

    @staticmethod
    async def get_instance_seed(service: str) -> str:
        """The hex seed of a service's instance identity ("" until it is created).

        Internal-only ``app_settings.<service>_instance_seed`` column, not in
        ``AppSettings`` and never in a backup (``*_seed`` columns are refused by
        the export and scrubbed from the database copy). Read it through
        ``app/services/instance_identity.py``, never directly.
        """
        column = AppSettingsRepository._instance_seed_column(service)
        async with db.readonly() as conn:
            async with conn.execute(f"SELECT {column} FROM app_settings WHERE id = 1") as cursor:
                row = await cursor.fetchone()
        return (row[column] or "") if row else ""

    @staticmethod
    async def set_instance_seed(service: str, seed: str) -> None:
        """Store a service's instance identity seed ("" forgets it)."""
        column = AppSettingsRepository._instance_seed_column(service)
        async with db.tx() as conn:
            await conn.execute(f"UPDATE app_settings SET {column} = ? WHERE id = 1", (seed,))

    @staticmethod
    async def claim_instance_seed(service: str, seed: str) -> str:
        """Store ``seed`` only when the service has none yet, and return the
        stored one: two concurrent first uses end with the same identity."""
        column = AppSettingsRepository._instance_seed_column(service)
        async with db.tx() as conn:
            await conn.execute(
                f"UPDATE app_settings SET {column} = ? "
                f"WHERE id = 1 AND ({column} IS NULL OR {column} = '')",
                (seed,),
            )
            async with conn.execute(f"SELECT {column} FROM app_settings WHERE id = 1") as cursor:
                row = await cursor.fetchone()
        return (row[column] or "") if row else ""

    @staticmethod
    async def get_vapid_keys() -> tuple[str, str]:
        """Return (private_key_pem, public_key_b64url) from app_settings.

        These are internal-only columns not exposed via the AppSettings model.
        """
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT vapid_private_key, vapid_public_key FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if row and row["vapid_private_key"] and row["vapid_public_key"]:
            return row["vapid_private_key"], row["vapid_public_key"]
        return "", ""

    @staticmethod
    async def set_vapid_keys(private_key: str, public_key: str) -> None:
        """Persist auto-generated VAPID key pair to app_settings."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET vapid_private_key = ?, vapid_public_key = ? WHERE id = 1",
                (private_key, public_key),
            )

    @staticmethod
    async def get_push_conversations() -> list[str]:
        """Return the global list of push-enabled conversation state keys.

        Internal-only column, not exposed via the AppSettings model.
        """
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT push_conversations FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if row and row["push_conversations"]:
            try:
                return json.loads(row["push_conversations"])
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    @staticmethod
    async def set_push_conversations(conversations: list[str]) -> list[str]:
        """Replace the global push-enabled conversation list."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET push_conversations = ? WHERE id = 1",
                (json.dumps(conversations),),
            )
        return conversations

    @staticmethod
    async def get_push_defaults() -> PushDefaults:
        """Return per-event media flags. Not part of AppSettings."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT push_defaults FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        parsed = _load_json_object(row["push_defaults"] if row else None)
        return _known_push_defaults(parsed)

    @staticmethod
    async def set_push_defaults(defaults: Mapping[str, Any]) -> PushDefaults:
        """Merge per-event media flags. Unknown keys are ignored."""
        current = await AppSettingsRepository.get_push_defaults()
        merged = _coerce_push_defaults(defaults, current)
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET push_defaults = ? WHERE id = 1",
                (json.dumps(merged),),
            )
        await _mirror_legacy_rules()
        return merged

    @staticmethod
    async def write_push_columns(
        defaults: Mapping[str, Any], overrides: Mapping[str, object]
    ) -> None:
        """Store both 4.18.x preference columns as they are, without mirroring them into the
        rules: ``legacy_sync.sync_columns`` calls it after the rules changed, so the deprecated
        preference routes keep showing what is delivered."""
        merged = _coerce_push_defaults(defaults, await AppSettingsRepository.get_push_defaults())
        stored = _store_push_overrides(overrides)
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET push_defaults = ?, push_conversation_overrides = ?"
                " WHERE id = 1",
                (json.dumps(merged), json.dumps(stored)),
            )

    @staticmethod
    async def get_push_conversation_overrides() -> dict[str, ConversationOverride]:
        """Return explicit per-conversation media overrides. Not part of AppSettings."""
        async with db.readonly() as conn:
            return await AppSettingsRepository._get_push_overrides_in_conn(conn)

    @staticmethod
    async def _get_push_overrides_in_conn(
        conn: aiosqlite.Connection,
    ) -> dict[str, ConversationOverride]:
        async with conn.execute(
            "SELECT push_conversation_overrides FROM app_settings WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
        return _parse_push_overrides(row["push_conversation_overrides"] if row else None)

    @staticmethod
    async def set_push_conversation_overrides(
        overrides: Mapping[str, object],
    ) -> dict[str, ConversationOverride]:
        """Replace the full per-conversation override map."""
        stored = _store_push_overrides(overrides)
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET push_conversation_overrides = ? WHERE id = 1",
                (json.dumps(stored),),
            )
        await _mirror_legacy_rules()
        return stored

    @staticmethod
    async def set_push_conversation_override(
        key: str, value: bool | Mapping[str, Any] | None
    ) -> dict[str, ConversationOverride]:
        """Write, merge, or remove (None) one conversation override."""
        async with db.tx() as conn:
            current = await AppSettingsRepository._get_push_overrides_in_conn(conn)
            if value is None:
                current.pop(key, None)
            elif isinstance(value, bool):
                current[key] = {"push": value}
            else:
                existing = dict(current.get(key) or {})
                for media in NOTIFICATION_MEDIA_KEYS:
                    if media in value and value[media] is not None:
                        existing[media] = bool(value[media])
                if existing:
                    current[key] = existing
                else:
                    current.pop(key, None)
            await conn.execute(
                "UPDATE app_settings SET push_conversation_overrides = ? WHERE id = 1",
                (json.dumps(current),),
            )
        await _mirror_legacy_rules()
        return current

    @staticmethod
    async def remap_push_conversation_override_keys(
        old_keys: list[str], new_key: str
    ) -> dict[str, ConversationOverride]:
        """Move override entries from old_keys to new_key when the target is absent."""
        async with db.tx() as conn:
            current = await AppSettingsRepository._get_push_overrides_in_conn(conn)
            changed = False
            for old_key in old_keys:
                if old_key not in current:
                    continue
                value = current.pop(old_key)
                changed = True
                if new_key not in current:
                    current[new_key] = value
            if changed:
                await conn.execute(
                    "UPDATE app_settings SET push_conversation_overrides = ? WHERE id = 1",
                    (json.dumps(current),),
                )
        return current

    @staticmethod
    async def get_vapid_subject() -> str:
        """Return the stored VAPID subject. Empty means the built-in default is used."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT vapid_subject FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return ""
        try:
            return row["vapid_subject"] or ""
        except (KeyError, TypeError):
            return ""

    @staticmethod
    async def get_legacy_env_settings_imported() -> bool:
        """True once the one-shot import of the retired MESHCORE_* settings ran."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT legacy_env_settings_imported FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return False
        try:
            return bool(row["legacy_env_settings_imported"])
        except (KeyError, TypeError, IndexError):
            return False

    @staticmethod
    async def mark_legacy_env_settings_imported() -> None:
        """Record that the retired MESHCORE_* settings must never be read again."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET legacy_env_settings_imported = 1 WHERE id = 1"
            )

    @staticmethod
    async def set_vapid_subject(subject: str) -> str:
        """Persist the VAPID subject. Empty string falls back to the built-in default."""
        stored = subject or ""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET vapid_subject = ? WHERE id = 1",
                (stored,),
            )
        return stored

    @staticmethod
    async def get_community() -> Any:
        """Load the Community opt-in and IATA. Not part of AppSettings.

        The Community addresses are fixed in code (``meshloom_community``); the
        old ``community_broker_host`` / ``community_api_base`` columns are
        emptied by migration 091 and never read.
        """
        from app.services.meshloom_community import CommunityRecord

        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT community_enabled, community_iata FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return CommunityRecord(enabled=False, iata="")
        try:
            enabled = bool(row["community_enabled"])
        except (KeyError, TypeError):
            enabled = False
        try:
            iata = (row["community_iata"] or "").upper().strip()
        except (KeyError, TypeError):
            iata = ""
        return CommunityRecord(enabled=enabled, iata=iata)

    @staticmethod
    async def update_community(
        *,
        enabled: bool | None = None,
        iata: str | None = None,
    ) -> Any:
        """Update the Community opt-in and IATA. Only provided fields change."""
        updates: list[str] = []
        params: list[Any] = []
        if enabled is not None:
            updates.append("community_enabled = ?")
            params.append(1 if enabled else 0)
        if iata is not None:
            updates.append("community_iata = ?")
            params.append(iata)
        if updates:
            async with db.tx() as conn:
                await conn.execute(
                    f"UPDATE app_settings SET {', '.join(updates)} WHERE id = 1",
                    params,
                )
        return await AppSettingsRepository.get_community()

    @staticmethod
    async def get_community_env_imported() -> bool:
        """Whether the retired MESHLOOM_COMMUNITY* env was already imported (migration 091)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT community_env_imported FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return False
        try:
            return bool(row["community_env_imported"])
        except (KeyError, TypeError, IndexError):
            return False

    @staticmethod
    async def mark_community_env_imported() -> None:
        async with db.tx() as conn:
            await conn.execute("UPDATE app_settings SET community_env_imported = 1 WHERE id = 1")

    @staticmethod
    async def get_community_status_clear() -> list[dict[str, Any]]:
        """Pending clears of a retained Community status. Not part of AppSettings."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT community_status_clear FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return []
        try:
            raw = row["community_status_clear"]
        except (KeyError, TypeError, IndexError):
            return []
        try:
            parsed = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [entry for entry in parsed if isinstance(entry, dict)]

    @staticmethod
    async def set_community_status_clear(entries: list[dict[str, Any]]) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET community_status_clear = ? WHERE id = 1",
                (json.dumps(entries, separators=(",", ":"), sort_keys=True),),
            )

    @staticmethod
    async def get_community_status_last() -> dict[str, Any] | None:
        """Last status topic the Stats publisher made retained. Not part of AppSettings."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT community_status_last FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        try:
            raw = row["community_status_last"]
        except (KeyError, TypeError, IndexError):
            return None
        try:
            parsed = json.loads(raw or "null")
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    async def set_community_status_last(entry: dict[str, Any] | None) -> None:
        value = "" if entry is None else json.dumps(entry, separators=(",", ":"), sort_keys=True)
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET community_status_last = ? WHERE id = 1", (value,)
            )

    @staticmethod
    async def get_community_waiting_for_position() -> bool:
        """Last known "Community waits for a position" (migration 092). Not part of AppSettings."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT community_waiting_for_position FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return False
        try:
            return bool(row["community_waiting_for_position"])
        except (KeyError, TypeError, IndexError):
            return False

    @staticmethod
    async def set_community_waiting_for_position(waiting: bool) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET community_waiting_for_position = ? WHERE id = 1",
                (1 if waiting else 0,),
            )

    @staticmethod
    async def get_community_opt_out_pending() -> dict[str, Any] | None:
        """An opt-out Community did not confirm (migration 093). Not part of AppSettings."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT community_opt_out_pending FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None
        try:
            raw = row["community_opt_out_pending"]
        except (KeyError, TypeError, IndexError):
            return None
        try:
            parsed = json.loads(raw or "null")
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    async def set_community_opt_out_pending(entry: dict[str, Any] | None) -> None:
        value = "" if entry is None else json.dumps(entry, separators=(",", ":"), sort_keys=True)
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET community_opt_out_pending = ? WHERE id = 1", (value,)
            )

    # -- Radio outage state (migration 099) ------------------------------------
    # Unix time a *notified* radio outage began, NULL when none. Not part of
    # AppSettings; written only by app/notifications/radio_watch.py.

    @staticmethod
    async def get_radio_outage_started_at() -> float | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT radio_outage_started_at FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row or row["radio_outage_started_at"] is None:
            return None
        return float(row["radio_outage_started_at"])

    @staticmethod
    async def set_radio_outage_started_at(started_at: float | None) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET radio_outage_started_at = ? WHERE id = 1", (started_at,)
            )

    # -- Wi-Fi prompt state (migration 095) -----------------------------------
    # One JSON object keyed by the lowercase radio public key. Every helper is a
    # single UPDATE with json_set/json_remove, so two writers (two tabs, a store
    # racing a dismissal) never lose each other's field. Never a password.

    @staticmethod
    async def get_wifi_prompt_state() -> dict[str, dict[str, Any]]:
        """What Meshloom asked about each radio's Wi-Fi credentials. Not part of AppSettings."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT wifi_prompt_state FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if not row:
            return {}
        try:
            parsed = json.loads(row["wifi_prompt_state"] or "{}")
        except (KeyError, TypeError, IndexError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {
            key: value
            for key, value in parsed.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    @staticmethod
    async def mark_wifi_prompt_dismissed(public_key: str) -> None:
        """The user chose "don't ask again for this radio" (never erased)."""
        await _wifi_prompt_set(public_key, {"dismissed_at": int(time.time())})

    @staticmethod
    async def mark_wifi_open_network(public_key: str, ssid: str) -> None:
        """The user stored an open network for ``ssid`` (keeps any dismissal)."""
        await _wifi_prompt_set(
            public_key, {"open_network_ssid": ssid, "open_network_at": int(time.time())}
        )

    @staticmethod
    async def clear_wifi_open_network(public_key: str) -> None:
        """A password was stored: drop the open-network record only."""
        await _wifi_prompt_remove(public_key, ("open_network_ssid", "open_network_at"))

    @staticmethod
    async def mark_wifi_store_started(public_key: str, ssid: str) -> None:
        """A credentials store is about to write to the radio (no password kept)."""
        await _wifi_prompt_set(
            public_key, {"store_started_at": int(time.time()), "store_target_ssid": ssid}
        )

    @staticmethod
    async def clear_wifi_store_marker(public_key: str) -> None:
        """The store was verified (or rolled back): the radio is consistent again."""
        await _wifi_prompt_remove(public_key, ("store_started_at", "store_target_ssid"))


_PUBLIC_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
# A column that is not a JSON object (hand-edited, truncated) restarts from {}.
_WIFI_PROMPT_BASE = (
    "CASE WHEN json_valid(wifi_prompt_state) AND json_type(wifi_prompt_state) = 'object' "
    "THEN wifi_prompt_state ELSE '{}' END"
)


def _wifi_prompt_key(public_key: str) -> str:
    if not isinstance(public_key, str) or not _PUBLIC_KEY_RE.match(public_key):
        raise ValueError("radio public key must be 64 hex characters")
    return public_key.lower()


async def _wifi_prompt_set(public_key: str, fields: dict[str, Any]) -> None:
    key = _wifi_prompt_key(public_key)
    # The key is validated hex, so the JSON path holds no quote; it is still
    # passed as a bound parameter, never formatted into the SQL text.
    expression = _WIFI_PROMPT_BASE
    params: list[Any] = []
    for name, value in fields.items():
        expression = f"json_set({expression}, ?, ?)"
        params.extend((f'$."{key}".{name}', value))
    async with db.tx() as conn:
        await conn.execute(
            f"UPDATE app_settings SET wifi_prompt_state = {expression} WHERE id = 1", params
        )


async def _wifi_prompt_remove(public_key: str, names: tuple[str, ...]) -> None:
    key = _wifi_prompt_key(public_key)
    placeholders = ", ".join("?" for _ in names)
    async with db.tx() as conn:
        await conn.execute(
            f"UPDATE app_settings SET wifi_prompt_state = "
            f"json_remove({_WIFI_PROMPT_BASE}, {placeholders}) WHERE id = 1",
            [f'$."{key}".{name}' for name in names],
        )


class StatisticsRepository:
    @staticmethod
    async def get_database_message_totals() -> dict[str, int]:
        """Return message totals needed by lightweight debug surfaces."""
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT
                    SUM(CASE WHEN type = 'PRIV' THEN 1 ELSE 0 END) AS total_dms,
                    SUM(CASE WHEN type = 'CHAN' THEN 1 ELSE 0 END) AS total_channel_messages,
                    SUM(CASE WHEN outgoing = 1 THEN 1 ELSE 0 END) AS total_outgoing
                FROM messages
                """
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        return {
            "total_dms": row["total_dms"] or 0,
            "total_channel_messages": row["total_channel_messages"] or 0,
            "total_outgoing": row["total_outgoing"] or 0,
        }

    @staticmethod
    async def _activity_counts(*, contact_type: int, exclude: bool = False) -> dict[str, int]:
        """Get time-windowed counts for contacts/repeaters heard."""
        now = int(time.time())
        op = "!=" if exclude else "="
        async with db.readonly() as conn:
            async with conn.execute(
                f"""
                SELECT
                    SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) AS last_hour,
                    SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) AS last_24_hours,
                    SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) AS last_week
                FROM contacts
                WHERE type {op} ? AND last_seen IS NOT NULL
                """,
                (now - SECONDS_1H, now - SECONDS_24H, now - SECONDS_7D, contact_type),
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None  # Aggregate query always returns a row
        return {
            "last_hour": row["last_hour"] or 0,
            "last_24_hours": row["last_24_hours"] or 0,
            "last_week": row["last_week"] or 0,
        }

    @staticmethod
    async def _known_channels_active() -> dict[str, int]:
        """Count known channel keys with any traffic in each time window.

        Channel keys are stored canonically as uppercase hex, so we can avoid
        the old UPPER(...) join and aggregate per known channel directly.
        """
        now = int(time.time())
        async with db.readonly() as conn:
            async with conn.execute(
                """
                WITH known AS (
                    SELECT conversation_key, MAX(received_at) AS last_received_at
                    FROM messages
                    WHERE type = 'CHAN'
                      AND conversation_key IN (SELECT key FROM channels)
                    GROUP BY conversation_key
                )
                SELECT
                    SUM(CASE WHEN last_received_at >= ? THEN 1 ELSE 0 END) AS last_hour,
                    SUM(CASE WHEN last_received_at >= ? THEN 1 ELSE 0 END) AS last_24_hours,
                    SUM(CASE WHEN last_received_at >= ? THEN 1 ELSE 0 END) AS last_week
                FROM known
                """,
                (now - SECONDS_1H, now - SECONDS_24H, now - SECONDS_7D),
            ) as cursor:
                row = await cursor.fetchone()
        assert row is not None
        return {
            "last_hour": row["last_hour"] or 0,
            "last_24_hours": row["last_24_hours"] or 0,
            "last_week": row["last_week"] or 0,
        }

    @staticmethod
    async def _packets_per_hour_72h() -> list[dict[str, int]]:
        """Return packet counts bucketed by hour for the last 72 hours."""
        now = int(time.time())
        cutoff = now - SECONDS_72H
        # Bucket timestamps to the start of each hour
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT (timestamp / 3600) * 3600 AS hour_ts, COUNT(*) AS count
                FROM raw_packets
                WHERE timestamp >= ?
                GROUP BY hour_ts
                ORDER BY hour_ts
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [{"timestamp": row["hour_ts"], "count": row["count"]} for row in rows]

    @staticmethod
    async def _packet_shape_24h() -> tuple[dict[str, int | float], dict[str, int | float]]:
        """Bucket the last 24h of raw packets by hop hash width and region scope.

        Both buckets come from one fetch so the snapshot only scans the packet
        table once. Returns ``(path_hash_width, region_scope)``.
        """
        now = int(time.time())
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT data FROM raw_packets WHERE timestamp >= ?",
                (now - SECONDS_24H,),
            ) as cursor:
                rows = await cursor.fetchall()
        return bucket_path_hash_widths(rows), bucket_region_scope(rows)

    @staticmethod
    async def _region_scope_senders_24h() -> dict[str, int | float]:
        """Count distinct channel-message senders who scoped at least one send.

        Sender attribution requires having decrypted the message, so this only
        covers channels we hold keys for — a narrower population than the
        packet-level count, but a self-validating one: a message we decrypted is
        provably not a corrupt capture, so this number needs no noise floor.

        Scoping is read from ``messages.transport_code`` where present, falling
        back to the linked raw packet for rows stored before region tagging
        existed. Senders are keyed by ``sender_key`` where resolved, falling back
        to ``sender_name`` — one physical operator may run several nodes, hence
        "senders" rather than "users".
        """
        now = int(time.time())
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT m.id, m.sender_key, m.sender_name, m.transport_code, p.data
                FROM messages m
                LEFT JOIN raw_packets p ON p.message_id = m.id
                WHERE m.type = 'CHAN' AND m.outgoing = 0 AND m.received_at >= ?
                """,
                (now - SECONDS_24H,),
            ) as cursor:
                rows = await cursor.fetchall()

        senders: set[str] = set()
        scoped_senders: set[str] = set()
        for row in rows:
            identity = row["sender_key"] or row["sender_name"]
            if not identity:
                continue
            senders.add(identity)

            if row["transport_code"] is not None:
                scoped_senders.add(identity)
                continue
            raw = row["data"]
            if raw is None:
                continue
            envelope = parse_packet_envelope(bytes(raw))
            if envelope is not None and envelope.transport_codes is not None:
                scoped_senders.add(identity)

        total = len(senders)
        scoped = len(scoped_senders)
        return {
            "total_senders": total,
            "scoped_senders": scoped,
            "scoped_senders_pct": (scoped / total) * 100 if total else 0.0,
        }

    @staticmethod
    async def get_all() -> dict:
        """Aggregate all statistics from existing tables.

        Each helper acquires its own lock; there's no requirement that the
        whole snapshot be atomic. If we ever wanted a consistent snapshot
        we'd batch all queries into a single ``db.readonly()`` and use
        ``_in_conn`` helpers, but statistics are intentionally approximate.
        """
        now = int(time.time())

        async with db.readonly() as conn:
            # Top 5 busiest channels in last 24h
            async with conn.execute(
                """
                SELECT m.conversation_key, COALESCE(c.name, m.conversation_key) AS channel_name,
                       COUNT(*) AS message_count
                FROM messages m
                LEFT JOIN channels c ON m.conversation_key = c.key
                WHERE m.type = 'CHAN' AND m.received_at >= ?
                GROUP BY m.conversation_key
                ORDER BY COUNT(*) DESC
                LIMIT 5
                """,
                (now - SECONDS_24H,),
            ) as cursor:
                rows = await cursor.fetchall()
            busiest_channels_24h = [
                {
                    "channel_key": row["conversation_key"],
                    "channel_name": row["channel_name"],
                    "message_count": row["message_count"],
                }
                for row in rows
            ]

            # Entity counts
            async with conn.execute(
                "SELECT COUNT(*) AS cnt FROM contacts WHERE type != 2"
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            contact_count: int = row["cnt"]

            async with conn.execute(
                "SELECT COUNT(*) AS cnt FROM contacts WHERE type = 2"
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            repeater_count: int = row["cnt"]

            async with conn.execute("SELECT COUNT(*) AS cnt FROM channels") as cursor:
                row = await cursor.fetchone()
            assert row is not None
            channel_count: int = row["cnt"]

            # Packet split
            async with conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN message_id IS NOT NULL THEN 1 ELSE 0 END) AS decrypted
                FROM raw_packets
                """
            ) as cursor:
                pkt_row = await cursor.fetchone()
            assert pkt_row is not None
            total_packets = pkt_row["total"] or 0
            decrypted_packets = pkt_row["decrypted"] or 0
            undecrypted_packets = total_packets - decrypted_packets

        # These each acquire their own lock. The snapshot isn't atomic across
        # them — fine for stats, which are approximate by nature.
        message_totals = await StatisticsRepository.get_database_message_totals()
        contacts_heard = await StatisticsRepository._activity_counts(contact_type=2, exclude=True)
        repeaters_heard = await StatisticsRepository._activity_counts(contact_type=2)
        known_channels_active = await StatisticsRepository._known_channels_active()
        path_hash_width_24h, region_scope = await StatisticsRepository._packet_shape_24h()
        region_scope.update(await StatisticsRepository._region_scope_senders_24h())
        packets_per_hour_72h = await StatisticsRepository._packets_per_hour_72h()

        return {
            "busiest_channels_24h": busiest_channels_24h,
            "contact_count": contact_count,
            "repeater_count": repeater_count,
            "channel_count": channel_count,
            "total_packets": total_packets,
            "decrypted_packets": decrypted_packets,
            "undecrypted_packets": undecrypted_packets,
            "total_dms": message_totals["total_dms"],
            "total_channel_messages": message_totals["total_channel_messages"],
            "total_outgoing": message_totals["total_outgoing"],
            "contacts_heard": contacts_heard,
            "repeaters_heard": repeaters_heard,
            "known_channels_active": known_channels_active,
            "path_hash_width_24h": path_hash_width_24h,
            "region_scope_24h": region_scope,
            "packets_per_hour_72h": packets_per_hour_72h,
        }
