"""Read state management endpoints."""

import logging
import time

from fastapi import APIRouter, Query

from app.models import UnreadCounts
from app.repository import (
    AppSettingsRepository,
    ChannelRepository,
    ContactRepository,
    MessageRepository,
)
from app.services.radio_registry import resolve_radio_id
from app.services.radio_runtime import radio_runtime as radio_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/read-state", tags=["read-state"])


@router.get("/unreads", response_model=UnreadCounts)
async def get_unreads(radio_id: str | None = Query(default=None)) -> UnreadCounts:
    """Get unread counts, mention flags, and last message times for all conversations.

    Computes unread counts server-side using last_read_at timestamps on
    channels and contacts, avoiding the need to fetch bulk messages.
    The radio's own name is sourced directly from the connected radio
    for @mention detection.
    """
    eff_radio = resolve_radio_id(radio_id)
    name: str | None = None
    target_radio = None
    if eff_radio == "default":
        target_radio = radio_manager
    else:
        try:
            target_radio = radio_manager.get(eff_radio) if hasattr(radio_manager, "get") else None
        except Exception:
            target_radio = None

    if target_radio is not None:
        mc = getattr(target_radio, "meshcore", None)
        if mc and getattr(mc, "self_info", None):
            name = mc.self_info.get("name") or None

    settings = await AppSettingsRepository.get()
    blocked_keys = settings.blocked_keys or None
    blocked_names = settings.blocked_names or None
    data = await MessageRepository.get_unread_counts(
        name, blocked_keys=blocked_keys, blocked_names=blocked_names, radio_id=eff_radio
    )
    return UnreadCounts(**data)


@router.post("/mark-all-read")
async def mark_all_read(radio_id: str | None = Query(default=None)) -> dict:
    """Mark all contacts and channels as read.

    Updates last_read_at to current timestamp for all contacts and channels
    using two repository updates (same timestamp value across both tables).
    """
    eff_radio = resolve_radio_id(radio_id)
    now = int(time.time())

    await ContactRepository.mark_all_read(now, radio_id=eff_radio)
    await ChannelRepository.mark_all_read(now, radio_id=eff_radio)

    logger.info("Marked all contacts and channels as read at %d (radio=%s)", now, eff_radio)
    return {"status": "ok", "timestamp": now}
