"""Telemetry alert evaluation after each poll (auto collect or manual fetch).

Threshold debounce is hysteresis, not a time window. RadioOperationBusyError
is not a miss. Untracked nodes never alert.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeGuard

from app.models import (
    NotificationDestinations,
    NotificationDestinationsUpdate,
    TelemetryAlertChannels,
    TelemetryAlertRuleOverride,
    TelemetryAlertRules,
    TelemetryRuleSpec,
)
from app.repository.contact_telemetry import ContactTelemetryRepository
from app.repository.repeater_telemetry import RepeaterTelemetryRepository
from app.repository.settings import AppSettingsRepository
from app.repository.telemetry_alert_state import TelemetryAlertStateRepository

logger = logging.getLogger(__name__)

RULE_BATTERY = "battery"
RULE_NOISE = "noise"
RULE_RSSI = "rssi"
RULE_SNR = "snr"
RULE_TX_QUEUE = "tx_queue"
RULE_SILENCE = "silence"
RULE_GPS_LOST = "gps_lost"

DEFAULT_BATTERY_VOLTS_MIN = 3.5
DEFAULT_NOISE_FLOOR_MAX_DBM = -90.0
DEFAULT_MISSES_BEFORE_ALERT = 2
REDACTED_SECRET = "********"
BATTERY_REARM_MARGIN_V = 0.2
NOISE_REARM_MARGIN_DB = 3.0
MISSES_MIN = 1
MISSES_MAX = 4

LPP_SCALAR_TYPES = frozenset(
    {
        "temperature",
        "humidity",
        "barometer",
        "voltage",
        "current",
        "luminosity",
        "altitude",
        "power",
        "distance",
        "energy",
        "direction",
        "concentration",
    }
)

GAUGE_SNAPSHOT_FIELDS = {
    RULE_BATTERY: "battery_volts",
    RULE_NOISE: "noise_floor_dbm",
    RULE_RSSI: "last_rssi_dbm",
    RULE_SNR: "last_snr_db",
    RULE_TX_QUEUE: "tx_queue_len",
}

DEFAULT_RULE_DICTS: dict[str, dict[str, Any]] = {
    RULE_BATTERY: {
        "enabled": True,
        "op": "lt",
        "threshold": DEFAULT_BATTERY_VOLTS_MIN,
        "hysteresis": BATTERY_REARM_MARGIN_V,
    },
    RULE_NOISE: {
        "enabled": True,
        "op": "gt",
        "threshold": DEFAULT_NOISE_FLOOR_MAX_DBM,
        "hysteresis": NOISE_REARM_MARGIN_DB,
    },
    RULE_RSSI: {"enabled": False, "op": "lt", "threshold": -120, "hysteresis": 5},
    RULE_SNR: {"enabled": False, "op": "lt", "threshold": 0, "hysteresis": 2},
    RULE_TX_QUEUE: {"enabled": False, "op": "gt", "threshold": 10, "hysteresis": 2},
    RULE_SILENCE: {"enabled": True, "threshold": DEFAULT_MISSES_BEFORE_ALERT},
    RULE_GPS_LOST: {"enabled": True},
    "lpp:temperature": {"enabled": False, "op": "gt", "threshold": 50, "hysteresis": 1},
}

DEFAULT_CHANNELS = {"push": True, "email": False, "webhook": False}

DEFAULT_NOTIFICATION_DESTINATIONS: dict[str, Any] = {
    "email": {
        "host": "",
        "port": 587,
        "mode": "starttls",
        "user": "",
        "password": "",
        "from": "",
        "to": "",
    },
    "webhook": {"url": "", "hmac_secret": ""},
}

_COMPAT_RULE_FIELDS = {"battery_volts_min", "noise_floor_max_dbm", "misses_before_alert"}

PollOutcome = Literal["success", "miss", "busy"]


@dataclass(frozen=True)
class ResolvedNodeRules:
    alerting: bool
    rules: dict[str, TelemetryRuleSpec]
    channels: TelemetryAlertChannels


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


def _optional_number(status: Mapping[str, Any], key: str) -> float | None:
    """Missing key → None. Present 0 is a real reading."""
    if key not in status:
        return None
    raw = status[key]
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_usable_status(status: object) -> TypeGuard[dict[str, Any]]:
    """Empty dict / None / non-dict is a miss, not a zero snapshot."""
    return isinstance(status, dict) and len(status) > 0


def status_snapshot_from_radio(status: Mapping[str, Any]) -> dict[str, Any]:
    """Map a radio status dict to the stored snapshot field names."""
    return {
        "battery_volts": battery_volts_from_status(status),
        "tx_queue_len": _optional_number(status, "tx_queue_len"),
        "noise_floor_dbm": noise_floor_from_status(status),
        "last_rssi_dbm": _optional_number(status, "last_rssi"),
        "last_snr_db": _optional_number(status, "last_snr"),
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
    """HTTP response fields: missing numeric radios stay 0 so ints stay compatible."""

    def _as_int(key: str, default: int = 0) -> int:
        value = snapshot.get(key)
        return int(value) if value is not None else default

    def _as_float(key: str, default: float = 0.0) -> float:
        value = snapshot.get(key)
        return float(value) if value is not None else default

    noise = snapshot.get("noise_floor_dbm")
    return {
        **dict(snapshot),
        "tx_queue_len": _as_int("tx_queue_len"),
        "noise_floor_dbm": int(noise) if noise is not None else 0,
        "last_rssi_dbm": _as_int("last_rssi_dbm"),
        "last_snr_db": _as_float("last_snr_db"),
    }


def lpp_sensors_from_radio(raw: object) -> list[dict[str, Any]]:
    """Normalize a CayenneLPP radio payload, including GPS dicts."""
    if not isinstance(raw, list):
        return []
    sensors: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        sensors.append(
            {
                "channel": entry.get("channel", 0),
                "type_name": str(entry.get("type") or entry.get("type_name") or "unknown"),
                "value": entry.get("value", 0),
            }
        )
    return sensors


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


def _as_optional_float(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_optional_bool(raw: object) -> bool | None:
    if raw is None:
        return None
    return bool(raw)


def _parse_json_object(raw: object) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _spec_from_mapping(raw: object) -> TelemetryRuleSpec:
    if not isinstance(raw, Mapping):
        return TelemetryRuleSpec()
    op = raw.get("op")
    return TelemetryRuleSpec(
        enabled=_as_optional_bool(raw["enabled"]) if "enabled" in raw else None,
        op=op if op in ("lt", "gt") else None,
        threshold=_as_optional_float(raw["threshold"]) if "threshold" in raw else None,
        hysteresis=_as_optional_float(raw["hysteresis"]) if "hysteresis" in raw else None,
    )


def _merge_spec(base: TelemetryRuleSpec, patch: TelemetryRuleSpec) -> TelemetryRuleSpec:
    return TelemetryRuleSpec(
        enabled=base.enabled if patch.enabled is None else patch.enabled,
        op=base.op if patch.op is None else patch.op,
        threshold=base.threshold if patch.threshold is None else patch.threshold,
        hysteresis=base.hysteresis if patch.hysteresis is None else patch.hysteresis,
    )


def _apply_legacy_thresholds(
    rules: dict[str, TelemetryRuleSpec], parsed: Mapping[str, Any]
) -> None:
    if "battery_volts_min" in parsed:
        current = rules.get(RULE_BATTERY, TelemetryRuleSpec())
        rules[RULE_BATTERY] = _merge_spec(
            current,
            TelemetryRuleSpec(
                threshold=_as_float(parsed["battery_volts_min"], DEFAULT_BATTERY_VOLTS_MIN)
            ),
        )
    if "noise_floor_max_dbm" in parsed:
        current = rules.get(RULE_NOISE, TelemetryRuleSpec())
        rules[RULE_NOISE] = _merge_spec(
            current,
            TelemetryRuleSpec(
                threshold=_as_float(parsed["noise_floor_max_dbm"], DEFAULT_NOISE_FLOOR_MAX_DBM)
            ),
        )
    if "misses_before_alert" in parsed:
        current = rules.get(RULE_SILENCE, TelemetryRuleSpec())
        rules[RULE_SILENCE] = _merge_spec(
            current,
            TelemetryRuleSpec(threshold=float(_clamp_misses(parsed["misses_before_alert"]))),
        )


def _coerce_override(raw: object) -> TelemetryAlertRuleOverride:
    if not isinstance(raw, Mapping):
        return TelemetryAlertRuleOverride()
    rules: dict[str, TelemetryRuleSpec] = {}
    raw_rules = raw.get("rules")
    if isinstance(raw_rules, Mapping):
        for rule_id, spec in raw_rules.items():
            if isinstance(rule_id, str):
                rules[rule_id] = _spec_from_mapping(spec)
    _apply_legacy_thresholds(rules, raw)
    alerting = _as_optional_bool(raw["alerting"]) if "alerting" in raw else None
    return TelemetryAlertRuleOverride(alerting=alerting, rules=rules or None)


def default_rule_specs() -> dict[str, TelemetryRuleSpec]:
    return {key: TelemetryRuleSpec.model_validate(spec) for key, spec in DEFAULT_RULE_DICTS.items()}


def stored_rules_dict(rules: TelemetryAlertRules) -> dict[str, Any]:
    """Persist the v2 shape only (no legacy top-level threshold keys)."""
    overrides: dict[str, Any] = {}
    for key, override in (rules.overrides or {}).items():
        entry: dict[str, Any] = {}
        if override.alerting is not None:
            entry["alerting"] = override.alerting
        if override.rules:
            entry["rules"] = {
                rule_id: spec.model_dump(exclude_none=True)
                for rule_id, spec in override.rules.items()
            }
        overrides[key] = entry
    return {
        "channels": rules.channels.model_dump(),
        "rules": {
            rule_id: spec.model_dump(exclude_none=True) for rule_id, spec in rules.rules.items()
        },
        "overrides": overrides,
    }


def coerce_telemetry_alert_rules(raw: object) -> TelemetryAlertRules:
    """Accept v2 JSON or the legacy 3-field shape; always return a filled v2 model."""
    if isinstance(raw, TelemetryAlertRules):
        parsed = stored_rules_dict(raw)
        parsed.update(
            {
                "battery_volts_min": raw.battery_volts_min,
                "noise_floor_max_dbm": raw.noise_floor_max_dbm,
                "misses_before_alert": raw.misses_before_alert,
            }
        )
    else:
        parsed = _parse_json_object(raw)

    channels_raw = parsed.get("channels")
    channels = TelemetryAlertChannels(
        push=bool(channels_raw.get("push", True)) if isinstance(channels_raw, Mapping) else True,
        email=bool(channels_raw.get("email", False))
        if isinstance(channels_raw, Mapping)
        else False,
        webhook=(
            bool(channels_raw.get("webhook", False)) if isinstance(channels_raw, Mapping) else False
        ),
    )

    rules = default_rule_specs()
    raw_rules = parsed.get("rules")
    if isinstance(raw_rules, Mapping):
        for rule_id, spec in raw_rules.items():
            if not isinstance(rule_id, str):
                continue
            incoming = _spec_from_mapping(spec)
            rules[rule_id] = _merge_spec(rules.get(rule_id, TelemetryRuleSpec()), incoming)
    _apply_legacy_thresholds(rules, parsed)

    silence = rules.get(RULE_SILENCE, TelemetryRuleSpec())
    if silence.threshold is not None:
        rules[RULE_SILENCE] = _merge_spec(
            silence, TelemetryRuleSpec(threshold=float(_clamp_misses(silence.threshold)))
        )

    overrides: dict[str, TelemetryAlertRuleOverride] = {}
    raw_overrides = parsed.get("overrides")
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            overrides[key.lower()] = _coerce_override(value)

    return TelemetryAlertRules(channels=channels, rules=rules, overrides=overrides)


def merge_telemetry_alert_rules(
    stored: TelemetryAlertRules, incoming: TelemetryAlertRules
) -> TelemetryAlertRules:
    """Deep-merge a PATCH fragment into stored rules.

    Channels merge field-by-field, rules by id, overrides by pubkey then rule.
    Omit/null keeps the stored value. ``overrides: {}`` replaces the map.
    A legacy 3-field PATCH only updates those three thresholds.
    """
    payload = incoming.model_dump(exclude_unset=True, exclude=_COMPAT_RULE_FIELDS)
    merged = stored_rules_dict(stored)

    incoming_dump = incoming.model_dump(exclude=_COMPAT_RULE_FIELDS)
    if any(field in incoming.model_fields_set for field in _COMPAT_RULE_FIELDS):
        payload.setdefault("rules", incoming_dump.get("rules") or {})

    raw_channels = payload.get("channels")
    if isinstance(raw_channels, Mapping):
        merged_channels = dict(merged.get("channels") or DEFAULT_CHANNELS)
        for key, value in raw_channels.items():
            if value is not None:
                merged_channels[key] = value
        merged["channels"] = merged_channels

    raw_rules = payload.get("rules")
    if isinstance(raw_rules, Mapping):
        merged_rules = dict(merged.get("rules") or {})
        for rule_id, spec in raw_rules.items():
            if not isinstance(rule_id, str) or spec is None:
                continue
            existing = merged_rules.get(rule_id) or {}
            patch = spec if isinstance(spec, Mapping) else {}
            merged_rules[rule_id] = {
                **existing,
                **{key: value for key, value in patch.items() if value is not None},
            }
        merged["rules"] = merged_rules

    if "overrides" in payload:
        raw_overrides = payload.get("overrides")
        if raw_overrides is None:
            pass
        elif raw_overrides == {}:
            merged["overrides"] = {}
        elif isinstance(raw_overrides, Mapping):
            # Full v2 documents (Alerts page) replace the map so deleted keys stay gone.
            # Legacy 3-field fragments without channels/rules still merge by pubkey.
            full_document = (
                "channels" in incoming.model_fields_set and "rules" in incoming.model_fields_set
            )
            if full_document:
                merged["overrides"] = {
                    key.lower(): dict(value)
                    for key, value in raw_overrides.items()
                    if isinstance(key, str) and isinstance(value, Mapping)
                }
            else:
                merged_overrides = dict(merged.get("overrides") or {})
                for key, value in raw_overrides.items():
                    if not isinstance(key, str) or not isinstance(value, Mapping):
                        continue
                    existing = dict(merged_overrides.get(key.lower()) or {})
                    if "alerting" in value and value["alerting"] is not None:
                        existing["alerting"] = value["alerting"]
                    raw_ov_rules = value.get("rules")
                    if isinstance(raw_ov_rules, Mapping):
                        existing_rules = dict(existing.get("rules") or {})
                        for rule_id, spec in raw_ov_rules.items():
                            if not isinstance(rule_id, str) or spec is None:
                                continue
                            prior = existing_rules.get(rule_id) or {}
                            patch = spec if isinstance(spec, Mapping) else {}
                            existing_rules[rule_id] = {
                                **prior,
                                **{k: v for k, v in patch.items() if v is not None},
                            }
                        existing["rules"] = existing_rules
                    merged_overrides[key.lower()] = existing
                merged["overrides"] = merged_overrides

    return coerce_telemetry_alert_rules(merged)


def resolve_node_rules(rules: TelemetryAlertRules, public_key: str) -> ResolvedNodeRules:
    """Globals with optional per-key rule fragments and alerting master."""
    override = (rules.overrides or {}).get(public_key.lower())
    alerting = True if override is None or override.alerting is None else bool(override.alerting)
    merged = {rule_id: spec.model_copy() for rule_id, spec in rules.rules.items()}
    if override is not None and override.rules:
        for rule_id, patch in override.rules.items():
            merged[rule_id] = _merge_spec(merged.get(rule_id, TelemetryRuleSpec()), patch)
    return ResolvedNodeRules(alerting=alerting, rules=merged, channels=rules.channels)


def alerting_off_patch(public_key: str) -> TelemetryAlertRules:
    """PATCH fragment that disables alerting for a newly tracked node."""
    return TelemetryAlertRules(
        overrides={public_key.lower(): TelemetryAlertRuleOverride(alerting=False)}
    )


def rule_is_enabled(spec: TelemetryRuleSpec | None, fallback: TelemetryRuleSpec | None) -> bool:
    if spec is not None and spec.enabled is not None:
        return bool(spec.enabled)
    if fallback is not None and fallback.enabled is not None:
        return bool(fallback.enabled)
    return False


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


def _lpp_type_name(sensor: Mapping[str, Any]) -> str:
    return str(sensor.get("type_name") or sensor.get("type") or "").lower()


def _lpp_channel(sensor: Mapping[str, Any]) -> int:
    try:
        return int(sensor.get("channel", 0))
    except (TypeError, ValueError):
        return 0


def _lpp_scalar_value(sensor: Mapping[str, Any]) -> float | None:
    type_name = _lpp_type_name(sensor)
    if type_name not in LPP_SCALAR_TYPES:
        return None
    value = sensor.get("value")
    if isinstance(value, dict):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def _history_had_lpp_fix(public_key: str) -> bool:
    for repo in (RepeaterTelemetryRepository, ContactTelemetryRepository):
        try:
            rows = await repo.get_history(public_key, 0)
        except Exception:
            logger.debug("Telemetry alerts: failed to read LPP history for GPS", exc_info=True)
            continue
        if any(lpp_has_fix((row.get("data") or {}).get("lpp_sensors")) for row in rows):
            return True
    return False


def coerce_notification_destinations(raw: object) -> NotificationDestinations:
    parsed = _parse_json_object(raw)
    try:
        return NotificationDestinations.model_validate(
            {**DEFAULT_NOTIFICATION_DESTINATIONS, **parsed}
        )
    except Exception:
        return NotificationDestinations()


def merge_notification_destinations(
    stored: NotificationDestinations,
    incoming: NotificationDestinations | NotificationDestinationsUpdate | Mapping[str, Any],
) -> NotificationDestinations:
    """Merge a PATCH. omit/null keeps a secret; empty string clears it."""
    if isinstance(incoming, Mapping):
        payload = dict(incoming)
    else:
        payload = incoming.model_dump(exclude_unset=True, by_alias=True)  # type: ignore[union-attr]
    merged = stored.model_dump(by_alias=True)

    email_patch = payload.get("email")
    if isinstance(email_patch, Mapping):
        email = dict(merged.get("email") or {})
        for key, value in email_patch.items():
            if key == "password":
                if value is None or value == REDACTED_SECRET:
                    continue
                email["password"] = value
            elif value is not None:
                email[key] = value
        merged["email"] = email

    webhook_patch = payload.get("webhook")
    if isinstance(webhook_patch, Mapping):
        webhook = dict(merged.get("webhook") or {})
        for key, value in webhook_patch.items():
            if key == "hmac_secret":
                if value is None or value == REDACTED_SECRET:
                    continue
                webhook["hmac_secret"] = value
            elif value is not None:
                webhook[key] = value
        merged["webhook"] = webhook

    return coerce_notification_destinations(merged)


def redact_notification_destinations(dest: NotificationDestinations) -> NotificationDestinations:
    dumped = dest.model_dump(by_alias=True)
    email = dumped.setdefault("email", {})
    webhook = dumped.setdefault("webhook", {})
    email["password"] = REDACTED_SECRET if email.get("password") else ""
    webhook["hmac_secret"] = REDACTED_SECRET if webhook.get("hmac_secret") else ""
    return NotificationDestinations.model_validate(dumped)


def catalog_metrics_from_snapshot(snapshot: Mapping[str, Any] | None) -> set[str]:
    metrics: set[str] = set()
    if not isinstance(snapshot, Mapping):
        return metrics
    for rule_id, field in GAUGE_SNAPSHOT_FIELDS.items():
        if field in snapshot and snapshot.get(field) is not None:
            metrics.add(rule_id)
    for sensor in snapshot.get("lpp_sensors") or []:
        if not isinstance(sensor, Mapping):
            continue
        type_name = _lpp_type_name(sensor)
        if type_name not in LPP_SCALAR_TYPES:
            continue
        if _lpp_scalar_value(sensor) is None:
            continue
        channel = _lpp_channel(sensor)
        metrics.add(f"lpp:{type_name}")
        metrics.add(f"lpp:{type_name}:{channel}")
    return metrics


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

    node = resolve_node_rules(settings.telemetry_alert_rules, public_key)
    if not node.alerting:
        return

    now = int(time.time())
    label = (name or "").strip() or public_key[:12]

    if outcome == "miss":
        await _apply_silence(
            public_key=public_key,
            name=label,
            node=node,
            now=now,
            increment=True,
        )
        return

    await _apply_silence(
        public_key=public_key,
        name=label,
        node=node,
        now=now,
        increment=False,
    )
    await _apply_status_gauges(
        public_key=public_key,
        name=label,
        snapshot=snapshot,
        node=node,
        now=now,
    )
    if allow_gps_lost:
        await _apply_lpp_gauges(
            public_key=public_key,
            name=label,
            snapshot=snapshot,
            node=node,
            now=now,
        )
        await _apply_gps_lost(
            public_key=public_key,
            name=label,
            snapshot=snapshot,
            node=node,
            now=now,
        )


async def _apply_silence(
    *,
    public_key: str,
    name: str,
    node: ResolvedNodeRules,
    now: int,
    increment: bool,
) -> None:
    spec = node.rules.get(RULE_SILENCE, TelemetryRuleSpec())
    threshold = _clamp_misses(
        spec.threshold if spec.threshold is not None else DEFAULT_MISSES_BEFORE_ALERT
    )
    enabled = rule_is_enabled(spec, None) if spec.enabled is not None else True

    state = await TelemetryAlertStateRepository.get(public_key, RULE_SILENCE)
    misses = int(state["consecutive_misses"]) if state else 0
    latched = bool(state and state.get("last_fired_at"))
    if increment:
        misses += 1
        should_fire = enabled and misses >= threshold and not latched
        fired_at = (
            state["last_fired_at"]
            if state is not None and latched
            else (now if should_fire else None)
        )
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
                threshold=float(threshold),
                channels=node.channels,
            )
        return

    await TelemetryAlertStateRepository.upsert(
        public_key,
        RULE_SILENCE,
        consecutive_misses=0,
        last_fired_at=None,
        last_value=0.0,
    )


async def _apply_status_gauges(
    *,
    public_key: str,
    name: str,
    snapshot: Mapping[str, Any] | None,
    node: ResolvedNodeRules,
    now: int,
) -> None:
    if snapshot is None:
        return
    for rule_id, field in GAUGE_SNAPSHOT_FIELDS.items():
        spec = node.rules.get(rule_id)
        fallback = (
            TelemetryRuleSpec.model_validate(DEFAULT_RULE_DICTS[rule_id])
            if rule_id in DEFAULT_RULE_DICTS
            else None
        )
        if spec is None or not rule_is_enabled(spec, fallback):
            continue
        if field not in snapshot:
            continue
        raw = snapshot.get(field)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if rule_id == RULE_BATTERY and value <= 0:
            continue
        await _apply_gauge(
            public_key=public_key,
            name=name,
            rule_id=rule_id,
            value=value,
            spec=spec,
            now=now,
            channels=node.channels,
        )


async def _apply_lpp_gauges(
    *,
    public_key: str,
    name: str,
    snapshot: Mapping[str, Any] | None,
    node: ResolvedNodeRules,
    now: int,
) -> None:
    if snapshot is None:
        return
    sensors = snapshot.get("lpp_sensors")
    if not isinstance(sensors, list):
        return
    present: dict[str, float] = {}
    for sensor in sensors:
        if not isinstance(sensor, Mapping):
            continue
        value = _lpp_scalar_value(sensor)
        if value is None:
            continue
        type_name = _lpp_type_name(sensor)
        channel = _lpp_channel(sensor)
        present[f"lpp:{type_name}:{channel}"] = value

    for latch_id, value in present.items():
        _, type_name, channel_s = latch_id.split(":", 2)
        type_rule = node.rules.get(f"lpp:{type_name}")
        channel_rule = node.rules.get(latch_id)
        spec = None
        if channel_rule is not None:
            spec = _merge_spec(type_rule or TelemetryRuleSpec(), channel_rule)
        elif type_rule is not None:
            spec = type_rule
        if spec is None or not rule_is_enabled(spec, None):
            continue
        if spec.threshold is None or spec.op is None:
            continue
        await _apply_gauge(
            public_key=public_key,
            name=name,
            rule_id=latch_id,
            value=value,
            spec=spec,
            now=now,
            channels=node.channels,
        )


async def _apply_gauge(
    *,
    public_key: str,
    name: str,
    rule_id: str,
    value: float,
    spec: TelemetryRuleSpec,
    now: int,
    channels: TelemetryAlertChannels,
) -> None:
    if spec.threshold is None or spec.op not in ("lt", "gt"):
        return
    threshold = float(spec.threshold)
    hysteresis = float(spec.hysteresis) if spec.hysteresis is not None else 0.0
    state = await TelemetryAlertStateRepository.get(public_key, rule_id)
    latched = bool(state and state.get("last_fired_at"))

    if spec.op == "lt":
        in_alarm = value < threshold
        rearmed = value >= threshold + hysteresis
    else:
        in_alarm = value > threshold
        rearmed = value <= threshold - hysteresis

    if latched:
        await TelemetryAlertStateRepository.upsert(
            public_key,
            rule_id,
            last_fired_at=None if rearmed else (state["last_fired_at"] if state else now),
            last_value=value,
        )
        return

    if in_alarm:
        await TelemetryAlertStateRepository.upsert(
            public_key,
            rule_id,
            last_fired_at=now,
            last_value=value,
        )
        await _dispatch(
            public_key=public_key,
            name=name,
            rule_id=rule_id,
            value=value,
            threshold=threshold,
            channels=channels,
        )
        return

    await TelemetryAlertStateRepository.upsert(
        public_key,
        rule_id,
        last_fired_at=None,
        last_value=value,
    )


async def _apply_gps_lost(
    *,
    public_key: str,
    name: str,
    snapshot: Mapping[str, Any] | None,
    node: ResolvedNodeRules,
    now: int,
) -> None:
    spec = node.rules.get(RULE_GPS_LOST, TelemetryRuleSpec())
    if not rule_is_enabled(spec, TelemetryRuleSpec(enabled=True)):
        return

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
            channels=node.channels,
        )
        return

    await TelemetryAlertStateRepository.upsert(
        public_key,
        RULE_GPS_LOST,
        last_fired_at=state["last_fired_at"] if state is not None and latched else None,
        last_value=0.0,
    )


async def _dispatch(
    *,
    public_key: str,
    name: str,
    rule_id: str,
    value: float,
    threshold: float,
    channels: TelemetryAlertChannels,
) -> None:
    from app.notify import dispatch_system_event

    await dispatch_system_event(
        {
            "event": "telemetry_alert",
            "public_key": public_key,
            "name": name,
            "rule_id": rule_id,
            "value": value,
            "threshold": threshold,
        },
        channels,
    )
