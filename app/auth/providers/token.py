"""API and device tokens: ``Authorization: Bearer mlt_…`` on HTTP and on the WebSocket upgrade.

Rules (``docs/dev/auth.md``, "API and device tokens"; cadrage §4.2, §4.4):

- **Secret.** ``mlt_`` + 256 random bits (``tokens.new_token()``), shown once,
  in the answer that created it. The database keeps only its SHA-256
  (``api_tokens.token_hash``); the lookup goes by that hash and the stored hash
  is compared with ``hmac.compare_digest`` (``ApiTokenRepository.get_by_hash``).
  A value without the prefix or longer than ``MAX_TOKEN_LENGTH`` is refused
  before any query. No JWT: revocation needs a table.
- **Header only.** The auth gate (``app/auth/middleware.py``) reads the token
  from ``Authorization`` alone, never from a query string or a cookie.
- **Scopes.** A token holds a non-empty subset of the five permissions
  (``app/auth/permissions.py``), checked at creation against its owner's role
  (``scope_violation``: 400 ``auth_token_scope``). At each use the principal
  carries the owner's *current* role and the token's scopes, and
  ``permissions_of()`` intersects them, so a token never exceeds its owner's
  role, even after a demotion. A stored scope list with nothing valid left
  refuses the token (fail closed: empty scopes would mean the full role).
- **Refused** (anonymous, 401 ``auth_invalid_credentials``): unknown,
  revoked, expired, an owner disabled or gone, or a request through Home
  Assistant ingress (the Supervisor proxies one shared origin for every
  add-on and signs the user in itself; a device talks to the instance
  directly or through the relay). Each refusal of a token that exists writes a
  ``token.used`` row with outcome ``failure`` and ``details.reason``
  (``revoked``, ``expired``, ``owner_disabled``, ``ingress``,
  ``scopes_invalid``), at most once a minute per token. An unknown value
  writes nothing (no row to attribute it to).
- **Last use.** ``last_used_at`` is written at most once a minute
  (``ApiTokenRepository.touch``).
- **Revocation** (``revoke_token``) closes the token's WebSockets at once
  (``ws_manager.close_tokens``, 1008); a socket closes at its token's
  ``expires_at`` too, and the manager's periodic recheck catches a disabled
  owner and a revocation from another process.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

from app.auth.models import ApiToken, Role, TokenOrigin, User
from app.auth.principal import Principal, Via
from app.auth.tokens import hash_token, new_token
from app.repository.auth import (
    LAST_SEEN_WRITE_INTERVAL_S,
    ApiTokenRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "mlt_"
# ``mlt_`` + 43 URL-safe characters; anything much longer is junk.
MAX_TOKEN_LENGTH = 128

# Roles that may hold tokens of their own (cadrage §4.2: ``member`` and up).
# The ``kiosk`` token comes from the host (T60), never from the API.
TOKEN_ROLES: frozenset[Role] = frozenset({"admin", "member"})

# Refusals of one token are audited at most this often.
FAILURE_AUDIT_INTERVAL_S = 60.0
_FAILURE_AUDIT_MAX_TOKENS = 1024
_failure_audited: dict[str, float] = {}


def fresh_token() -> str:
    """A fresh token secret: ``mlt_`` + 256 random bits."""
    return TOKEN_PREFIX + new_token()


def looks_like_token(value: str) -> bool:
    return value.startswith(TOKEN_PREFIX) and len(value) <= MAX_TOKEN_LENGTH


def scope_violation(role: Role, scopes: Iterable[str]) -> str | None:
    """The first scope ``role`` does not grant (or that is not a permission),
    else None."""
    from app.auth.permissions import PERMISSIONS, ROLE_PERMISSIONS

    granted = ROLE_PERMISSIONS.get(role, frozenset())
    for scope in scopes:
        if scope not in PERMISSIONS or scope not in granted:
            return scope
    return None


def _valid_scopes(scopes: Iterable[str]) -> frozenset[str]:
    from app.auth.permissions import PERMISSIONS

    return frozenset(scope for scope in scopes if scope in PERMISSIONS)


async def issue_token(
    user: User,
    name: str,
    scopes: list[str],
    *,
    expires_at: int | None = None,
    origin: TokenOrigin = "ui",
) -> tuple[ApiToken, str]:
    """Create a token for ``user`` and return its row and its secret, to be
    shown once. The caller checked the scopes (``scope_violation``)."""
    token = fresh_token()
    row = await ApiTokenRepository.create(
        user.id,
        name,
        hash_token(token),
        sorted(set(scopes)),
        expires_at=expires_at,
        origin=origin,
    )
    return row, token


def close_token_sockets(token_ids: Iterable[str]) -> int:
    """Close the WebSockets opened with these tokens. Returns how many."""
    ids = list(token_ids)
    if not ids:
        return 0
    from app.websocket import ws_manager

    return ws_manager.close_tokens(ids)


async def revoke_token(token_id: str) -> bool:
    """Revoke one token and close its sockets. True when it was live."""
    revoked = await ApiTokenRepository.revoke(token_id)
    close_token_sockets([token_id])
    return revoked


async def _audit_failure(token: ApiToken, reason: str, via: Via, ip: str | None) -> None:
    now = time.monotonic()
    last = _failure_audited.get(token.id)
    if last is not None and now - last < FAILURE_AUDIT_INTERVAL_S:
        return
    if token.id not in _failure_audited and len(_failure_audited) >= _FAILURE_AUDIT_MAX_TOKENS:
        _failure_audited.pop(next(iter(_failure_audited)))
    _failure_audited[token.id] = now
    from app.auth import audit

    await audit.record(
        "token.used",
        "failure",
        actor=audit.AuditActor(via=via, user_id=token.user_id, token_id=token.id, ip=ip),
        target=f"token:{token.id}",
        details={"reason": reason},
    )


async def resolve_token(
    presented: str, via: Via, ip: str | None, *, now: int | None = None
) -> Principal | None:
    """The principal of a bearer token, or None (refused)."""
    if not looks_like_token(presented):
        return None
    token = await ApiTokenRepository.get_by_hash(hash_token(presented))
    if token is None:
        return None
    ts = int(time.time()) if now is None else now
    reason: str | None = None
    user: User | None = None
    scopes = _valid_scopes(token.scopes)
    if token.revoked_at is not None:
        reason = "revoked"
    elif token.expires_at is not None and token.expires_at <= ts:
        reason = "expired"
    elif via == "ingress":
        reason = "ingress"
    elif not scopes:
        reason = "scopes_invalid"
    else:
        user = await UserRepository.get(token.user_id)
        if user is None or user.disabled:
            reason = "owner_disabled"
    if reason is not None or user is None:
        await _audit_failure(token, reason or "owner_disabled", via, ip)
        return None
    if token.last_used_at is None or token.last_used_at <= ts - LAST_SEEN_WRITE_INTERVAL_S:
        try:
            await ApiTokenRepository.touch(token.id, now=ts)
        except Exception:
            # A missed last-use write never refuses a valid token.
            logger.debug("Could not record the last use of a token", exc_info=True)
    return Principal(
        user=user,
        via=via,
        credential="token",
        token_id=token.id,
        scopes=scopes,
        token_expires_at=token.expires_at,
    )


def reset_failure_audit() -> None:
    """Forget the audit throttle (tests)."""
    _failure_audited.clear()
