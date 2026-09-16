"""Unlock unknown GroupText from the bundled name list, then Stats resolve.

Layer 2 (bundled names) always runs. Layer 3 (POST /v1/hashtags/resolve) runs
only when Community is on and an IATA is set. The browser never calls Stats.
No sample-queue upload lives here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from app.data.meshcore_channels import (
    bundled_names_by_hash_byte,
    channel_key_hash_byte,
    hashtag_key_from_name,
)
from app.decoder import try_decrypt_packet_with_channel_key
from app.repository.channels import ChannelRepository
from app.repository.raw_packets import RawPacketRepository
from app.repository.settings import AppSettingsRepository
from app.services.meshloom_community import (
    get_community_effective,
    schedule_hashtag_names_publish,
)

logger = logging.getLogger(__name__)

RESOLVE_HASH_BYTES_MAX = 32
_SAMPLE_MAX_HASHES = 32
_SAMPLE_MAX_PER_HASH = 4
_SAMPLE_MAX_SCAN = 8000
_SAMPLE_WINDOW_DAYS = 30
_DEBOUNCE_SECONDS = 1.0
CATALOGUE_PASS_INTERVAL_SECONDS = 300
_HASH_BYTE_RE = re.compile(r"^[0-9a-f]{2}$")

_pass_lock = asyncio.Lock()
_debounce_task: asyncio.Task[None] | None = None
_poll_task: asyncio.Task[None] | None = None


def _display_name(name: str) -> str:
    text = (name or "").strip()
    if text.startswith("#"):
        text = text[1:].strip()
    return f"#{text}" if text else ""


def _publish_name(name: str) -> str:
    text = (name or "").strip()
    if text.startswith("#"):
        text = text[1:].strip()
    return text


def _normalize_hash_bytes(raw: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        hb = (item or "").strip().lower()
        if not _HASH_BYTE_RE.fullmatch(hb) or hb in seen:
            continue
        seen.add(hb)
        out.append(hb)
        if len(out) >= RESOLVE_HASH_BYTES_MAX:
            break
    return out


async def _unknown_samples() -> dict[str, list[bytes]]:
    received_since = int(time.time()) - (_SAMPLE_WINDOW_DAYS * 86400)
    _scanned, _count, rows = await RawPacketRepository.get_undecrypted_group_text_samples(
        max_hashes=_SAMPLE_MAX_HASHES,
        max_per_hash=_SAMPLE_MAX_PER_HASH,
        max_scan=_SAMPLE_MAX_SCAN,
        received_since=received_since,
    )
    by_hash: dict[str, list[bytes]] = {}
    for channel_hash, _packet_id, data, _timestamp, _mac in rows:
        hb = channel_hash.lower()
        if not _HASH_BYTE_RE.fullmatch(hb):
            continue
        by_hash.setdefault(hb, []).append(data)
    return by_hash


def _mac_matches(name: str, packets: list[bytes]) -> bytes | None:
    key = hashtag_key_from_name(name)
    expected = channel_key_hash_byte(key)
    for packet in packets:
        decrypted = try_decrypt_packet_with_channel_key(packet, key)
        if decrypted is not None and decrypted.channel_hash == expected:
            return key
    return None


async def _notify_channel_found(name: str, key_hex: str) -> None:
    defaults = await AppSettingsRepository.get_push_defaults()
    if not defaults["channel_found"]:
        return
    from app.push.manager import push_manager

    await push_manager.dispatch_event(
        {
            "event": "channel_found",
            "name": name,
            "channel_key": key_hex,
        }
    )


async def _apply_matched_name(name: str, packets: list[bytes]) -> bool:
    """Upsert a hashtag, historical-decrypt, PUT the name, notify once.

    MAC must pass on a local sample. Returns True when a new channel was opened.
    """
    publish = _publish_name(name)
    if not publish:
        return False
    key = _mac_matches(publish, packets)
    if key is None:
        return False

    key_hex = key.hex().upper()
    display = _display_name(publish)
    existing = await ChannelRepository.get_by_key(key_hex)
    if existing is not None:
        return False

    await ChannelRepository.upsert_name(key_hex, display, is_hashtag=True)
    stored = await ChannelRepository.get_by_key(key_hex)
    if stored is None:
        return False

    from app.websocket import broadcast_event

    broadcast_event("channel", stored.model_dump())

    from app.routers.packets import _run_historical_channel_decryption

    await _run_historical_channel_decryption(key, key_hex, display)
    await schedule_hashtag_names_publish([display], is_hashtag=True)
    await _notify_channel_found(display, key_hex)
    logger.info("Hashtag catalogue opened %s", display)
    return True


async def _resolve_remaining(hash_bytes: list[str]) -> list[dict[str, str]]:
    from app.services.meshloom_community import resolve_hashtag_names

    try:
        return await resolve_hashtag_names(hash_bytes)
    except Exception:
        logger.info("Hashtag resolve skipped", exc_info=True)
        return []


async def run_catalogue_pass() -> list[str]:
    """Try bundled names, then Stats resolve, against capped unknown samples."""
    async with _pass_lock:
        samples = await _unknown_samples()
        if not samples:
            return []

        opened: list[str] = []
        remaining = [hb for hb in samples if samples[hb]]

        for hb in list(remaining):
            for candidate in bundled_names_by_hash_byte().get(hb, ()):
                if await _apply_matched_name(candidate, samples[hb]):
                    opened.append(_publish_name(candidate))
                    remaining.remove(hb)
                    break

        if not remaining:
            return opened

        state = await get_community_effective()
        if not state.enabled or not state.iata:
            return opened

        resolved = await _resolve_remaining(_normalize_hash_bytes(remaining))
        for item in resolved:
            name = item.get("name") or ""
            hb = (item.get("hash_byte") or "").lower()
            packets = samples.get(hb, [])
            if not packets:
                continue
            if await _apply_matched_name(name, packets):
                opened.append(_publish_name(name))
        return opened


async def _debounced_pass() -> None:
    await asyncio.sleep(_DEBOUNCE_SECONDS)
    try:
        await run_catalogue_pass()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.info("Hashtag catalogue pass failed", exc_info=True)


def schedule_unknown_group_text_resolve() -> None:
    """Coalesce an ingest-triggered pass. Never raises."""
    global _debounce_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _debounce_task is not None and not _debounce_task.done():
        return
    _debounce_task = loop.create_task(_debounced_pass())


async def _catalogue_loop() -> None:
    while True:
        try:
            await asyncio.sleep(CATALOGUE_PASS_INTERVAL_SECONDS)
            await run_catalogue_pass()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.info("Periodic hashtag catalogue pass failed", exc_info=True)


def start_hashtag_catalogue_polling() -> None:
    """Periodic pass over already-capped unknown samples. Sleeps first."""
    global _poll_task
    if _poll_task is not None and not _poll_task.done():
        return
    _poll_task = asyncio.create_task(_catalogue_loop())


async def stop_hashtag_catalogue_polling() -> None:
    global _poll_task, _debounce_task
    for task in (_debounce_task, _poll_task):
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _debounce_task = None
    _poll_task = None


async def reset_for_tests() -> None:
    await stop_hashtag_catalogue_polling()
