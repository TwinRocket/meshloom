"""Store egress: the one gate in front of every request made for the Store (MSHL-194).

The Store is a separate egress class, not Community: it never goes through
``community_egress_state()``. Its consent is ``app_settings.store_online``, off by
default, never restored from a backup and reset to 0 in a database copy.

- ``store_egress_allowed()`` is the consent check. Off: nothing leaves for the Store
  (custom GitHub repositories in M2a, the signed index on ``store.meshloom.app`` in
  M2b); installed plugins keep working.
- ``store_fetch(method, url, ...)`` is the only way to send a Store request. It
  raises ``StoreOffline`` (code ``store_offline``) before any DNS lookup when the
  consent is off, else sends through the shared egress guard
  (``follow_checked_redirects``) with ``STORE_ALLOWLIST`` (``STORE_HOSTS``, exact
  hosts), HTTPS only, every redirect hop re-validated, the answer capped, and a
  fixed ``User-Agent: Meshloom/<version>``. Turning the consent off cancels the
  requests in flight (they raise ``StoreOffline``).
- The background refresh runs every ``REFRESH_INTERVAL_S`` (6 h) only while the
  consent is on and at least one ``custom`` or ``store`` instance is installed. It
  starts at boot when the consent is on, and when the setting is turned on (one
  tick at once); turning it off stops it before its next tick. The refresh work
  itself (update checks, revocations) registers with ``register_store_refresher``
  (later tickets); each refresher sends through ``store_fetch``.
- ``store_status()`` builds ``GET /api/store/status`` from the same constants, so
  the activation card names exactly the hosts the guard allows.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

import httpx

from app.models import StoreStatus
from app.net.egress_guard import CheckedResponse, HostAllowlist, follow_checked_redirects
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

# Custom GitHub repositories (M2a): the site and the exact release-asset redirect hosts.
STORE_GITHUB_HOSTS: tuple[str, ...] = (
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
)
# The signed index and its mirrored archives (M2b): the one *.meshloom.app host the
# Store may reach, and only while store_online is on.
STORE_INDEX_HOST = "store.meshloom.app"
STORE_HOSTS: tuple[str, ...] = (*STORE_GITHUB_HOSTS, STORE_INDEX_HOST)
STORE_ALLOWLIST = HostAllowlist.of(*STORE_HOSTS)
STORE_SCHEMES = frozenset({"https"})

REFRESH_INTERVAL_S = 6 * 3600
STORE_TIMEOUT = httpx.Timeout(10.0, read=30.0)
STORE_TOTAL_TIMEOUT_S = 120.0
STORE_MAX_REDIRECTS = 5
# Instance kinds whose updates come from the network.
ONLINE_KINDS = ("custom", "store")

Refresher = Callable[[], Awaitable[None]]


class StoreOffline(Exception):
    """The Store egress consent is off (or was turned off while the request was out)."""

    code = "store_offline"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass
class _State:
    task: asyncio.Task | None = None
    wake: asyncio.Event | None = None
    # Raised on every opt-out: a request that checked the consent before it no longer sends.
    generation: int = 0
    inflight: set[asyncio.Future] = field(default_factory=set)
    refreshers: list[Refresher] = field(default_factory=list)
    last_refresh_at: int | None = None
    clock: Callable[[], float] = time.time


_state = _State()


def store_user_agent() -> str:
    return f"Meshloom/{get_app_build_info().version}"


async def store_egress_allowed() -> bool:
    """True only while the owner turned the Store on (``app_settings.store_online``)."""
    from app.repository import AppSettingsRepository

    return (await AppSettingsRepository.get()).store_online


async def store_fetch(
    method: str,
    url: str,
    *,
    max_response_bytes: int,
    headers: Mapping[str, str] | None = None,
    max_redirects: int = STORE_MAX_REDIRECTS,
    timeout: httpx.Timeout | float = STORE_TIMEOUT,
    total_timeout: float = STORE_TOTAL_TIMEOUT_S,
) -> CheckedResponse:
    """Send one Store request, or raise ``StoreOffline`` without any lookup.

    ``EgressBlocked`` (a host outside ``STORE_HOSTS``, a private address, too many
    redirects, an answer above ``max_response_bytes``) and ``httpx`` errors pass
    through. No body is sent: the Store only reads.
    """
    generation = _state.generation
    if not await store_egress_allowed() or generation != _state.generation:
        raise StoreOffline()
    sent_headers = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
    sent_headers["User-Agent"] = store_user_agent()
    send = asyncio.ensure_future(
        follow_checked_redirects(
            method,
            url,
            timeout=timeout,
            total_timeout=total_timeout,
            allowed_hosts=STORE_ALLOWLIST,
            schemes=STORE_SCHEMES,
            max_redirects=max_redirects,
            max_response_bytes=max_response_bytes,
            headers=sent_headers,
        )
    )
    _state.inflight.add(send)
    try:
        return await send
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if send.cancelled() and (current is None or current.cancelling() == 0):
            # Cancelled by the opt-out, not by our caller.
            raise StoreOffline() from None
        raise
    finally:
        _state.inflight.discard(send)


def register_store_refresher(refresher: Refresher) -> None:
    """Add a step to the background refresh (update checks, revocations)."""
    if refresher not in _state.refreshers:
        _state.refreshers.append(refresher)


def unregister_store_refresher(refresher: Refresher) -> None:
    if refresher in _state.refreshers:
        _state.refreshers.remove(refresher)


async def has_online_instances() -> bool:
    """At least one installed instance updates from the network (custom or store)."""
    from app.repository.plugins import PluginRepository

    return any(row.get("kind") in ONLINE_KINDS for row in await PluginRepository.get_all())


async def run_store_refresh() -> bool:
    """One refresh tick. True when it ran (consent on, an online instance, a refresher)."""
    if not _state.refreshers:
        return False
    if not await store_egress_allowed() or not await has_online_instances():
        return False
    for refresher in list(_state.refreshers):
        try:
            await refresher()
        except StoreOffline:
            return False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Store refresh step failed")
    _state.last_refresh_at = int(_state.clock())
    return True


async def _refresh_loop(wake: asyncio.Event) -> None:
    while True:
        if not await store_egress_allowed():
            return
        try:
            await run_store_refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Store refresh failed")
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), timeout=REFRESH_INTERVAL_S)
        except TimeoutError:
            pass


def store_refresh_running() -> bool:
    return _state.task is not None and not _state.task.done()


def _start_loop() -> None:
    if store_refresh_running():
        if _state.wake is not None:
            _state.wake.set()
        return
    _state.wake = asyncio.Event()
    _state.task = asyncio.create_task(_refresh_loop(_state.wake), name="store-refresh")


async def _stop_loop() -> None:
    task = _state.task
    _state.task = None
    _state.wake = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def start_store_refresh() -> None:
    """At startup: run the refresh loop only when the consent is already on."""
    if await store_egress_allowed():
        _start_loop()


async def stop_store_refresh() -> None:
    """At shutdown."""
    await _stop_loop()


async def note_store_online_changed(enabled: bool) -> None:
    """Called after every write of ``store_online``: on wakes the loop, off stops it all."""
    if enabled:
        _start_loop()
        return
    _state.generation += 1
    for send in list(_state.inflight):
        send.cancel()
    await _stop_loop()


async def store_status() -> StoreStatus:
    return StoreStatus(
        enabled=await store_egress_allowed(),
        hosts=sorted(host for host, _port in STORE_ALLOWLIST.entries),
        sends=["ip_address", "user_agent"],
        user_agent=store_user_agent(),
        refresh_interval_s=REFRESH_INTERVAL_S,
        missed_when_disabled=["updates", "revocation_alerts"],
        refresh_running=store_refresh_running(),
        last_refresh_at=_state.last_refresh_at,
    )


async def reset_store_egress_for_tests() -> None:
    await _stop_loop()
    for send in list(_state.inflight):
        send.cancel()
    _state.inflight.clear()
    _state.refreshers.clear()
    _state.last_refresh_at = None
    _state.generation = 0
