"""Backups: the Meshloom-only JSON (v1), the full backup (v2), the SQLite file.

- ``meshloom-backup-v1`` (``export_json`` / ``restore_json``): contacts,
  channels, settings, groups and push preferences, merged into the live DB.
- ``meshloom-backup-v2``: the v1 fields plus the radio identity, every radio
  setting, Wi-Fi credentials stored in the radio and the rest of the Meshloom
  settings (``BACKUP_COVERAGE`` lists every column and table with a decision).
- The SQLite file download (``write_sqlite_backup``) never replaces the live DB.
"""

import logging
import re
import time
import unicodedata
from typing import Any, Literal, TypeVar
from urllib.parse import quote

import aiosqlite
from pydantic import BaseModel, SecretStr, ValidationError

from app.database import Database
from app.models import (
    AppSettings,
    BackupChannel,
    BackupContact,
    BackupEnvelope,
    BackupExport,
    BackupExportV2,
    BackupFanoutConfig,
    BackupIdentity,
    BackupPushSubscription,
    BackupRadioProxy,
    BackupRestoreRequest,
    BackupRestoreResult,
    BackupV1Document,
    BackupVapid,
    BackupWifi,
    ContactGroup,
    ContactUpsert,
    MeshloomExtra,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactGroupRepository,
    ContactRepository,
)

logger = logging.getLogger(__name__)

BACKUP_FORMAT = "meshloom-backup-v1"  # older files may still say remoteterm-backup-v1
BACKUP_FORMAT_V2 = "meshloom-backup-v2"

PRIVATE_KEY_WARNING = (
    "The radio private key is NOT in this file (Meshloom keeps it in memory only). "
    "The full backup in Settings > Database includes it."
)

CoverageDecision = Literal["included", "excluded"]

# Every app_settings column ("app_settings.<column>") and every table ("<table>")
# with a decision and its reason. tests/test_backup_coverage.py fails when a
# column or a table is missing here (extra entries are allowed).
_V1 = "v1 settings"
_RUNTIME = "runtime, derived or one-shot migration marker"
_COMMUNITY = "Community opt-in is consent: never restored silently"
_TRANSPORT = "machine-specific radio transport"
_IDENTITY = "handled by the identity step"
_HISTORY = "history or cache (the database download covers history)"
BACKUP_COVERAGE: dict[str, tuple[CoverageDecision, str]] = {
    **{
        f"app_settings.{column}": ("included", _V1)
        for column in (
            "max_radio_contacts",
            "auto_decrypt_dm_on_advert",
            "advert_interval",
            "flood_scope",
            "known_regions",
            "blocked_keys",
            "blocked_names",
            "rejected_channels",
            "discovery_blocked_types",
            "tracked_telemetry_repeaters",
            "tracked_telemetry_contacts",
            "auto_resend_channel",
            "telemetry_interval_hours",
            "telemetry_routed_hourly",
            "stale_contact_days",
            "raw_packet_retention_days",
            "telemetry_alert_rules",
            "notification_destinations",
            "auto_update",
            "auto_update_window_start",
            "auto_update_window_end",
            "auto_update_weekdays",
            "enable_message_poll_fallback",
            "force_channel_slot_reconfigure",
            "load_with_autoevict",
            "auto_clock_wraparound",
            "ui_preferences",
        )
    },
    "app_settings.push_defaults": ("included", "v1 push_defaults"),
    "app_settings.push_conversation_overrides": ("included", "v1 push_conversation_overrides"),
    "app_settings.vapid_subject": ("included", "v1 vapid_subject"),
    "app_settings.path_hash_one_byte_opt_in": ("included", "meshloom_extra"),
    "app_settings.vapid_private_key": ("included", "meshloom_extra.vapid (host without push)"),
    "app_settings.vapid_public_key": ("included", "meshloom_extra.vapid (host without push)"),
    **{
        f"app_settings.radio_proxy_{name}": ("included", "meshloom_extra.radio_proxy")
        for name in ("enabled", "bind", "port", "max_clients")
    },
    **{
        f"app_settings.{column}": ("excluded", _COMMUNITY)
        for column in (
            "community_enabled",
            "community_iata",
            "community_broker_host",
            "community_api_base",
            "community_status_clear",
            "community_status_last",
            "community_waiting_for_position",
            "community_opt_out_pending",
        )
    },
    **{
        f"app_settings.{column}": ("excluded", _TRANSPORT)
        for column in (
            "radio_transport",
            "radio_serial_port",
            "radio_serial_baudrate",
            "radio_tcp_host",
            "radio_tcp_port",
            "radio_ble_address",
            "radio_ble_pin",
            "radio_previous_transport",
        )
    },
    **{
        f"app_settings.{column}": ("excluded", _IDENTITY)
        for column in (
            "radio_bound_public_key",
            "radio_identity_state",
            "radio_mismatch_previous_public_key",
            "radio_mismatch_new_public_key",
            "radio_mismatch_new_name",
        )
    },
    "app_settings.wifi_prompt_state": ("excluded", "per-host prompt state, never a secret"),
    **{
        f"app_settings.{column}": ("excluded", _RUNTIME)
        for column in (
            "last_message_times",
            "last_advert_time",
            "last_notified_update_version",
            "push_conversations",
            "preferences_migrated",
            "radio_transport_env_imported",
            "legacy_env_settings_imported",
            "community_env_imported",
        )
    },
    "app_settings.id": ("excluded", "internal"),
    "contacts": ("included", "v1 contacts"),
    "channels": ("included", "v1 channels (secrets and region scope)"),
    "contact_groups": ("included", "v1 groups"),
    "contact_group_members": ("included", "v1 groups"),
    "app_settings": ("included", "per column, above"),
    "fanout_configs": ("included", "meshloom_extra.fanout_configs (bots restored disabled)"),
    "push_subscriptions": ("included", "meshloom_extra.push_subscriptions (host without push)"),
    **dict.fromkeys(
        (
            "messages",
            "raw_packets",
            "contact_advert_paths",
            "contact_name_history",
            "repeater_telemetry_history",
            "contact_telemetry_history",
            "telemetry_alert_state",
            "directory_hop_cache",
            "repeater_pane_cache",
        ),
        ("excluded", _HISTORY),
    ),
}


async def write_sqlite_backup(database: Database, dest_path: str) -> None:
    """Copy the live DB to ``dest_path`` via SQLite's backup API (lock held)."""
    dest = await aiosqlite.connect(dest_path)
    try:
        async with database.readonly() as conn:
            await conn.backup(dest)
        await dest.commit()
    finally:
        await dest.close()


def _contact_to_backup(contact) -> BackupContact:
    return BackupContact(
        public_key=contact.public_key,
        name=contact.name,
        type=contact.type,
        flags=contact.flags,
        direct_path=contact.direct_path,
        direct_path_len=contact.direct_path_len if contact.direct_path_len >= 0 else None,
        direct_path_hash_mode=(
            contact.direct_path_hash_mode if contact.direct_path_hash_mode >= 0 else None
        ),
        direct_path_updated_at=contact.direct_path_updated_at,
        route_override_path=contact.route_override_path,
        route_override_len=contact.route_override_len,
        route_override_hash_mode=contact.route_override_hash_mode,
        last_advert=contact.last_advert,
        lat=contact.lat,
        lon=contact.lon,
        last_seen=contact.last_seen,
        last_contacted=contact.last_contacted,
        first_seen=contact.first_seen,
        favorite=contact.favorite,
        pinned=contact.pinned,
    )


def _channel_to_backup(channel) -> BackupChannel:
    return BackupChannel(
        key=channel.key,
        name=channel.name,
        is_hashtag=channel.is_hashtag,
        flood_scope_override=channel.flood_scope_override,
        path_hash_mode_override=channel.path_hash_mode_override,
        favorite=channel.favorite,
        pinned=channel.pinned,
        muted=channel.muted,
        muted_until=channel.muted_until,
        membership=channel.membership,
    )


async def export_json() -> BackupExport:
    contacts = await ContactRepository.get_all(limit=100_000)
    channels = await ChannelRepository.get_all()
    settings = await AppSettingsRepository.get()
    groups = await ContactGroupRepository.list_all()
    exported_overrides: dict[str, bool | dict[str, bool]] = {
        key: dict(flags)
        for key, flags in (await AppSettingsRepository.get_push_conversation_overrides()).items()
    }
    return BackupExport(
        format=BACKUP_FORMAT,
        exported_at=int(time.time()),
        contacts=[_contact_to_backup(c) for c in contacts],
        channels=[_channel_to_backup(c) for c in channels],
        settings=settings,
        groups=groups,
        push_defaults=dict(await AppSettingsRepository.get_push_defaults()),
        push_conversation_overrides=exported_overrides,
        vapid_subject=await AppSettingsRepository.get_vapid_subject(),
    )


async def restore_json(request: BackupRestoreRequest) -> BackupRestoreResult:
    if not request.confirm:
        raise ValueError(
            "Restore requires confirm=true. This merges into the live database "
            "and does not replace meshcore.db or delete messages/packets."
        )

    contacts_upserted = 0
    for item in request.contacts:
        await ContactRepository.upsert(
            ContactUpsert(
                public_key=item.public_key,
                name=item.name,
                type=item.type,
                flags=item.flags,
                direct_path=item.direct_path,
                direct_path_len=item.direct_path_len,
                direct_path_hash_mode=item.direct_path_hash_mode,
                direct_path_updated_at=item.direct_path_updated_at,
                route_override_path=item.route_override_path,
                route_override_len=item.route_override_len,
                route_override_hash_mode=item.route_override_hash_mode,
                last_advert=item.last_advert,
                lat=item.lat,
                lon=item.lon,
                last_seen=item.last_seen,
                last_contacted=item.last_contacted,
                first_seen=item.first_seen,
            )
        )
        await ContactRepository.set_favorite(item.public_key, item.favorite)
        await ContactRepository.set_pinned(item.public_key, item.pinned)
        contacts_upserted += 1

    channels_upserted = 0
    for item in request.channels:
        await ChannelRepository.upsert(
            item.key, item.name, item.is_hashtag, membership=item.membership
        )
        await ChannelRepository.set_favorite(item.key, item.favorite)
        await ChannelRepository.set_pinned(item.key, item.pinned)
        await ChannelRepository.set_muted(item.key, item.muted, item.muted_until)
        await ChannelRepository.update_flood_scope_override(item.key, item.flood_scope_override)
        await ChannelRepository.update_path_hash_mode_override(
            item.key, item.path_hash_mode_override
        )
        channels_upserted += 1

    settings_updated = False
    if request.settings is not None:
        await _restore_settings(request.settings)
        settings_updated = True
    if request.push_defaults is not None:
        await AppSettingsRepository.set_push_defaults(request.push_defaults)
        settings_updated = True
    if request.push_conversation_overrides is not None:
        await AppSettingsRepository.set_push_conversation_overrides(
            request.push_conversation_overrides
        )
        settings_updated = True
    if request.vapid_subject is not None:
        await AppSettingsRepository.set_vapid_subject(request.vapid_subject)
        settings_updated = True

    groups_upserted = 0
    for group in request.groups:
        await _restore_group(group)
        groups_upserted += 1

    logger.info(
        "JSON restore merged contacts=%d channels=%d settings=%s groups=%d",
        contacts_upserted,
        channels_upserted,
        settings_updated,
        groups_upserted,
    )
    return BackupRestoreResult(
        contacts_upserted=contacts_upserted,
        channels_upserted=channels_upserted,
        settings_updated=settings_updated,
        groups_upserted=groups_upserted,
    )


async def _restore_settings(settings: AppSettings) -> None:
    await AppSettingsRepository.update(
        max_radio_contacts=settings.max_radio_contacts,
        auto_decrypt_dm_on_advert=settings.auto_decrypt_dm_on_advert,
        advert_interval=settings.advert_interval,
        flood_scope=settings.flood_scope,
        known_regions=settings.known_regions,
        blocked_keys=settings.blocked_keys,
        blocked_names=settings.blocked_names,
        rejected_channels=settings.rejected_channels,
        discovery_blocked_types=settings.discovery_blocked_types,
        tracked_telemetry_repeaters=settings.tracked_telemetry_repeaters,
        tracked_telemetry_contacts=settings.tracked_telemetry_contacts,
        auto_resend_channel=settings.auto_resend_channel,
        telemetry_interval_hours=settings.telemetry_interval_hours,
        telemetry_routed_hourly=settings.telemetry_routed_hourly,
        stale_contact_days=settings.stale_contact_days,
        raw_packet_retention_days=settings.raw_packet_retention_days,
        telemetry_alert_rules=settings.telemetry_alert_rules,
        notification_destinations=settings.notification_destinations,
        auto_update=settings.auto_update,
        auto_update_window_start=settings.auto_update_window_start,
        auto_update_window_end=settings.auto_update_window_end,
        auto_update_weekdays=settings.auto_update_weekdays,
        # Through update(), so the in-memory copy the radio code reads follows.
        enable_message_poll_fallback=settings.enable_message_poll_fallback,
        force_channel_slot_reconfigure=settings.force_channel_slot_reconfigure,
        load_with_autoevict=settings.load_with_autoevict,
        auto_clock_wraparound=settings.auto_clock_wraparound,
    )


async def _restore_group(group: ContactGroup) -> None:
    existing_id = await ContactGroupRepository.find_id_by_name(group.name)
    if existing_id is None:
        created = await ContactGroupRepository.create(group.name)
        existing_id = created.id
        if group.sort_order != created.sort_order:
            await ContactGroupRepository.update(existing_id, sort_order=group.sort_order)
    await ContactGroupRepository.set_members(existing_id, group.public_keys)


# -- Full backup (v2): errors and document parsing ----------------------------


class BackupError(Exception):
    """A coded backup failure. ``code`` and ``params`` never hold a secret."""

    def __init__(
        self,
        code: str,
        *,
        status: int = 400,
        message: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.message = message or code
        self.params = params or {}
        super().__init__(code)

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "params": self.params}


V1_FORMATS = ("meshloom-backup-v1", "remoteterm-backup-v1")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


def validation_error_fields(exc: ValidationError) -> list[str]:
    """Field paths only: never an input value."""
    return sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})


def parse_backup_model(model: type[_ModelT], raw: object) -> _ModelT:
    """Validate a backup model; a failure is a 400 ``backup_invalid`` listing
    field paths only."""
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise BackupError(
            "backup_invalid",
            message="The backup file is not valid",
            params={"fields": validation_error_fields(exc)[:20]},
        ) from None


def normalize_backup_document(raw: object) -> object:
    """A backup without ``format`` is a ``meshloom-backup-v1`` file (old exports)."""
    if isinstance(raw, dict) and "format" not in raw:
        return {**raw, "format": BACKUP_FORMAT}
    return raw


def normalize_backup_request(raw: object) -> object:
    """Same rule for the ``backup`` field of an inspect or restore request."""
    if isinstance(raw, dict) and "backup" in raw:
        return {**raw, "backup": normalize_backup_document(raw["backup"])}
    return raw


async def open_backup_document(
    doc: BackupV1Document | BackupExportV2 | BackupEnvelope, passphrase: str | None
) -> BackupV1Document | BackupExportV2:
    """Decrypt an envelope, then check a v2 identity derives to its public key."""
    from app.decoder import derive_public_key
    from app.services.backup_crypto import BackupCryptoError, decrypt_backup_async

    if isinstance(doc, BackupEnvelope):
        if not passphrase:
            raise BackupError(
                "backup_passphrase_required",
                message="This backup is encrypted: enter its passphrase",
            )
        try:
            payload = await decrypt_backup_async(doc.model_dump(), passphrase)
        except BackupCryptoError as exc:
            raise BackupError(exc.code) from None
        if payload.get("format") != BACKUP_FORMAT_V2:
            raise BackupError("backup_bad_envelope")
        doc = parse_backup_model(BackupExportV2, payload)
    if isinstance(doc, BackupExportV2) and doc.identity is not None:
        private_key = doc.identity.private_key
        if private_key is not None:
            try:
                derived = derive_public_key(bytes.fromhex(private_key.get_secret_value())).hex()
            except Exception:
                derived = None
            if derived is None or derived != (doc.identity.public_key or ""):
                raise BackupError(
                    "backup_identity_invalid",
                    message="The private key in this backup does not match its public key",
                )
    return doc


def export_document(doc: BackupExportV2) -> dict[str, Any]:
    """The file content: the only dump that reveals the secret fields."""
    return doc.model_dump(mode="json", context={"backup_export": True})


def backup_filename(radio_name: str | None, exported_at: int) -> tuple[str, str]:
    """(ASCII ``filename=``, RFC 5987 ``filename*=`` value) for the download."""
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(exported_at))
    name = (radio_name or "").strip() or "meshloom"
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_name).strip("-.") or "meshloom"
    utf8_name = re.sub(r"[\x00-\x1f/\\]+", "-", name)
    return (
        f"meshloom-backup-{slug[:40]}-{stamp}.json",
        quote(f"meshloom-backup-{utf8_name[:40]}-{stamp}.json", safe=""),
    )


# -- Full backup (v2): export ---------------------------------------------------


async def export_meshloom_extra() -> MeshloomExtra:
    """Meshloom settings outside the v1 document (see BACKUP_COVERAGE)."""
    from app.fanout.manager import is_reserved_fanout_id
    from app.repository.fanout import FanoutConfigRepository
    from app.repository.push_subscriptions import PushSubscriptionRepository
    from app.repository.radio_proxy import RadioProxyRepository

    vapid_private, vapid_public = await AppSettingsRepository.get_vapid_keys()
    proxy = await RadioProxyRepository.get()
    return MeshloomExtra(
        path_hash_one_byte_opt_in=await AppSettingsRepository.get_path_hash_one_byte_opt_in(),
        vapid=BackupVapid(private_key=SecretStr(vapid_private), public_key=vapid_public)
        if vapid_private and vapid_public
        else None,
        push_subscriptions=[
            BackupPushSubscription(
                endpoint=sub["endpoint"],
                p256dh=sub["p256dh"],
                auth=sub["auth"],
                label=sub.get("label") or "",
                language=sub.get("language") or "fr",
            )
            for sub in await PushSubscriptionRepository.get_all()
        ],
        radio_proxy=BackupRadioProxy(
            enabled=proxy.enabled, bind=proxy.bind, port=proxy.port, max_clients=proxy.max_clients
        ),
        fanout_configs=[
            BackupFanoutConfig(
                id=str(cfg["id"]),
                type=str(cfg["type"]),
                name=str(cfg.get("name") or ""),
                enabled=bool(cfg.get("enabled")),
                config=dict(cfg.get("config") or {}),
                scope=dict(cfg.get("scope") or {}),
                sort_order=int(cfg.get("sort_order") or 0),
            )
            for cfg in await FanoutConfigRepository.get_all()
            # Managed by Meshloom (system:*): never in a file.
            if not is_reserved_fanout_id(str(cfg["id"]))
        ],
    )


def _identity_for(live_public_key: str | None) -> BackupIdentity:
    """The keystore key, included only when it derives to the live radio key."""
    from app import keystore

    key = keystore.get_private_key()
    public = keystore.get_public_key()
    if (
        key is not None
        and public is not None
        and live_public_key
        and public.hex() == (live_public_key.lower())
    ):
        return BackupIdentity(
            public_key=live_public_key,
            private_key=SecretStr(key.hex()),
            private_key_status="included",
        )
    return BackupIdentity(
        public_key=live_public_key,
        private_key=None,
        private_key_status="export_disabled" if keystore.last_export_disabled() else "unavailable",
    )


async def _read_wifi(cli) -> tuple[BackupWifi | None, list[str]]:
    """Wi-Fi credentials stored in the radio (read live, never stored by Meshloom)."""
    from app.services.companion_cli import (
        CliError,
        wifi_enabled,
        wifi_ssid,
        wifi_stored_password,
    )

    try:
        ssid = await wifi_ssid(cli)
        password = await wifi_stored_password(cli)
    except CliError:
        return None, ["wifi_read_failed"]
    try:
        enabled: bool | None = await wifi_enabled(cli)
    except CliError:
        enabled = None
    if ssid is None or not password.get_secret_value():
        # Nothing stored (or an open network, which reads the same).
        return None, ["wifi_not_stored"]
    try:
        return BackupWifi(ssid=ssid, password=password, enabled=enabled), []
    except ValidationError:
        return None, ["wifi_read_failed"]


async def export_backup_v2() -> BackupExportV2:
    """The full backup. Works with the radio disconnected (Meshloom part only)."""
    from app.radio import RadioOperationError
    from app.services.backup_radio import read_radio_node_settings
    from app.services.companion_cli import CliError, companion_cli_session
    from app.services.radio_runtime import radio_runtime
    from app.version_info import get_app_build_info

    base = await export_json()
    extra = await export_meshloom_extra()
    warnings: list[str] = []
    identity: BackupIdentity | None = None
    radio = None
    wifi: BackupWifi | None = None
    manager = radio_runtime.manager

    if not manager.is_connected:
        warnings.append("radio_not_connected")
    elif not manager.is_setup_complete:
        # The CLI session refuses before setup: its exit restarts auto-fetch,
        # which would pull queued messages before the handlers exist.
        warnings.append("radio_not_ready")
    else:
        try:
            async with companion_cli_session("backup_export") as cli:
                mc = manager.meshcore
                try:
                    capabilities = await cli.capabilities()
                except CliError:
                    capabilities = None
                cli_supported = capabilities is not None and capabilities.supported
                snapshot = await read_radio_node_settings(mc, cli if cli_supported else None)
                radio = snapshot.settings
                warnings.extend(snapshot.warnings)
                identity = _identity_for(snapshot.public_key)
                if cli_supported and capabilities is not None and capabilities.wifi:
                    wifi, wifi_warnings = await _read_wifi(cli)
                    warnings.extend(wifi_warnings)
        except (CliError, RadioOperationError):
            warnings.append("radio_not_ready")

    if identity is None or identity.private_key is None:
        warnings.append(
            "private_key_export_disabled"
            if identity is not None and identity.private_key_status == "export_disabled"
            else "private_key_unavailable"
        )
    try:
        version: str | None = get_app_build_info().version
    except Exception:
        version = None
    return BackupExportV2(
        **base.model_dump(exclude={"format"}),
        meshloom_version=version,
        warnings=list(dict.fromkeys(warnings)),
        identity=identity,
        radio=radio,
        wifi=wifi,
        meshloom_extra=extra,
    )
