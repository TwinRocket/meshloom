"""The global plugin pause (MSHL-216): the incident switch of Settings > Extensions.

One persisted state (`app_settings.plugins_paused*`, migration 120) and the
in-memory copy the supervisor reads (`plugin_manager.paused`). While paused:

- every running plugin instance is stopped cleanly and none starts (boot,
  install, enable, config change, manual restart), the `enabled` flags kept;
- events are dropped, never queued, and `POST /api/plugins/{id}/test` answers
  409 `plugins_paused`;
- the legacy bot modules are held the same way (`fanout_manager.set_bots_paused`).

Resume starts exactly the enabled instances again, staggered. The host lock
(`MESHCORE_DISABLE_PLUGINS`) is separate and takes precedence in health; this
switch is not the old bots "disable until restart" toggle: it survives a
restart until an admin resumes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.plugins import host

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PauseState:
    paused: bool = False
    since: int | None = None
    by: str | None = None


_state = PauseState()
_lock = asyncio.Lock()


def state() -> PauseState:
    return _state


async def load(manager: host.PluginManager | None = None) -> None:
    """Read the persisted pause at boot, before the fanout and plugin starts. Never raises."""
    global _state
    from app.repository.settings import AppSettingsRepository

    try:
        paused, since, by = await AppSettingsRepository.get_plugins_pause()
    except Exception:
        logger.exception("Could not read the plugin pause; plugins start normally")
        return
    _state = PauseState(paused, since, by) if paused else PauseState()
    _apply_flags(manager or host.plugin_manager, paused)
    if paused:
        logger.warning("All plugins are paused (since %s by %s)", since, by)


def _apply_flags(manager: host.PluginManager, paused: bool) -> None:
    from app.fanout.manager import fanout_manager

    manager.paused = paused
    fanout_manager._bots_paused = paused


async def set_paused(
    paused: bool, by: str | None, manager: host.PluginManager | None = None
) -> tuple[PauseState, bool]:
    """Pause or resume; returns the new state and whether it changed (idempotent)."""
    global _state
    plugin_manager = manager or host.plugin_manager
    from app.fanout.manager import fanout_manager
    from app.repository.settings import AppSettingsRepository

    async with _lock:
        if paused == _state.paused:
            return _state, False
        new = PauseState(True, int(time.time()), by) if paused else PauseState()
        # Persist first: the next boot honours the switch even if a stop fails here.
        await AppSettingsRepository.set_plugins_pause(new.paused, new.since, new.by)
        _state = new
        if paused:
            await plugin_manager.pause_all()
            await fanout_manager.set_bots_paused(True)
        else:
            await fanout_manager.set_bots_paused(False)
            await plugin_manager.resume_all()
        return _state, True
