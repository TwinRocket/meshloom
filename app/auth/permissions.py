"""Who may call what: the route permission registry (docs/dev/auth.md, "Permissions").

Five permissions, granted by the four roles (cadrage §4.2, decision Q-A7):

- ``kiosk``: ``read``. Channels, contacts, packets (control packets included),
  stats, map, health; muted and pending channels too. Never a direct message,
  no settings, no profile.
- ``viewer``: ``read`` + ``read_private`` (direct messages, marking read).
- ``member``: ``viewer`` + ``send`` (send, create and edit contacts and
  channels, favourites, its browser's push subscription).
- ``admin``: everything. ``admin`` implies every other permission.

``repeater_admin`` (repeaters and rooms) is granted by ``admin`` only; a token
scope (M3) can narrow a principal to it. A principal's ``scopes``, when set,
narrow its role's permissions and never extend them.

Every route declares exactly one access rule in its decorator:
``dependencies=[Depends(require("send"))]``, ``Depends(public())`` for a route in
``PUBLIC_ROUTES``, or ``Depends(own_account())`` for the caller's own profile
and sessions (every role but ``kiosk``). ``enforce_declaration`` is installed on
the app: an ``/api`` route without a declaration is refused to everyone but an
admin (default deny). ``tests/test_auth_route_coverage.py`` fails on a route,
or a WebSocket event type, that is not classified.

A refusal is 403 ``auth_forbidden`` with ``params.permission``; a WebSocket
upgrade is closed with 1008 before ``accept()``.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Literal, get_args

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection

from app.auth.middleware import WS_REFUSED_CLOSE_CODE, is_api_path
from app.auth.models import Role
from app.auth.principal import Principal, get_principal

Permission = Literal["read", "read_private", "send", "repeater_admin", "admin"]
PERMISSIONS: tuple[Permission, ...] = get_args(Permission)

# What a route can declare: a permission, or one of the two rules that are not one.
Access = Permission | Literal["public", "account"]

_ALL: frozenset[Permission] = frozenset(PERMISSIONS)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    "kiosk": frozenset({"read"}),
    "viewer": frozenset({"read", "read_private"}),
    "member": frozenset({"read", "read_private", "send"}),
    "admin": _ALL,
}

# Roles with a profile, sessions and tokens of their own. ``kiosk`` has none (Q-A7).
ACCOUNT_ROLES: frozenset[Role] = frozenset({"admin", "member", "viewer"})

ACCESS_ATTRIBUTE = "__meshloom_access__"

FORBIDDEN_CODE = "auth_forbidden"


def permissions_of(principal: Principal | None) -> frozenset[Permission]:
    """The permissions a principal holds: its role's, narrowed by its token scopes."""
    if principal is None:
        return frozenset()
    granted = ROLE_PERMISSIONS.get(principal.role, frozenset())
    if principal.scopes:
        granted = granted & principal.scopes
    if "admin" in granted:
        return _ALL
    return granted


def has_permission(principal: Principal | None, permission: Permission) -> bool:
    return permission in permissions_of(principal)


def can_read_private(connection: HTTPConnection) -> bool:
    """Whether the caller may see direct messages (``read_private``)."""
    return has_permission(get_principal(connection), "read_private")


def forbidden(permission: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "code": FORBIDDEN_CODE,
            "message": "Not allowed for this account",
            "params": {"permission": permission},
        },
    )


def _refuse(connection: HTTPConnection, permission: str) -> None:
    if connection.scope.get("type") == "websocket":
        # Before ``accept()``: uvicorn answers HTTP 403, like the auth gate.
        raise WebSocketException(code=WS_REFUSED_CLOSE_CODE, reason=FORBIDDEN_CODE)
    if get_principal(connection) is None:
        # The auth gate already refuses anonymous callers outside PUBLIC_ROUTES;
        # this only runs when a public route declares a permission by mistake.
        raise HTTPException(
            status_code=401,
            detail={"code": "auth_required", "message": "Authentication required"},
        )
    raise forbidden(permission)


AccessDependency = Callable[[HTTPConnection], Coroutine[Any, Any, None]]


def _declared(access: Access, check: Callable[[Principal | None], bool]) -> AccessDependency:
    async def dependency(connection: HTTPConnection) -> None:
        if not check(get_principal(connection)):
            _refuse(connection, access)

    dependency.__name__ = f"require_{access}"
    setattr(dependency, ACCESS_ATTRIBUTE, access)
    return dependency


@functools.cache
def require(permission: Permission) -> AccessDependency:
    """The dependency a route declares to need ``permission``."""
    if permission not in PERMISSIONS:
        raise ValueError(f"Unknown permission: {permission!r}")
    return _declared(permission, lambda principal: has_permission(principal, permission))


@functools.cache
def public() -> AccessDependency:
    """The marker of a route reachable without a principal (``PUBLIC_ROUTES``)."""
    return _declared("public", lambda _principal: True)


@functools.cache
def own_account() -> AccessDependency:
    """The caller's own profile, sessions and tokens: every role but ``kiosk``.

    Never through a scoped token: a scope only narrows (cadrage §4.2), and none
    of the five grants the profile, the password or the sessions.
    """
    return _declared(
        "account",
        lambda principal: (
            principal is not None and principal.role in ACCOUNT_ROLES and not principal.scopes
        ),
    )


def declared_access(route: Any) -> list[Access]:
    """The access rules a route declares in its ``dependencies``."""
    out: list[Access] = []
    for depends in getattr(route, "dependencies", None) or ():
        access = getattr(getattr(depends, "dependency", None), ACCESS_ATTRIBUTE, None)
        if access is not None:
            out.append(access)
    return out


async def enforce_declaration(connection: HTTPConnection) -> None:
    """App-wide fallback: an ``/api`` route that declares nothing is admin-only."""
    if not is_api_path(connection.scope.get("path", "")):
        return
    if declared_access(connection.scope.get("route")):
        return
    if not has_permission(get_principal(connection), "admin"):
        _refuse(connection, "admin")


# Every WebSocket event type, with the permission a client needs to receive it.
# A type missing here is delivered to admins only (``ws_event_permission``);
# ``tests/test_auth_route_coverage.py`` fails on an unclassified type.
# A ``message`` of type ``PRIV``, and a ``raw_packet`` decrypted from one,
# also need ``read_private`` (``app/websocket.py``).
WS_EVENT_PERMISSIONS: Mapping[str, Permission] = {
    "health": "read",
    "message": "read",
    "contact": "read",
    "contact_resolved": "read",
    "channel": "read",
    "contact_deleted": "read",
    "channel_deleted": "read",
    "raw_packet": "read",
    "community_packet": "read",
    "community_live": "read",
    "message_acked": "read",
    "message_deleted": "read",
    "radio_wifi": "admin",
    # Same audience as the `plugin_statuses` of `health` for now; the plugin
    # permissions (Store T34) may narrow both.
    "plugin_status": "read",
    "error": "read",
    "success": "read",
}


# A direct message travels as a TEXT_MESSAGE packet, a channel message as
# GROUP_TEXT (``app/decoder.py``, ``PayloadType``).
DIRECT_MESSAGE_PAYLOAD_TYPE = "TEXT_MESSAGE"


def packet_decryption_is_private(payload_type: str | None, channel_key: str | None) -> bool:
    """Whether a decrypted raw packet reveals a DM (``read_private``).

    Decided by the packet type, not by ``contact_key``: a channel message keeps
    its resolved sender there. A decryption that names no channel is private
    too (fail closed). Control packets carry no decryption and stay readable.
    """
    return payload_type == DIRECT_MESSAGE_PAYLOAD_TYPE or channel_key is None


def ws_event_permission(event_type: str) -> Permission:
    return WS_EVENT_PERMISSIONS.get(event_type, "admin")
