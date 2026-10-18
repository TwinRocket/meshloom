"""Known-device cookies (OWASP "device cookie" pattern) for the login delays.

After a successful password login, ``POST /api/auth/login`` sets a signed
cookie that says "this browser signed in as user N". A later login attempt
for that username that carries a valid one is a **known device**: it skips
the per-username slot and the address rules of ``app/auth/ratelimit.py`` and
has a failure budget of its own instead, so a guesser sharing the owner's
address (Docker without a trusted proxy) cannot keep the owner out, however
fast it sends.

- **Value.** ``v1.<user id>.<issued at>.<device id>.<generation>.<mac>``:
  ``device id`` is 128 random bits (the key of the per-device failure count),
  ``generation`` the first 16 hex digits of SHA-256 over the user's stored
  password hash, so any password change (``PATCH /api/auth/me``, the CLI, a
  rehash) invalidates every cookie of that user, and ``mac`` an HMAC-SHA256
  over the rest. The account must also be enabled when the cookie is checked.
- **Key.** A random per-process secret: after a restart every cookie is
  unknown until the next successful login on that browser (the owner then
  shares the one-attempt-a-second slot like any unknown browser). Persisting
  it needs a column (follow-up).
- **Attributes.** ``HttpOnly``, ``SameSite=Strict``, ``Path=/api/auth``,
  ``Max-Age`` ``DEVICE_COOKIE_TTL_S`` (also checked against ``issued at``),
  plus ``Secure`` and the ``__Secure-`` name prefix when the request is https
  (not ``__Host-``, which requires ``Path=/``). Never under ingress (login is refused there). One cookie
  per browser: the last user who signed in on it.

Nothing here logs a cookie value.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from starlette.requests import cookie_parser

from app.repository.auth import UserRepository

DEVICE_COOKIE_TTL_S = 180 * 86400
SECURE_COOKIE_NAME = "__Secure-meshloom_device"
PLAIN_COOKIE_NAME = "meshloom_device"
COOKIE_PATH = "/api/auth"
_VERSION = "v1"
_MAX_LENGTH = 256

_secret = secrets.token_bytes(32)


def reset_device_secret() -> None:
    """A new key: every issued cookie becomes unknown (tests)."""
    global _secret
    _secret = secrets.token_bytes(32)


def cookie_name(secure: bool) -> str:
    return SECURE_COOKIE_NAME if secure else PLAIN_COOKIE_NAME


def password_generation(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _mac(payload: str) -> str:
    return hmac.new(_secret, payload.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class DeviceCookie:
    user_id: int
    issued_at: int
    device_id: str
    generation: str


def issue(user_id: int, password_hash: str, device_id: str | None = None) -> str:
    """A signed cookie value for this user, keeping ``device_id`` when given."""
    device = device_id or secrets.token_hex(16)
    payload = ".".join(
        [_VERSION, str(user_id), str(int(time.time())), device, password_generation(password_hash)]
    )
    return f"{payload}.{_mac(payload)}"


def parse(value: str | None) -> DeviceCookie | None:
    """The signed fields, or None for anything malformed, unsigned or expired."""
    if not value or len(value) > _MAX_LENGTH:
        return None
    payload, _, mac = value.rpartition(".")
    if not payload or not hmac.compare_digest(mac, _mac(payload)):
        return None
    parts = payload.split(".")
    if len(parts) != 5 or parts[0] != _VERSION:
        return None
    try:
        user_id, issued_at = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if issued_at + DEVICE_COOKIE_TTL_S <= time.time():
        return None
    return DeviceCookie(user_id, issued_at, parts[3], parts[4])


def read(cookie_header: str | None, *, secure: bool) -> DeviceCookie | None:
    if not cookie_header:
        return None
    return parse(cookie_parser(cookie_header).get(cookie_name(secure)))


async def known_device(cookie: DeviceCookie | None, username: str) -> str | None:
    """The device id when ``cookie`` is valid for ``username`` now, else None."""
    if cookie is None:
        return None
    credentials = await UserRepository.get_credentials(username)
    if credentials is None or credentials.user.disabled or credentials.user.id != cookie.user_id:
        return None
    if credentials.password_hash is None:
        return None
    current = password_generation(credentials.password_hash.get_secret_value())
    if not hmac.compare_digest(current, cookie.generation):
        return None
    return cookie.device_id


def cookie_header(value: str, *, secure: bool) -> str:
    """The ``Set-Cookie`` value."""
    parts = [
        f"{cookie_name(secure)}={value}",
        f"Path={COOKIE_PATH}",
        f"Max-Age={DEVICE_COOKIE_TTL_S}",
        "HttpOnly",
        "SameSite=Strict",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
