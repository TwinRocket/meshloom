"""The dispatcher: JSON-RPC methods of a child mapped to capability handlers.

Every method names the permission it needs (`None` for `status.set` and `log`,
which every instance has). The check runs before the handler, so a refused call
has no side effect, uses no quota and touches no network. A refusal is a
JSON-RPC error whose `data.code` is stable (`REFUSAL_CODES`), with `data.params`
when a value helps the plugin (`limit`, `retry_after`...): `-32602` for
`invalid_params`, `REFUSED` (-32010) for the others. A method nobody registered
is `-32601`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.plugins.host import HostMethodError
from meshloom_plugin import rpc

if TYPE_CHECKING:
    from app.plugins.host import PluginInstance, _Event

# JSON-RPC error code of every typed refusal but `invalid_params`.
REFUSED = -32010

REFUSAL_CODES = (
    "permission_denied",
    "quota_exceeded",
    "target_not_allowed",
    "target_unknown",
    "region_unknown",
    "message_too_long",
    "send_failed",
    "quota_unavailable",
    "host_not_allowed",
    "https_required",
    "fetch_failed",
    "invalid_params",
)


class CapabilityError(HostMethodError):
    """A typed refusal, answered to the child as `{code, message, data: {code, params?}}`."""

    def __init__(self, code: str, message: str, params: dict[str, Any] | None = None):
        data: dict[str, Any] = {"code": code}
        if params:
            data["params"] = params
        super().__init__(rpc.INVALID_PARAMS if code == "invalid_params" else REFUSED, message, data)
        self.refusal = code
        self.params = params or {}


def invalid_params(message: str, **params: Any) -> CapabilityError:
    return CapabilityError("invalid_params", message, params or None)


@dataclass
class Call:
    """One request of a child: the instance, the method, its params, and the event
    being handled when the request names it (`event_id`) or is the only one in flight."""

    instance: PluginInstance
    method: str
    params: dict[str, Any]
    event: _Event | None = None


Handler = Callable[[Call], Awaitable[Any]]


@dataclass(frozen=True)
class Capability:
    permission: str | None
    handler: Handler


REGISTRY: dict[str, Capability] = {}


def capability(method: str, permission: str | None) -> Callable[[Handler], Handler]:
    def register(handler: Handler) -> Handler:
        REGISTRY[method] = Capability(permission, handler)
        return handler

    return register


async def handle(call: Call) -> Any:
    entry = REGISTRY.get(call.method)
    if entry is None:
        raise HostMethodError(rpc.METHOD_NOT_FOUND, f"{call.method} is not available")
    if entry.permission is not None and entry.permission not in call.instance.permissions:
        raise CapabilityError(
            "permission_denied",
            f"{call.method} needs the {entry.permission} permission",
            {"permission": entry.permission},
        )
    return await entry.handler(call)
