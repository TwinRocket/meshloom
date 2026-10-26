"""The audit log: who did what, from where, and how it ended (``docs/dev/auth.md``, "Audit").

Every sensitive action writes exactly one ``auth_audit`` row through ``record()``
(or ``record_request()`` / ``audited()`` from a route): an action from
``AuditAction``, an outcome from ``AuditOutcome``, the actor (user, token, IP,
``via``), a target and JSON ``details``. ``details`` go through the secret-key
filter of ``AuthAuditRepository.add()`` (``app/repository/auth.py``): never put
a password, a token, a code or a key in them.

Writing a row never fails the action it describes: a write error is logged
(action and outcome only) and swallowed. ``purge_audit()`` runs in the hourly
housekeeping loop and deletes rows older than
``auth_settings.audit_retention_days`` (366 by default, owner decision Q-A14).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args

from fastapi import Depends, HTTPException, Request

from app.auth.principal import Principal, get_principal
from app.repository.auth import AuthAuditRepository, AuthSettingsRepository, UserRepository

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400

# The vocabulary. Names marked "later" are reserved for the tickets that add
# their feature (tokens, pairing, 2FA, passkeys, the Store, Remote), so the
# viewer and its translations know them from the start.
AuditAction = Literal[
    # Sign-in and sessions
    "setup",
    "login",
    "logout",
    "session.revoked",
    # Accounts
    "user.created",
    "user.changed",
    "user.disabled",
    "user.enabled",
    "user.deleted",
    "users.listed",
    "password.changed",
    "setup_code.issued",
    "settings.changed",
    # Devices (API and device tokens) and second factors (later)
    "token.created",
    "token.revoked",
    "token.used",
    "pairing.created",
    "pairing.revoked",
    "pairing.used",
    "totp.enrolled",
    "totp.disabled",
    "totp.recovery_regenerated",
    "passkey.added",
    "passkey.removed",
    # Code that runs on the instance: the plugins (`app/routers/plugins.py`, target
    # `plugin:<instance_id>`). The `bot.*`/`bots.*` actions are no longer written since
    # 5.0.0 (bots are plugins, MSHL-192); kept so 4.18 rows still read.
    "bot.created",
    "bot.changed",
    "bot.deleted",
    "bots.disabled",
    "plugin.created",
    "plugin.changed",
    "plugin.config_changed",
    "plugin.enabled",
    "plugin.disabled",
    "plugin.installed",
    "plugin.updated",
    "plugin.removed",
    "plugins.paused",
    "plugins.resumed",
    # Custom Store repositories (`app/routers/store.py`, target `repository:<owner/name>`).
    "store.repository_added",
    "store.repository_removed",
    # Instance-wide actions
    "backup.restored",
    "private_key.imported",
    "factory_reset",
    "update.triggered",
    "notify.channel_changed",
    "notify.rules_changed",
    # Link and file sharing (`app/routers/share.py`, `PATCH /settings`).
    "share.enabled",
    "share.disabled",
    "share.object_deleted",
    "share.report_sent",
    # Remote access (later)
    "remote.linked",
    "remote.enabled",
    "remote.disabled",
]
AUDIT_ACTIONS: tuple[str, ...] = get_args(AuditAction)

# ``success``: done. ``failure``: a check failed (wrong password, invalid code,
# the radio refused, a 4xx). ``refused``: a precondition stopped it before any
# attempt (``meshloom-admin``, a login refused per IP). ``delayed``: a login
# refused while a delay runs (``app/auth/ratelimit.py``, one row per delay).
# ``error``: the server failed.
# ``pending``: a password that checked for an account that still owes its
# second factor (``POST /auth/login``, ``app/auth/totp.py``).
AuditOutcome = Literal["success", "failure", "refused", "delayed", "error", "pending"]
AUDIT_OUTCOMES: tuple[str, ...] = get_args(AuditOutcome)

# The request ``via`` values (``app/auth/principal.py``) plus ``cli``
# (``meshloom-admin``) and ``system`` (the server on its own: auto-update,
# the 4.x Basic import).
AuditVia = Literal["lan", "ingress", "trusted", "relay", "cli", "system"]


@dataclass(frozen=True)
class AuditActor:
    """Who acted. ``user_id`` is None for an anonymous caller (a failed login),
    the CLI and the server itself."""

    via: AuditVia
    user_id: int | None = None
    token_id: str | None = None
    ip: str | None = None


SYSTEM_ACTOR = AuditActor(via="system")
CLI_ACTOR = AuditActor(via="cli")


def actor_from_request(request: Any, principal: Principal | None = None) -> AuditActor:
    """The actor of a FastAPI request: its principal (or ``principal``, for a
    route that just signed someone in), the client IP and the ``via``."""
    # Imported here: the middleware imports the providers, which record rows.
    from app.auth.middleware import client_ip

    principal = principal if principal is not None else get_principal(request)
    return AuditActor(
        via=request.scope.get("auth_via", "lan"),
        user_id=principal.user_id if principal is not None else None,
        token_id=principal.token_id if principal is not None else None,
        ip=client_ip(request.scope),
    )


def request_actor(request: Request) -> AuditActor:
    """FastAPI dependency: the actor of the current request."""
    return actor_from_request(request)


# A route parameter ``actor: RequestActor = SYSTEM_ACTOR`` gets the request's
# actor from FastAPI; a direct call (tests) records the system.
RequestActor = Annotated[AuditActor, Depends(request_actor)]


def user_target(user_id: int) -> str:
    return f"user:{user_id}"


# The target of a typed name that matches no account. The typed text itself is
# never stored: a password typed into the username field would stay a year.
UNKNOWN_USERNAME_TARGET = "username:unknown"


async def typed_username_target(username: str) -> str:
    """The target for a name someone typed (a failed login, a refused CLI
    command): ``user:<id>`` when it names an account (same normalisation as
    sign-in), ``UNKNOWN_USERNAME_TARGET`` otherwise."""
    try:
        user = await UserRepository.get_by_username(username)
    except Exception:
        user = None
    return user_target(user.id) if user is not None else UNKNOWN_USERNAME_TARGET


async def record(
    action: AuditAction,
    outcome: AuditOutcome,
    *,
    actor: AuditActor,
    target: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> bool:
    """Write one audit row. Never raises: True when the row was written."""
    try:
        await AuthAuditRepository.add(
            action,
            outcome,
            actor_user_id=actor.user_id,
            actor_token_id=actor.token_id,
            target=target,
            ip=actor.ip,
            via=actor.via,
            details=details,
        )
    except ValueError:
        # A secret-looking key in ``details``: a bug at the call site.
        logger.error("Audit row %s/%s refused: details hold a secret-looking key", action, outcome)
        return False
    except Exception:
        logger.warning("Could not write the audit row %s/%s", action, outcome)
        return False
    return True


async def record_request(
    request: Any,
    action: AuditAction,
    outcome: AuditOutcome,
    *,
    principal: Principal | None = None,
    target: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> bool:
    """``record()`` with the actor of a FastAPI request."""
    return await record(
        action,
        outcome,
        actor=actor_from_request(request, principal),
        target=target,
        details=details,
    )


@dataclass
class AuditScope:
    """What an ``audited()`` block may fill in before its row is written."""

    target: str | None
    details: dict[str, Any]


def _http_outcome(status: int) -> AuditOutcome:
    # 403: a rule stopped it (bots disabled, a role). Other 4xx: a check failed.
    if status == 403:
        return "refused"
    return "error" if status >= 500 else "failure"


@asynccontextmanager
async def audited(
    actor: AuditActor,
    action: AuditAction,
    *,
    target: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> AsyncIterator[AuditScope]:
    """Write one row for the action the block performs: ``success`` when it
    ends normally; on an ``HTTPException`` ``refused`` (403), ``error`` (5xx)
    or ``failure`` (other statuses), with ``details.status``; ``error`` on any
    other exception. Exceptions are re-raised. The block may set the target and
    add details through the yielded scope. A cancelled block writes nothing."""
    scope = AuditScope(target=target, details=dict(details or {}))
    try:
        yield scope
    except HTTPException as exc:
        await record(
            action,
            _http_outcome(exc.status_code),
            actor=actor,
            target=scope.target,
            details={**scope.details, "status": exc.status_code},
        )
        raise
    except Exception:
        await record(action, "error", actor=actor, target=scope.target, details=scope.details)
        raise
    await record(action, "success", actor=actor, target=scope.target, details=scope.details)


async def purge_audit(*, now: int | None = None) -> int:
    """Delete rows older than ``audit_retention_days`` (hourly housekeeping)."""
    days = (await AuthSettingsRepository.get()).audit_retention_days
    ts = int(time.time()) if now is None else int(now)
    deleted = await AuthAuditRepository.purge_older_than(ts - days * SECONDS_PER_DAY)
    if deleted:
        logger.info("Audit purge removed %d row(s) older than %d day(s)", deleted, days)
    return deleted
