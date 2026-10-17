"""In-memory copy of the radio workarounds kept in ``app_settings``.

Settings → Radio → Advanced edits four workarounds for misbehaving radios. Three
used to be environment variables (``MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK``,
``MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE``, ``MESHCORE_LOAD_WITH_AUTOEVICT``) and
are now stored in the database; ``services/legacy_env_settings.py`` imports a
leftover value once. The fourth, ``auto_clock_wraparound`` (on by default), replaces
the developer-only ``__CLOWNTOWN_DO_CLOCK_WRAPAROUND``, which is only warned about.

They are read on hot, synchronous paths (every channel send asks whether a slot
may be reused), so the values live here. ``load()`` fills them at startup and
``AppSettingsRepository.update()`` refreshes them after every write, so this copy
never drifts from the database. This module imports nothing from the app at load
time, so the repository and ``app.radio`` can both import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import AppSettings


@dataclass(frozen=True)
class RadioWorkarounds:
    enable_message_poll_fallback: bool = False
    force_channel_slot_reconfigure: bool = False
    load_with_autoevict: bool = False
    auto_clock_wraparound: bool = True


_current = RadioWorkarounds()


def current() -> RadioWorkarounds:
    """The workarounds as last read from the database."""
    return _current


def apply(app_settings: AppSettings) -> RadioWorkarounds:
    """Replace the in-memory copy with the values of ``app_settings``."""
    global _current
    _current = RadioWorkarounds(
        enable_message_poll_fallback=app_settings.enable_message_poll_fallback,
        force_channel_slot_reconfigure=app_settings.force_channel_slot_reconfigure,
        load_with_autoevict=app_settings.load_with_autoevict,
        auto_clock_wraparound=app_settings.auto_clock_wraparound,
    )
    return _current


async def load() -> RadioWorkarounds:
    """Read the workarounds from the database. Call at startup, after the import."""
    from app.repository.settings import AppSettingsRepository

    return apply(await AppSettingsRepository.get())


def reset() -> None:
    """Back to the defaults (tests, and a fresh process)."""
    global _current
    _current = RadioWorkarounds()
