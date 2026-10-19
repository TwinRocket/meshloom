"""Backups: the Meshloom-only JSON (v1), the full backup (v2), the SQLite file.

- ``meshloom-backup-v1`` (``export_json`` / ``restore_json``): contacts,
  channels, settings, groups and push preferences, merged into the live DB.
- ``meshloom-backup-v2``: the v1 fields plus the radio identity, every radio
  setting, Wi-Fi credentials stored in the radio and the rest of the Meshloom
  settings (``BACKUP_COVERAGE`` lists every column and table with a decision),
  and the optional named ``sections`` other features register
  (``app/services/backup_sections.py``, ``docs/dev/backup.md``).
- The SQLite file download (``write_sqlite_backup``) never replaces the live DB;
  the copy carries a marker that runs the restore hooks if it is put back.
"""

import ipaddress
import json
import logging
import re
import time
import unicodedata
from typing import Any, Literal, TypeVar
from urllib.parse import quote, urlsplit

import aiosqlite
from pydantic import BaseModel, SecretStr, ValidationError
from starlette.requests import Request

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
    NotificationDestinations,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactGroupRepository,
    ContactRepository,
)

# Imported for its side effect: the instance identities' on_restore hook, so it is
# registered before the lifespan runs the SQLite restore hooks.
from app.services import (
    backup_notifications,  # noqa: F401  registers the notifications section
    instance_identity,  # noqa: F401, E402
)
from app.services.backup_accounts import ACCOUNT_SECTIONS  # registers the users section
from app.services.backup_sections import (
    export_sections,
    prepare_sqlite_snapshot,
    refuse_identity_seeds,
    run_on_restore_hooks,
    sensitive_section_names,
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
# The v1 settings: exported in ``settings`` and written back by ``restore_json``
# (one list, so a column marked included here is never dropped on restore).
V1_SETTING_COLUMNS: tuple[str, ...] = (
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
    "push_allow_private_endpoints",
    "push_private_endpoints_grandfathered",
    "radio_offline_grace_s",
    "share_shorten_mode",
    "share_preview_policy",
    "notification_language",
    "ui_preferences",
)
BACKUP_COVERAGE: dict[str, tuple[CoverageDecision, str]] = {
    **{f"app_settings.{column}": ("included", _V1) for column in V1_SETTING_COLUMNS},
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
    "app_settings.radio_outage_started_at": (
        "excluded",
        "live outage state of this host's radio: meaningless on another host",
    ),
    "app_settings.push_private_endpoints_review": (
        "excluded",
        "notice of the upgrade on this host, meaningless on another one",
    ),
    "app_settings.share_enabled": (
        "excluded",
        "sharing consent: a copy restored elsewhere would enable an egress nobody clicked",
    ),
    "app_settings.share_instance_seed": (
        "excluded",
        "instance identity seed: never exported, created on first use, rotated by every restore",
    ),
    "app_settings.store_online": (
        "excluded",
        "Store consent: a copy restored elsewhere would enable an egress nobody clicked",
    ),
    "app_settings.wifi_prompt_state": ("excluded", "per-host prompt state, never a secret"),
    "app_settings.auth_allowed_origins": (
        "excluded",
        "access control for this host's addresses: never restored silently",
    ),
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
    # Local accounts (docs/dev/auth.md). Users travel in the ``users`` section
    # (``services/backup_accounts.py``), not in the v1 fields. Sessions and the
    # setup code never leave; the audit trail is not in the JSON backup but
    # stays in the SQLite download.
    "users": ("excluded", "the users backup section (services/backup_accounts.py)"),
    "sessions": ("excluded", "login sessions, never exported"),
    "pairing_codes": (
        "excluded",
        "device pairing codes (short-lived hashes), never exported, emptied in the SQLite download",
    ),
    "api_tokens": ("excluded", "the api_tokens backup section (services/backup_accounts.py)"),
    "recovery_codes": (
        "excluded",
        "TOTP recovery code hashes: inside the users backup section (services/backup_accounts.py)",
    ),
    "webauthn_credentials": (
        "excluded",
        "passkeys (public keys): inside the users backup section (services/backup_accounts.py)",
    ),
    "auth_audit": ("excluded", "audit trail: not in the JSON backup, kept in the SQLite download"),
    "auth_settings": (
        "excluded",
        "access settings of this host (networks, proxies, URLs) and the setup code hash",
    ),
    # Notification channels (MSHL-108, MSHL-117): the ``notifications`` backup section
    # (services/backup_notifications.py) holds the channels without their secrets and the
    # rules. The SQLite download carries the secrets, like ``notification_destinations``.
    "app_settings.notification_rules": (
        "included",
        "notifications section (services/backup_notifications.py)",
    ),
    "notification_channels": (
        "included",
        "notifications section, write-only fields never exported (secret_omitted)",
    ),
    "notification_deliveries": ("excluded", "delivery journal: runtime state"),
    "notification_counters": ("excluded", "hourly send counters: runtime state"),
    "notification_state": ("excluded", "dedup timestamps and outage state: runtime state"),
    # Plugins (docs/dev/plugins.md, "Data model"). The instances and their KV get a
    # backup section with the plugins API; the secrets never leave (emptied in the
    # SQLite download too).
    "plugins": ("excluded", "plugin instances: backup section with the plugins API"),
    "plugin_kv": ("excluded", "plugin storage: backup section with the plugins API"),
    "plugin_secrets": ("excluded", "plugin config secrets, never exported"),
    "plugin_send_quota": ("excluded", "plugin send counters: runtime state"),
    "plugin_send_guard": (
        "excluded",
        "plugin send budget counters and loop-guard stamps: runtime state",
    ),
    "plugin_send_limits": (
        "excluded",
        "limits the user lowered for the plugin send guards: instance tuning",
    ),
}


async def write_sqlite_backup(database: Database, dest_path: str) -> None:
    """Copy the live DB to ``dest_path`` via SQLite's backup API (lock held),
    then clear the identity seeds and the excluded secrets in the copy and mark
    it as a snapshot (``prepare_sqlite_snapshot``, ``run_sqlite_restore_hooks``)."""
    dest = await aiosqlite.connect(dest_path)
    try:
        async with database.readonly() as conn:
            await conn.backup(dest)
        # Cleared values must not survive in the free space of the copy's pages.
        await dest.execute("PRAGMA secure_delete = ON")
        await prepare_sqlite_snapshot(dest, int(time.time()))
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


def _omit_settings_secrets(settings: AppSettings) -> tuple[AppSettings, dict[str, bool]]:
    """The settings with every notification secret ``********`` (``app/secret_fields.py``),
    and the paths of the secrets that were set (``secret_omitted``)."""
    from app.secret_fields import NOTIFICATION_SECRET_KEYS
    from app.telemetry_alerts import redact_notification_destinations

    dumped = settings.notification_destinations.model_dump(by_alias=True)
    omitted = {
        f"settings.notification_destinations.{kind}.{key}": True
        for kind, keys in NOTIFICATION_SECRET_KEYS.items()
        for key in keys
        if isinstance(dumped.get(kind), dict) and dumped[kind].get(key)
    }
    redacted = settings.model_copy(
        update={
            "notification_destinations": redact_notification_destinations(
                settings.notification_destinations
            )
        }
    )
    return redacted, omitted


async def export_json(*, include_secrets: bool = False) -> BackupExport:
    """The ``meshloom-backup-v1`` document.

    Without ``include_secrets`` (the download, ``GET /settings/backup/json``)
    the SMTP password and the webhook secret read ``********`` and
    ``secret_omitted`` names them; restoring the file keeps the stored ones.
    The full backup passes ``include_secrets=True``.
    """
    contacts = await ContactRepository.get_all(limit=100_000)
    channels = await ChannelRepository.get_all()
    settings = await AppSettingsRepository.get()
    groups = await ContactGroupRepository.list_all()
    exported_overrides: dict[str, bool | dict[str, bool]] = {
        key: dict(flags)
        for key, flags in (await AppSettingsRepository.get_push_conversation_overrides()).items()
    }
    secret_omitted: dict[str, bool] = {}
    if not include_secrets:
        settings, secret_omitted = _omit_settings_secrets(settings)
    export = BackupExport(
        format=BACKUP_FORMAT,
        exported_at=int(time.time()),
        contacts=[_contact_to_backup(c) for c in contacts],
        channels=[_channel_to_backup(c) for c in channels],
        settings=settings,
        secret_omitted=secret_omitted,
        groups=groups,
        push_defaults=dict(await AppSettingsRepository.get_push_defaults()),
        push_conversation_overrides=exported_overrides,
        vapid_subject=await AppSettingsRepository.get_vapid_subject(),
    )
    await refuse_identity_seeds(export.model_dump(mode="json"))
    return export


async def restore_json(
    request: BackupRestoreRequest, *, run_hooks: bool = True
) -> BackupRestoreResult:
    """Merge the v1 fields. ``run_hooks=False`` for the full restore job, which
    runs the restore hooks itself, once, at its end."""
    if not request.confirm:
        raise ValueError(
            "Restore requires confirm=true. This merges into the live database "
            "and does not replace meshcore.db or delete messages/packets."
        )

    from app.services.companion_cli import CONTACT_FLAG_REMOTE_CLI

    contacts_upserted = 0
    for item in request.contacts:
        await ContactRepository.upsert(
            ContactUpsert(
                public_key=item.public_key,
                name=item.name,
                type=item.type,
                # Never grant the remote CLI over the mesh, even from a file.
                flags=item.flags & ~CONTACT_FLAG_REMOTE_CLI,
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
    warnings: list[str] = []
    if request.settings is not None:
        await _restore_settings(request.settings, warnings)
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
        from app.push.vapid import set_cached_vapid_subject

        await AppSettingsRepository.set_vapid_subject(request.vapid_subject)
        # The signing code reads the cached copy (as routers/push.py keeps it).
        set_cached_vapid_subject(request.vapid_subject)
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
    if run_hooks:
        await run_on_restore_hooks("json")
    return BackupRestoreResult(
        contacts_upserted=contacts_upserted,
        channels_upserted=channels_upserted,
        settings_updated=settings_updated,
        groups_upserted=groups_upserted,
        warnings=warnings,
    )


# The AppSettings fields a restore writes back (through update(), so the
# in-memory copy the radio code reads follows).
_RESTORED_SETTINGS = V1_SETTING_COLUMNS


async def _restore_settings(settings: AppSettings, warnings: list[str]) -> None:
    """Write back only the fields the file carries: an older file without a
    later field (``ui_preferences``...) keeps the current value."""
    present = settings.model_fields_set
    updates = {name: getattr(settings, name) for name in _RESTORED_SETTINGS if name in present}
    if "notification_destinations" in updates:
        updates["notification_destinations"] = await _restorable_destinations(
            updates["notification_destinations"], warnings
        )
    if updates:
        await AppSettingsRepository.update(**updates)


async def _restorable_destinations(
    dest: NotificationDestinations, warnings: list[str]
) -> NotificationDestinations:
    """A file that points a destination somewhere else gets it without the kept
    secret: the stored password or HMAC secret never follows a new host or URL."""
    from app.secret_fields import SecretRequiredError
    from app.telemetry_alerts import merge_notification_destinations

    current = (await AppSettingsRepository.get()).notification_destinations
    try:
        merge_notification_destinations(current, dest)
    except SecretRequiredError as exc:
        dumped = dest.model_dump(by_alias=True)
        for field in exc.keys:
            kind, key = field.split(".", 1)
            dumped[kind][key] = ""
            warnings.append(f"secret_dropped_destination_changed:{field}")
        return NotificationDestinations.model_validate(dumped)
    return dest


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


# A backup body (inspect, restore) is read before parsing, up to this size: a
# plain file with tens of thousands of contacts stays far below it.
MAX_BACKUP_BODY_BYTES = 64 * 1024 * 1024


async def read_backup_body(request: Request) -> dict[str, Any]:
    """The JSON object of an inspect or restore request, refused with a 413
    ``backup_too_large`` past ``MAX_BACKUP_BODY_BYTES`` (read in chunks, so
    an oversized body is never held whole)."""
    too_large = BackupError(
        "backup_too_large",
        status=413,
        message="This backup file is too large",
        params={"max_bytes": MAX_BACKUP_BODY_BYTES},
    )
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BACKUP_BODY_BYTES:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BACKUP_BODY_BYTES:
            raise too_large
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise BackupError(
            "backup_invalid", message="The backup file is not valid", params={"fields": []}
        )
    return parsed


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


async def _ensure_keystore_key(mc, live_public_key: str | None) -> None:
    """Export the key once more when the keystore has none.

    The keystore is filled once per connection by the post-connect setup; one
    export that timed out there would otherwise leave every full backup without
    the identity until the next reconnect. A firmware that refused the export
    (DISABLED) is not asked again, and a keystore key for another radio is left
    alone (the identity is then reported unavailable).
    """
    from app import keystore

    if not live_public_key or keystore.last_export_disabled():
        return
    if keystore.get_private_key() is not None:
        return
    try:
        await keystore.export_and_store_private_key(mc)
    except RuntimeError:
        # No answer at all: the backup reports the identity unavailable.
        logger.warning("Backup export: the radio did not answer the key export")


def _self_info_public_key(mc) -> str | None:
    """The public key meshcore cached from the last SELF_INFO, when the export's
    own appstart read failed."""
    info = getattr(mc, "self_info", None)
    key = info.get("public_key") if isinstance(info, dict) else None
    return key.lower() if isinstance(key, str) and key else None


async def _open_network_recorded(public_key: str | None, ssid: str) -> bool:
    """Meshloom recorded ``ssid`` as an open network stored in this radio (the
    Wi-Fi panel's ``wifi_prompt_state``), and no interrupted store casts doubt
    on it (the same rule as the panel's state)."""
    if not public_key:
        return False
    entry = (await AppSettingsRepository.get_wifi_prompt_state()).get(public_key.lower(), {})
    return entry.get("open_network_ssid") == ssid and "store_started_at" not in entry


async def _read_wifi(cli, public_key: str | None) -> tuple[BackupWifi | None, list[str]]:
    """Wi-Fi credentials stored in the radio (read live, never stored by Meshloom)
    and its on/off flag, kept even when no credentials are stored. An empty
    stored password is an open network only when Meshloom recorded one for this
    radio and SSID (the radio cannot tell it from "nothing stored")."""
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
    if ssid is not None and not password.get_secret_value():
        try:
            if await _open_network_recorded(public_key, ssid):
                return BackupWifi(
                    ssid=ssid, password=SecretStr(""), open_network=True, enabled=enabled
                ), []
        except ValidationError:
            return None, ["wifi_read_failed"]
    if ssid is None or not password.get_secret_value():
        # Nothing stored (or an open network Meshloom did not record, which reads
        # the same): keep the on/off flag, which also governs credentials built
        # into the firmware.
        return (BackupWifi(enabled=enabled) if enabled is not None else None), ["wifi_not_stored"]
    try:
        return BackupWifi(ssid=ssid, password=password, enabled=enabled), []
    except ValidationError:
        return None, ["wifi_read_failed"]


async def export_backup_v2(
    *, encrypted: bool = False, include_accounts: bool = True
) -> BackupExportV2:
    """The full backup. Works with the radio disconnected (Meshloom part only).

    ``encrypted``: the caller wraps it in a passphrase envelope; otherwise each
    sensitive section adds ``section_sensitive_unencrypted:<name>``.
    ``include_accounts`` False leaves the account sections out
    (``ACCOUNT_SECTIONS``: users, their hashes and two-factor secrets)."""
    from app.radio import RadioOperationError
    from app.services.backup_radio import read_radio_node_settings
    from app.services.companion_cli import CliError, companion_cli_session
    from app.services.radio_runtime import radio_runtime
    from app.version_info import get_app_build_info

    base = await export_json(include_secrets=True)
    # The notification secrets never leave, the full backup included: the destinations read
    # ``********`` and a restore keeps the stored ones (the notifications section's rule).
    redacted, omitted = _omit_settings_secrets(base.settings) if base.settings else (None, {})
    if redacted is not None:
        base = base.model_copy(
            update={"settings": redacted, "secret_omitted": {**base.secret_omitted, **omitted}}
        )
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
                live_public_key = snapshot.public_key or _self_info_public_key(mc)
                await _ensure_keystore_key(mc, live_public_key)
                identity = _identity_for(live_public_key)
                if cli_supported and capabilities is not None and capabilities.wifi:
                    wifi, wifi_warnings = await _read_wifi(cli, live_public_key)
                    warnings.extend(wifi_warnings)
        except (CliError, RadioOperationError):
            warnings.append("radio_not_ready")

    if identity is None or identity.private_key is None:
        warnings.append(
            "private_key_export_disabled"
            if identity is not None and identity.private_key_status == "export_disabled"
            else "private_key_unavailable"
        )
    sections, section_warnings = await export_sections(
        skip=() if include_accounts else ACCOUNT_SECTIONS
    )
    warnings.extend(section_warnings)
    if not encrypted:
        warnings.extend(
            f"section_sensitive_unencrypted:{name}" for name in sensitive_section_names(sections)
        )
    try:
        version: str | None = get_app_build_info().version
    except Exception:
        version = None
    doc = BackupExportV2(
        **base.model_dump(exclude={"format"}),
        meshloom_version=version,
        warnings=list(dict.fromkeys(warnings)),
        identity=identity,
        radio=radio,
        wifi=wifi,
        meshloom_extra=extra,
        sections=sections,
    )
    await refuse_identity_seeds(export_document(doc))
    return doc


# -- Full backup (v2): Meshloom settings outside v1 ------------------------------


async def _restore_fanout_configs(entries: list[BackupFanoutConfig], warnings: list[str]) -> int:
    """Upsert by id through the fanout route checks. Bots come back disabled."""
    from fastapi import HTTPException

    from app.fanout.manager import fanout_manager, is_reserved_fanout_id
    from app.repository.fanout import FanoutConfigRepository
    from app.routers.fanout import (
        allows_private_network,
        prepare_fanout_config,
        prepare_refused_apprise_config,
    )
    from app.secret_fields import SecretRequiredError, merge_secrets

    written: list[str] = []
    restored_bots: list[str] = []
    for entry in entries:
        label = entry.name or entry.id
        if is_reserved_fanout_id(entry.id):
            # system:* modules are Meshloom's own: never taken from a file.
            warnings.append(f"fanout_reserved:{entry.id}")
            continue
        if entry.type == "bot" and fanout_manager.bots_disabled_effective():
            warnings.append(f"bots_disabled:{label}")
            continue
        existing = await FanoutConfigRepository.get(entry.id)
        if existing is not None and existing["type"] != entry.type:
            warnings.append(f"fanout_invalid:{entry.id}:{entry.type}")
            continue
        # A secret written as ******** (a file from an older JSON export, or
        # one edited by hand) keeps the stored value, else reads as unset.
        stored_config = existing["config"] if existing is not None else None
        try:
            file_config = merge_secrets(entry.type, stored_config, entry.config)
        except SecretRequiredError as exc:
            # The file points this row somewhere else: the stored secret stays
            # here, the row comes back without it.
            cleared = {**entry.config, **dict.fromkeys(exc.keys, "")}
            file_config = merge_secrets(entry.type, stored_config, cleared)
            warnings.append(f"secret_dropped_destination_changed:{label}")
        refused_apprise = False
        try:
            config, scope = prepare_fanout_config(
                entry.type, file_config, entry.scope, restored=True
            )
        except HTTPException as exc:
            # A coded Apprise refusal (a scheme no longer allowed, a Telegram
            # URL with no chat id) is a row an older Meshloom accepted: kept,
            # disabled, like migration 100 keeps it. Anything else is invalid.
            if entry.type != "apprise" or not isinstance(exc.detail, dict):
                warnings.append(f"fanout_invalid:{entry.id}:{entry.type}")
                continue
            config, scope = prepare_refused_apprise_config(file_config, entry.scope)
            refused_apprise = True
            warnings.append(f"apprise_restored_disabled:{label}")
        # A bot runs code through exec() once enabled, and a backup file may
        # come from someone else: restored disabled, the user enables it.
        enabled = entry.enabled and entry.type != "bot" and not refused_apprise
        # A LAN opt-in granted on another host would let this file reach this
        # host's network at once: restored disabled, the user re-enables it.
        if enabled and allows_private_network(entry.type, config):
            enabled = False
            warnings.append(f"private_network_restored_disabled:{label}")
        if existing is not None:
            await FanoutConfigRepository.update(
                entry.id,
                name=entry.name or existing["name"],
                enabled=enabled,
                config=config,
                scope=scope,
                sort_order=entry.sort_order,
            )
        else:
            await FanoutConfigRepository.create(
                config_type=entry.type,
                name=entry.name or entry.type,
                config=config,
                scope=scope,
                enabled=enabled,
                config_id=entry.id,
            )
            # create() appends; the user's order is the file's.
            await FanoutConfigRepository.update(entry.id, sort_order=entry.sort_order)
        written.append(entry.id)
        if entry.type == "bot":
            restored_bots.append(label)
    for config_id in written:
        await fanout_manager.reload_config(config_id)
    if restored_bots:
        warnings.append("bots_restored_disabled:" + ", ".join(restored_bots))
    return len(written)


async def push_restore_applies(extra: MeshloomExtra) -> bool | None:
    """Whether a restore writes the file's VAPID keys and subscriptions.

    None: the file has no VAPID keys (nothing to restore). False: this host has
    subscriptions of its own under another key, which are kept.
    """
    from app.repository.push_subscriptions import PushSubscriptionRepository

    if extra.vapid is None:
        return None
    existing = await PushSubscriptionRepository.get_all()
    _private, current_public = await AppSettingsRepository.get_vapid_keys()
    # Its subscriptions were made with its own key: the backup's would fail
    # (403/410) and be deleted on the first push.
    return not (existing and current_public != extra.vapid.public_key)


_PRIVATE_NAME_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localdomain")
# Listed, not ``is_global``: the answer must not change with the Python version.
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)


def endpoint_looks_private(endpoint: str) -> bool:
    """Whether a push endpoint visibly targets a private network: an IP literal
    in a private range, a dotless name or a ``.local``/``.lan``/``.home.arpa``/
    ``.internal``/``.localdomain`` name. No DNS: a public name (ntfy.sh) never
    counts. Migration 104 holds a frozen copy of this rule."""
    try:
        host = (urlsplit(endpoint).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return "." not in host or host.endswith(_PRIVATE_NAME_SUFFIXES)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _PRIVATE_NETWORKS if net.version == ip.version)


async def _regrandfather_private_push(extra: MeshloomExtra) -> None:
    """A backup taken before the egress guard carries no ``push_allow_private_endpoints``:
    restored LAN subscriptions would be refused. Same rule as migration 097/104 and
    the webhook (``grandfather_legacy_webhook``): turn the opt-in on, flagged for the
    one-time notice, only when a restored endpoint visibly targets a private network.
    The opt-in is back on, so a pending ``push_private_endpoints_review`` (migration 104
    withdrew it) no longer applies: cleared with it."""
    if any(endpoint_looks_private(sub.endpoint) for sub in extra.push_subscriptions):
        await AppSettingsRepository.update(
            push_allow_private_endpoints=True,
            push_private_endpoints_grandfathered=True,
            push_private_endpoints_review=False,
        )


async def _restore_push(
    extra: MeshloomExtra, warnings: list[str], *, settings_have_push_opt_in: bool = True
) -> int:
    """VAPID keys and subscriptions belong together: restored only on a host
    with no subscriptions of its own (or already using the backup's key)."""
    from app.push.vapid import ensure_vapid_keys
    from app.repository.push_subscriptions import PushSubscriptionRepository

    applies = await push_restore_applies(extra)
    if applies is None:
        return 0
    if not applies:
        warnings.append("push_kept_existing")
        return 0
    assert extra.vapid is not None
    await AppSettingsRepository.set_vapid_keys(
        extra.vapid.private_key.get_secret_value(), extra.vapid.public_key
    )
    await ensure_vapid_keys()  # the signing code reads the cached keys
    for sub in extra.push_subscriptions:
        await PushSubscriptionRepository.create(
            endpoint=sub.endpoint,
            p256dh=sub.p256dh,
            auth=sub.auth,
            label=sub.label,
            language=sub.language,
        )
    if not settings_have_push_opt_in:
        await _regrandfather_private_push(extra)
    return len(extra.push_subscriptions)


async def restore_meshloom_extra(
    extra: MeshloomExtra, *, settings_have_push_opt_in: bool = True
) -> list[str]:
    """Restore ``meshloom_extra`` through the same checks as the routes.
    Idempotent: a second run changes no count. Returns warnings.

    ``settings_have_push_opt_in``: the file's settings carried
    ``push_allow_private_endpoints`` (False for a backup from before the egress guard)."""
    from app.services.radio_proxy_settings import update_radio_proxy_settings

    warnings: list[str] = []
    await _restore_fanout_configs(extra.fanout_configs, warnings)
    await _restore_push(extra, warnings, settings_have_push_opt_in=settings_have_push_opt_in)
    if extra.radio_proxy is not None:
        _status, proxy_warnings = await update_radio_proxy_settings(
            enabled=extra.radio_proxy.enabled,
            bind=extra.radio_proxy.bind,
            port=extra.radio_proxy.port,
            max_clients=extra.radio_proxy.max_clients,
            lenient=True,
        )
        warnings.extend(proxy_warnings)
    if extra.path_hash_one_byte_opt_in is not None:
        # The only source of the opt-in in a restore (the radio step never sets it).
        await AppSettingsRepository.set_path_hash_one_byte_opt_in(extra.path_hash_one_byte_opt_in)
    return warnings
