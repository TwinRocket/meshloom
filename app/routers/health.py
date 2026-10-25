import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.permissions import Permission, has_permission, public
from app.auth.principal import get_principal
from app.config import settings
from app.models import RadioHealthState, RadioIdentityInfo, RadioProxyStatus
from app.notifications.dispatcher import notification_statuses, notifications_capped
from app.plugins import lifecycle as plugin_lifecycle
from app.repository import RawPacketRepository
from app.services.radio_lifecycle import expected_reboot_reason
from app.services.radio_runtime import radio_runtime as radio_manager
from app.services.radio_stats import get_latest_radio_stats
from app.version_info import get_app_build_info

router = APIRouter(tags=["health"])

# Below this permission `health` (REST and WebSocket) carries no error text:
# the fanout, notification, radio proxy and plugin `last_error` (raw exception
# text, hosts, ports) and the plugins' `send_guard` are emptied, the statuses
# stay (MSHL-64: fanout and plugin status are admin-only).
HEALTH_DETAIL_PERMISSION: Permission = "admin"


def _without_last_error(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_copy(update={"last_error": None})
    if isinstance(value, dict):
        return {**value, "last_error": None}
    return value


def _blank_last_error(entries: Any) -> Any:
    if not isinstance(entries, dict):
        return entries
    return {key: _without_last_error(value) for key, value in entries.items()}


def without_admin_details(health: dict[str, Any]) -> dict[str, Any]:
    """`health` as a principal without `HEALTH_DETAIL_PERMISSION` gets it."""
    out = dict(plugin_lifecycle.without_plugin_errors(health))
    for key in ("fanout_statuses", "notification_statuses"):
        if key in out:
            out[key] = _blank_last_error(out[key])
    if out.get("radio_proxy") is not None:
        out["radio_proxy"] = _without_last_error(out["radio_proxy"])
    return out


class RadioDeviceInfoResponse(BaseModel):
    model: str | None = None
    firmware_build: str | None = None
    firmware_version: str | None = None
    max_contacts: int | None = None
    max_channels: int | None = None


class AppInfoResponse(BaseModel):
    version: str
    commit_hash: str | None = None


class FanoutStatusResponse(BaseModel):
    name: str
    type: str
    status: str
    last_error: str | None = None


class PluginStatusResponse(BaseModel):
    """One plugin instance as the supervisor sees it (docs/dev/plugins.md, "Statuses")."""

    instance_id: str
    plugin_id: str | None = None
    label: str | None = None
    status: str | None = None
    last_error: str | None = None
    dropped_events: int = 0
    restarts: int = 0
    # What the send guards refused since start (`total`, `by_scope`, `last`; `{}` when
    # nothing); blanked below `HEALTH_DETAIL_PERMISSION`.
    send_guard: dict[str, Any] = Field(default_factory=dict)


class NotificationStatusResponse(BaseModel):
    """One notification channel, keyed by its id (``app/notifications/dispatcher.py``).

    ``last_error`` is a code (for Apprise with the URL's line and redacted host),
    scrubbed of the channel's secrets; never an exception text. Emptied below
    ``HEALTH_DETAIL_PERMISSION``.
    """

    status: Literal["connected", "error", "paused", "disconnected"]
    last_error: str | None = None
    last_ok_at: int | None = None


class RadioStatsSnapshot(BaseModel):
    """Latest cached stats from the local radio's periodic 60s poll."""

    timestamp: int | None = None
    # Core stats
    battery_mv: int | None = None
    uptime_secs: int | None = None
    queue_len: int | None = None
    errors: int | None = None
    # Radio stats
    noise_floor: int | None = None
    last_rssi: int | None = None
    last_snr: float | None = None
    tx_air_secs: int | None = None
    rx_air_secs: int | None = None
    # Packet stats
    packets_recv: int | None = None
    packets_sent: int | None = None
    flood_tx: int | None = None
    direct_tx: int | None = None
    flood_rx: int | None = None
    direct_rx: int | None = None
    recv_errors: int | None = None


class HealthResponse(BaseModel):
    status: str
    radio_connected: bool
    radio_initializing: bool = False
    radio_state: RadioHealthState = "disconnected"
    # Why Meshloom rebooted the radio, while radio_state is "rebooting".
    reboot_reason: Literal["clock_sync", "wifi_config"] | None = None
    connection_info: str | None
    transport_configured: bool = False
    identity: RadioIdentityInfo | None = None
    app_info: AppInfoResponse | None = None
    radio_device_info: RadioDeviceInfoResponse | None = None
    radio_stats: RadioStatsSnapshot | None = None
    database_size_mb: float
    oldest_undecrypted_timestamp: int | None
    fanout_statuses: dict[str, FanoutStatusResponse] = Field(default_factory=dict)
    notification_statuses: dict[str, NotificationStatusResponse] = Field(default_factory=dict)
    # The global notification cap (rules ``limits.global_per_hour``) is reached.
    notifications_capped: bool = False
    # Deprecated (the legacy bots): mirrors the global plugin pause as "paused".
    bots_disabled: bool = False
    bots_disabled_source: Literal["env", "until_restart", "paused"] | None = None
    # The plugin host (app/plugins/lifecycle.py): None when the system is off.
    plugins_isolation: Literal["none", "setuid", "bwrap"] | None = None
    # Off or held: the host lock, the platform and the isolation take precedence
    # over the global pause ("paused", app/plugins/pause.py).
    plugins_disabled: bool = False
    plugins_disabled_source: Literal["env", "platform", "isolation", "paused"] | None = None
    # When the global pause started (unix seconds); None when not paused.
    plugins_paused_since: int | None = None
    plugin_statuses: dict[str, PluginStatusResponse] = Field(default_factory=dict)
    radio_proxy: RadioProxyStatus | None = None


def _radio_proxy_status() -> RadioProxyStatus | None:
    try:
        from app.radio_proxy.manager import radio_proxy_manager

        return RadioProxyStatus(**radio_proxy_manager.status_dict())
    except Exception:
        return None


def _clean_optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _read_optional_bool_setting(name: str) -> bool:
    value = getattr(settings, name, False)
    return value if isinstance(value, bool) else False


async def build_health_data(radio_connected: bool, connection_info: str | None) -> dict:
    """Build the health status payload used by REST endpoint and WebSocket broadcasts."""
    app_build_info = get_app_build_info()
    db_size_mb = 0.0
    try:
        db_size_bytes = os.path.getsize(settings.database_path)
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)
    except OSError:
        pass

    oldest_ts = None
    try:
        oldest_ts = await RawPacketRepository.get_oldest_undecrypted()
    except RuntimeError:
        pass  # Database not connected

    # Fanout module statuses
    fanout_statuses: dict[str, Any] = {}
    bots_disabled_source = "env" if _read_optional_bool_setting("disable_bots") else None
    try:
        from app.fanout.manager import fanout_manager

        fanout_statuses = fanout_manager.get_statuses()
        manager_bots_disabled_source = fanout_manager.get_bots_disabled_source()
        if manager_bots_disabled_source is not None:
            bots_disabled_source = manager_bots_disabled_source
    except Exception:
        pass

    setup_in_progress = getattr(radio_manager, "is_setup_in_progress", False)
    setup_complete = getattr(radio_manager, "is_setup_complete", radio_connected)
    if not radio_connected:
        setup_complete = False

    connection_desired = getattr(radio_manager, "connection_desired", True)
    is_reconnecting = getattr(radio_manager, "is_reconnecting", False)

    radio_initializing = bool(radio_connected and (setup_in_progress or not setup_complete))
    reboot_reason = None
    if not connection_desired:
        radio_state = "paused"
    elif (reason := expected_reboot_reason(radio_manager)) is not None:
        # Rebooting on Meshloom's request (clock fix, Wi-Fi change) until setup
        # completes again; the frontend picks its copy from reboot_reason.
        radio_state = "rebooting"
        reboot_reason = reason
    elif radio_initializing:
        radio_state = "initializing"
    elif radio_connected:
        radio_state = "connected"
    elif is_reconnecting:
        radio_state = "connecting"
    else:
        radio_state = "disconnected"

    transport_configured = False
    identity = None
    try:
        from app.services.radio_identity import identity_health_info
        from app.services.radio_transport import get_transport

        snapshot = await get_transport()
        transport_configured = snapshot.configured
        if snapshot.identity_state in ("identity_mismatch", "identity_unbound_legacy"):
            radio_state = snapshot.identity_state
            reboot_reason = None
            identity = await identity_health_info()
    except Exception:
        pass

    radio_device_info = None
    device_info_loaded = getattr(radio_manager, "device_info_loaded", False)
    if radio_connected and device_info_loaded:
        radio_device_info = {
            "model": _clean_optional_str(getattr(radio_manager, "device_model", None)),
            "firmware_build": _clean_optional_str(getattr(radio_manager, "firmware_build", None)),
            "firmware_version": _clean_optional_str(
                getattr(radio_manager, "firmware_version", None)
            ),
            "max_contacts": getattr(radio_manager, "max_contacts", None),
            "max_channels": getattr(radio_manager, "max_channels", None),
        }

    # Local radio stats from the 60s background sampler
    raw_stats = get_latest_radio_stats()
    radio_stats = None
    if raw_stats:
        packets = raw_stats.get("packets") or {}
        radio_stats = {
            "timestamp": raw_stats.get("timestamp"),
            "battery_mv": raw_stats.get("battery_mv"),
            "uptime_secs": raw_stats.get("uptime_secs"),
            "queue_len": raw_stats.get("queue_len"),
            "errors": raw_stats.get("errors"),
            "noise_floor": raw_stats.get("noise_floor"),
            "last_rssi": raw_stats.get("last_rssi"),
            "last_snr": raw_stats.get("last_snr"),
            "tx_air_secs": raw_stats.get("tx_air_secs"),
            "rx_air_secs": raw_stats.get("rx_air_secs"),
            "packets_recv": packets.get("recv"),
            "packets_sent": packets.get("sent"),
            "flood_tx": packets.get("flood_tx"),
            "direct_tx": packets.get("direct_tx"),
            "flood_rx": packets.get("flood_rx"),
            "direct_rx": packets.get("direct_rx"),
            "recv_errors": packets.get("recv_errors"),
        }

    return {
        "status": "ok" if radio_connected and not radio_initializing else "degraded",
        "radio_connected": radio_connected,
        "radio_initializing": radio_initializing,
        "radio_state": radio_state,
        "reboot_reason": reboot_reason,
        "connection_info": connection_info,
        "app_info": {
            "version": app_build_info.version,
            "commit_hash": app_build_info.commit_hash,
        },
        "radio_device_info": radio_device_info,
        "radio_stats": radio_stats,
        "database_size_mb": db_size_mb,
        "oldest_undecrypted_timestamp": oldest_ts,
        "fanout_statuses": fanout_statuses,
        "notification_statuses": await notification_statuses(),
        "notifications_capped": notifications_capped(),
        "bots_disabled": bots_disabled_source is not None,
        "bots_disabled_source": bots_disabled_source,
        "transport_configured": transport_configured,
        "identity": identity,
        "radio_proxy": _radio_proxy_status(),
        **plugin_lifecycle.health_fields(),
    }


class MinimalHealthResponse(BaseModel):
    """What an anonymous caller gets: the process answers, nothing else."""

    ok: bool = True


@router.get(
    "/health",
    response_model=HealthResponse | MinimalHealthResponse,
    dependencies=[Depends(public())],
)
async def healthcheck(request: Request) -> HealthResponse | MinimalHealthResponse:
    """Check if the API is running and if the radio is connected.

    Public, for probes (Uptime Kuma, Home Assistant REST sensors, Docker, the
    kiosk launcher): an anonymous caller gets only ``{"ok": true}``, the details
    need a signed-in principal (``app/auth/middleware.py``).
    """
    if get_principal(request) is None:
        return MinimalHealthResponse()
    data = await build_health_data(radio_manager.is_connected, radio_manager.connection_info)
    if not has_permission(get_principal(request), HEALTH_DETAIL_PERMISSION):
        data = without_admin_details(data)
    return HealthResponse(**data)
