"""``Authorization: Basic``: the compatibility credential of the 4.x Basic auth.

Up to 4.18 the only gate was ``MESHCORE_BASIC_AUTH_USERNAME`` /
``MESHCORE_BASIC_AUTH_PASSWORD``, checked by an ASGI middleware. In 5.0 the
gate is ``app/auth/middleware.py`` and those credentials become an account
(cadrage, upgrade path P3):

- **Import, once.** At startup, when both variables are set and no user
  exists, they create an ``admin`` with that name and password (no password
  policy and no installation code: the credentials are the proof of
  ownership) and ``auth_settings.basic_imported`` is set. From then on the
  variables are never read again; every start logs a warning while they are
  still set.
- **Header still accepted** in 5.0 and 5.1 (removed in 5.2): scripts and Home
  Assistant REST sensors keep sending it. It is checked against ``users``,
  any account with a password, not only the imported one. A browser replays it
  like a cookie, so it is an ambient credential: unsafe methods go through the
  same Origin rule as the session cookie.
- **Verification cache.** argon2id costs a large fraction of a second per
  check, so a successful check is remembered for ``BASIC_CACHE_TTL_S`` under
  a low-cost scrypt of the password (salt: a random per-process secret and
  ``username_key``):
  100 consecutive requests cost one
  verification. The user is read again on every request: a cached check is
  used only while ``users.updated_at`` is unchanged, so a disabled account or
  a password reset (even from the CLI, another process) applies at once;
  ``invalidate_basic_cache()`` drops entries on a password change.

The rest of the migration (health/debug cleanup, ``auth_compat`` logging) is a
later Access ticket.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass

from app.auth.models import User
from app.auth.passwords import MAX_PASSWORD_LENGTH, hash_password_async
from app.auth.providers.password import authenticate
from app.repository.auth import (
    AuthSettingsRepository,
    InvalidUsernameError,
    UserRepository,
    username_key,
)

logger = logging.getLogger(__name__)

BASIC_REALM = "Meshloom"
BASIC_CHALLENGE = f'Basic realm="{BASIC_REALM}", charset="UTF-8"'

BASIC_CACHE_TTL_S = 300
BASIC_CACHE_MAX_ENTRIES = 256


@dataclass(frozen=True)
class BasicCredentials:
    username: str
    password: str


def parse_basic_authorization(value: str | None) -> BasicCredentials | None:
    """``Authorization: Basic <base64(user:password)>``, decoded; None if malformed."""
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "basic" or not token.strip():
        return None
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep or not username or not password or len(password) > MAX_PASSWORD_LENGTH:
        return None
    return BasicCredentials(username=username, password=password)


# Salts the cache keys: an entry is derived from the credentials and this
# per-process secret, so the cache alone cannot be brute-forced offline.
_CACHE_SECRET = secrets.token_bytes(32)

# cache key -> (user id, users.updated_at when verified, monotonic expiry)
_cache: dict[str, tuple[int, int, float]] = {}


def _cache_key(credentials: BasicCredentials) -> str | None:
    try:
        key = username_key(credentials.username)
    except InvalidUsernameError:
        return None
    # A KDF, not a bare hash, even for an in-memory key: scrypt at a low cost
    # (1 MiB, about a millisecond), salted with the per-process secret and the user.
    return hashlib.scrypt(
        credentials.password.encode(),
        salt=_CACHE_SECRET + key.encode(),
        n=1024,
        r=8,
        p=1,
        dklen=32,
    ).hex()


def invalidate_basic_cache(user_id: int | None = None) -> None:
    """Forget cached Basic checks, for one user or all of them."""
    if user_id is None:
        _cache.clear()
        return
    for key in [k for k, (uid, _, _) in _cache.items() if uid == user_id]:
        _cache.pop(key, None)


async def authenticate_basic(credentials: BasicCredentials) -> User | None:
    """The enabled user these Basic credentials prove, using the cache."""
    key = _cache_key(credentials)
    if key is None:
        return None
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[2] > now:
        user = await UserRepository.get(cached[0])
        if user is not None and not user.disabled and user.updated_at == cached[1]:
            return user
        # Changed since (password reset from the CLI, disabled): check again.
        _cache.pop(key, None)
    user = await authenticate(credentials.username, credentials.password)
    if user is None:
        return None
    if len(_cache) >= BASIC_CACHE_MAX_ENTRIES:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (user.id, user.updated_at, now + BASIC_CACHE_TTL_S)
    return user


async def basic_compat_active() -> bool:
    """Whether 401 answers carry the Basic challenge: the instance came from 4.x Basic."""
    try:
        return (await AuthSettingsRepository.get()).basic_imported
    except Exception:
        return False


async def import_basic_credentials(username: str, password: str) -> bool:
    """Create the admin from the retired Basic variables when no user exists. True if created."""
    if not username or not password:
        return False
    if await UserRepository.list_all():
        return False
    try:
        password_hash = await hash_password_async(password)
        await UserRepository.create(username, "admin", password_hash=password_hash)
    except (InvalidUsernameError, ValueError) as exc:
        # The variables stay unused; the installation code path takes over.
        logger.error("Could not import MESHCORE_BASIC_AUTH_USERNAME as an account: %s", exc)
        return False
    await AuthSettingsRepository.update(basic_imported=True)
    logger.warning(
        "Imported MESHCORE_BASIC_AUTH_USERNAME as the administrator account %r. "
        "Sign in with the same name and password.",
        username,
    )
    return True


def warn_retired_basic_env(configured: bool) -> None:
    if configured:
        logger.warning(
            "MESHCORE_BASIC_AUTH_USERNAME / MESHCORE_BASIC_AUTH_PASSWORD are retired and no "
            "longer read: accounts live in the database (docs/user, accounts page). Remove "
            "them from the environment. Authorization: Basic is still accepted until 5.2 "
            "and is checked against the accounts."
        )
