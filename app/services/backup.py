"""JSON backup/restore of contacts, channels, settings, and groups.

The SQLite file download lives beside this module so callers can take a
consistent snapshot without replacing the live database.
"""

import logging
import time

import aiosqlite

from app.database import Database
from app.models import (
    AppSettings,
    BackupChannel,
    BackupContact,
    BackupExport,
    BackupRestoreRequest,
    BackupRestoreResult,
    ContactGroup,
    ContactUpsert,
)
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactGroupRepository,
    ContactRepository,
)

logger = logging.getLogger(__name__)

BACKUP_FORMAT = "meshloom-backup-v1"  # older files may still say remoteterm-backup-v1

PRIVATE_KEY_WARNING = "The radio private key is stored in memory only and is NOT in this backup."


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
    )


def _channel_to_backup(channel) -> BackupChannel:
    return BackupChannel(
        key=channel.key,
        name=channel.name,
        is_hashtag=channel.is_hashtag,
        flood_scope_override=channel.flood_scope_override,
        path_hash_mode_override=channel.path_hash_mode_override,
        favorite=channel.favorite,
        muted=channel.muted,
    )


async def export_json() -> BackupExport:
    contacts = await ContactRepository.get_all(limit=100_000)
    channels = await ChannelRepository.get_all()
    settings = await AppSettingsRepository.get()
    groups = await ContactGroupRepository.list_all()
    return BackupExport(
        format=BACKUP_FORMAT,
        exported_at=int(time.time()),
        contacts=[_contact_to_backup(c) for c in contacts],
        channels=[_channel_to_backup(c) for c in channels],
        settings=settings,
        groups=groups,
        push_defaults=dict(await AppSettingsRepository.get_push_defaults()),
        push_conversation_overrides=await AppSettingsRepository.get_push_conversation_overrides(),
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
        contacts_upserted += 1

    channels_upserted = 0
    for item in request.channels:
        await ChannelRepository.upsert(item.key, item.name, item.is_hashtag)
        await ChannelRepository.set_favorite(item.key, item.favorite)
        await ChannelRepository.set_muted(item.key, item.muted)
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
        discovery_blocked_types=settings.discovery_blocked_types,
        tracked_telemetry_repeaters=settings.tracked_telemetry_repeaters,
        tracked_telemetry_contacts=settings.tracked_telemetry_contacts,
        auto_resend_channel=settings.auto_resend_channel,
        telemetry_interval_hours=settings.telemetry_interval_hours,
        telemetry_routed_hourly=settings.telemetry_routed_hourly,
        stale_contact_days=settings.stale_contact_days,
        telemetry_alert_rules=settings.telemetry_alert_rules,
        notification_destinations=settings.notification_destinations,
        auto_update=settings.auto_update,
        auto_update_window_start=settings.auto_update_window_start,
        auto_update_window_end=settings.auto_update_window_end,
        auto_update_weekdays=settings.auto_update_weekdays,
    )


async def _restore_group(group: ContactGroup) -> None:
    existing_id = await ContactGroupRepository.find_id_by_name(group.name)
    if existing_id is None:
        created = await ContactGroupRepository.create(group.name)
        existing_id = created.id
        if group.sort_order != created.sort_order:
            await ContactGroupRepository.update(existing_id, sort_order=group.sort_order)
    await ContactGroupRepository.set_members(existing_id, group.public_keys)
