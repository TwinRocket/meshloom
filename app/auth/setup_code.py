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
  ``meshloom-admin setup-code`` prints a fresh one too. A retry from the setup
  route (below) keeps a code that is already stored: it may be the one the
  CLI printed while the startup preparation was failing;
- a database created by this start asks for no code from the local network:
  whoever installed it is in front of it, the same trust as before accounts
  existed. On a kiosk host (``app/services/kiosk.py``) a code is issued all
  the same, and the kiosk screen shows it.

**Exposed first run** (MSHL-424). A fresh instance published on the Internet
must not go to its first visitor. A first-run request is *exposed*
(``request_exposed()``) when it did not come straight from the local network:
it came through the relay, its client address (after a trusted proxy's
rewrite, ``app/middleware/proxy_headers.py``) is not private or loopback (not
``is_global``: RFC 1918, loopback, link-local, ULA and CGNAT count as local),
it carries forwarding headers from a peer that is not a trusted proxy (the
client is unknown), or a public address is declared (``auth_settings.public_urls``).
An exposed request always needs the code: on a fresh database with none
stored, the first exposed ``GET /api/auth/session`` or ``POST /api/auth/setup``
issues one and logs it (``ensure_exposure_code()``), and setup answers 403
``auth_setup_code_invalid`` without it. A request from the local network
still needs no code on a fresh database, even after such a code was issued:
only a code issued for that reason (its hash is remembered in memory) is
waived. A code from the CLI or a restart is asked of everyone. Docker's
userland proxy (every client seen as the gateway) is the case this cannot see:
declaring the public address closes it.

**Fail closed.** Setup is refused (503 ``auth_setup_unavailable``) until
``prepare_first_run`` has succeeded in this process: when it fails at startup
(a locked database, say) the setup route tries it again, and keeps refusing
while it still fails. It is also refused when the database existed before this
start and no code hash is stored (cleared by hand, or a failed write), so an
error never leaves an existing instance open to the first visitor.

Once an account exists the stored hash is cleared. Wrong codes are answered
one at a time, ``SETUP_FAILURE_DELAY_S`` apart (``app/routers/auth.py``): at
most one guess a second against 10**8 codes, so the login delays
(``app/auth/ratelimit.py``) do not apply to setup.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Mapping
from typing import Any

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


class SetupUnavailable(RuntimeError):
    """First-run preparation has not succeeded: refuse setup (503)."""


# What the last successful prepare_first_run() saw; None until one succeeded.
_existing_database: bool | None = None
# The arguments of the last attempt, for a retry from the setup route.
_last_args: dict | None = None
# The hash of a code issued because an exposed request reached a fresh database.
_exposure_code_hash: str | None = None
_exposure_lock = asyncio.Lock()


def reset_first_run_state() -> None:
    """Back to "never prepared" (tests, and a fresh process)."""
    global _existing_database, _last_args, _exposure_code_hash
    from app.services.kiosk import remember_displayed_code

    _existing_database = None
    _last_args = None
    _exposure_code_hash = None
    remember_displayed_code(None)


async def request_exposed(scope: Mapping[str, Any]) -> bool:
    """Whether a first-run request may come from outside the local network
    (module docstring, "Exposed first run")."""
    import ipaddress

    from starlette.datastructures import Headers

    from app.auth.providers import trusted_network
    from app.auth.ratelimit import client_is_public
    from app.middleware import proxy_headers

    if scope.get("auth_via", "lan") != "lan":
        return True
    if trusted_network.carries_proxy_headers(Headers(scope=scope)):
        try:
            peer = ipaddress.ip_address(proxy_headers.peer_address(scope) or "")
        except ValueError:
            return True
        if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped is not None:
            peer = peer.ipv4_mapped
        if not any(peer in ipaddress.ip_network(p) for p in proxy_headers.current()):
            return True
    if client_is_public(scope):
        return True
    return bool((await AuthSettingsRepository.get()).public_urls)


async def ensure_exposure_code() -> None:
    """Issue and log a code when an exposed request reaches a fresh database
    that holds none. Never on an existing database: that one stays closed."""
    global _exposure_code_hash
    if _existing_database is not False:
        return
    async with _exposure_lock:
        if await AuthSettingsRepository.get_setup_code_hash() is not None:
            return
        code = new_setup_code()
        stored = hash_token(code)
        await AuthSettingsRepository.set_setup_code_hash(stored)
        _exposure_code_hash = stored
    logger.warning(
        "Meshloom was opened from outside the local network while no account exists. "
        "To create the administrator from there, enter the installation code %s "
        "(valid until the next restart; `meshloom-admin setup-code` prints a new one). "
        "From the local network no code is needed.",
        code,
    )


async def ensure_first_run_ready() -> None:
    """Raise ``SetupUnavailable`` unless first-run preparation succeeded (retrying it once)."""
    if _existing_database is not None:
        return
    if _last_args is None:
        raise SetupUnavailable("first run was never prepared")
    try:
        await prepare_first_run(**_last_args, keep_stored_code=True)
    except Exception as exc:
        logger.warning("First-run preparation failed again; setup stays refused")
        raise SetupUnavailable("first run preparation failed") from exc


def _waived(stored: str, exposed: bool) -> bool:
    """A code issued only for exposed requests is not asked of the local network."""
    return not exposed and _existing_database is False and stored == _exposure_code_hash


async def setup_code_required(exposed: bool = False) -> bool:
    if _existing_database:
        return True
    stored = await AuthSettingsRepository.get_setup_code_hash()
    if stored is None:
        return exposed
    return not _waived(stored, exposed)


async def check_setup_code(raw: str | None, exposed: bool = False) -> bool:
    """Whether ``raw`` opens setup. Raises ``SetupUnavailable`` when it must stay closed."""
    await ensure_first_run_ready()
    stored = await AuthSettingsRepository.get_setup_code_hash()
    if stored is None:
        if _existing_database:
            # An existing database without a code: never open, whatever was sent.
            raise SetupUnavailable("no installation code stored for an existing database")
        if not exposed:
            return True
        await ensure_exposure_code()
        return False
    if _waived(stored, exposed):
        return True
    code = normalize_setup_code(raw or "")
    return len(code) == SETUP_CODE_DIGITS and token_matches(code, stored)


async def clear_setup_code() -> None:
    await AuthSettingsRepository.set_setup_code_hash(None)


async def prepare_first_run(
    *,
    existing_database: bool,
    basic_username: str,
    basic_password: str,
    keep_stored_code: bool = False,
) -> None:
    """Import Basic, or issue the installation code, while no account exists.

    Setup stays refused until this returns without raising (module docstring).
    ``keep_stored_code`` (the retry from the setup route) issues no new code
    when one is already stored.
    """
    global _existing_database, _last_args
    from app.services.kiosk import ensure_kiosk_token, ensure_kiosk_user

    try:
        await ensure_kiosk_user()
        # The launcher's device token (T60): the screen signs in with it.
        await ensure_kiosk_token()
    except Exception:
        # The screen only lacks its account; setup does not depend on it.
        logger.exception("Failed to create the kiosk account or its device token")
    _last_args = {
        "existing_database": existing_database,
        "basic_username": basic_username,
        "basic_password": basic_password,
    }
    _existing_database = None
    await _prepare(existing_database, basic_username, basic_password, keep_stored_code)
    _existing_database = existing_database


async def _prepare(
    existing_database: bool, basic_username: str, basic_password: str, keep_stored_code: bool
) -> None:
    from app.security import import_basic_credentials

    basic_configured = bool(basic_username and basic_password)
    if await UserRepository.any_exist():
        if await AuthSettingsRepository.get_setup_code_hash() is not None:
            await clear_setup_code()
        return
    if basic_configured and await import_basic_credentials(basic_username, basic_password):
        await clear_setup_code()
        return
    from app.services.kiosk import kiosk_host, remember_displayed_code

    kiosk = kiosk_host()
    if not existing_database and not kiosk:
        await clear_setup_code()
        logger.info("No account yet: open Meshloom to create the administrator")
        return
    if keep_stored_code and await AuthSettingsRepository.get_setup_code_hash() is not None:
        logger.warning(
            "No account exists yet on this existing database. Keeping the installation "
            "code already issued (in an earlier log line, or printed by "
            "`meshloom-admin setup-code`, which prints a new one)."
        )
        return
    code = await issue_setup_code()
    if kiosk:
        remember_displayed_code(code)
    logger.warning(
        "No account exists yet on this existing database. To create the administrator, "
        "open Meshloom and enter the installation code %s (valid until the next restart; "
        "`meshloom-admin setup-code` prints a new one).",
        code,
    )
