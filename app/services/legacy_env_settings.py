"""One-shot import of retired ``MESHCORE_*`` / ``MESHLOOM_COMMUNITY*`` settings.

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
set. Unlike the radio transport import (``services/radio_transport.py``), which a
new database skips, this one also runs on a new database: a variable set before the
very first start is copied too.

The developer-only ``__CLOWNTOWN_DO_CLOCK_WRAPAROUND`` is retired without an
import: it could only switch the clock wraparound on, and ``auto_clock_wraparound``
(Settings → Radio → Advanced, migration 094) is now on by default. It only gets the
startup warning, plus a second one when it is set to a false value (it kept the fix
off, the setting now starts on).

``MESHCORE_BASIC_AUTH_USERNAME`` / ``_PASSWORD`` are retired too, but their one-time
import is not here: ``app/security.py`` turns them into the administrator account
(``app/auth/setup_code.py`` runs it first). They only get the startup warning, plus a
second one when only one of the two is set.

The ``MESHLOOM_COMMUNITY*`` variables are retired too (Community is an opt-in in
the UI, its addresses are fixed): ``import_legacy_community_iata`` copies a
leftover ``MESHLOOM_COMMUNITY_IATA`` into an existing database only, once
(``community_env_imported``, migration 091); the others are never imported. All
of them get the same startup warning.
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

ENV_COMMUNITY_IATA = "MESHLOOM_COMMUNITY_IATA"
ENV_BASIC_USERNAME = "MESHCORE_BASIC_AUTH_USERNAME"
ENV_BASIC_PASSWORD = "MESHCORE_BASIC_AUTH_PASSWORD"
_BASIC_UI = (
    "Settings → Users & Access (read only at the first start with no account, to create "
    "the administrator, see app/security.py; Authorization: Basic is still accepted "
    "until 5.2)"
)
# Never imported: the setting that replaces it is on by default.
ENV_CLOCK_WRAPAROUND = "__CLOWNTOWN_DO_CLOCK_WRAPAROUND"
_COMMUNITY_UI = "Settings → Meshloom Community"
_COMMUNITY_FIXED = "no setting at all (the Meshloom Community addresses are fixed)"
_PRIVATE_KEY_ALWAYS = (
    "no setting at all (GET /api/radio/private-key and the full backup in "
    "Settings → Database always include the key)"
)

# Retired variable → where its setting lives now (for the startup warning).
RETIRED_ENV: dict[str, str] = {
    ENV_VAPID_SUBJECT: "Settings → Notifications",
    **dict.fromkeys(RADIO_WORKAROUND_ENV, "Settings → Radio → Advanced"),
    "MESHLOOM_COMMUNITY": f"{_COMMUNITY_UI} (an opt-in; a new database starts off)",
    ENV_COMMUNITY_IATA: _COMMUNITY_UI,
    "MESHLOOM_COMMUNITY_BROKER_HOST": _COMMUNITY_FIXED,
    "MESHLOOM_COMMUNITY_API_BASE": _COMMUNITY_FIXED,
    ENV_CLOCK_WRAPAROUND: "Settings → Radio → Advanced (on by default)",
    ENV_BASIC_USERNAME: _BASIC_UI,
    ENV_BASIC_PASSWORD: _BASIC_UI,
    "MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT": _PRIVATE_KEY_ALWAYS,
}

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _env_true(name: str) -> bool:
    return _env(name).lower() in _TRUE_VALUES


def warn_about_retired_env() -> list[str]:
    """Log one warning per retired variable still set (and not blank). Returns their names."""
    still_set = [name for name in RETIRED_ENV if _env(name)]
    for name in still_set:
        logger.warning(
            "%s is set but no longer read: this setting lives in %s. "
            "Remove the variable from your environment.",
            name,
            RETIRED_ENV[name],
        )
    if len({ENV_BASIC_USERNAME, ENV_BASIC_PASSWORD} & set(still_set)) == 1:
        # Never an error since 5.0 (it used to stop the start): half a pair imports nothing.
        logger.warning(
            "Only one of MESHCORE_BASIC_AUTH_USERNAME and MESHCORE_BASIC_AUTH_PASSWORD "
            "is set: nothing was imported as an account."
        )
    if ENV_CLOCK_WRAPAROUND in still_set and not _env_true(ENV_CLOCK_WRAPAROUND):
        # The variable kept the fix off; the setting that replaces it starts on.
        logger.warning(
            "%s=%s used to keep the radio clock wraparound off; it is now on by "
            "default. Turn it off in Settings → Radio → Advanced if this board needs it off.",
            ENV_CLOCK_WRAPAROUND,
            _env(ENV_CLOCK_WRAPAROUND),
        )
    return still_set


async def import_legacy_community_iata(*, existing_database: bool) -> str | None:
    """Copy a leftover ``MESHLOOM_COMMUNITY_IATA`` into an existing database, once.

    Returns the IATA written, if any. Runs while ``community_env_imported`` is
    unset, then never reads the variable again (the warning is
    ``warn_about_retired_env``'s).

    - A new database ignores it: joining Community is an explicit opt-in in the
      UI, and the database starts off.
    - An existing database: the variable overrode the stored IATA on every
      read, so a valid value is written when it differs, to keep the region
      Community already knows. ``MESHLOOM_COMMUNITY`` only ever seeded a
      brand-new database, so the stored opt-in is left as it is.
    """
    from app.services.meshloom_community import get_community_record, normalize_iata

    if await AppSettingsRepository.get_community_env_imported():
        return None
    written = None
    if existing_database:
        env_iata = normalize_iata(_env(ENV_COMMUNITY_IATA))
        stored = (await get_community_record()).iata
        if env_iata and env_iata != stored:
            await AppSettingsRepository.update_community(iata=env_iata)
            written = env_iata
            logger.warning(
                "Imported %s=%s into the database (was %r)", ENV_COMMUNITY_IATA, env_iata, stored
            )
    await AppSettingsRepository.mark_community_env_imported()
    return written


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
