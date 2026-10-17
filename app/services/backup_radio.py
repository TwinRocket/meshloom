"""Radio side of the full backup: read every node setting, write them back.

Reads run inside the export's companion CLI session (radio lock held, polling
paused, auto-fetch suspended). Each read is best effort: a field the radio does
not answer stays None and adds a warning, it never aborts the export.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from meshcore import EventType

from app.models import (
    BackupAutoadd,
    BackupFirmwareInfo,
    BackupFloodScope,
    BackupOtherParams,
    BackupRadioParams,
    BackupTuning,
    RadioNodeSettings,
)
from app.services.companion_cli import CompanionCli, read_cli_node_prefs

logger = logging.getLogger(__name__)


@dataclass
class RadioSnapshot:
    settings: RadioNodeSettings
    public_key: str | None
    warnings: list[str] = field(default_factory=list)


def _payload(result: Any, expected: EventType) -> dict | None:
    if result is None or getattr(result, "type", None) != expected:
        return None
    payload = getattr(result, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def _read(name: str, warnings: list[str], call) -> Any:
    try:
        return await call()
    except Exception as exc:  # best effort: one field, never the export
        logger.warning("Backup: reading %s from the radio failed: %s", name, type(exc).__name__)
        warnings.append(f"radio_read_failed:{name}")
        return None


def _self_info_fields(info: dict, settings: dict[str, Any]) -> None:
    settings["name"] = _str(info.get("name"))
    lat, lon = info.get("adv_lat"), info.get("adv_lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        settings["lat"], settings["lon"] = float(lat), float(lon)
    settings["tx_power"] = _int(info.get("tx_power"))
    settings["max_tx_power"] = _int(info.get("max_tx_power"))
    freq, bw = info.get("radio_freq"), info.get("radio_bw")
    sf, cr = _int(info.get("radio_sf")), _int(info.get("radio_cr"))
    if isinstance(freq, (int, float)) and isinstance(bw, (int, float)) and sf and cr:
        try:
            settings["radio"] = BackupRadioParams(freq=float(freq), bw=float(bw), sf=sf, cr=cr)
        except ValueError:
            settings["radio"] = None
    try:
        settings["other"] = BackupOtherParams(
            # meshcore reads this byte as a bool (> 0).
            manual_add_contacts=int(bool(info.get("manual_add_contacts"))),
            telemetry_mode_base=int(info.get("telemetry_mode_base") or 0),
            telemetry_mode_loc=int(info.get("telemetry_mode_loc") or 0),
            telemetry_mode_env=int(info.get("telemetry_mode_env") or 0),
            adv_loc_policy=int(info.get("adv_loc_policy") or 0),
            multi_acks=int(info.get("multi_acks") or 0),
        )
    except (TypeError, ValueError):
        settings["other"] = None


async def read_radio_node_settings(mc, cli: CompanionCli | None) -> RadioSnapshot:
    """Read every node setting the radio exposes (see ``RadioNodeSettings``).

    ``cli`` is the export's companion CLI (None skips the CLI-only prefs).
    """
    from app.services.radio_runtime import radio_runtime

    manager = radio_runtime.manager
    warnings: list[str] = []
    fields: dict[str, Any] = {}
    public_key: str | None = None

    self_info = _payload(
        await _read("self_info", warnings, mc.commands.send_appstart), EventType.SELF_INFO
    )
    if self_info is None:
        warnings.append("radio_read_failed:self_info")
    else:
        key = _str(self_info.get("public_key"))
        public_key = key.lower() if key else None
        _self_info_fields(self_info, fields)

    device = _payload(
        await _read("device_info", warnings, mc.commands.send_device_query), EventType.DEVICE_INFO
    )
    ver_code = getattr(manager, "firmware_ver_code", None)
    if device is None:
        warnings.append("radio_read_failed:device_info")
    else:
        ver_code = _int(device.get("fw ver")) or ver_code
        if (ver_code or 0) >= 3:
            fields["ble_pin"] = _int(device.get("ble_pin"))
        if isinstance(device.get("repeat"), bool):
            fields["client_repeat"] = device["repeat"]
        if (ver_code or 0) >= 10:
            mode = _int(device.get("path_hash_mode"))
            fields["path_hash_mode"] = mode if mode is not None and 0 <= mode <= 2 else None
    fields["firmware"] = BackupFirmwareInfo(
        ver_code=ver_code,
        version=_str((device or {}).get("ver")) or getattr(manager, "firmware_version", None),
        build=_str((device or {}).get("fw_build")) or getattr(manager, "firmware_build", None),
        model=_str((device or {}).get("model")) or getattr(manager, "device_model", None),
        max_contacts=_int((device or {}).get("max_contacts"))
        or getattr(manager, "max_contacts", None),
        max_channels=_int((device or {}).get("max_channels"))
        or getattr(manager, "max_channels", None),
    )

    tuning = _payload(
        await _read("tuning", warnings, mc.commands.get_tuning), EventType.TUNING_PARAMS
    )
    if tuning is None:
        warnings.append("radio_read_failed:tuning")
    else:
        rx, af = _int(tuning.get("rx_delay")), _int(tuning.get("airtime_factor"))
        if rx is not None and af is not None:
            fields["tuning"] = BackupTuning(rx_delay_raw=rx, airtime_factor_raw=af)

    custom = _payload(
        await _read("custom_vars", warnings, mc.commands.get_custom_vars), EventType.CUSTOM_VARS
    )
    if custom is None:
        warnings.append("radio_read_failed:custom_vars")
    else:
        fields["custom_vars"] = {str(k): str(v) for k, v in custom.items()}

    scope = _payload(
        await _read("default_flood_scope", warnings, mc.commands.get_default_flood_scope),
        EventType.DEFAULT_FLOOD_SCOPE,
    )
    if scope is None:
        warnings.append("radio_read_failed:default_flood_scope")
    else:
        fields["default_flood_scope_read"] = True
        name, key_hex = _str(scope.get("scope_name")), _str(scope.get("scope_key"))
        if key_hex and set(key_hex) != {"0"}:
            try:
                fields["default_flood_scope"] = BackupFloodScope(name=name or "", key_hex=key_hex)
            except ValueError:
                fields["default_flood_scope_read"] = False
                warnings.append("radio_read_failed:default_flood_scope")

    autoadd = _payload(
        await _read("autoadd", warnings, mc.commands.get_autoadd_config), EventType.AUTOADD_CONFIG
    )
    if autoadd is None:
        warnings.append("radio_read_failed:autoadd")
    else:
        config = _int(autoadd.get("config"))
        if config is not None:
            fields["autoadd"] = BackupAutoadd(config=config, max_hops=_int(autoadd.get("max_hops")))

    if cli is not None:
        prefs = await _read("cli_prefs", warnings, lambda: read_cli_node_prefs(cli))
        if prefs is not None:
            fields["cli_prefs"] = prefs

    # Dedupe while keeping the order (a failed call may add two warnings).
    unique = list(dict.fromkeys(warnings))
    return RadioSnapshot(
        settings=RadioNodeSettings(**fields), public_key=public_key, warnings=unique
    )
