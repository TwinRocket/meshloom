"""Who is making a request: the principal the auth middleware resolves (docs/dev/auth.md).

``AuthMiddleware`` (``app/auth/middleware.py``) stores one ``Principal`` per
HTTP request or WebSocket upgrade in ``scope["state"]["principal"]``, or None
for an anonymous caller. Routes read it with ``get_principal()``.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any, Literal

from app.auth.models import Role, User

# How the request reached the instance. ``lan`` is everything direct today;
# ``ingress`` is the Home Assistant Supervisor (``app/auth/providers/ingress.py``,
# ``request_via`` in ``app/auth/middleware.py``); ``trusted`` is a direct request
# a trusted network rule signed in (``app/auth/providers/trusted_network.py``).
# ``relay`` comes with a later Access ticket.
Via = Literal["lan", "ingress", "trusted", "relay"]

# Which credential proved the principal. ``session`` (the cookie), ``basic``,
# ``ingress`` (the Supervisor headers, which follow Home Assistant's own
# ingress cookie) and ``trusted_network`` (the client address alone) are
# ambient: a browser replays them on its own, so unsafe methods need the Origin
# rule. ``token`` (``Authorization: Bearer``) is not.
CredentialKind = Literal["session", "basic", "token", "ingress", "trusted_network"]
AMBIENT_CREDENTIALS: frozenset[str] = frozenset({"session", "basic", "ingress", "trusted_network"})

PRINCIPAL_STATE_KEY = "principal"


@dataclass(frozen=True)
class Principal:
    user: User
    via: Via
    credential: CredentialKind
    session_id: str | None = None
    token_id: str | None = None
    # Token scopes (M3). Empty means the full rights of ``role``.
    scopes: frozenset[str] = frozenset()

    @property
    def role(self) -> Role:
        return self.user.role

    @property
    def user_id(self) -> int:
        return self.user.id


def set_principal(scope: MutableMapping[str, Any], principal: Principal | None) -> None:
    scope.setdefault("state", {})[PRINCIPAL_STATE_KEY] = principal


def principal_from_scope(scope: Any) -> Principal | None:
    state = scope.get("state") if hasattr(scope, "get") else None
    if not isinstance(state, dict):
        return None
    principal = state.get(PRINCIPAL_STATE_KEY)
    return principal if isinstance(principal, Principal) else None


def get_principal(request: Any) -> Principal | None:
    """The principal of a FastAPI ``Request`` or ``WebSocket`` (None when anonymous)."""
    return principal_from_scope(request.scope)
