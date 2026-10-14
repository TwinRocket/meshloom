"""System FanoutModule: Meshloom Stats MQTT publisher.

Invisible to GET/PATCH/DELETE /api/fanout. Started from FanoutManager, not
_MODULE_TYPES / fanout_configs. Coexists with a user LetsMesh mqtt_community row.
"""

from __future__ import annotations

import logging
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
    DEFAULT_BROKER_PORT,
    DEFAULT_WEBSOCKET_PATH,
    MQTT_KEEPALIVE_SECONDS,
    SYSTEM_MESHLOOM_STATS_ID,
    get_community_effective,
)

logger = logging.getLogger(__name__)


def _state_to_settings(state: Any) -> SimpleNamespace:
    return SimpleNamespace(
        community_mqtt_enabled=state.enabled,
        # Always the fixed Community broker (get_community_effective).
        community_mqtt_broker_host=state.broker_host,
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

    async def _pre_connect(self, settings: object) -> bool:
        """Re-check the enrolled-level Community guard before every (re)connect.

        ``stop()`` normally ends the loop on opt-out. If a stop ever abandons
        the loop (a DISCONNECT that hangs past ``STOP_TIMEOUT_S``), this keeps
        the loop from reconnecting to the broker afterwards. Enrolled-level
        (``community_enrolled_state``): it still connects while Community waits
        for a position, to say so in its status. It answers None as soon as an
        opt-out starts (``opt_out_in_progress``): Community kicks this session
        while answering ``POST /v1/me/opt-out``, before the local teardown
        stops the publisher, and refuses the key from then on.
        """
        from app.services.meshloom_community import community_enrolled_state

        if await community_enrolled_state() is None:
            if self._settings is not None:
                self._settings.community_mqtt_enabled = False
            self.connected = False
            return False
        return await super()._pre_connect(settings)

    def _status_extra(self) -> dict[str, Any]:
        """``state: waiting_for_position`` while the radio has no publishable position."""
        from app.services.meshloom_community import (
            STATE_WAITING_FOR_POSITION,
            waiting_for_position,
        )

        if waiting_for_position():
            return {"state": STATE_WAITING_FOR_POSITION}
        return {}

    async def republish_status(self) -> None:
        """Publish the retained status again at once (a waiting state change)."""
        if self.connected and self._settings is not None:
            await self._publish_status(self._settings, refresh_stats=False)

    async def _on_status_retained(self, settings: Any, pubkey_hex: str) -> None:
        """Remember the topic, so a restart with another IATA can clear it."""
        from app.services.community_status_clear import StatusClearTarget, note_published

        broker = getattr(settings, "community_mqtt_broker_host", "")
        audience = (getattr(settings, "community_mqtt_token_audience", "") or "").strip()
        target = StatusClearTarget(
            iata=(getattr(settings, "community_mqtt_iata", "") or "").upper().strip(),
            broker_host=broker,
            audience=audience or broker,
            pubkey=pubkey_hex,
        )
        try:
            await note_published(target)
        except Exception:
            logger.warning("Could not record the Community status topic", exc_info=True)

    def _error_toast_allowed(self) -> bool:
        """No toast for the connection Community drops during an opt-out."""
        from app.services.meshloom_community import opt_out_in_progress

        return not opt_out_in_progress()

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
        host = getattr(settings, "community_mqtt_broker_host", "")
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

    @property
    def retired_status_topic(self) -> str | None:
        """Status topic the last stop cleared (empty retained payload acknowledged)."""
        return self._publisher.retired_status_topic

    async def republish_status(self) -> None:
        await self._publisher.republish_status()

    async def on_raw(self, data: dict) -> None:
        from app.services.meshloom_community import waiting_for_position

        if not self._publisher.connected or self._publisher._settings is None:
            return
        # Waiting for a position: the status says so, and no packet leaves.
        if waiting_for_position():
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
