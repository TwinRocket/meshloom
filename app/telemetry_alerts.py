"""Telemetry alert evaluation after each poll (auto collect or manual fetch).

Threshold debounce is hysteresis, not a time window. RadioOperationBusyError
is not a miss. Untracked nodes never alert.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from typing import Any, Literal

from app.models import TelemetryAlertRuleOverride, TelemetryAlertRules
from app.repository.contact_telemetry import ContactTelemetryRepository
from app.repository.settings import AppSettingsRepository
from app.repository.telemetry_alert_state import TelemetryAlertStateRepository

logger = logging.getLogger(__name__)

RULE_BATTERY = "battery"
RULE_NOISE = "noise"
RULE_SILENCE = "silence"
RULE_GPS_LOST = "gps_lost"

DEFAULT_BATTERY_VOLTS_MIN = 3.5
DEFAULT_NOISE_FLOOR_MAX_DBM = -90.0
DEFAULT_MISSES_BEFORE_ALERT = 2
BATTERY_REARM_MARGIN_V = 0.2
NOISE_REARM_MARGIN_DB = 3.0
MISSES_MIN = 1
MISSES_MAX = 4

PollOutcome = Literal["success", "miss", "busy"]


def battery_volts_from_status(status: Mapping[str, Any] | None) -> float | None:
    """Repeater/room ``bat`` is millivolts. Missing or non-numeric → None."""
    if not isinstance(status, Mapping) or "bat" not in status:
        return None
    try:
        return float(status["bat"]) / 1000.0
    except (TypeError, ValueError):
        return None


def noise_floor_from_status(status: Mapping[str, Any] | None) -> float | None:
    """Missing or non-numeric ``noise_floor`` → None (do not treat as 0 dBm)."""
    if not isinstance(status, Mapping) or "noise_floor" not in status:
        return None
    try:
        return float(status["noise_floor"])
    except (TypeError, ValueError):
        return None


def is_usable_status(status: object) -> bool:
    """Empty dict / None / non-dict is a miss, not a zero snapshot."""
    return isinstance(status, dict) and len(status) > 0


def status_snapshot_from_radio(status: Mapping[str, Any]) -> dict[str, Any]:
    """Map a radio status dict to the stored snapshot field names."""
    return {
        "battery_volts": battery_volts_from_status(status),
        "tx_queue_len": status.get("tx_queue_len", 0),
        "noise_floor_dbm": noise_floor_from_status(status),
        "last_rssi_dbm": status.get("last_rssi", 0),
        "last_snr_db": status.get("last_snr", 0.0),
        "packets_received": status.get("nb_recv", 0),
        "packets_sent": status.get("nb_sent", 0),
        "airtime_seconds": status.get("airtime", 0),
        "rx_airtime_seconds": status.get("rx_airtime", 0),
        "uptime_seconds": status.get("uptime", 0),
        "sent_flood": status.get("sent_flood", 0),
        "sent_direct": status.get("sent_direct", 0),
        "recv_flood": status.get("recv_flood", 0),
        "recv_direct": status.get("recv_direct", 0),
        "flood_dups": status.get("flood_dups", 0),
        "direct_dups": status.get("direct_dups", 0),
        "full_events": status.get("full_evts", 0),
        "recv_errors": status.get("recv_errors"),
    }


def repeater_status_response_kwargs(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """HTTP response fields: missing noise stays 0 so the int field stays compatible."""
    noise = snapshot.get("noise_floor_dbm")
    return {
        **dict(snapshot),
        "noise_floor_dbm": int(noise) if noise is not None else 0,
    }


def _clamp_misses(raw: object) -> int:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_MISSES_BEFORE_ALERT
    return max(MISSES_MIN, min(MISSES_MAX, value))


def _as_float(raw: object, default: float) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def coerce_telemetry_alert_rules(raw: object) -> TelemetryAlertRules:
    """Keep known keys; clamp misses to 1–4; drop malformed overrides."""
    parsed: dict[str, Any] = {}
    if raw:
        try:
            loaded = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(loaded, dict):
                parsed = loaded
        except (TypeError, ValueError):
            parsed = {}

    overrides: dict[str, TelemetryAlertRuleOverride] = {}
    raw_overrides = parsed.get("overrides")
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            override = TelemetryAlertRuleOverride()
            if "battery_volts_min" in value:
                override.battery_volts_min = _as_float(
                    value["battery_volts_min"], DEFAULT_BATTERY_VOLTS_MIN
                )
            if "noise_floor_max_dbm" in value:
                override.noise_floor_max_dbm = _as_float(
                    value["noise_floor_max_dbm"], DEFAULT_NOISE_FLOOR_MAX_DBM
                )
            if "misses_before_alert" in value:
                override.misses_before_alert = _clamp_misses(value["misses_before_alert"])
            overrides[key.lower()] = override

    return TelemetryAlertRules(
        battery_volts_min=_as_float(parsed.get("battery_volts_min"), DEFAULT_BATTERY_VOLTS_MIN),
        noise_floor_max_dbm=_as_float(
            parsed.get("noise_floor_max_dbm"), DEFAULT_NOISE_FLOOR_MAX_DBM
        ),
        misses_before_alert=_clamp_misses(
            parsed.get("misses_before_alert", DEFAULT_MISSES_BEFORE_ALERT)
        ),
        overrides=overrides,
    )


def merge_telemetry_alert_rules(
    stored: TelemetryAlertRules, incoming: TelemetryAlertRules
) -> TelemetryAlertRules:
    """Merge a PATCH fragment into stored rules.

    Provided global fields replace stored values. ``overrides`` omitted or
    null keeps the stored map — Radio-App only sends the three globals, and
    Pydantic would otherwise default ``overrides`` to ``{}`` and wipe it.
    An object (including ``{}``) replaces the stored map.
    """
    payload = incoming.model_dump(exclude_unset=True)
    if payload.get("overrides") is None:
        payload.pop("overrides", None)
    merged = stored.model_dump()
    merged.update(payload)
    return coerce_telemetry_alert_rules(merged)


def resolve_node_rules(rules: TelemetryAlertRules, public_key: str) -> TelemetryAlertRules:
    """Globals with optional per-key overrides (override fields only)."""
    override = (rules.overrides or {}).get(public_key.lower())
    if override is None:
        return TelemetryAlertRules(
            battery_volts_min=rules.battery_volts_min,
            noise_floor_max_dbm=rules.noise_floor_max_dbm,
            misses_before_alert=rules.misses_before_alert,
        )
    return TelemetryAlertRules(
        battery_volts_min=(
            override.battery_volts_min
            if override.battery_volts_min is not None
            else rules.battery_volts_min
        ),
        noise_floor_max_dbm=(
            override.noise_floor_max_dbm
            if override.noise_floor_max_dbm is not None
            else rules.noise_floor_max_dbm
        ),
        misses_before_alert=(
            override.misses_before_alert
            if override.misses_before_alert is not None
            else rules.misses_before_alert
        ),
    )


def lpp_has_fix(sensors: object) -> bool:
    """True when an LPP sample carries a usable GPS fix (not 0,0 / missing)."""
    if not isinstance(sensors, list):
        return False
    for sensor in sensors:
        if not isinstance(sensor, Mapping):
            continue
        type_name = str(sensor.get("type_name") or sensor.get("type") or "").lower()
        value = sensor.get("value")
        looks_geo = type_name in {"gps", "gps_location", "location"} or (
            isinstance(value, dict) and ("latitude" in value or "lat" in value)
        )
        if not looks_geo or not isinstance(value, dict):
            continue
        lat = value.get("latitude", value.get("lat"))
        lon = value.get("longitude", value.get("lon"))
        try:
            lat_f = float(lat)  # type: ignore[arg-type]
            lon_f = float(lon)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if lat_f == 0.0 and lon_f == 0.0:
            continue
        return True
    return False


def _snapshot_has_lpp_fix(snapshot: Mapping[str, Any] | None) -> bool:
    if snapshot is None:
        return False
    return lpp_has_fix(snapshot.get("lpp_sensors"))


async def _history_had_lpp_fix(public_key: str) -> bool:
    try:
        rows = await ContactTelemetryRepository.get_history(public_key, 0)
    except Exception:
        logger.debug("Telemetry alerts: failed to read LPP history for GPS", exc_info=True)
        return False
    return any(lpp_has_fix((row.get("data") or {}).get("lpp_sensors")) for row in rows)


async def note_telemetry_poll(
    *,
    public_key: str,
    name: str = "",
    outcome: PollOutcome,
    snapshot: Mapping[str, Any] | None = None,
    allow_gps_lost: bool = False,
) -> None:
    """Evaluate alert rules after one poll. Never raises to callers."""
    try:
        await _note_telemetry_poll(
            public_key=public_key,
            name=name,
            outcome=outcome,
            snapshot=snapshot,
            allow_gps_lost=allow_gps_lost,
        )
    except Exception:
        logger.debug("Telemetry alert evaluation failed", exc_info=True)


async def _note_telemetry_poll(
    *,
    public_key: str,
    name: str,
    outcome: PollOutcome,
    snapshot: Mapping[str, Any] | None,
    allow_gps_lost: bool,
) -> None:
    if outcome == "busy":
        return
    if not public_key:
        return

    settings = await AppSettingsRepository.get()
    tracked = public_key in settings.tracked_telemetry_repeaters or (
        public_key in settings.tracked_telemetry_contacts
    )
    if not tracked:
        return

    node_rules = resolve_node_rules(settings.telemetry_alert_rules, public_key)
    push_on = True
    try:
        defaults = await AppSettingsRepository.get_push_defaults()
        push_on = bool(defaults.get("telemetry_alert", True))
    except Exception:
        logger.debug("Telemetry alerts: failed to load push defaults", exc_info=True)

    now = int(time.time())
    label = (name or "").strip() or public_key[:12]

    if outcome == "miss":
        await _apply_silence(
            public_key=public_key,
            name=label,
            rules=node_rules,
            now=now,
            push_on=push_on,
            increment=True,
        )
        return

    await _apply_silence(
        public_key=public_key,
        name=label,
        rules=node_rules,
        now=now,
        push_on=push_on,
        increment=False,
    )
    await _apply_battery(
        public_key=public_key,
        name=label,
        snapshot=snapshot,
        rules=node_rules,
        now=now,
        push_on=push_on,
    )
    await _apply_noise(
        public_key=public_key,
        name=label,
        snapshot=snapshot,
        rules=node_rules,
        now=now,
        push_on=push_on,
    )
    if allow_gps_lost and public_key in settings.tracked_telemetry_contacts:
        await _apply_gps_lost(
            public_key=public_key,
            name=label,
            snapshot=snapshot,
            now=now,
            push_on=push_on,
        )


async def _apply_silence(
    *,
    public_key: str,
    name: str,
    rules: TelemetryAlertRules,
    now: int,
    push_on: bool,
    increment: bool,
) -> None:
    state = await TelemetryAlertStateRepository.get(public_key, RULE_SILENCE)
    misses = int(state["consecutive_misses"]) if state else 0
    latched = bool(state and state.get("last_fired_at"))
    if increment:
        misses += 1
        should_fire = misses >= rules.misses_before_alert and not latched
        fired_at = state["last_fired_at"] if latched else (now if should_fire else None)
        await TelemetryAlertStateRepository.upsert(
            public_key,
            RULE_SILENCE,
            consecutive_misses=misses,
            last_fired_at=fired_at,
            last_value=float(misses),
        )
        if should_fire:
            await _dispatch(
                public_key=public_key,
                name=name,
                rule_id=RULE_SILENCE,
                value=float(misses),
                threshold=float(rules.misses_before_alert),
                push_on=push_on,
            )
        return

    await TelemetryAlertStateRepository.upsert(
        public_key,
        RULE_SILENCE,
        consecutive_misses=0,
        last_fired_at=None,
        last_value=0.0,
    )


async def _apply_battery(
    *,
    public_key: str,
    name: str,
    snapshot: Mapping[str, Any] | None,
    rules: TelemetryAlertRules,
    now: int,
    push_on: bool,
) -> None:
    if snapshot is None or "battery_volts" not in snapshot:
        return
    raw = snapshot.get("battery_volts")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return
    if value <= 0:
        return

    state = await TelemetryAlertStateRepository.get(public_key, RULE_BATTERY)
    latched = bool(state and state.get("last_fired_at"))
    threshold = rules.battery_volts_min
    if latched:
        if value >= threshold + BATTERY_REARM_MARGIN_V:
            await TelemetryAlertStateRepository.upsert(
                public_key,
                RULE_BATTERY,
                last_fired_at=None,
                last_value=value,
            )
        else:
            await TelemetryAlertStateRepository.upsert(
                public_key,
                RULE_BATTERY,
                last_fired_at=state["last_fired_at"] if state else now,
                last_value=value,
            )
        return

    if value < threshold:
        await TelemetryAlertStateRepository.upsert(
            public_key,
            RULE_BATTERY,
            last_fired_at=now,
            last_value=value,
        )
        await _dispatch(
            public_key=public_key,
            name=name,
            rule_id=RULE_BATTERY,
            value=value,
            threshold=threshold,
            push_on=push_on,
        )
        return

    await TelemetryAlertStateRepository.upsert(
        public_key,
        RULE_BATTERY,
        last_fired_at=None,
        last_value=value,
    )


async def _apply_noise(
    *,
    public_key: str,
    name: str,
    snapshot: Mapping[str, Any] | None,
    rules: TelemetryAlertRules,
    now: int,
    push_on: bool,
) -> None:
    if snapshot is None or "noise_floor_dbm" not in snapshot:
        return
    raw = snapshot.get("noise_floor_dbm")
    if raw is None:
        return
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return

    state = await TelemetryAlertStateRepository.get(public_key, RULE_NOISE)
    latched = bool(state and state.get("last_fired_at"))
    threshold = rules.noise_floor_max_dbm
    if latched:
        if value <= threshold - NOISE_REARM_MARGIN_DB:
            await TelemetryAlertStateRepository.upsert(
                public_key,
                RULE_NOISE,
                last_fired_at=None,
                last_value=value,
            )
        else:
            await TelemetryAlertStateRepository.upsert(
                public_key,
                RULE_NOISE,
                last_fired_at=state["last_fired_at"] if state else now,
                last_value=value,
            )
        return

    if value > threshold:
        await TelemetryAlertStateRepository.upsert(
            public_key,
            RULE_NOISE,
            last_fired_at=now,
            last_value=value,
        )
        await _dispatch(
            public_key=public_key,
            name=name,
            rule_id=RULE_NOISE,
            value=value,
            threshold=threshold,
            push_on=push_on,
        )
        return

    await TelemetryAlertStateRepository.upsert(
        public_key,
        RULE_NOISE,
        last_fired_at=None,
        last_value=value,
    )


async def _apply_gps_lost(
    *,
    public_key: str,
    name: str,
    snapshot: Mapping[str, Any] | None,
    now: int,
    push_on: bool,
) -> None:
    current_fix = _snapshot_has_lpp_fix(snapshot)
    state = await TelemetryAlertStateRepository.get(public_key, RULE_GPS_LOST)
    if state is not None and state.get("last_value") is not None:
        previously = float(state["last_value"]) >= 1.0
    else:
        previously = await _history_had_lpp_fix(public_key)
    latched = bool(state and state.get("last_fired_at"))

    if current_fix:
        await TelemetryAlertStateRepository.upsert(
            public_key,
            RULE_GPS_LOST,
            last_fired_at=None,
            last_value=1.0,
        )
        return

    if previously and not latched:
        await TelemetryAlertStateRepository.upsert(
            public_key,
            RULE_GPS_LOST,
            last_fired_at=now,
            last_value=0.0,
        )
        await _dispatch(
            public_key=public_key,
            name=name,
            rule_id=RULE_GPS_LOST,
            value=0.0,
            threshold=1.0,
            push_on=push_on,
        )
        return

    await TelemetryAlertStateRepository.upsert(
        public_key,
        RULE_GPS_LOST,
        last_fired_at=state["last_fired_at"] if latched else None,
        last_value=0.0,
    )


async def _dispatch(
    *,
    public_key: str,
    name: str,
    rule_id: str,
    value: float,
    threshold: float,
    push_on: bool,
) -> None:
    if not push_on:
        return
    from app.push.manager import push_manager

    await push_manager.dispatch_event(
        {
            "event": "telemetry_alert",
            "public_key": public_key,
            "name": name,
            "rule_id": rule_id,
            "value": value,
            "threshold": threshold,
        }
    )
