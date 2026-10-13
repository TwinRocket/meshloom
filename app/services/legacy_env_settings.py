"""One-shot import of retired ``MESHCORE_*`` settings into ``app_settings``.

Meshloom's behaviour is set in the UI, not through environment variables. These
variables used to be read on every start and are now settings in the database:

- ``MESHCORE_VAPID_SUBJECT`` → ``vapid_subject`` (Settings → Notifications);
- ``MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK``, ``MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE``
  and ``MESHCORE_LOAD_WITH_AUTOEVICT`` → columns of the same name without the prefix
  (Settings → Radio → Advanced).

At the first start after the upgrade, a value still set is copied once, so an
existing install keeps behaving as before: a usable VAPID subject only when the
field in the UI is empty, a workaround only when it is switched on (``true``).
``legacy_env_settings_imported`` then records that the import ran; from there on
the variables are never read again, and each start only warns that one is still
set. The same pattern as the radio transport (``services/radio_transport.py``).
"""

from __future__ import annotations

import logging
import os

from app.push.vapid import normalize_vapid_subject, vapid_subject_is_usable
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

ENV_VAPID_SUBJECT = "MESHCORE_VAPID_SUBJECT"

# Environment variable → app_settings column, with where it now lives in the UI.
RADIO_WORKAROUND_ENV: dict[str, str] = {
    "MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK": "enable_message_poll_fallback",
    "MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE": "force_channel_slot_reconfigure",
    "MESHCORE_LOAD_WITH_AUTOEVICT": "load_with_autoevict",
}

RETIRED_ENV: dict[str, str] = {
    ENV_VAPID_SUBJECT: "Settings → Notifications",
    **dict.fromkeys(RADIO_WORKAROUND_ENV, "Settings → Radio → Advanced"),
}

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _env_true(name: str) -> bool:
    return _env(name).lower() in _TRUE_VALUES


def warn_about_retired_env() -> list[str]:
    """Log one warning per retired variable still set. Returns their names."""
    still_set = [name for name in RETIRED_ENV if name in os.environ]
    for name in still_set:
        logger.warning(
            "%s is set but no longer read: this setting lives in %s. "
            "Remove the variable from your environment.",
            name,
            RETIRED_ENV[name],
        )
    return still_set


async def import_legacy_env_settings() -> dict[str, object]:
    """Copy leftover retired variables into the database, once. Returns what was copied.

    Never overwrites a value already in the database. Runs only while the marker is
    unset; afterwards it only warns about variables that are still defined.
    """
    if await AppSettingsRepository.get_legacy_env_settings_imported():
        warn_about_retired_env()
        return {}

    imported: dict[str, object] = {}

    raw_subject = _env(ENV_VAPID_SUBJECT)
    if raw_subject:
        subject = normalize_vapid_subject(raw_subject)
        stored = await AppSettingsRepository.get_vapid_subject()
        if stored.strip():
            logger.info(
                "%s ignored: Settings → Notifications already has a contact", ENV_VAPID_SUBJECT
            )
        elif vapid_subject_is_usable(subject):
            await AppSettingsRepository.set_vapid_subject(subject)
            imported["vapid_subject"] = subject
        else:
            logger.warning(
                "%s=%r is not a mailto: address or an https:// origin; not imported",
                ENV_VAPID_SUBJECT,
                raw_subject,
            )

    current = await AppSettingsRepository.get()
    flags: dict[str, bool] = {}
    for name, column in RADIO_WORKAROUND_ENV.items():
        if _env_true(name) and not getattr(current, column):
            flags[column] = True
    if flags:
        await AppSettingsRepository.update(
            enable_message_poll_fallback=flags.get("enable_message_poll_fallback"),
            force_channel_slot_reconfigure=flags.get("force_channel_slot_reconfigure"),
            load_with_autoevict=flags.get("load_with_autoevict"),
        )
        imported.update(flags)

    await AppSettingsRepository.mark_legacy_env_settings_imported()
    for key, value in imported.items():
        logger.warning("Imported %s=%r from the environment into app_settings", key, value)
    warn_about_retired_env()
    return imported
