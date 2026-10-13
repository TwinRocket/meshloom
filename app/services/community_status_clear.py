"""Clear a retained Community status that the opt-out could not clear.

This is the one Community connection made after an opt-out, and nothing else
ever reconnects. It exists for one case: the broker was unreachable when the
operator left (or when the IATA changed), so the publisher could not
send the empty retained payload before its DISCONNECT, and a retained
``offline`` (the Will, or the one from an earlier clean stop) stays on
``meshcore/{IATA}/{PUBKEY}/status``.

How it is bounded:

- one target per topic left behind (the old IATA too, after an IATA change);
- each attempt is one short MQTT connection, no Will, that publishes only an
  empty retained payload (QoS 1) on that topic, then disconnects, within
  ``CLEAR_ATTEMPT_TIMEOUT_S``;
- at most ``CLEAR_MAX_ATTEMPTS`` attempts, spaced by ``CLEAR_RETRY_DELAYS_S``,
  and never after ``CLEAR_GIVE_UP_AFTER_S`` (24 h) from the opt-out;
- the target is persisted (``app_settings.community_status_clear``) before the
  opt-out tries its own clear, removed as soon as a clear succeeds or the target
  is abandoned, and the attempt count is saved before each attempt, so a
  restart neither loses nor repeats it.

A topic can also be left behind while the app is stopped: the IATA changes in
the database, or Community is turned off, before the next start, or the
upgrade retired a custom broker (the Community addresses are fixed now; the
recorded topic keeps its broker, so that old broker is cleared once). The Stats publisher therefore records the last topic it made
retained (``note_published``, ``app_settings.community_status_last``), and at
startup ``adopt_orphaned_status`` hands that topic to this same bounded clear
when it is no longer the current one.

Community's ingest drops the empty payload (``invalid_json``) before any bind or
status write; the broker deletes the retained message (MQTT 3.1.1 §3.3.1.3).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Any

from app.services.meshloom_community import (
    DEFAULT_BROKER_PORT,
    DEFAULT_WEBSOCKET_PATH,
    MQTT_KEEPALIVE_SECONDS,
    CommunityEffective,
)

logger = logging.getLogger(__name__)

# Delay before attempt n (n = 0 is the first), counted from the opt-out for the
# first one and from the previous attempt after that. Total ≈ 13.5 h < 24 h.
CLEAR_RETRY_DELAYS_S: tuple[float, ...] = (60.0, 300.0, 900.0, 3600.0, 4 * 3600.0, 8 * 3600.0)
CLEAR_MAX_ATTEMPTS = len(CLEAR_RETRY_DELAYS_S)
CLEAR_GIVE_UP_AFTER_S = 24 * 3600.0
# Connect, publish (QoS 1, waits for the PUBACK) and DISCONNECT, all together.
CLEAR_ATTEMPT_TIMEOUT_S = 15.0
# How often the worker looks again while it waits (a due time, or the radio key).
WORKER_POLL_S = 60.0

_worker_task: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class StatusClearTarget:
    iata: str
    broker_host: str
    audience: str
    pubkey: str  # uppercase hex, as in the topic

    @property
    def topic(self) -> str:
        return f"meshcore/{self.iata}/{self.pubkey}/status"

    def key(self) -> tuple[str, str, str]:
        # Hostnames are case-insensitive: MQTT.meshloom.app is the same broker.
        return (self.iata, self.broker_host.lower(), self.pubkey)


def target_for(state: CommunityEffective) -> StatusClearTarget | None:
    """The status topic this node publishes on with ``state``, if any."""
    from app.keystore import get_public_key

    if not state.iata:
        return None
    public_key = get_public_key()
    if public_key is None:
        return None
    return StatusClearTarget(
        iata=state.iata,
        broker_host=state.broker_host,
        audience=state.mqtt_audience or state.broker_host,
        pubkey=public_key.hex().upper(),
    )


def _entry_target(entry: dict[str, Any]) -> StatusClearTarget | None:
    try:
        target = StatusClearTarget(
            iata=str(entry["iata"]),
            broker_host=str(entry["broker_host"]),
            audience=str(entry.get("audience") or entry["broker_host"]),
            pubkey=str(entry["pubkey"]),
        )
    except (KeyError, TypeError):
        return None
    if not (target.iata and target.broker_host and target.pubkey):
        return None
    return target


def _number(entry: dict[str, Any], name: str) -> float:
    try:
        return float(entry.get(name) or 0)
    except (TypeError, ValueError):
        return 0.0


def _due_at(entry: dict[str, Any]) -> float:
    attempts = int(_number(entry, "attempts"))
    return _number(entry, "last_attempt") + CLEAR_RETRY_DELAYS_S[attempts]


def _expired(entry: dict[str, Any], now: float) -> bool:
    return (
        _entry_target(entry) is None
        or int(_number(entry, "attempts")) >= CLEAR_MAX_ATTEMPTS
        or now - _number(entry, "since") >= CLEAR_GIVE_UP_AFTER_S
    )


async def _load() -> list[dict[str, Any]]:
    from app.repository.settings import AppSettingsRepository

    return await AppSettingsRepository.get_community_status_clear()


async def _save(entries: list[dict[str, Any]]) -> None:
    from app.repository.settings import AppSettingsRepository

    await AppSettingsRepository.set_community_status_clear(entries)


def _without(entries: list[dict[str, Any]], key: tuple[str, str, str]) -> list[dict[str, Any]]:
    out = []
    for entry in entries:
        target = _entry_target(entry)
        if target is None or target.key() != key:
            out.append(entry)
    return out


async def mark_pending(target: StatusClearTarget) -> None:
    """Persist ``target`` before the publisher tries its own clear (write-ahead)."""
    now = time.time()
    entries = _without(await _load(), target.key())
    entries.append(
        {
            "iata": target.iata,
            "broker_host": target.broker_host,
            "audience": target.audience,
            "pubkey": target.pubkey,
            "since": now,
            "last_attempt": now,
            "attempts": 0,
        }
    )
    await _save(entries)


async def resolve(target: StatusClearTarget) -> None:
    """The status on ``target`` is cleared: forget it."""
    entries = await _load()
    remaining = _without(entries, target.key())
    if len(remaining) != len(entries):
        await _save(remaining)


async def drop_current(state: CommunityEffective) -> None:
    """Community is on again on this topic: the publisher owns it, never clear it."""
    if not state.enabled:
        return
    target = target_for(state)
    if target is not None:
        await resolve(target)


def _target_entry(target: StatusClearTarget) -> dict[str, str]:
    return {
        "iata": target.iata,
        "broker_host": target.broker_host,
        "audience": target.audience,
        "pubkey": target.pubkey,
    }


async def last_published() -> StatusClearTarget | None:
    """The status topic the Stats publisher last made retained, if remembered."""
    from app.repository.settings import AppSettingsRepository

    entry = await AppSettingsRepository.get_community_status_last()
    return _entry_target(entry) if entry is not None else None


async def note_published(target: StatusClearTarget) -> None:
    """The Stats publisher made its status retained on ``target``: remember it.

    Written only when the topic changes, so the periodic status refresh costs
    one read.
    """
    from app.repository.settings import AppSettingsRepository

    if await last_published() == target:
        return
    await AppSettingsRepository.set_community_status_last(_target_entry(target))


async def forget_published(target: StatusClearTarget) -> None:
    """The status on ``target`` is cleared, or pending a clear: stop tracking it."""
    from app.repository.settings import AppSettingsRepository

    last = await last_published()
    if last is not None and last.key() == target.key():
        await AppSettingsRepository.set_community_status_last(None)


async def adopt_orphaned_status() -> None:
    """At startup: a retained status left on a topic that is no longer ours.

    The IATA changed while the app was stopped, the topic sits on a broker that
    is no longer the fixed Community one (a custom broker retired by the
    upgrade: cleared once, on purpose), or Community is off now: the topic the publisher last used keeps a retained
    ``offline`` (or the Will's). Hand it to the bounded clear. The public key is
    not compared: it is not known before the radio connects, and only that
    radio's key could sign for its topic anyway (the worker waits for it).
    """
    from app.repository.settings import AppSettingsRepository
    from app.services.meshloom_community import get_community_effective

    last = await last_published()
    if last is None:
        return
    state = await get_community_effective()
    if state.enabled and (state.iata, state.broker_host.lower()) == (
        last.iata,
        last.broker_host.lower(),
    ):
        return
    pending = {t.key() for e in await _load() if (t := _entry_target(e)) is not None}
    if last.key() not in pending:
        await mark_pending(last)
        logger.info("Status topic %s is no longer current: clearing it", last.topic)
    await AppSettingsRepository.set_community_status_last(None)


def _is_current(target: StatusClearTarget, state: CommunityEffective) -> bool:
    current = target_for(state)
    return state.enabled and current is not None and current.key() == target.key()


async def _clear_once(target: StatusClearTarget, private_key: bytes, public_key: bytes) -> None:
    """One connection, one empty retained publish on the status topic, DISCONNECT."""
    import aiomqtt

    from app.fanout.community_mqtt import _generate_jwt_token

    token = _generate_jwt_token(private_key, public_key, audience=target.audience, iata=target.iata)
    # No Will: if this connection drops, the broker must not publish anything.
    async with aiomqtt.Client(
        hostname=target.broker_host,
        port=DEFAULT_BROKER_PORT,
        transport="websockets",
        websocket_path=DEFAULT_WEBSOCKET_PATH,
        tls_context=ssl.create_default_context(),
        username=f"v1_{target.pubkey}",
        password=token,
        keepalive=MQTT_KEEPALIVE_SECONDS,
    ) as client:
        await client.publish(target.topic, b"", qos=1, retain=True)


def _signing_keys(target: StatusClearTarget) -> tuple[bytes, bytes] | None:
    """This radio's (private, public) key, if it is the one that owns ``target``."""
    from app import keystore

    public_key = keystore.get_public_key()
    private_key = keystore.get_private_key()
    if public_key is None or private_key is None or public_key.hex().upper() != target.pubkey:
        return None
    return private_key, public_key


async def _attempt(target: StatusClearTarget) -> None:
    """Count the attempt, make it, and forget the target if it worked.

    Runs under ``update_community()``'s lock: an opt-in on this very topic
    cannot land between the check and the clear and lose its fresh ``online``.
    The key is read again under the lock: the radio may have gone while it
    waited for it.
    """
    from app.services.meshloom_community import _update_lock, get_community_effective

    async with _update_lock:
        keys = _signing_keys(target)
        if keys is None:
            return
        private_key, public_key = keys
        entries = await _load()
        entry = next(
            (e for e in entries if (t := _entry_target(e)) and t.key() == target.key()), None
        )
        if entry is None:
            return
        if _is_current(target, await get_community_effective()):
            await resolve(target)
            return
        entry["attempts"] = int(_number(entry, "attempts")) + 1
        entry["last_attempt"] = time.time()
        await _save(entries)
        try:
            await asyncio.wait_for(
                _clear_once(target, private_key, public_key), timeout=CLEAR_ATTEMPT_TIMEOUT_S
            )
        except Exception as exc:  # TimeoutError included
            logger.info(
                "Community status clear for %s failed (attempt %d of %d): %s",
                target.topic,
                entry["attempts"],
                CLEAR_MAX_ATTEMPTS,
                exc,
            )
            return
        await resolve(target)
        logger.info("Cleared the retained Community status on %s", target.topic)


async def _worker() -> None:
    try:
        await _worker_loop()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Community status clear worker stopped")


async def _worker_loop() -> None:
    while True:
        now = time.time()
        entries = await _load()
        live = [e for e in entries if not _expired(e, now)]
        if len(live) != len(entries):
            for entry in entries:
                if entry not in live:
                    logger.info(
                        "Giving up on clearing the retained Community status on %s",
                        (t.topic if (t := _entry_target(entry)) else "an invalid target"),
                    )
            await _save(live)
        if not live:
            return

        entry = min(live, key=_due_at)
        wait = _due_at(entry) - now
        if wait > 0:
            await asyncio.sleep(min(wait, WORKER_POLL_S))
            continue

        target = _entry_target(entry)
        assert target is not None  # _expired() drops invalid entries
        if _signing_keys(target) is None:
            # Only this radio's key can sign for this topic. Wait for it
            # without spending an attempt; the 24 h limit still applies.
            await asyncio.sleep(WORKER_POLL_S)
            continue
        await _attempt(target)


def schedule() -> None:
    """Start the worker unless it already runs. It stops once nothing is pending."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.get_running_loop().create_task(_worker(), name="community-status-clear")


async def start_status_clear_worker() -> None:
    """At startup: adopt a topic orphaned while stopped, resume pending clears.

    Runs before the fanout modules start, so the publisher has not yet
    recorded the current topic over the one it used before the restart.
    """
    await adopt_orphaned_status()
    if await _load():
        schedule()


async def stop_status_clear_worker() -> None:
    global _worker_task
    task, _worker_task = _worker_task, None
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
