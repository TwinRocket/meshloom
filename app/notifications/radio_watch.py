"""Radio offline / online and app started notifications.

``connection_monitor_loop`` calls :meth:`RadioWatch.observe` once per pass (no
second poll). A loss of the transport starts a grace delay
(``app_settings.radio_offline_grace_s``, 0 = at once); a radio still unreachable
when it ends sends one ``radio_offline`` for the outage, and its return sends one
``radio_online`` with the downtime. The grace is judged on a monotonic clock at
each pass, so no timer is lost when the process stops. Granularity: a pass runs
every 5 s but can be held up by a reconnect or setup in the same loop iteration
(up to the setup timeout), so ``radio_offline`` can arrive late and the loss is
stamped at the first pass that sees it.

What never notifies:

- an expected reboot (``expected_reboot_reason``): Meshloom rebooted the radio;
- an operator pause (``connection_desired`` false): it closes a notified outage
  silently;
- a radio that has not completed a setup since the process started, and any
  moment before ``radio_offline_grace_s`` has elapsed since the start (an
  automatic update whose radio takes a minute to come back must not alert).

Only a *notified* outage is persisted (``radio_outage_started_at``), so a restart
during it still reports the right downtime. A pending loss does not survive a
restart. ``radio_offline`` cannot cover a Meshloom outage: ``app_started`` makes
an unexpected restart visible.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.background_tasks import spawn

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict[str, Any]], None]


class RadioView(Protocol):
    """What the watch reads from the radio manager."""

    @property
    def is_connected(self) -> bool: ...
    @property
    def is_setup_complete(self) -> bool: ...
    @property
    def connection_desired(self) -> bool: ...
    @property
    def connection_info(self) -> str | None: ...


class OutageStore(Protocol):
    async def load(self) -> float | None: ...
    async def save(self, started_at: float | None) -> None: ...


class DbOutageStore:
    async def load(self) -> float | None:
        from app.repository.settings import AppSettingsRepository

        return await AppSettingsRepository.get_radio_outage_started_at()

    async def save(self, started_at: float | None) -> None:
        from app.repository.settings import AppSettingsRepository

        await AppSettingsRepository.set_radio_outage_started_at(started_at)


async def _db_grace() -> int:
    from app.repository.settings import AppSettingsRepository

    return (await AppSettingsRepository.get()).radio_offline_grace_s


def _dispatch_in_background(event: str, payload: dict[str, Any]) -> None:
    """Send through every medium without holding up the monitor loop."""
    spawn(_dispatch(event, payload), name=f"notify-{event}")


async def _dispatch(event: str, payload: dict[str, Any]) -> None:
    from app.notify import dispatch_system_event
    from app.repository.settings import AppSettingsRepository

    try:
        defaults = await AppSettingsRepository.get_push_defaults()
        await dispatch_system_event({"event": event, **payload}, defaults[event])
    except Exception:
        logger.warning("Failed to dispatch %s", event, exc_info=True)


class RadioWatch:
    def __init__(
        self,
        *,
        emit: Emit = _dispatch_in_background,
        store: OutageStore | None = None,
        get_grace: Callable[[], Awaitable[int]] = _db_grace,
        is_excused: Callable[[Any], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._emit = emit
        self._store: OutageStore = store or DbOutageStore()
        self._get_grace = get_grace
        self._is_excused = is_excused or _expected_reboot
        self._monotonic = monotonic
        self._wall = wall
        self.reset()

    def reset(self) -> None:
        """Forget everything: a new process start."""
        self._started_mono = self._monotonic()
        self._loaded = False
        self._first_setup_done = False
        self._pending: tuple[float, float] | None = None  # (monotonic, wall) of the loss
        self._notified_since: float | None = None  # wall time of the notified outage
        self._app_started_sent = False

    async def _load(self) -> None:
        if self._loaded:
            return
        try:
            self._notified_since = await self._store.load()
        except Exception:
            logger.warning("Failed to load the radio outage state", exc_info=True)
        self._loaded = True

    async def _persist(self, started_at: float | None) -> None:
        try:
            await self._store.save(started_at)
        except Exception:
            logger.warning("Failed to save the radio outage state", exc_info=True)

    async def observe(self, radio: RadioView) -> None:
        """One monitor pass. Never raises."""
        try:
            await self._observe(radio)
        except Exception:
            logger.warning("Radio watch failed", exc_info=True)

    async def _observe(self, radio: RadioView) -> None:
        await self._load()

        if not radio.connection_desired:
            self._pending = None
            if self._notified_since is not None:
                self._notified_since = None
                await self._persist(None)
            return

        if radio.is_connected:
            # Only a completed setup ends a loss: a transport that opens but
            # never finishes setup (connect, setup failure, drop, repeat) must
            # not restart the grace on every cycle.
            if radio.is_setup_complete:
                self._pending = None
                self._first_setup_done = True
                await self._recovered(radio)
            return

        if self._is_excused(radio):
            self._pending = None
            return
        if not self._first_setup_done:
            return

        now = self._monotonic()
        if self._pending is None:
            self._pending = (now, self._wall())
        if self._notified_since is not None:
            return
        grace = await self._get_grace()
        lost_mono, lost_wall = self._pending
        if now - lost_mono < grace or now - self._started_mono < grace:
            return
        self._notified_since = lost_wall
        await self._persist(lost_wall)
        self._emit(
            "radio_offline",
            {
                "urgent": True,
                "connection": radio.connection_info or "",
                "since": int(lost_wall),
                "offline_s": max(int(self._wall() - lost_wall), 0),
            },
        )

    async def _recovered(self, radio: RadioView) -> None:
        if self._notified_since is None:
            return
        downtime = max(int(self._wall() - self._notified_since), 0)
        self._notified_since = None
        await self._persist(None)
        self._emit(
            "radio_online",
            {"connection": radio.connection_info or "", "downtime_s": downtime},
        )

    def note_startup_complete(
        self, *, version: str, radio_connected: bool, connect_s: float | None
    ) -> None:
        """Send ``app_started``, once per process start."""
        if self._app_started_sent:
            return
        self._app_started_sent = True
        self._emit(
            "app_started",
            {
                "version": version,
                "radio_connected": radio_connected,
                "connect_s": None if connect_s is None else round(connect_s, 1),
            },
        )


def _expected_reboot(radio: Any) -> bool:
    from app.services.radio_lifecycle import expected_reboot_reason

    return expected_reboot_reason(radio) is not None


radio_watch = RadioWatch()
