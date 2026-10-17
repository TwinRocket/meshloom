"""Radio proxy listen settings: the checks every writer goes through.

Shared by ``PATCH /api/radio/proxy`` and the backup restore. The managed-ports
rule (the host forwards one port, so the proxy must listen there) and the
self-target guard (never listen on the address the radio transport dials) are
enforced here; the route refuses (409), the restore drops the value and warns.
"""

from __future__ import annotations

from typing import Any

PROXY_PORT_MANAGED_MESSAGE = (
    "The proxy port is set by the host running this instance. Remap it there instead."
)
PROXY_TARGETS_TRANSPORT_MESSAGE = "Refusing to listen: radio transport already targets this proxy"


class ProxySettingsConflict(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


async def update_radio_proxy_settings(
    *,
    enabled: bool | None = None,
    bind: str | None = None,
    port: int | None = None,
    max_clients: int | None = None,
    lenient: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Store and apply proxy settings. Returns (status, warnings).

    Strict (default): a conflict raises ``ProxySettingsConflict``. Lenient (the
    restore): a managed port is dropped (the other fields still apply) and a
    self-targeting proxy is left disabled, each with a warning.
    """
    from app.config import settings as server_settings
    from app.radio_proxy.manager import radio_proxy_manager, targets_this_proxy
    from app.repository.radio_proxy import RadioProxyRepository
    from app.services.radio_transport import get_transport

    warnings: list[str] = []
    if (
        port is not None
        and server_settings.managed_ports
        and port != radio_proxy_manager.settings.port
    ):
        if not lenient:
            raise ProxySettingsConflict("proxy_port_managed", PROXY_PORT_MANAGED_MESSAGE)
        port = None
        warnings.append("proxy_port_managed")

    stored = await RadioProxyRepository.update(
        enabled=enabled, bind=bind, port=port, max_clients=max_clients
    )
    snapshot = await get_transport()
    if (
        stored.enabled
        and snapshot.transport == "tcp"
        and targets_this_proxy(
            snapshot.tcp_host, snapshot.tcp_port, bind=stored.bind, listen_port=stored.port
        )
    ):
        stored = await RadioProxyRepository.update(enabled=False)
        if not lenient:
            raise ProxySettingsConflict("proxy_targets_transport", PROXY_TARGETS_TRANSPORT_MESSAGE)
        warnings.append("proxy_targets_transport")
    status = await radio_proxy_manager.apply_settings(stored)
    return status, warnings
