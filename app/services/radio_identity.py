"""Identity gate helpers and transactional mesh wipe."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.database import db
from app.models import RadioIdentityInfo
from app.repository.radio_transport import RadioTransportRepository
from app.services import dm_ack_tracker
from app.services.radio_ingest_gate import deny_ingest
from app.services.radio_transport import get_transport

logger = logging.getLogger(__name__)


def _normalize_key(value: object) -> str | None:
    """A radio public key, or None when what arrived cannot be one.

    An all-zero key is not an identity, it is a peer that does not know its own
    yet. It is a well-formed hex string, so it passes every shape check and then
    compares unequal to the bound key — which reads as "the radio was swapped"
    and offers to erase the mesh history to adopt it. Treated as the absence it
    is, the caller fails closed instead.
    """
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if not key:
        return None
    if set(key) == {"0"}:
        logger.warning("Radio reported an all-zero public key; treating it as unknown")
        return None
    return key


async def read_radio_public_key(mc) -> tuple[str | None, str | None]:
    """Return (public_key, name) from a live send_appstart, then cached self_info.

    self_info is updated by an async SELF_INFO subscriber and can still hold
    the previous radio's key across a library auto-reconnect.
    """
    try:
        result = await mc.commands.send_appstart()
        payload = result.payload if result is not None and isinstance(result.payload, dict) else {}
        key = _normalize_key(payload.get("public_key"))
        name = payload.get("name") if isinstance(payload.get("name"), str) else None
        if key:
            return key, name
    except Exception:
        logger.exception("Failed to read radio public key via send_appstart")
    info = getattr(mc, "self_info", None)
    if isinstance(info, dict):
        key = _normalize_key(info.get("public_key"))
        name = info.get("name") if isinstance(info.get("name"), str) else None
        if key:
            return key, name
    return None, None


async def evaluate_connected_identity(mc) -> str:
    """Compare the live radio key with the bound identity.

    Returns ``continue`` or ``stop``. Always closes ingest first.
    """
    from app.repository.radio_transport import snapshot_restore_dict
    from app.services.radio_ingest_gate import allow_ingest, begin_connection_session
    from app.services.radio_transport import database_has_mesh_history

    session = begin_connection_session()
    public_key, name = await read_radio_public_key(mc)
    if not public_key:
        logger.error("Radio identity unavailable; failing closed")
        return "stop"

    snapshot = await get_transport()
    if snapshot.identity_state in ("identity_mismatch", "identity_unbound_legacy"):
        return "stop"

    if not snapshot.bound_public_key:
        if await database_has_mesh_history():
            await RadioTransportRepository.set_identity_gate(
                state="identity_unbound_legacy",
                new_public_key=public_key,
                new_name=name,
                previous_transport=snapshot_restore_dict(snapshot) if snapshot.configured else None,
            )
            return "stop"
        await RadioTransportRepository.bind_public_key(public_key)
        allow_ingest(session)
        return "continue"

    if snapshot.bound_public_key.lower() == public_key:
        allow_ingest(session)
        return "continue"

    await RadioTransportRepository.set_identity_gate(
        state="identity_mismatch",
        previous_public_key=snapshot.bound_public_key,
        new_public_key=public_key,
        new_name=name,
        previous_transport=snapshot_restore_dict(snapshot) if snapshot.configured else None,
    )
    return "stop"


async def mesh_history_counts() -> tuple[int, int, int | None]:
    """Return (contacts, messages, last_activity)."""
    contacts = messages = 0
    last_activity: int | None = None
    async with db.readonly() as conn:
        try:
            async with conn.execute("SELECT COUNT(*) FROM contacts") as cursor:
                row = await cursor.fetchone()
                contacts = int(row[0]) if row else 0
        except Exception:
            contacts = 0
        try:
            async with conn.execute("SELECT COUNT(*) FROM messages") as cursor:
                row = await cursor.fetchone()
                messages = int(row[0]) if row else 0
        except Exception:
            messages = 0
        try:
            async with conn.execute("SELECT MAX(received_at) FROM messages") as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    last_activity = int(row[0])
        except Exception:
            last_activity = None
    return contacts, messages, last_activity


async def identity_health_info() -> RadioIdentityInfo | None:
    snapshot = await get_transport()
    if snapshot.identity_state not in ("identity_mismatch", "identity_unbound_legacy"):
        return None
    contacts, messages, last_activity = await mesh_history_counts()
    return RadioIdentityInfo(
        previous_public_key=snapshot.mismatch_previous_public_key,
        new_public_key=snapshot.mismatch_new_public_key,
        new_name=snapshot.mismatch_new_name,
        mesh_contacts=contacts,
        mesh_messages=messages,
        last_activity=last_activity,
    )


async def wipe_mesh_identity_data() -> None:
    """Delete identity-bound mesh rows in one non-reentrant transaction.

    Callers must not invoke other repositories inside this function.
    """
    deny_ingest()
    async with db.tx() as conn:
        async with conn.execute(
            """
            SELECT push_conversations, push_conversation_overrides
            FROM app_settings WHERE id = 1
            """
        ) as cursor:
            row = await cursor.fetchone()
        push: list[str] = []
        overrides: dict[str, bool] = {}
        if row is not None:
            raw_push = row["push_conversations"]
            if raw_push:
                try:
                    parsed = json.loads(raw_push)
                    if isinstance(parsed, list):
                        push = [str(item) for item in parsed if isinstance(item, str)]
                except (json.JSONDecodeError, TypeError):
                    push = []
            raw_overrides = row["push_conversation_overrides"]
            if raw_overrides:
                try:
                    parsed = json.loads(raw_overrides)
                    if isinstance(parsed, dict):
                        overrides = {
                            str(key): bool(value)
                            for key, value in parsed.items()
                            if isinstance(key, str)
                        }
                except (json.JSONDecodeError, TypeError):
                    overrides = {}
        kept_push = [item for item in push if not item.startswith("contact-")]
        kept_overrides = {
            key: value for key, value in overrides.items() if not key.startswith("contact-")
        }

        await conn.execute("DELETE FROM raw_packets")
        await conn.execute("DELETE FROM messages")
        await conn.execute("DELETE FROM contacts")
        await conn.execute("DELETE FROM contact_groups")
        await conn.execute("DELETE FROM directory_hop_cache")
        try:
            await conn.execute("DELETE FROM repeater_telemetry_history")
        except Exception:
            pass
        try:
            await conn.execute("DELETE FROM contact_telemetry_history")
        except Exception:
            pass
        await conn.execute("UPDATE channels SET last_read_at = NULL, on_radio = 0")
        await conn.execute(
            """
            UPDATE app_settings SET
                last_message_times = '{}',
                last_advert_time = 0,
                tracked_telemetry_repeaters = '[]',
                tracked_telemetry_contacts = '[]',
                push_conversations = ?,
                push_conversation_overrides = ?
            WHERE id = 1
            """,
            (json.dumps(kept_push), json.dumps(kept_overrides)),
        )

    dm_ack_tracker.clear_all()
    try:
        from app.radio_sync import reset_contact_sync_throttle

        reset_contact_sync_throttle()
    except Exception:
        logger.debug("Could not reset contact sync throttle", exc_info=True)
    try:
        from app.services.radio_runtime import radio_runtime

        radio_runtime.reset_channel_send_cache()
        radio_runtime.clear_pending_message_channel_slots()
    except Exception:
        logger.debug("Could not reset radio caches", exc_info=True)
    try:
        from app.services.radio_stats import clear_latest_radio_stats

        clear_latest_radio_stats()
    except Exception:
        logger.debug("Could not clear radio stats", exc_info=True)

    logger.warning("Wiped mesh identity data after operator adopt")


async def snapshot_ha_retained_topics() -> list[tuple[Any, list[str]]]:
    """Capture HA discovery topics before DELETE so they can be cleared after commit."""
    snapshots: list[tuple[Any, list[str]]] = []
    try:
        from app.fanout.manager import fanout_manager

        for module, _scope in list(fanout_manager._modules.values()):
            if type(module).__name__ != "MqttHaModule":
                continue
            topics = list(getattr(module, "_discovery_topics", []))
            snapshots.append((module, topics))
    except Exception:
        logger.debug("Could not snapshot HA retained topics", exc_info=True)
    return snapshots


async def clear_ha_after_identity_wipe(
    topic_snapshots: list[tuple[Any, list[str]]] | None = None,
) -> None:
    """Clear HA tracked contacts/repeaters and retained topics after a mesh wipe.

    Fanout scopes that cite deleted contacts are left as-is: they simply stop
    matching after the wipe. Brokers and bot code are preserved.
    """
    try:
        from app.repository.fanout import FanoutConfigRepository

        for cfg in await FanoutConfigRepository.get_all():
            if cfg.get("type") != "mqtt_ha":
                continue
            config = dict(cfg.get("config") or {})
            config["tracked_contacts"] = []
            config["tracked_repeaters"] = []
            await FanoutConfigRepository.update(str(cfg["id"]), config=config)
    except Exception:
        logger.debug("Could not clear HA tracked lists in fanout configs", exc_info=True)

    snapshots = (
        topic_snapshots if topic_snapshots is not None else await snapshot_ha_retained_topics()
    )
    for module, topics in snapshots:
        try:
            if topics and hasattr(module, "_clear_retained_topics"):
                await module._clear_retained_topics(topics)
            if hasattr(module, "_discovery_topics"):
                module._discovery_topics.clear()
            if hasattr(module, "_radio_key"):
                module._radio_key = None
            config = getattr(module, "config", None)
            if isinstance(config, dict):
                config["tracked_contacts"] = []
                config["tracked_repeaters"] = []
        except Exception:
            logger.debug("Could not clear HA module topics after wipe", exc_info=True)
