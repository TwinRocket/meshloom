"""Username and password, checked against ``users.password_hash`` (argon2id).

Used by ``POST /api/auth/login`` and by the ``Authorization: Basic``
compatibility credential (``app/security.py``). At most
``MAX_CONCURRENT_VERIFICATIONS`` argon2id checks run at once (64 MiB each,
``app/auth/passwords.py``); the others wait. On ``POST /api/auth/login`` the
login delays (``app/auth/ratelimit.py``) run first, so an attempt they refuse
never takes a place here. Beyond those plus
``MAX_QUEUED_VERIFICATIONS`` waiting, a new check is not queued:
``VerificationBusy`` is raised at once and the caller answers 503
``auth_busy``, so a flood of garbage credentials cannot stall logins for long.

An unknown username, a disabled account and an account without a password
(ingress, kiosk) all fail the same way, and each runs one argon2id check
against a fixed hash so the answer time does not tell them apart.
"""

from __future__ import annotations

import asyncio
import logging

from app.auth.models import User
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    hash_password,
    hash_password_async,
    needs_rehash,
    verify_password,
)
from app.repository.auth import UserRepository

logger = logging.getLogger(__name__)

MAX_CONCURRENT_VERIFICATIONS = 2
MAX_QUEUED_VERIFICATIONS = 8

# Checks running or waiting for the semaphore.
_pending = 0
_semaphore: asyncio.Semaphore | None = None
_dummy_hash: str | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_VERIFICATIONS)
    return _semaphore


def _verify_or_dummy(password_hash: str | None, password: str) -> bool:
    """One argon2id check, against a fixed hash when the account has none."""
    global _dummy_hash
    if password_hash is None:
        if _dummy_hash is None:
            _dummy_hash = hash_password("meshloom-dummy-password-for-timing")
        verify_password(_dummy_hash, password or "x")
        return False
    return verify_password(password_hash, password)


class VerificationBusy(RuntimeError):
    """Too many password checks running or waiting: answer 503 ``auth_busy``."""


async def _verify(stored: str | None, password: str) -> bool:
    global _pending
    if _pending >= MAX_CONCURRENT_VERIFICATIONS + MAX_QUEUED_VERIFICATIONS:
        raise VerificationBusy("password verification queue full")
    _pending += 1
    try:
        async with _get_semaphore():
            return await asyncio.to_thread(_verify_or_dummy, stored, password)
    finally:
        _pending -= 1


async def authenticate_with_hash(username: str, password: str) -> tuple[User, str] | None:
    """The enabled user whose password this is and its stored hash, or None.

    Raises ``VerificationBusy`` when the verification queue is full.
    """
    if not username or not password or len(password) > MAX_PASSWORD_LENGTH:
        return None
    credentials = await UserRepository.get_credentials(username)
    stored = None
    if credentials is not None and not credentials.user.disabled:
        secret = credentials.password_hash
        stored = secret.get_secret_value() if secret is not None else None
    ok = await _verify(stored, password)
    if not ok or credentials is None or stored is None:
        return None
    if needs_rehash(stored):
        try:
            fresh = await hash_password_async(password)
            await UserRepository.set_password_hash(
                credentials.user.id,
                fresh,
                must_change_password=credentials.user.must_change_password,
            )
            stored = fresh
        except Exception:
            logger.warning("Could not store a refreshed password hash", exc_info=False)
    return credentials.user, stored


async def authenticate(username: str, password: str) -> User | None:
    """The enabled user whose password this is, or None (``VerificationBusy`` when full)."""
    result = await authenticate_with_hash(username, password)
    return result[0] if result is not None else None
