"""First run: who may create the administrator (cadrage P1, P3).

At startup, while no account exists (``prepare_first_run``, called by the lifespan):

- the retired ``MESHCORE_BASIC_AUTH_*`` variables, when set, become the
  administrator (``app/security.py``): nothing else is asked;
- otherwise, on a database that existed before this start (an instance that
  updated itself overnight, ``app/routers/updates.py``), an **installation
  code** of ``SETUP_CODE_DIGITS`` digits is generated, its SHA-256 stored in
  ``auth_settings.setup_code_hash``, and the code logged at WARNING.
  ``POST /api/auth/setup`` then requires it, so the first person on the LAN
  cannot claim the instance. A new code replaces the old one at every start;
  ``meshloom-admin setup-code`` prints a fresh one too;
- a database created by this start asks for no code: whoever installed it is
  in front of it, the same trust as before accounts existed.

Once an account exists the stored hash is cleared. Wrong codes are answered
one at a time, ``SETUP_FAILURE_DELAY_S`` apart (``app/routers/auth.py``),
until the login limiter of a later Access ticket.
"""

from __future__ import annotations

import logging
import secrets

from app.auth.tokens import hash_token, token_matches
from app.repository.auth import AuthSettingsRepository, UserRepository

logger = logging.getLogger(__name__)

SETUP_CODE_DIGITS = 8


def new_setup_code() -> str:
    return f"{secrets.randbelow(10**SETUP_CODE_DIGITS):0{SETUP_CODE_DIGITS}d}"


def normalize_setup_code(raw: str) -> str:
    """Digits only: a code typed as ``1234 5678`` or ``1234-5678`` still matches."""
    return "".join(ch for ch in raw if ch.isdigit())


async def issue_setup_code() -> str:
    """Store a new installation code and return it (the CLI prints it)."""
    code = new_setup_code()
    await AuthSettingsRepository.set_setup_code_hash(hash_token(code))
    return code


async def setup_code_required() -> bool:
    return await AuthSettingsRepository.get_setup_code_hash() is not None


async def check_setup_code(raw: str | None) -> bool:
    stored = await AuthSettingsRepository.get_setup_code_hash()
    if stored is None:
        return True
    code = normalize_setup_code(raw or "")
    return len(code) == SETUP_CODE_DIGITS and token_matches(code, stored)


async def clear_setup_code() -> None:
    await AuthSettingsRepository.set_setup_code_hash(None)


async def prepare_first_run(
    *, existing_database: bool, basic_username: str, basic_password: str
) -> None:
    """Import Basic, or issue the installation code, while no account exists."""
    from app.security import import_basic_credentials, warn_retired_basic_env

    basic_configured = bool(basic_username and basic_password)
    if await UserRepository.any_exist():
        if await setup_code_required():
            await clear_setup_code()
        warn_retired_basic_env(basic_configured)
        return
    if basic_configured and await import_basic_credentials(basic_username, basic_password):
        await clear_setup_code()
        warn_retired_basic_env(True)
        return
    if not existing_database:
        await clear_setup_code()
        logger.info("No account yet: open Meshloom to create the administrator")
        return
    code = await issue_setup_code()
    logger.warning(
        "No account exists yet on this existing database. To create the administrator, "
        "open Meshloom and enter the installation code %s (valid until the next restart; "
        "`meshloom-admin setup-code` prints a new one).",
        code,
    )
