"""Capability API v1: what a plugin child may ask the host, checked against the
instance's effective permissions (fail closed) and quotas.

One module per capability, each registering its JSON-RPC methods in
`base.REGISTRY`; `dispatch()` is the only entry point of the supervisor
(`app/plugins/host.py`). Reference: docs/dev/plugins.md, "Capabilities".
"""

from typing import TYPE_CHECKING, Any

from app.plugins.capabilities import channels, contacts, log, messages, status, storage, timer
from app.plugins.capabilities.base import (
    REFUSAL_CODES,
    REFUSED,
    REGISTRY,
    Call,
    CapabilityError,
    handle,
    invalid_params,
)

if TYPE_CHECKING:
    from app.plugins.host import PluginInstance, _Event

__all__ = [
    "REFUSAL_CODES",
    "REFUSED",
    "REGISTRY",
    "Call",
    "CapabilityError",
    "channels",
    "contacts",
    "dispatch",
    "log",
    "messages",
    "status",
    "storage",
    "timer",
]


async def dispatch(
    instance: "PluginInstance", method: str, params: Any, event: "_Event | None"
) -> Any:
    """Answer one child request: the method's permission, then its handler."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        if method in REGISTRY:
            raise invalid_params("params must be an object")
        params = {}
    return await handle(Call(instance, method, params, event))
