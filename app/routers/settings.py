import logging
import os
import tempfile
import time
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.background_tasks import spawn
from app.cors import normalize_origins
from app.database import db
from app.models import (
    CONTACT_TYPE_REPEATER,
    AppSettings,
    BackupExport,
    BackupExportRequest,
    BackupInspectRequest,
    BackupInspectResult,
    BackupRestoreJob,
    BackupRestoreRequest,
    BackupRestoreResult,
    BackupRestoreV2Request,
    NotificationDestinationsUpdate,
    TelemetryAlertRules,
    UiPreferences,
)
from app.net.egress_guard import EgressBlocked, egress_detail, error_code, validate_destination
from app.region_scope import normalize_region_scope
from app.repository import AppSettingsRepository, ChannelRepository, ContactRepository
from app.services.backup import (
    PRIVATE_KEY_WARNING,
    BackupError,
    backup_filename,
    export_backup_v2,
    export_document,
    export_json,
    normalize_backup_request,
    parse_backup_model,
    read_backup_body,
    restore_json,
    write_sqlite_backup,
)
from app.services.backup_crypto import (
    PASSPHRASE_MIN_CHARS,
    BackupCryptoError,
    encrypt_backup_async,
)
from app.telemetry_interval import (
    DEFAULT_TELEMETRY_INTERVAL_HOURS,
    TELEMETRY_INTERVAL_OPTIONS_HOURS,
    clamp_telemetry_interval,
    legal_interval_options,
    next_run_timestamp_utc,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])

MAX_TRACKED_TELEMETRY_REPEATERS = 8
MAX_TRACKED_TELEMETRY_CONTACTS = 8


class AppSettingsUpdate(BaseModel):
    ui_preferences: UiPreferences | None = Field(
        default=None,
        description="Interface preferences shared by every device reaching this instance",
    )
    max_radio_contacts: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description=(
            "Configured radio contact capacity used for maintenance thresholds and "
            "background refill behavior"
        ),
    )
    auto_decrypt_dm_on_advert: bool | None = Field(
        default=None,
        description="Whether to attempt historical DM decryption on new contact advertisement",
    )
    advert_interval: int | None = Field(
        default=None,
        ge=0,
        description="Periodic advertisement interval in seconds (0 = disabled, minimum 3600)",
    )
    flood_scope: str | None = Field(
        default=None,
        description="Outbound flood scope / region name (empty = disabled)",
    )
    known_regions: list[str] | None = Field(
        default=None,
        description=(
            "Region scope names used to decode incoming transport-scoped packets "
            "(stored without a leading '#')"
        ),
    )
    blocked_keys: list[str] | None = Field(
        default=None,
        description="Public keys whose messages are hidden from the UI",
    )
    blocked_names: list[str] | None = Field(
        default=None,
        description="Display names whose messages are hidden from the UI",
    )
    discovery_blocked_types: list[int] | None = Field(
        default=None,
        description=(
            "Contact type codes (1=Client, 2=Repeater, 3=Room, 4=Sensor) whose "
            "advertisements should not create new contacts"
        ),
    )
    auto_resend_channel: bool | None = Field(
        default=None,
        description="Auto-resend channel messages once if no echo heard within 2 seconds",
    )
    enable_message_poll_fallback: bool | None = Field(
        default=None,
        description=(
            "Radio workaround: poll the radio for waiting messages every 10 seconds "
            "instead of auditing once an hour"
        ),
    )
    force_channel_slot_reconfigure: bool | None = Field(
        default=None,
        description="Radio workaround: write the channel into its slot before every channel send",
    )
    load_with_autoevict: bool | None = Field(
        default=None,
        description=(
            "Radio workaround: let the radio overwrite its oldest non-favorite contact "
            "when its table is full. Applies at the next connection. Turning it off "
            "does not clear the radio's own preference"
        ),
    )
    auto_clock_wraparound: bool | None = Field(
        default=None,
        description=(
            "Radio workaround: fix a radio clock still ahead after a reboot by rolling "
            "it over (0xFFFFFFFF, then the current time). Applies at the next connection"
        ),
    )
    auth_allowed_origins: list[str] | None = Field(
        default=None,
        description=(
            "Other sites (scheme://host[:port]) allowed to call this API from a browser "
            "with credentials (CORS). Replaces the whole list; empty means same-origin "
            "only. No wildcard, no path"
        ),
    )
    push_allow_private_endpoints: bool | None = Field(
        default=None,
        description="Accept Web Push endpoints on a private network (self-hosted UnifiedPush)",
    )
    radio_offline_grace_s: int | None = Field(
        default=None,
        ge=0,
        le=3600,
        description=(
            "Seconds the radio may stay unreachable before radio_offline is sent (0 = at once)"
        ),
    )
    share_enabled: bool | None = Field(
        default=None,
        description="Consent to the link and file sharing service (off by default)",
    )
    share_shorten_mode: Literal["auto", "always", "never"] | None = Field(
        default=None, description="When the composer shortens a link"
    )
    share_preview_policy: Literal["always", "private", "never"] | None = Field(
        default=None, description="When a shared image or link preview is fetched automatically"
    )
    push_private_endpoints_grandfathered: bool | None = Field(
        default=None,
        description="Set to false to dismiss the upgrade notice (setting it also clears it)",
    )
    telemetry_interval_hours: int | None = Field(
        default=None,
        description=(
            "Preferred tracked-repeater telemetry interval in hours. "
            f"Must be one of {list(TELEMETRY_INTERVAL_OPTIONS_HOURS)}. "
            "Effective interval is clamped up to the shortest legal value "
            "based on the current tracked-repeater count."
        ),
    )
    telemetry_routed_hourly: bool | None = Field(
        default=None,
        description=(
            "When enabled, tracked repeaters with a direct or routed (non-flood) "
            "path are polled every hour instead of on the normal scheduled interval."
        ),
    )
    stale_contact_days: int | None = Field(
        default=None,
        ge=0,
        le=3650,
        description="Automatic stale-contact purge in days (0 = disabled)",
    )
    raw_packet_retention_days: int | None = Field(
        default=None,
        ge=0,
        le=3650,
        description=(
            "Automatic prune of undecrypted raw packets older than N days (0 = disabled). "
            "Pruned packets can no longer be decrypted by channel/contact keys added later."
        ),
    )
    telemetry_alert_rules: TelemetryAlertRules | None = Field(
        default=None,
        description="Global telemetry alert rules plus optional per-node overrides",
    )
    notification_destinations: NotificationDestinationsUpdate | None = Field(
        default=None,
        description="SMTP, webhook and Apprise destinations; omit/null secrets keep stored values",
    )

    @field_validator("auth_allowed_origins")
    @classmethod
    def _normalize_allowed_origins(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else normalize_origins(value)


class BlockKeyRequest(BaseModel):
    key: str = Field(description="Public key to toggle block status")


class BlockNameRequest(BaseModel):
    name: str = Field(description="Display name to toggle block status")


class FavoriteRequest(BaseModel):
    type: Literal["channel", "contact"] = Field(description="'channel' or 'contact'")
    id: str = Field(description="Channel key or contact public key")


class FavoriteToggleResponse(BaseModel):
    type: Literal["channel", "contact"]
    id: str
    favorite: bool


class PinRequest(BaseModel):
    type: Literal["channel", "contact"] = Field(description="'channel' or 'contact'")
    id: str = Field(description="Channel key or contact public key")


class PinToggleResponse(BaseModel):
    type: Literal["channel", "contact"]
    id: str
    pinned: bool


class MuteChannelRequest(BaseModel):
    key: str = Field(description="Channel key to mute or unmute")
    duration_seconds: int | None = Field(
        default=None,
        description=(
            "When omitted, toggle mute. 0 unmutes, -1 mutes indefinitely, "
            "a positive value mutes for that many seconds."
        ),
    )


class MuteChannelToggleResponse(BaseModel):
    key: str
    muted: bool
    muted_until: int | None = None


class TrackedTelemetryRequest(BaseModel):
    public_key: str = Field(description="Public key of the repeater to toggle tracking")


class TelemetrySchedule(BaseModel):
    """Surface of telemetry scheduling derivations for the UI.

    ``preferred_hours`` is the stored user choice. ``effective_hours`` is the
    value the scheduler actually uses (preferred, clamped up to the shortest
    legal interval given the current tracked-repeater count). ``options``
    lists the subset of the menu that is legal at the current count; the UI
    should hide anything not in this list. ``next_run_at`` is the Unix
    timestamp (seconds, UTC) of the next scheduled cycle, or ``None`` when
    no repeaters are tracked (nothing to schedule).
    """

    preferred_hours: int = Field(description="User's saved telemetry interval preference")
    effective_hours: int = Field(description="Scheduler's clamped interval")
    options: list[int] = Field(description="Legal interval choices at the current count")
    tracked_count: int = Field(description="Number of repeaters currently tracked")
    max_tracked: int = Field(description="Maximum number of repeaters that can be tracked")
    next_run_at: int | None = Field(
        default=None,
        description="Unix timestamp (UTC seconds) of the next scheduled flood cycle",
    )
    routed_hourly: bool = Field(
        default=False,
        description="Whether hourly routed/direct-path telemetry is enabled",
    )
    next_routed_run_at: int | None = Field(
        default=None,
        description=(
            "Unix timestamp (UTC seconds) of the next hourly routed/direct check, "
            "or None when routed_hourly is off or no repeaters are tracked"
        ),
    )


class TrackedTelemetryResponse(BaseModel):
    tracked_telemetry_repeaters: list[str] = Field(
        description="Current list of tracked repeater public keys"
    )
    names: dict[str, str] = Field(
        description="Map of public key to display name for tracked repeaters"
    )
    schedule: TelemetrySchedule = Field(description="Current scheduling state")


def _build_schedule(
    tracked_count: int,
    preferred_hours: int | None,
    routed_hourly: bool = False,
) -> TelemetrySchedule:
    pref = (
        preferred_hours
        if preferred_hours in TELEMETRY_INTERVAL_OPTIONS_HOURS
        else DEFAULT_TELEMETRY_INTERVAL_HOURS
    )
    effective = clamp_telemetry_interval(pref, tracked_count)
    has_tracked = tracked_count > 0
    return TelemetrySchedule(
        preferred_hours=pref,
        effective_hours=effective,
        options=legal_interval_options(tracked_count),
        tracked_count=tracked_count,
        max_tracked=MAX_TRACKED_TELEMETRY_REPEATERS,
        next_run_at=next_run_timestamp_utc(effective) if has_tracked else None,
        routed_hourly=routed_hourly,
        next_routed_run_at=(next_run_timestamp_utc(1) if has_tracked and routed_hourly else None),
    )


async def _present_settings(settings: AppSettings) -> AppSettings:
    from app.services.directory import annotate_directory_available
    from app.telemetry_alerts import present_notification_destinations

    redacted = settings.model_copy(
        update={
            "notification_destinations": present_notification_destinations(
                settings.notification_destinations
            )
        }
    )
    return await annotate_directory_available(redacted)


async def _settings_response() -> AppSettings:
    return await _present_settings(await AppSettingsRepository.get())


@router.get("", response_model=AppSettings)
async def get_settings() -> AppSettings:
    """Get current application settings."""
    return await _settings_response()


@router.patch("", response_model=AppSettings)
async def update_settings(update: AppSettingsUpdate) -> AppSettings:
    """Update application settings.

    Settings are persisted to the database and survive restarts.
    """
    kwargs = {}
    if update.ui_preferences is not None:
        kwargs["ui_preferences"] = update.ui_preferences

    if update.max_radio_contacts is not None:
        logger.info("Updating max_radio_contacts to %d", update.max_radio_contacts)
        kwargs["max_radio_contacts"] = update.max_radio_contacts

    if update.auto_decrypt_dm_on_advert is not None:
        logger.info("Updating auto_decrypt_dm_on_advert to %s", update.auto_decrypt_dm_on_advert)
        kwargs["auto_decrypt_dm_on_advert"] = update.auto_decrypt_dm_on_advert

    if update.advert_interval is not None:
        # Enforce minimum 1-hour interval; 0 means disabled
        interval = update.advert_interval
        if 0 < interval < 3600:
            interval = 3600
        logger.info("Updating advert_interval to %d", interval)
        kwargs["advert_interval"] = interval

    # Known regions for scope decoding. Normalize to user-facing form (no leading
    # '#'), trim blanks, and dedupe case-insensitively while preserving order.
    known_regions_changed = False
    if update.known_regions is not None:
        cleaned_regions: list[str] = []
        seen_regions: set[str] = set()
        for raw_name in update.known_regions:
            name = (raw_name or "").strip()
            if name.startswith("#"):
                name = name[1:].strip()
            if name and name.lower() not in seen_regions:
                seen_regions.add(name.lower())
                cleaned_regions.append(name)
        current = await AppSettingsRepository.get()
        known_regions_changed = cleaned_regions != current.known_regions
        kwargs["known_regions"] = cleaned_regions

    # Block lists
    if update.blocked_keys is not None:
        kwargs["blocked_keys"] = [k.lower() for k in update.blocked_keys]
    if update.blocked_names is not None:
        kwargs["blocked_names"] = update.blocked_names

    # Discovery blocked types
    if update.discovery_blocked_types is not None:
        # Only allow valid contact type codes (1-4)
        valid = [t for t in update.discovery_blocked_types if t in (1, 2, 3, 4)]
        kwargs["discovery_blocked_types"] = sorted(set(valid))

    # Auto-resend channel
    if update.auto_resend_channel is not None:
        kwargs["auto_resend_channel"] = update.auto_resend_channel

    # Radio workarounds (Settings → Radio → Advanced). The repository refreshes the
    # in-memory copy the radio code reads (app/radio_workarounds.py).
    poll_fallback_changed = False
    for field in (
        "enable_message_poll_fallback",
        "force_channel_slot_reconfigure",
        "load_with_autoevict",
        "auto_clock_wraparound",
    ):
        value = getattr(update, field)
        if value is not None:
            logger.info("Updating %s to %s", field, value)
            kwargs[field] = value
    # CORS allowlist (already normalized by the model). The repository refreshes
    # the in-memory copy the CORS middleware reads (app/cors.py).
    if update.auth_allowed_origins is not None:
        logger.info("Updating auth_allowed_origins to %s", update.auth_allowed_origins)
        kwargs["auth_allowed_origins"] = update.auth_allowed_origins

    if update.enable_message_poll_fallback is not None:
        current_settings = await AppSettingsRepository.get()
        poll_fallback_changed = (
            update.enable_message_poll_fallback != current_settings.enable_message_poll_fallback
        )

    # Telemetry interval preference. Invalid values fall back to default
    # rather than 400-ing so a stale client can't brick settings saves.
    if update.telemetry_interval_hours is not None:
        raw_interval = update.telemetry_interval_hours
        if raw_interval not in TELEMETRY_INTERVAL_OPTIONS_HOURS:
            logger.warning(
                "telemetry_interval_hours=%r is not in the menu; defaulting to %d",
                raw_interval,
                DEFAULT_TELEMETRY_INTERVAL_HOURS,
            )
            raw_interval = DEFAULT_TELEMETRY_INTERVAL_HOURS
        logger.info("Updating telemetry_interval_hours to %d", raw_interval)
        kwargs["telemetry_interval_hours"] = raw_interval

    # Telemetry routed hourly
    if update.telemetry_routed_hourly is not None:
        logger.info("Updating telemetry_routed_hourly to %s", update.telemetry_routed_hourly)
        kwargs["telemetry_routed_hourly"] = update.telemetry_routed_hourly

    if update.stale_contact_days is not None:
        logger.info("Updating stale_contact_days to %d", update.stale_contact_days)
        kwargs["stale_contact_days"] = update.stale_contact_days
    if update.raw_packet_retention_days is not None:
        logger.info("Updating raw_packet_retention_days to %d", update.raw_packet_retention_days)
        kwargs["raw_packet_retention_days"] = update.raw_packet_retention_days

    if update.telemetry_alert_rules is not None:
        # Pass the PATCH fragment through; persist merges unset/null overrides.
        kwargs["telemetry_alert_rules"] = update.telemetry_alert_rules

    if update.notification_destinations is not None:
        from app.secret_fields import SecretRequiredError

        try:
            await _check_webhook_destination(update.notification_destinations)
        except SecretRequiredError as exc:
            # A kept secret never follows a destination change: 400 secret_required.
            raise HTTPException(status_code=400, detail=exc.detail()) from None
        kwargs["notification_destinations"] = update.notification_destinations

    if update.push_allow_private_endpoints is not None:
        logger.info(
            "Updating push_allow_private_endpoints to %s", update.push_allow_private_endpoints
        )
        kwargs["push_allow_private_endpoints"] = update.push_allow_private_endpoints
        # An explicit choice replaces the upgrade default and its notice.
        kwargs["push_private_endpoints_grandfathered"] = False
    if update.radio_offline_grace_s is not None:
        logger.info("Updating radio_offline_grace_s to %s", update.radio_offline_grace_s)
        kwargs["radio_offline_grace_s"] = update.radio_offline_grace_s
    if update.share_enabled is not None:
        logger.info("Updating share_enabled to %s", update.share_enabled)
        kwargs["share_enabled"] = update.share_enabled
    if update.share_shorten_mode is not None:
        kwargs["share_shorten_mode"] = update.share_shorten_mode
    if update.share_preview_policy is not None:
        kwargs["share_preview_policy"] = update.share_preview_policy
    if update.push_private_endpoints_grandfathered is False:
        kwargs["push_private_endpoints_grandfathered"] = False

    # Flood scope
    flood_scope_changed = False
    if update.flood_scope is not None:
        kwargs["flood_scope"] = normalize_region_scope(update.flood_scope)
        flood_scope_changed = True

    if kwargs:
        result = await AppSettingsRepository.update(**kwargs)

        # The poll loop chose its interval before sleeping: restart it so a switch
        # to (or from) the 10-second fallback applies now, not after an hour.
        if poll_fallback_changed:
            from app.radio_sync import restart_message_polling_if_running

            await restart_message_polling_if_running()

        # Apply flood scope to radio immediately if changed
        if flood_scope_changed:
            from app.services.flood_scope import set_radio_flood_scope
            from app.services.radio_runtime import radio_runtime as radio_manager

            if radio_manager.is_connected:
                try:
                    scope = result.flood_scope
                    async with radio_manager.radio_operation("set_flood_scope") as mc:
                        await set_radio_flood_scope(
                            mc, scope, fw_ver=radio_manager.firmware_ver_code
                        )
                        logger.info("Applied flood_scope=%r to radio", scope or "(disabled)")
                except Exception as e:
                    logger.warning("Failed to apply flood_scope to radio: %s", e)

        # Retroactively tag stored messages when the region list changed. Runs in
        # the background since it walks every channel message with a retained raw
        # packet; clients refetch conversations to see updated badges.
        if known_regions_changed:
            from app.services.messages import schedule_region_backfill

            logger.info("known_regions changed; scheduling region backfill")
            schedule_region_backfill(result.known_regions)

        return await _present_settings(result)

    return await _settings_response()


@router.post("/favorites/toggle", response_model=FavoriteToggleResponse)
async def toggle_favorite(request: FavoriteRequest) -> FavoriteToggleResponse:
    """Toggle a conversation's favorite status."""
    if request.type == "contact":
        contact = await ContactRepository.get_by_key(request.id)
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")
        new_value = not contact.favorite
        await ContactRepository.set_favorite(request.id, new_value)
        logger.info("%s contact favorite: %s", "Added" if new_value else "Removed", request.id[:12])
        # When newly favorited, load to radio immediately for DM ACK support
        if new_value:
            from app.radio_sync import ensure_contact_on_radio

            spawn(ensure_contact_on_radio(request.id, force=True))
    else:
        channel = await ChannelRepository.get_by_key(request.id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        new_value = not channel.favorite
        await ChannelRepository.set_favorite(request.id, new_value)
        logger.info("%s channel favorite: %s", "Added" if new_value else "Removed", request.id[:12])

    return FavoriteToggleResponse(type=request.type, id=request.id, favorite=new_value)


@router.post("/pins/toggle", response_model=PinToggleResponse)
async def toggle_pin(request: PinRequest) -> PinToggleResponse:
    """Toggle a conversation's pinned status."""
    if request.type == "contact":
        contact = await ContactRepository.get_by_key(request.id)
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")
        new_value = not contact.pinned
        await ContactRepository.set_pinned(request.id, new_value)
        logger.info("%s contact pin: %s", "Added" if new_value else "Removed", request.id[:12])
    else:
        channel = await ChannelRepository.get_by_key(request.id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        new_value = not channel.pinned
        await ChannelRepository.set_pinned(request.id, new_value)
        logger.info("%s channel pin: %s", "Added" if new_value else "Removed", request.id[:12])

    return PinToggleResponse(type=request.type, id=request.id, pinned=new_value)


@router.post("/muted-channels/toggle", response_model=MuteChannelToggleResponse)
async def toggle_muted_channel(request: MuteChannelRequest) -> MuteChannelToggleResponse:
    """Set or toggle a channel's muted status, optionally with an expiry."""
    channel = await ChannelRepository.get_by_key(request.key)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    duration = request.duration_seconds
    if duration is None:
        muted = not channel.muted
        muted_until = None
    elif duration == 0:
        muted = False
        muted_until = None
    elif duration < 0:
        muted = True
        muted_until = None
    else:
        muted = True
        muted_until = int(time.time()) + duration
    await ChannelRepository.set_muted(request.key, muted, muted_until)
    logger.info(
        "%s channel mute: %s until=%s",
        "Muted" if muted else "Unmuted",
        request.key[:12],
        muted_until,
    )

    refreshed = await ChannelRepository.get_by_key(request.key)
    if refreshed:
        from app.websocket import broadcast_event

        broadcast_event("channel", refreshed.model_dump())
        return MuteChannelToggleResponse(
            key=request.key, muted=refreshed.muted, muted_until=refreshed.muted_until
        )

    return MuteChannelToggleResponse(key=request.key, muted=muted, muted_until=muted_until)


@router.post("/blocked-keys/toggle", response_model=AppSettings)
async def toggle_blocked_key(request: BlockKeyRequest) -> AppSettings:
    """Toggle a public key's blocked status."""
    logger.info("Toggling blocked key: %s", request.key[:12])
    return await _present_settings(await AppSettingsRepository.toggle_blocked_key(request.key))


@router.post("/blocked-names/toggle", response_model=AppSettings)
async def toggle_blocked_name(request: BlockNameRequest) -> AppSettings:
    """Toggle a display name's blocked status."""
    logger.info("Toggling blocked name: %s", request.name)
    return await _present_settings(await AppSettingsRepository.toggle_blocked_name(request.name))


@router.post("/tracked-telemetry/toggle", response_model=TrackedTelemetryResponse)
async def toggle_tracked_telemetry(request: TrackedTelemetryRequest) -> TrackedTelemetryResponse:
    """Toggle periodic telemetry collection for a repeater.

    Max 8 repeaters may be tracked. Returns 409 if the limit is reached and
    the requested repeater is not already tracked.
    """
    key = request.public_key.lower()
    settings = await AppSettingsRepository.get()
    current = settings.tracked_telemetry_repeaters

    async def _resolve_names(keys: list[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        for k in keys:
            contact = await ContactRepository.get_by_key(k)
            names[k] = contact.name if contact and contact.name else k[:12]
        return names

    if key in current:
        # Remove
        new_list = [k for k in current if k != key]
        logger.info("Removing repeater %s from tracked telemetry", key[:12])
        await AppSettingsRepository.update(tracked_telemetry_repeaters=new_list)
        return TrackedTelemetryResponse(
            tracked_telemetry_repeaters=new_list,
            names=await _resolve_names(new_list),
            schedule=_build_schedule(
                len(new_list),
                settings.telemetry_interval_hours,
                settings.telemetry_routed_hourly,
            ),
        )

    # Validate it's a repeater
    contact = await ContactRepository.get_by_key(key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    if contact.type != CONTACT_TYPE_REPEATER:
        raise HTTPException(status_code=400, detail="Contact is not a repeater")

    if len(current) >= MAX_TRACKED_TELEMETRY_REPEATERS:
        names = await _resolve_names(current)
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Limit of {MAX_TRACKED_TELEMETRY_REPEATERS} tracked repeaters reached",
                "tracked_telemetry_repeaters": current,
                "names": names,
            },
        )

    new_list = current + [key]
    logger.info("Adding repeater %s to tracked telemetry", key[:12])
    from app.telemetry_alerts import alerting_off_patch

    await AppSettingsRepository.update(
        tracked_telemetry_repeaters=new_list,
        telemetry_alert_rules=alerting_off_patch(key),
    )
    return TrackedTelemetryResponse(
        tracked_telemetry_repeaters=new_list,
        names=await _resolve_names(new_list),
        schedule=_build_schedule(
            len(new_list),
            settings.telemetry_interval_hours,
            settings.telemetry_routed_hourly,
        ),
    )


@router.get("/tracked-telemetry/schedule", response_model=TelemetrySchedule)
async def get_telemetry_schedule() -> TelemetrySchedule:
    """Return the current telemetry scheduling derivation.

    The UI uses this to render the interval dropdown (legal options),
    surface saved-vs-effective when they differ, and show the next-run-at
    timestamp so users know when the next cycle will fire.

    """
    app_settings = await AppSettingsRepository.get()
    return _build_schedule(
        len(app_settings.tracked_telemetry_repeaters),
        app_settings.telemetry_interval_hours,
        app_settings.telemetry_routed_hourly,
    )


# ---------------------------------------------------------------------------
# Tracked contact telemetry (non-repeater LPP telemetry collection)
# ---------------------------------------------------------------------------


class TrackedTelemetryContactsResponse(BaseModel):
    tracked_telemetry_contacts: list[str] = Field(
        description="Current list of tracked contact public keys"
    )
    names: dict[str, str] = Field(
        description="Map of public key to display name for tracked contacts"
    )
    schedule: TelemetrySchedule = Field(description="Current scheduling state")


@router.post("/tracked-telemetry-contacts/toggle", response_model=TrackedTelemetryContactsResponse)
async def toggle_tracked_telemetry_contact(
    request: TrackedTelemetryRequest,
) -> TrackedTelemetryContactsResponse:
    """Toggle periodic LPP telemetry collection for any contact.

    Max 8 contacts may be tracked. The daily check ceiling is shared with
    tracked repeaters.
    """
    key = request.public_key.lower()
    settings = await AppSettingsRepository.get()
    current = settings.tracked_telemetry_contacts

    async def _resolve_names(keys: list[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        for k in keys:
            contact = await ContactRepository.get_by_key(k)
            names[k] = contact.name if contact and contact.name else k[:12]
        return names

    if key in current:
        # Remove
        new_list = [k for k in current if k != key]
        logger.info("Removing contact %s from tracked telemetry", key[:12])
        await AppSettingsRepository.update(tracked_telemetry_contacts=new_list)
        return TrackedTelemetryContactsResponse(
            tracked_telemetry_contacts=new_list,
            names=await _resolve_names(new_list),
            schedule=_build_schedule(
                len(new_list),
                settings.telemetry_interval_hours,
                settings.telemetry_routed_hourly,
            ),
        )

    # Validate contact exists and is not a repeater (repeaters use tracked_telemetry_repeaters)
    contact = await ContactRepository.get_by_key(key)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    if contact.type == CONTACT_TYPE_REPEATER:
        raise HTTPException(
            status_code=400,
            detail="Repeaters use the dedicated repeater telemetry tracking list",
        )

    if len(current) >= MAX_TRACKED_TELEMETRY_CONTACTS:
        names = await _resolve_names(current)
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Limit of {MAX_TRACKED_TELEMETRY_CONTACTS} tracked contacts reached",
                "tracked_telemetry_contacts": current,
                "names": names,
            },
        )

    new_list = current + [key]
    logger.info("Adding contact %s to tracked telemetry", key[:12])
    from app.telemetry_alerts import alerting_off_patch

    await AppSettingsRepository.update(
        tracked_telemetry_contacts=new_list,
        telemetry_alert_rules=alerting_off_patch(key),
    )
    return TrackedTelemetryContactsResponse(
        tracked_telemetry_contacts=new_list,
        names=await _resolve_names(new_list),
        schedule=_build_schedule(
            len(new_list),
            settings.telemetry_interval_hours,
            settings.telemetry_routed_hourly,
        ),
    )


class TelemetryAlertLatch(BaseModel):
    public_key: str
    rule_id: str
    consecutive_misses: int = 0
    last_fired_at: int | None = None
    last_value: float | None = None


class TelemetryAlertCatalogNode(BaseModel):
    public_key: str
    name: str
    alerting: bool


class TelemetryAlertCatalog(BaseModel):
    metrics: list[str]
    latches: list[TelemetryAlertLatch]
    tracked: list[TelemetryAlertCatalogNode] = Field(default_factory=list)


class NotificationDestTestRequest(BaseModel):
    channel: Literal["email", "webhook", "apprise"]


class NotificationDestTestResponse(BaseModel):
    status: str
    channel: Literal["email", "webhook", "apprise"]


@router.get("/telemetry-alert-catalog", response_model=TelemetryAlertCatalog)
async def get_telemetry_alert_catalog() -> TelemetryAlertCatalog:
    """Available metrics from last tracked snapshots, plus current latches."""
    from app.repository.contact_telemetry import ContactTelemetryRepository
    from app.repository.repeater_telemetry import RepeaterTelemetryRepository
    from app.repository.telemetry_alert_state import TelemetryAlertStateRepository
    from app.telemetry_alerts import catalog_metrics_from_snapshot, resolve_node_rules

    settings = await AppSettingsRepository.get()
    tracked = list(
        dict.fromkeys(settings.tracked_telemetry_repeaters + settings.tracked_telemetry_contacts)
    )
    metrics: set[str] = set()
    nodes: list[TelemetryAlertCatalogNode] = []
    for key in tracked:
        snap = await RepeaterTelemetryRepository.get_latest(key)
        if snap is None:
            snap = await ContactTelemetryRepository.get_latest(key)
        data = snap.get("data") if snap else None
        metrics |= catalog_metrics_from_snapshot(data if isinstance(data, dict) else None)
        contact = await ContactRepository.get_by_key(key)
        name = contact.name if contact and contact.name else key[:12]
        nodes.append(
            TelemetryAlertCatalogNode(
                public_key=key,
                name=name,
                alerting=resolve_node_rules(settings.telemetry_alert_rules, key).alerting,
            )
        )
    latches = await TelemetryAlertStateRepository.list_latched(tracked)
    return TelemetryAlertCatalog(
        metrics=sorted(metrics),
        latches=[TelemetryAlertLatch(**row) for row in latches],
        tracked=nodes,
    )


_WEBHOOK_TEST_MESSAGES = {
    "timeout": "The destination did not answer in time",
    "tls_error": "The TLS handshake with the destination failed (certificate or protocol)",
    "service_error": "The destination answered with an HTTP error",
}


def _webhook_test_detail(exc: BaseException) -> dict[str, Any]:
    code = error_code(exc)
    if code not in _WEBHOOK_TEST_MESSAGES:
        return {"code": "notification_test_failed", "message": "Notification test failed"}
    detail: dict[str, Any] = {"code": code, "message": _WEBHOOK_TEST_MESSAGES[code]}
    if isinstance(exc, httpx.HTTPStatusError):
        detail["params"] = {"status": exc.response.status_code}
    return detail


async def _check_webhook_destination(patch: NotificationDestinationsUpdate) -> None:
    """Refuse at save a webhook or Apprise URL the guard would refuse at send
    (400, coded), and a kept secret that would follow a destination change
    (``SecretRequiredError``).

    Checked only when the URLs or their private-network flag change, so saving an
    unrelated field never depends on the destination hosts' DNS.
    """
    from app.net.apprise_urls import AppriseUrlRefused, split_apprise_urls, validate_apprise_urls
    from app.telemetry_alerts import merge_notification_destinations

    current = (await AppSettingsRepository.get()).notification_destinations
    # Raises SecretRequiredError when a kept secret would follow a new destination.
    merged_all = merge_notification_destinations(current, patch)
    apprise = merged_all.apprise
    if apprise.urls.strip() and (
        apprise.urls != current.apprise.urls
        or apprise.allow_private_network != current.apprise.allow_private_network
    ):
        try:
            await validate_apprise_urls(
                split_apprise_urls(apprise.urls),
                allow_private_network=apprise.allow_private_network,
            )
        except AppriseUrlRefused as exc:
            raise HTTPException(status_code=400, detail=exc.detail()) from None
    if patch.webhook is None:
        return
    merged = merged_all.webhook
    url = merged.url.strip()
    if not url or (
        url == current.webhook.url.strip()
        and merged.allow_private_network == current.webhook.allow_private_network
    ):
        return
    try:
        await validate_destination(url, allow_private_network=merged.allow_private_network)
    except EgressBlocked as exc:
        raise HTTPException(status_code=400, detail=egress_detail(exc.code)) from None


@router.post(
    "/notification-destinations/test",
    response_model=NotificationDestTestResponse,
)
async def test_notification_destinations(
    request: NotificationDestTestRequest,
) -> NotificationDestTestResponse:
    """Send a fixed test payload on one channel. Does not create a latch."""
    from app.net.apprise_urls import AppriseUrlRefused
    from app.notifications.apprise_medium import AppriseSendFailed
    from app.notify import (
        TEST_ALERT_PAYLOAD,
        describe_webhook_failure,
        send_apprise_alert,
        send_email_alert,
        send_webhook_alert,
    )

    dest = (await AppSettingsRepository.get()).notification_destinations
    try:
        if request.channel == "email":
            await send_email_alert(dest, TEST_ALERT_PAYLOAD)
        elif request.channel == "apprise":
            await send_apprise_alert(dest, TEST_ALERT_PAYLOAD)
        else:
            await send_webhook_alert(dest, TEST_ALERT_PAYLOAD)
    except AppriseUrlRefused as exc:
        raise HTTPException(status_code=400, detail=exc.detail()) from None
    except AppriseSendFailed as exc:
        # Line, scheme, host and HTTP status: the guard passed for that URL.
        raise HTTPException(status_code=502, detail=exc.detail()) from None
    except ValueError as exc:
        logger.info("Notification test (%s) refused: %s", request.channel, exc)
        raise HTTPException(
            status_code=400,
            detail={
                "code": "notification_destination_invalid",
                "message": "Notification destination is not configured or invalid",
            },
        ) from None
    except EgressBlocked as exc:
        # Refused before any connection, so the answer time says nothing either.
        logger.info("Notification test (%s) refused by the egress guard: %s", request.channel, exc)
        raise HTTPException(status_code=400, detail=egress_detail(exc.code)) from None
    except Exception as exc:
        # Never the exception text, in the answer (a LAN-scanning oracle) or in
        # the log (an HTTPStatusError carries the full webhook URL and its token).
        if request.channel != "webhook":
            logger.warning("Notification test (email) failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail={"code": "notification_test_failed", "message": "Notification test failed"},
            ) from None
        logger.warning(
            "Notification test (webhook) failed: %s",
            describe_webhook_failure(exc, dest.webhook.url or ""),
        )
        # The guard passed: the destination is public, or one the user allowed
        # on their network, so the failure class may be shown.
        raise HTTPException(status_code=502, detail=_webhook_test_detail(exc)) from None
    return NotificationDestTestResponse(status="ok", channel=request.channel)


@router.get("/tracked-telemetry-contacts/schedule", response_model=TelemetrySchedule)
async def get_contact_telemetry_schedule() -> TelemetrySchedule:
    """Return the current telemetry scheduling derivation for contacts."""
    app_settings = await AppSettingsRepository.get()
    return _build_schedule(
        len(app_settings.tracked_telemetry_contacts),
        app_settings.telemetry_interval_hours,
        app_settings.telemetry_routed_hourly,
    )


@router.get("/backup/database")
async def download_database_backup(background_tasks: BackgroundTasks) -> FileResponse:
    """Download a consistent snapshot of meshcore.db.

    The radio private key is NOT in this file (Meshloom keeps it in memory
    only; the full backup, ``POST /backup/export``, includes it). Everything
    else in the live database is, including channel keys, VAPID keys, fanout
    configs, and push subscriptions. This endpoint never replaces the live
    database.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="meshcore-backup-", suffix=".db")
    os.close(fd)
    try:
        await write_sqlite_backup(db, tmp_path)
    except Exception:
        os.unlink(tmp_path)
        raise
    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        tmp_path,
        filename="meshcore.db",
        media_type="application/vnd.sqlite3",
        headers={"X-Private-Key-Warning": PRIVATE_KEY_WARNING},
    )


@router.get("/backup/json", response_model=BackupExport)
async def download_json_backup() -> BackupExport:
    """Meshloom data only (``meshloom-backup-v1``): contacts, channels, settings,
    groups. No radio identity or radio settings: the full backup has them."""
    try:
        return await export_json()
    except BackupError as exc:
        raise _backup_http_error(exc) from None


def _backup_http_error(exc: BackupError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail())


@router.post("/backup/export")
async def export_full_backup(request: BackupExportRequest | None = None) -> JSONResponse:
    """The full backup (``meshloom-backup-v2``), optionally encrypted.

    Holds the radio private key and, when the radio stores them, its Wi-Fi
    credentials. Works with the radio disconnected (Meshloom part only, with
    warnings). Built from the export dump (never ``response_model``, which would
    write the secret masks into the file).
    """
    passphrase = request.passphrase.get_secret_value() if request and request.passphrase else None
    if passphrase is not None and len(passphrase) < PASSPHRASE_MIN_CHARS:
        raise _backup_http_error(
            BackupError(
                "backup_passphrase_too_short",
                message=f"The passphrase needs at least {PASSPHRASE_MIN_CHARS} characters",
            )
        )
    try:
        doc = await export_backup_v2(encrypted=bool(passphrase))
    except BackupError as exc:
        raise _backup_http_error(exc) from None
    body = export_document(doc)
    if passphrase:
        try:
            body = await encrypt_backup_async(
                body, passphrase, public_key=doc.identity.public_key if doc.identity else None
            )
        except BackupCryptoError as exc:
            # Too large for the restore to decrypt: say so now, not after a reflash.
            raise _backup_http_error(
                BackupError(
                    exc.code,
                    message="This backup is too large to encrypt: download it without encryption",
                )
            ) from None
    ascii_name, utf8_name = backup_filename(doc.radio.name if doc.radio else None, doc.exported_at)
    return JSONResponse(
        body,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
            ),
            "Cache-Control": "no-store",
            # Not secret; readable even when the body is encrypted.
            "X-Backup-Warnings": ",".join(doc.warnings),
        },
    )


_BACKUP_BODY_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    }
}


@router.post(
    "/backup/inspect", response_model=BackupInspectResult, openapi_extra=_BACKUP_BODY_OPENAPI
)
async def inspect_full_backup(http_request: Request) -> BackupInspectResult:
    """Summarise a backup file and what restoring it would do; writes nothing.

    Body ``{"backup": <file>, "passphrase": "..."}``. A file without ``format``
    is a ``meshloom-backup-v1`` file. Over 64 MiB: 413 ``backup_too_large``.
    """
    try:
        body = await read_backup_body(http_request)
        request = parse_backup_model(BackupInspectRequest, normalize_backup_request(body))
        from app.services.backup_restore import inspect_backup

        return await inspect_backup(
            request.backup,
            request.passphrase.get_secret_value() if request.passphrase else None,
        )
    except BackupError as exc:
        raise _backup_http_error(exc) from None


async def restore_json_backup(request: BackupRestoreRequest) -> BackupRestoreResult:
    """Merge a Meshloom-only (v1) JSON backup into the live database.

    Requires ``confirm=true``. Does not replace meshcore.db and does not delete
    messages, packets, or rows missing from the file.
    """
    try:
        return await restore_json(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/backup/restore",
    responses={
        200: {"model": BackupRestoreResult, "description": "Flat v1 body: merged now"},
        202: {"description": "Body with `backup`: the restore job started, `{job_id}`"},
        413: {"description": "Body over 64 MiB: `backup_too_large`"},
    },
    openapi_extra=_BACKUP_BODY_OPENAPI,
)
async def restore_backup(http_request: Request) -> Any:
    """Restore a backup.

    - Flat body (no ``backup`` key): the v1 request, ``{"confirm": true, ...v1
      fields}``, merged synchronously (scripts). A flat body whose ``format`` is
      not a v1 format answers 400 ``backup_use_full_restore``.
    - ``{"confirm": true, "backup": <file>, "passphrase"?, "steps"?, ...}``: any
      file (v1, v2, encrypted) through the restore job: 202 ``{"job_id"}``, then
      ``GET /backup/restore/jobs/{job_id}``. Identity refusals are 409, before
      anything is written.

    A body over 64 MiB is refused with 413 ``backup_too_large`` before parsing.
    """
    from app.services.backup import V1_FORMATS, open_backup_document
    from app.services.backup_restore import (
        RestoreOptions,
        restore_job_running,
        start_restore_backup_v2,
    )

    try:
        body = await read_backup_body(http_request)
        if "backup" not in body:
            if body.get("format") is not None and body.get("format") not in V1_FORMATS:
                raise BackupError(
                    "backup_use_full_restore",
                    message="This file is a full backup: restore it with the backup field",
                )
            return await restore_json_backup(parse_backup_model(BackupRestoreRequest, body))
        request = parse_backup_model(BackupRestoreV2Request, normalize_backup_request(body))
        if not request.confirm:
            raise BackupError("backup_confirm_required", message="Restore requires confirm=true")
        if restore_job_running():
            # Early answer, before the KDF of an encrypted file (one at a time,
            # tens of MiB); start_restore_backup_v2 stays the authoritative guard.
            raise BackupError(
                "backup_restore_running", status=409, message="A restore is already running"
            )
        opened = await open_backup_document(
            request.backup,
            request.passphrase.get_secret_value() if request.passphrase else None,
        )
        job_id = await start_restore_backup_v2(
            opened,
            RestoreOptions(
                steps=request.steps,
                replace_live_identity=request.replace_live_identity,
                wipe_history=request.wipe_history,
                expected_live_public_key=request.expected_live_public_key,
            ),
        )
    except BackupError as exc:
        raise _backup_http_error(exc) from None
    return JSONResponse({"job_id": job_id}, status_code=202)


def _job_not_found() -> HTTPException:
    return _backup_http_error(
        BackupError("backup_restore_job_not_found", status=404, message="No such restore job")
    )


@router.get("/backup/restore/jobs/latest", response_model=BackupRestoreJob)
async def get_latest_backup_restore_job() -> BackupRestoreJob:
    """The most recent restore job, running or finished (lets the UI find it
    again after a reload). Holds step statuses, counts and warnings only."""
    from app.services.backup_restore import get_latest_restore_job

    job = get_latest_restore_job()
    if job is None:
        raise _job_not_found()
    return job


@router.get("/backup/restore/jobs/{job_id}", response_model=BackupRestoreJob)
async def get_backup_restore_job(job_id: str) -> BackupRestoreJob:
    from app.services.backup_restore import get_restore_job

    job = get_restore_job(job_id)
    if job is None:
        raise _job_not_found()
    return job
