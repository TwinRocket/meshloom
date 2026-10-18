"""Login sessions: the opaque cookie, its row in ``sessions``, sliding expiry.

The cookie holds a 256-bit secret (``tokens.new_token()``); the database keeps
only its SHA-256. Each login creates a new session, so a session id planted
before the login is never promoted (fixation). The expiry slides: a session
seen again moves ``expires_at`` to now + ``auth_settings.session_lifetime_days``
at most once a minute (``AuthSessionRepository.touch``), and the middleware
sends the cookie again with the same Max-Age.

Cookie (cadrage §4.4): ``HttpOnly``, ``SameSite=Lax``, ``Path=/``. Over https
it is ``__Host-meshloom_session`` with ``Secure`` (the prefix forbids a
``Domain`` and requires ``Secure`` and ``Path=/``, so a sibling host cannot
plant one); over plain http it is ``meshloom_session`` without ``Secure``,
which browsers would refuse there. Only the name that matches the request's
scheme is read. No cookie at all when the request comes through Home
Assistant's ingress: its origin is shared by every add-on.

Revocation goes through this module (``revoke_session``,
``revoke_user_sessions``, the cap in ``issue_session``): it revokes the rows,
then closes the WebSockets opened with them (``ws_manager.close_sessions``,
policy close 1008). A revocation from another process (``meshloom-admin``), an
expiry or a disabled account is caught by the manager's periodic recheck
(``app/websocket.py``). ``purge_sessions`` runs in the hourly housekeeping loop
(``app/services/stale_contacts.py``).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass

from starlette.requests import cookie_parser

from app.auth.models import AuthSession, User
from app.auth.principal import Via
from app.auth.tokens import hash_token, new_token
from app.repository.auth import (
    LAST_SEEN_WRITE_INTERVAL_S,
    AuthSessionRepository,
    AuthSettingsRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)

SECURE_COOKIE_NAME = "__Host-meshloom_session"
PLAIN_COOKIE_NAME = "meshloom_session"

DAY_S = 86400


def cookie_name(secure: bool) -> str:
    return SECURE_COOKIE_NAME if secure else PLAIN_COOKIE_NAME


def session_cookie_header(token: str, *, secure: bool, max_age: int) -> str:
    """The ``Set-Cookie`` value for a session secret."""
    parts = [
        f"{cookie_name(secure)}={token}",
        "Path=/",
        f"Max-Age={max_age}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(*, secure: bool) -> str:
    parts = [f'{cookie_name(secure)}=""', "Path=/", "Max-Age=0", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def read_session_cookie(cookie_header: str | None, *, secure: bool) -> str | None:
    """The session secret from a ``Cookie`` header, under the name for this scheme."""
    if not cookie_header:
        return None
    # Starlette's parser: lenient like browsers, so another app's odd cookie
    # on the same host does not hide ours.
    value = cookie_parser(cookie_header).get(cookie_name(secure), "")
    # A secret from new_token() is 43 URL-safe characters; anything else is junk.
    if not value or len(value) > 128:
        return None
    return value


async def lifetime_seconds() -> int:
    settings = await AuthSettingsRepository.get()
    return int(settings.session_lifetime_days) * DAY_S


@dataclass(frozen=True)
class IssuedSession:
    session: AuthSession
    token: str
    max_age: int


async def issue_session(
    user: User, *, ip: str | None, user_agent: str | None, via: Via
) -> IssuedSession:
    """Create a session for ``user`` and return its secret, shown only to the cookie."""
    lifetime = await lifetime_seconds()
    token = new_token()
    session, evicted = await AuthSessionRepository.create_capped(
        user.id, hash_token(token), lifetime_s=lifetime, ip=ip, user_agent=user_agent, via=via
    )
    close_session_sockets(evicted)
    return IssuedSession(session=session, token=token, max_age=lifetime)


def close_session_sockets(session_ids: Iterable[str]) -> int:
    """Close the WebSockets opened with these sessions. Returns how many."""
    ids = list(session_ids)
    if not ids:
        return 0
    from app.websocket import ws_manager

    return ws_manager.close_sessions(ids)


async def revoke_session(session_id: str) -> bool:
    """Revoke one session and close its sockets. True when it was live."""
    revoked = await AuthSessionRepository.revoke(session_id)
    close_session_sockets([session_id])
    return revoked


async def revoke_user_sessions(user_id: int, *, except_session_id: str | None = None) -> int:
    """Revoke a user's sessions (all but ``except_session_id``) and close their
    sockets: a password change, a disable. Returns how many were revoked."""
    revoked = await AuthSessionRepository.revoke_all_for_user(
        user_id, except_session_id=except_session_id
    )
    close_session_sockets(revoked)
    return len(revoked)


async def purge_sessions(*, now: int | None = None) -> int:
    """Delete expired and revoked session rows (hourly housekeeping)."""
    deleted = await AuthSessionRepository.purge(now=now)
    if deleted:
        logger.info("Session purge removed %d expired or revoked session(s)", deleted)
    return deleted


@dataclass(frozen=True)
class ResolvedSession:
    session: AuthSession
    user: User
    # Set when the expiry slid on this request: send the cookie again.
    refreshed_max_age: int | None = None


async def resolve_session(token: str, *, now: int | None = None) -> ResolvedSession | None:
    """The live session and its enabled user for a cookie secret, sliding the expiry."""
    ts = int(time.time()) if now is None else now
    session = await AuthSessionRepository.get_active(hash_token(token), now=ts)
    if session is None:
        return None
    user = await UserRepository.get(session.user_id)
    if user is None or user.disabled:
        return None
    refreshed = None
    if session.last_seen_at <= ts - LAST_SEEN_WRITE_INTERVAL_S:
        lifetime = await lifetime_seconds()
        if await AuthSessionRepository.touch(session.id, now=ts, expires_at=ts + lifetime):
            refreshed = lifetime
    return ResolvedSession(session=session, user=user, refreshed_max_age=refreshed)


async def slide_session(session_id: str, *, now: int | None = None) -> bool:
    """Slide a session from activity that is not an HTTP request (WebSocket
    frames), with the same once-a-minute ``touch()``. True when written.

    No cookie goes out: the browser's cookie keeps its old ``Max-Age``, and the
    next HTTP request (which slides too) sends it again. There is no absolute
    lifetime (cadrage §4.4 defines only ``session_lifetime_days``, sliding)."""
    ts = int(time.time()) if now is None else now
    lifetime = await lifetime_seconds()
    return await AuthSessionRepository.touch(session_id, now=ts, expires_at=ts + lifetime)
