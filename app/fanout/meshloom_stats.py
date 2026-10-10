"""System FanoutModule: Meshloom Stats MQTT publisher.

Invisible to GET/PATCH/DELETE /api/fanout. Started from FanoutManager, not
_MODULE_TYPES / fanout_configs. Coexists with a user LetsMesh mqtt_community row.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.fanout.base import FanoutModule
from app.fanout.community_mqtt import CommunityMqttPublisher, _generate_jwt_token
from app.fanout.mqtt_community import (
    _DEFAULT_PACKET_TOPIC_TEMPLATE,
    _IATA_RE,
    _publish_community_packet,
)
from app.services.meshloom_community import (
    DEFAULT_BROKER_HOST,
    DEFAULT_BROKER_PORT,
    DEFAULT_WEBSOCKET_PATH,
    MQTT_KEEPALIVE_SECONDS,
    SYSTEM_MESHLOOM_STATS_ID,
    get_community_effective,
)


def _state_to_settings(state: Any) -> SimpleNamespace:
    return SimpleNamespace(
        community_mqtt_enabled=state.enabled,
        community_mqtt_broker_host=state.broker_host or DEFAULT_BROKER_HOST,
        community_mqtt_broker_port=DEFAULT_BROKER_PORT,
        community_mqtt_transport="websockets",
        community_mqtt_use_tls=True,
        community_mqtt_tls_verify=True,
        community_mqtt_auth_mode="token",
        community_mqtt_username="",
        community_mqtt_password="",
        community_mqtt_iata=state.iata,
        community_mqtt_email="",
        community_mqtt_token_audience=state.mqtt_audience,
        community_mqtt_websocket_path=DEFAULT_WEBSOCKET_PATH,
    )


class MeshloomStatsPublisher(CommunityMqttPublisher):
    """LetsMesh-shaped publisher with Stats JWT iata, keepalive 30, full jitter."""

    _backoff_max = 3600
    _log_prefix = "Meshloom Stats MQTT"
    _full_jitter_backoff = True
    _not_configured_timeout: float | None = 30

    def _is_configured(self) -> bool:
        from app.keystore import get_public_key, has_private_key

        s = self._settings
        if not s or not s.community_mqtt_enabled:
            return False
        iata = (s.community_mqtt_iata or "").upper().strip()
        if not _IATA_RE.fullmatch(iata):
            return False
        return get_public_key() is not None and has_private_key()

    def _on_not_configured(self) -> None:
        # No IATA nag — LetsMesh mqtt_community already owns that prompt.
        return

    def _build_client_kwargs(self, settings: object) -> dict[str, Any]:
        kwargs = super()._build_client_kwargs(settings)
        kwargs["keepalive"] = MQTT_KEEPALIVE_SECONDS
        from app.keystore import get_private_key, get_public_key

        private_key = get_private_key()
        public_key = get_public_key()
        if private_key is not None and public_key is not None:
            iata = (getattr(settings, "community_mqtt_iata", "") or "").upper().strip()
            audience = (
                getattr(settings, "community_mqtt_token_audience", "") or ""
            ).strip() or kwargs["hostname"]
            kwargs["password"] = _generate_jwt_token(
                private_key,
                public_key,
                audience=audience,
                iata=iata,
            )
        return kwargs

    def _on_connected(self, settings: object) -> tuple[str, str]:
        host = getattr(settings, "community_mqtt_broker_host", "") or DEFAULT_BROKER_HOST
        return ("Meshloom Stats MQTT connected", f"{host}:{DEFAULT_BROKER_PORT}")

    def _on_error(self) -> tuple[str, str]:
        return (
            "Meshloom Stats MQTT connection failure",
            "Check your internet connection or try again later.",
        )


class MeshloomStatsModule(FanoutModule):
    """System publisher. Constructed by FanoutManager, never from a DB config row."""

    def __init__(
        self,
        config_id: str = SYSTEM_MESHLOOM_STATS_ID,
        config: dict | None = None,
        *,
        name: str = "Meshloom Stats",
    ) -> None:
        super().__init__(config_id, config or {}, name=name)
        self._publisher = MeshloomStatsPublisher()
        self._publisher.set_integration_name(name or config_id)

    async def start(self) -> None:
        state = await get_community_effective()
        await self._publisher.start(_state_to_settings(state))

    async def stop(self) -> None:
        await self._publisher.stop()

    def retire_status(self) -> None:
        """Clear the retained status topic on the next stop (opt-out, IATA change)."""
        self._publisher.retire_status()

    async def on_raw(self, data: dict) -> None:
        if not self._publisher.connected or self._publisher._settings is None:
            return
        state_iata = getattr(self._publisher._settings, "community_mqtt_iata", "")
        await _publish_community_packet(
            self._publisher,
            {"iata": state_iata, "topic_template": _DEFAULT_PACKET_TOPIC_TEMPLATE},
            data,
        )

    @property
    def status(self) -> str:
        if self.last_error:
            return "error"
        if self._publisher._is_configured():
            return "connected" if self._publisher.connected else "disconnected"
        return "disconnected"

    @property
    def last_error(self) -> str | None:
        if self._publisher.last_error:
            return self._publisher.last_error
        return None
