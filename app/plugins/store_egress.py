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
  fixed ``User-Agent: Meshloom/<version>``. A URL carrying user info
  (``https://user:pw@host``) is refused (``egress_userinfo``) before any lookup, so a
  repository URL can never smuggle credentials. A caller may narrow the hosts
  (``allowed_hosts``, intersected with ``STORE_ALLOWLIST``), never widen them. Turning
  the consent off cancels the requests in flight (they raise ``StoreOffline``); the
  plugin installer downloads through it too (``app/plugins/installer.py``).
- The background refresh runs every ``REFRESH_INTERVAL_S`` (6 h) only while the
  consent is on and at least one ``custom`` or ``store`` instance is installed. It
  starts at boot when the consent is on, and when the setting goes from off to on
  (one tick at once); writing ``store_online=true`` again while it runs does not wake
  it, and two ticks are never closer than ``MIN_REFRESH_GAP_S`` (10 min), however
  often the setting is toggled (a skipped tick waits only the rest of that gap, not
  6 h). Turning it off stops it before its next tick. A
  restore (JSON or SQLite snapshot) re-reads the consent (``on_restore`` hook). The
  refresh work itself (update checks, revocations) registers with
  ``register_store_refresher`` (later tickets); each refresher sends through
  ``store_fetch``.
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
from app.net.egress_guard import (
    CheckedResponse,
    EgressBlocked,
    HostAllowlist,
    follow_checked_redirects,
)
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
# However often the consent is toggled, two refresh ticks are at least this far apart.
MIN_REFRESH_GAP_S = 10 * 60
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
    allowed_hosts: HostAllowlist | None = None,
    max_redirects: int = STORE_MAX_REDIRECTS,
    timeout: httpx.Timeout | float = STORE_TIMEOUT,
    total_timeout: float = STORE_TOTAL_TIMEOUT_S,
) -> CheckedResponse:
    """Send one Store request, or raise ``StoreOffline`` without any lookup.

    ``allowed_hosts`` narrows ``STORE_ALLOWLIST`` (only the entries in both are
    reachable). ``EgressBlocked`` (user info in the URL, a host outside
    ``STORE_HOSTS``, a private address, too many redirects, an answer above
    ``max_response_bytes``) and ``httpx`` errors pass through. No body is sent: the
    Store only reads.
    """
    generation = _state.generation
    if not await store_egress_allowed() or generation != _state.generation:
        raise StoreOffline()
    if _has_userinfo(url):
        raise EgressBlocked("egress_userinfo", permanent=True)
    hosts = STORE_ALLOWLIST
    if allowed_hosts is not None:
        hosts = HostAllowlist(STORE_ALLOWLIST.entries & allowed_hosts.entries)
    sent_headers = {k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"}
    sent_headers["User-Agent"] = store_user_agent()
    send = asyncio.ensure_future(
        follow_checked_redirects(
            method,
            url,
            timeout=timeout,
            total_timeout=total_timeout,
            allowed_hosts=hosts,
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


def _has_userinfo(url: str) -> bool:
    """Whether ``url`` names a user or a password. A URL httpx cannot parse is left to
    the guard, which refuses it (``egress_scheme``)."""
    try:
        return bool(httpx.URL(url.strip()).userinfo)
    except Exception:
        return False


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
    """One refresh tick. True when it ran (consent on, an online instance, a refresher,
    and no tick in the last ``MIN_REFRESH_GAP_S``)."""
    if not _state.refreshers:
        return False
    last = _state.last_refresh_at
    if last is not None and _state.clock() - last < MIN_REFRESH_GAP_S:
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


async def _refresh_loop() -> None:
    while True:
        if not await store_egress_allowed():
            return
        ran = False
        try:
            ran = await run_store_refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Store refresh failed")
        await asyncio.sleep(_next_delay(ran))


def _next_delay(ran: bool) -> float:
    """``REFRESH_INTERVAL_S`` after a tick; when a tick was skipped only because the last
    one is less than ``MIN_REFRESH_GAP_S`` old (off then on again quickly), just the rest
    of that gap, so turning the Store back on never postpones the refresh by 6 h."""
    last = _state.last_refresh_at
    if not ran and last is not None:
        remaining = MIN_REFRESH_GAP_S - (_state.clock() - last)
        if remaining > 0:
            return remaining
    return REFRESH_INTERVAL_S


def store_refresh_running() -> bool:
    return _state.task is not None and not _state.task.done()


def _start_loop() -> None:
    """Start the loop (its first tick runs at once); a running loop is left alone, so
    writing ``store_online=true`` again does not trigger a request."""
    if store_refresh_running():
        return
    _state.task = asyncio.create_task(_refresh_loop(), name="store-refresh")


async def _stop_loop() -> None:
    task = _state.task
    _state.task = None
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
    """Called after every write of ``store_online`` and after a restore: on starts the
    loop when it is not running, off stops it all."""
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


async def _after_restore(_source: str) -> None:
    """A restore (JSON, or a SQLite snapshot put back in place) never carries the
    consent, but whatever the database now says is applied at once: off cancels the
    requests in flight and stops the loop."""
    await note_store_online_changed(await store_egress_allowed())


def _register_restore_hook() -> None:
    from app.services.backup_sections import register_on_restore, unregister_on_restore

    unregister_on_restore("store_egress")
    register_on_restore("store_egress", _after_restore)


_register_restore_hook()


async def reset_store_egress_for_tests() -> None:
    await _stop_loop()
    for send in list(_state.inflight):
        send.cancel()
    _state.inflight.clear()
    _state.refreshers.clear()
    _state.last_refresh_at = None
    _state.generation = 0
