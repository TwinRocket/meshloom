"""Periodic best-effort housekeeping.

- Stale-contact purge: off unless ``stale_contact_days`` > 0.
- Raw-packet retention: off unless ``raw_packet_retention_days`` > 0; prunes
  undecrypted raw packets older than that many days, in short batches.
- Bounded ``PRAGMA incremental_vacuum`` so freed pages go back to the OS.
"""

import asyncio
import logging
import time

from app.repository import AppSettingsRepository, ContactRepository, RawPacketRepository

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
STALE_PURGE_INTERVAL_SECONDS = 3600
# ~8 MB at the default 4 KiB page size per hourly pass.
INCREMENTAL_VACUUM_MAX_PAGES = 2048

_purge_task: asyncio.Task | None = None


async def delete_contacts_best_effort(public_keys: list[str]) -> int:
    """Delete contacts from the DB and, if connected, from the radio.

    Radio removal is best-effort: a radio failure still deletes the DB rows.
    """
    from app.services.radio_runtime import radio_runtime
    from app.websocket import broadcast_event

    contacts = []
    for key in public_keys:
        contact = await ContactRepository.get_by_key(key.lower())
        if contact:
            contacts.append(contact)
    if not contacts:
        return 0

    if radio_runtime.is_connected:
        try:
            async with radio_runtime.radio_operation("stale_contact_purge") as mc:
                for contact in contacts:
                    radio_contact = mc.get_contact_by_key_prefix(contact.public_key[:12])
                    if radio_contact:
                        await mc.commands.remove_contact(radio_contact)
        except Exception as e:
            logger.warning("Radio removal during stale purge failed: %s", e)

    deleted = 0
    for contact in contacts:
        await ContactRepository.delete(contact.public_key)
        broadcast_event("contact_deleted", {"public_key": contact.public_key})
        deleted += 1
    return deleted


async def purge_stale_contacts() -> int:
    """Delete non-favorite contacts matching the bulk-delete last-heard filter."""
    settings = await AppSettingsRepository.get()
    days = settings.stale_contact_days
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * SECONDS_PER_DAY
    keys = await ContactRepository.list_stale_public_keys(cutoff)
    if not keys:
        return 0
    deleted = await delete_contacts_best_effort(keys)
    logger.info("Stale contact purge removed %d contact(s) (days=%d)", deleted, days)
    return deleted


async def prune_raw_packets() -> int:
    """Delete undecrypted raw packets past the opt-in retention window."""
    settings = await AppSettingsRepository.get()
    days = settings.raw_packet_retention_days
    if days <= 0:
        return 0
    deleted = await RawPacketRepository.prune_old_undecrypted_batched(days)
    if deleted:
        logger.info(
            "Raw packet retention removed %d undecrypted packet(s) (days=%d)", deleted, days
        )
    return deleted


async def reclaim_free_pages() -> int:
    from app.database import db

    freed = await db.incremental_vacuum(INCREMENTAL_VACUUM_MAX_PAGES)
    if freed:
        logger.info("Incremental vacuum returned %d page(s) to the OS", freed)
    return freed


async def _stale_purge_loop() -> None:
    await asyncio.sleep(60)
    while True:
        for step, label in (
            (purge_stale_contacts, "Stale contact purge"),
            (prune_raw_packets, "Raw packet retention"),
            (reclaim_free_pages, "Incremental vacuum"),
        ):
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s failed", label)
        await asyncio.sleep(STALE_PURGE_INTERVAL_SECONDS)


def start_stale_contact_purge() -> None:
    global _purge_task
    if _purge_task is None or _purge_task.done():
        _purge_task = asyncio.create_task(_stale_purge_loop())
        logger.info(
            "Started stale contact purge task (interval: %ds)", STALE_PURGE_INTERVAL_SECONDS
        )


async def stop_stale_contact_purge() -> None:
    global _purge_task
    if _purge_task is None:
        return
    _purge_task.cancel()
    try:
        await _purge_task
    except asyncio.CancelledError:
        pass
    _purge_task = None
    logger.info("Stopped stale contact purge")
