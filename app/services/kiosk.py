"""The Raspberry Pi kiosk image: host marker, ``kiosk`` account, setup banner code.

``/var/lib/meshloom/kiosk`` is a host-provisioned flag, the same class as
``MESHCORE_MANAGED_PORTS`` (``app/AGENTS.md``, "Host flags"): written by
``scripts/build/build_rpi_kiosk_image.sh`` for new images and by
``pkg/nfpm/postinstall.sh`` on an image flashed before 5.0.0. It is not a
user setting and is not in the user docs. With it:

- the first start creates the ``kiosk`` account (role ``kiosk``, no password,
  provider ``system``) when it is absent. **No trusted-network rule is seeded**:
  the launcher signs in with a device token (below);
- the installation code is issued even on a database created by this start (the
  screen is in a room, the LAN is not), and the screen shows it while no
  administrator exists (``kiosk_setup_code``);
- the same start provisions the launcher's **device token** (T60, below).

**Device token** (``docs/dev/auth.md``, "Kiosk device token"). The launcher
(``pkg/rpi/meshloom-kiosk``) never signs in through a trusted-network rule. The
server keeps one live ``read`` token of the ``kiosk`` account (origin
``pairing``: a device token nobody typed) and writes its secret to
``KIOSK_TOKEN_DIR/kiosk.token``. The directory is provisioned by
``pkg/nfpm/postinstall.sh`` on kiosk hosts: ``meshloom:meshloom-kiosk``, mode
``2750`` (setgid, so the file gets the group), and the session user is in
``meshloom-kiosk``. The file is ``0640``; nobody else can read it. The server
refuses a directory it does not own or that others may enter (fail closed: the
screen shows the sign-in page). ``ensure_kiosk_token()`` runs at each start
and after an admin revokes the kiosk's token (``app/routers/auth_tokens.py``):
when the file does not hold the one live token, every live token of the account
is revoked and a new one written (rotation). The secret is never logged.

**Bootstrap.** The launcher sends the token in ``Authorization`` to ``POST
/api/auth/kiosk-bootstrap`` (``app/routers/auth_kiosk.py``) and gets a one-time
path, ``/api/auth/kiosk-session?code=…``, which Chromium opens: the code (256
bits, ``BOOTSTRAP_CODE_TTL_S``, single use, in memory, at most
``MAX_PENDING_CODES``) is exchanged for a session cookie of the ``kiosk``
account. Both routes answer only a direct loopback request
(``direct_loopback_request``) on a kiosk host. The token never appears in a URL
or on a command line; the code does, briefly, on Chromium's.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from starlette.types import Scope

from app.auth.models import User
from app.auth.providers.trusted_network import rule_blocker
from app.middleware import proxy_headers

logger = logging.getLogger(__name__)

KIOSK_MARKER = Path("/var/lib/meshloom/kiosk")
KIOSK_USERNAME = "kiosk"

# The launcher's device token (module docstring). Same path in the launcher,
# ``pkg/nfpm/postinstall.sh`` and the docs.
KIOSK_TOKEN_DIR = Path("/var/lib/meshloom-kiosk")
KIOSK_TOKEN_FILENAME = "kiosk.token"
KIOSK_TOKEN_NAME = "Kiosk screen"
KIOSK_TOKEN_SCOPES = ("read",)

BOOTSTRAP_CODE_TTL_S = 60.0
MAX_PENDING_CODES = 8
# hash of a bootstrap code -> (kiosk user id, monotonic deadline)
_pending_codes: dict[str, tuple[int, float]] = {}
_provision_lock = asyncio.Lock()

# The plaintext of the code issued at this start, for the kiosk screen only.
_displayed_code: str | None = None


def kiosk_host() -> bool:
    return KIOSK_MARKER.exists()


async def ensure_kiosk_user() -> None:
    """Create the ``kiosk`` account when the host is a kiosk and it is absent."""
    from app.repository.auth import UserRepository

    if not kiosk_host():
        return
    if await UserRepository.get_credentials(KIOSK_USERNAME) is not None:
        return
    await UserRepository.create(KIOSK_USERNAME, "kiosk", display_name="Kiosk", provider="system")
    logger.info("Kiosk host: created the %r account", KIOSK_USERNAME)


def remember_displayed_code(code: str | None) -> None:
    global _displayed_code
    _displayed_code = code


async def displayed_setup_code(scope: Scope, headers: Mapping[str, str]) -> str | None:
    """The installation code for the kiosk screen, or None (fail closed).

    Only on a kiosk host, only while the code issued at this start is still the
    stored one (``meshloom-admin setup-code`` replaces it from another process),
    and only to the request the kiosk browser makes: a direct loopback peer that
    is no trusted proxy, carries no proxy header, did not come through the relay
    or ingress, and passes the strict ``Host`` guard. Those are the checks of the
    trusted-network provider (``rule_blocker``, whose loopback refusal is lifted
    here because loopback is exactly the kiosk browser).
    """
    if _displayed_code is None or not kiosk_host():
        return None
    if not await direct_loopback_request(scope, headers):
        return None
    try:
        from app.auth.tokens import hash_token
        from app.repository.auth import AuthSettingsRepository

        stored = await AuthSettingsRepository.get_setup_code_hash()
    except Exception:
        logger.exception("Kiosk setup code check failed; not shown")
        return None
    if stored is None or stored != hash_token(_displayed_code):
        return None
    return _displayed_code


async def direct_loopback_request(scope: Scope, headers: Mapping[str, str]) -> bool:
    """True only for the request the kiosk browser or launcher makes: a direct
    loopback peer that is no trusted proxy, carries no proxy header, did not
    come through the relay or ingress, and passes the strict ``Host`` guard.
    Those are the checks of the trusted-network provider (``rule_blocker``,
    whose loopback refusal is lifted here because loopback is exactly the
    kiosk). Any error answers False (fail closed)."""
    try:
        from app.auth.middleware import request_via
        from app.repository.auth import AuthSettingsRepository

        peer = proxy_headers.peer_address(scope)
        if peer not in ("127.0.0.1", "::1"):
            return False
        auth_settings = await AuthSettingsRepository.get()
        return (
            rule_blocker(
                scope,
                headers,
                request_via(scope),
                trusted_proxies=proxy_headers.current(),
                public_urls=auth_settings.public_urls,
                install_kind="addon",  # only to let loopback through the check
            )
            is None
        )
    except Exception:
        logger.exception("Kiosk loopback check failed; refused")
        return False


# --- Device token -------------------------------------------------------------


def token_path() -> Path:
    return KIOSK_TOKEN_DIR / KIOSK_TOKEN_FILENAME


def is_kiosk_account(user: User | None) -> bool:
    """The system ``kiosk`` account this module creates (not any user whose
    role is ``kiosk``: an admin may give that role to a local account)."""
    return (
        user is not None
        and user.username == KIOSK_USERNAME
        and user.role == "kiosk"
        and user.provider == "system"
    )


async def kiosk_account() -> User | None:
    from app.repository.auth import UserRepository

    user = await UserRepository.get_by_username(KIOSK_USERNAME)
    return user if is_kiosk_account(user) else None


def _token_dir_problem(directory: Path) -> str | None:
    """Why the token directory cannot hold the secret, or None."""
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    if not stat.S_ISDIR(info.st_mode):
        return "not a directory"
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return "not owned by this account"
    if info.st_mode & 0o007:
        return "open to other users"
    return None


def _read_token_file(path: Path) -> str | None:
    from app.auth.providers.token import MAX_TOKEN_LENGTH

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        raw = os.read(fd, MAX_TOKEN_LENGTH + 2)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        return raw.decode("ascii").strip() or None
    except UnicodeDecodeError:
        return None


def _write_token_file(path: Path, secret: str) -> None:
    """Replace the token file atomically: a private temporary file in the same
    directory (the setgid directory gives it the session group), ``0640``,
    flushed, then renamed over the old one."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".kiosk.token.")
    try:
        try:
            os.fchmod(fd, 0o640)
            os.write(fd, (secret + "\n").encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def _file_holds_live_token(user: User, path: Path) -> str | None:
    """The id of the live ``read`` token of ``user`` whose secret the file
    holds, or None."""
    from app.auth.providers.token import looks_like_token
    from app.auth.tokens import hash_token
    from app.repository.auth import ApiTokenRepository

    secret = await asyncio.to_thread(_read_token_file, path)
    if secret is None or not looks_like_token(secret):
        return None
    row = await ApiTokenRepository.get_by_hash(hash_token(secret))
    if (
        row is None
        or row.user_id != user.id
        or row.revoked_at is not None
        or (row.expires_at is not None and row.expires_at <= int(time.time()))
        or sorted(row.scopes) != sorted(KIOSK_TOKEN_SCOPES)
    ):
        return None
    return row.id


async def ensure_kiosk_token() -> bool:
    """Make the token file hold the one live device token of the ``kiosk``
    account. True when it does. Never raises for a host problem: it logs and
    answers False (the screen then shows the sign-in page)."""
    if not kiosk_host():
        return False
    async with _provision_lock:
        return await _ensure_kiosk_token_locked()


async def _ensure_kiosk_token_locked() -> bool:
    from app.auth import audit
    from app.auth.providers.token import issue_token, revoke_token
    from app.repository.auth import ApiTokenRepository

    user = await kiosk_account()
    if user is None or user.disabled:
        return False
    problem = _token_dir_problem(KIOSK_TOKEN_DIR)
    if problem is not None:
        logger.warning(
            "Kiosk host: %s is %s; no device token written, the screen will show the sign-in page",
            KIOSK_TOKEN_DIR,
            problem,
        )
        return False
    path = token_path()
    current = await _file_holds_live_token(user, path)
    live = await ApiTokenRepository.list_live(user.id)
    if current is not None and [row.id for row in live] == [current]:
        return True

    # Rotation: no other token of the account may stay live.
    for row in live:
        if row.id == current:
            continue
        if await revoke_token(row.id):
            await audit.record(
                "token.revoked",
                "success",
                actor=audit.SYSTEM_ACTOR,
                target=f"token:{row.id}",
                details={"owner": user.id, "reason": "kiosk_rotation"},
            )
    if current is not None:
        return True
    row, secret = await issue_token(
        user, KIOSK_TOKEN_NAME, list(KIOSK_TOKEN_SCOPES), origin="pairing"
    )
    try:
        await asyncio.to_thread(_write_token_file, path, secret)
    except OSError as exc:
        # Fail closed: a token nobody holds must not stay live.
        await revoke_token(row.id)
        logger.warning(
            "Kiosk host: could not write the device token file (%s); the screen will show "
            "the sign-in page",
            type(exc).__name__,
        )
        return False
    await audit.record(
        "token.created",
        "success",
        actor=audit.SYSTEM_ACTOR,
        target=f"token:{row.id}",
        details={"scopes": row.scopes, "expires_at": None, "origin": row.origin},
    )
    logger.info("Kiosk host: wrote a new device token for the %r account", KIOSK_USERNAME)
    return True


# --- One-time bootstrap codes ---------------------------------------------------


def _purge_codes(now: float) -> None:
    for key in [key for key, (_, deadline) in _pending_codes.items() if deadline <= now]:
        del _pending_codes[key]


def issue_bootstrap_code(user: User, *, now: float | None = None) -> str:
    """A fresh one-time code for ``user``'s session (``BOOTSTRAP_CODE_TTL_S``).
    The oldest pending code goes past ``MAX_PENDING_CODES``."""
    from app.auth.tokens import hash_token, new_token

    ts = time.monotonic() if now is None else now
    _purge_codes(ts)
    while len(_pending_codes) >= MAX_PENDING_CODES:
        _pending_codes.pop(next(iter(_pending_codes)))
    code = new_token()
    _pending_codes[hash_token(code)] = (user.id, ts + BOOTSTRAP_CODE_TTL_S)
    return code


def redeem_bootstrap_code(code: str, *, now: float | None = None) -> int | None:
    """The user id of a pending code, which is spent; None when unknown, used
    or expired."""
    from app.auth.tokens import hash_token

    if not code or len(code) > 128:
        return None
    ts = time.monotonic() if now is None else now
    _purge_codes(ts)
    entry = _pending_codes.pop(hash_token(code), None)
    return entry[0] if entry is not None else None


def reset_bootstrap_codes() -> None:
    """Forget every pending code (tests)."""
    _pending_codes.clear()
