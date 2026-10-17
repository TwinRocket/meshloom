"""Radio side of the full backup: read every node setting, write them back.

Reads run inside the export's companion CLI session (radio lock held, polling
paused, auto-fetch suspended). Each read is best effort: a field the radio does
not answer stays None and adds a warning, it never aborts the export.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from meshcore import EventType

from app.models import (
    BackupAutoadd,
    BackupFirmwareInfo,
    BackupFloodScope,
    BackupOtherParams,
    BackupRadioParams,
    BackupRestoreItem,
    BackupTuning,
    RadioConfigUpdate,
    RadioNodeSettings,
    RadioSettings,
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


def _int8(value: object) -> int | None:
    """A signed byte meshcore reads unsigned (tx_power: SX126x goes below 0 dBm)."""
    raw = _int(value)
    if raw is None:
        return None
    return raw - 256 if 127 < raw <= 255 else raw


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
    settings["tx_power"] = _int8(info.get("tx_power"))
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
            pin = _int(device.get("ble_pin"))
            if pin is not None and not 0 <= pin <= 999999:
                warnings.append("radio_read_failed:ble_pin")
                pin = None
            fields["ble_pin"] = pin
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
            try:
                fields["tuning"] = BackupTuning(rx_delay_raw=rx, airtime_factor_raw=af)
            except ValueError:
                warnings.append("radio_read_failed:tuning")

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
            try:
                fields["autoadd"] = BackupAutoadd(
                    config=config, max_hops=_int(autoadd.get("max_hops"))
                )
            except ValueError:
                warnings.append("radio_read_failed:autoadd")

    if cli is not None:
        prefs = await _read("cli_prefs", warnings, lambda: read_cli_node_prefs(cli))
        if prefs is not None:
            fields["cli_prefs"] = prefs

    # Dedupe while keeping the order (a failed call may add two warnings).
    unique = list(dict.fromkeys(warnings))
    return RadioSnapshot(
        settings=RadioNodeSettings(**fields), public_key=public_key, warnings=unique
    )


# -- Write back ----------------------------------------------------------------

_CUSTOM_VAR_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")
CHANNEL_NAME_MAX_BYTES = 31  # 32-byte field, NUL kept by the firmware
FLOOD_SCOPE_NAME_MAX_BYTES = 31


def utf8_prefix(text: str, max_bytes: int) -> bytes:
    """At most ``max_bytes`` of UTF-8, cut on a character boundary."""
    out = bytearray()
    for char in text:
        encoded = char.encode("utf-8")
        if len(out) + len(encoded) > max_bytes:
            break
        out += encoded
    return bytes(out)


def _custom_var_valid(name: str, value: str) -> bool:
    # Same rule as PUT /radio/custom-vars/{name}.
    return (
        bool(_CUSTOM_VAR_NAME_RE.fullmatch(name))
        and 1 <= len(value) <= 64
        and all(0x20 <= ord(ch) <= 0x7E for ch in value)
        and ":" not in value
        and "," not in value
    )


async def _noop_time_sync(_mc) -> None:
    """Restore groups never trigger the clock-fix reboot: one sync at the end."""


def _rejected(result: Any) -> bool:
    return result is None or getattr(result, "type", None) == EventType.ERROR


@dataclass
class RadioWriteOutcome:
    items: list[BackupRestoreItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, name: str, status: str, code: str | None = None) -> None:
        self.items.append(BackupRestoreItem(name=name, status=status, code=code))  # type: ignore[arg-type]


async def write_radio_node_settings(
    mc,
    cli: CompanionCli | None,
    settings: RadioNodeSettings,
    *,
    write_ble_pin: bool,
    flood_scope: str | None,
) -> RadioWriteOutcome:
    """Write every backed-up setting, one group at a time, in a fixed order.

    A rejected group is ``failed`` with its code and the others still run.
    Never syncs the clock (the caller does it once, after).
    """
    from app.services.companion_cli import (
        CLI_NODE_PREFS,
        CliError,
        CliUnknownCommandError,
        write_cli_node_pref,
    )
    from app.services.flood_scope import set_radio_flood_scope
    from app.services.radio_commands import (
        PathHashModeUnsupportedError,
        RadioCommandRejectedError,
        apply_radio_config_update,
    )
    from app.services.radio_runtime import radio_runtime

    manager = radio_runtime.manager
    out = RadioWriteOutcome()
    fw_ver = getattr(manager, "firmware_ver_code", None)

    async def apply(name: str, update: RadioConfigUpdate, **kwargs: Any) -> bool:
        try:
            await apply_radio_config_update(
                mc,
                update,
                path_hash_mode_supported=bool(getattr(manager, "path_hash_mode_supported", False)),
                set_path_hash_mode=lambda mode: setattr(manager, "path_hash_mode", mode),
                sync_radio_time_fn=_noop_time_sync,
                **kwargs,
            )
        except PathHashModeUnsupportedError:
            out.add(name, "unsupported", "path_hash_mode_unsupported")
            return False
        except RadioCommandRejectedError as exc:
            out.add(name, "failed", exc.code or f"{name}_rejected")
            return False
        out.add(name, "done")
        return True

    def missing(name: str) -> None:
        out.add(name, "skipped", "not_in_backup")

    if settings.name is not None:
        await apply("name", RadioConfigUpdate(name=settings.name))
    else:
        missing("name")

    if settings.lat is not None and settings.lon is not None:
        await apply("coords", RadioConfigUpdate(lat=settings.lat, lon=settings.lon))
    else:
        missing("coords")

    if settings.tx_power is not None:
        # Files written before the signed read hold -9 dBm as 247.
        power = _int8(settings.tx_power)
        info = getattr(mc, "self_info", None)
        target_max = info.get("max_tx_power") if isinstance(info, dict) else None
        if power is None or power < 0:
            # meshcore's set_tx_power sends an unsigned value: a negative power
            # cannot be written back, and raising it to a default would not be
            # what the user had.
            out.add("tx_power", "unsupported", "tx_power_negative")
        else:
            if isinstance(target_max, int) and target_max > 0 and power > target_max:
                power = target_max
                out.warnings.append("tx_power_clamped")
            await apply("tx_power", RadioConfigUpdate(tx_power=power))
    else:
        missing("tx_power")

    if settings.radio is not None:
        params = RadioSettings(
            freq=settings.radio.freq,
            bw=settings.radio.bw,
            sf=settings.radio.sf,
            cr=settings.radio.cr,
        )
        repeat = settings.client_repeat if (fw_ver or 0) >= 9 else None
        try:
            # A frame refused with repeat on is sent again without it (the
            # firmware does not allow repeat on every frequency).
            param_warnings = await apply_radio_config_update(
                mc,
                RadioConfigUpdate(radio=params),
                path_hash_mode_supported=False,
                set_path_hash_mode=lambda _mode: None,
                sync_radio_time_fn=_noop_time_sync,
                client_repeat=repeat,
            )
        except RadioCommandRejectedError:
            out.add("radio_params", "failed", "radio_params_rejected")
        else:
            if "client_repeat_not_allowed" in param_warnings:
                out.add("radio_params", "done", "client_repeat_not_allowed")
                out.warnings.append("client_repeat_not_allowed")
            else:
                out.add("radio_params", "done")
    else:
        missing("radio_params")

    if settings.other is not None:
        # Raw values in one frame (lossless; RadioConfigUpdate collapses them).
        result = await mc.commands.set_other_params_from_infos(settings.other.model_dump())
        if _rejected(result):
            out.add("other_params", "failed", "other_params_rejected")
        else:
            out.add("other_params", "done")
    else:
        missing("other_params")

    if settings.tuning is not None:
        result = await mc.commands.set_tuning(
            settings.tuning.rx_delay_raw, settings.tuning.airtime_factor_raw
        )
        out.add(
            "tuning",
            "failed" if _rejected(result) else "done",
            "tuning_rejected" if _rejected(result) else None,
        )
    else:
        missing("tuning")

    if settings.path_hash_mode is not None:
        await apply("path_hash_mode", RadioConfigUpdate(path_hash_mode=settings.path_hash_mode))
    else:
        missing("path_hash_mode")

    if settings.custom_vars:
        failed_vars = 0
        for name, value in settings.custom_vars.items():
            if not _custom_var_valid(name, value):
                failed_vars += 1
                continue
            result = await mc.commands.set_custom_var(name, value)
            if _rejected(result):
                failed_vars += 1
        if failed_vars:
            out.add("custom_vars", "failed", "custom_var_rejected")
        else:
            out.add("custom_vars", "done")
    else:
        missing("custom_vars")

    if settings.default_flood_scope_read:
        scope = settings.default_flood_scope
        if scope is None:
            frame = b"\x3f"  # a 1-byte frame clears it
        else:
            frame = (
                b"\x3f"
                + utf8_prefix(scope.name, FLOOD_SCOPE_NAME_MAX_BYTES).ljust(31, b"\x00")
                + bytes.fromhex(scope.key_hex)
            )
        result = await mc.commands.send(frame, [EventType.OK, EventType.ERROR])
        rejected = _rejected(result)
        out.add(
            "default_flood_scope",
            "failed" if rejected else "done",
            "default_flood_scope_rejected" if rejected else None,
        )
    else:
        missing("default_flood_scope")

    if settings.autoadd is not None:
        # Raw frame: meshcore's helper drops max_hops.
        frame = b"\x3a" + bytes([settings.autoadd.config])
        if settings.autoadd.max_hops is not None:
            frame += bytes([settings.autoadd.max_hops])
        result = await mc.commands.send(frame, [EventType.OK, EventType.ERROR])
        rejected = _rejected(result)
        out.add(
            "autoadd", "failed" if rejected else "done", "autoadd_rejected" if rejected else None
        )
    else:
        missing("autoadd")

    if settings.ble_pin is None:
        missing("ble_pin")
    elif not write_ble_pin:
        out.add("ble_pin", "skipped", "step_off")
    elif settings.ble_pin != 0 and not 100000 <= settings.ble_pin <= 999999:
        out.add("ble_pin", "failed", "ble_pin_invalid")
    else:
        result = await mc.commands.set_devicepin(settings.ble_pin)
        rejected = _rejected(result)
        out.add(
            "ble_pin", "failed" if rejected else "done", "ble_pin_rejected" if rejected else None
        )

    if settings.cli_prefs:
        if cli is None:
            out.add("cli_prefs", "unsupported", "cli_unsupported")
        else:
            status, code = "done", None
            for name, value in settings.cli_prefs.items():
                if name not in CLI_NODE_PREFS:
                    continue
                try:
                    await write_cli_node_pref(cli, name, value)
                except CliUnknownCommandError:
                    continue  # this board does not have it
                except (CliError, ValueError):
                    status, code = "failed", "cli_pref_rejected"
            out.add("cli_prefs", status, code)
    else:
        missing("cli_prefs")

    # The standing flood scope is Meshloom's (restored by the meshloom step).
    try:
        await set_radio_flood_scope(mc, flood_scope, fw_ver=fw_ver)
    except Exception:
        out.warnings.append("flood_scope_not_applied")
    await mc.commands.send_appstart()
    return out


async def _channel_activity() -> dict[str, int]:
    from app.repository import channels as channels_repo

    async with channels_repo.db.readonly() as conn:
        async with conn.execute(
            "SELECT conversation_key, MAX(received_at) FROM messages "
            "WHERE type = 'CHAN' GROUP BY conversation_key"
        ) as cursor:
            rows = await cursor.fetchall()
    return {str(row[0]).upper(): int(row[1] or 0) for row in rows}


async def push_channels_to_radio(mc) -> tuple[int, list[str]]:
    """Write the adopted channels into slots 0..n-1 (Public, favourites,
    pinned, then by last activity), raw frames keeping each secret."""
    from app.channel_constants import PUBLIC_CHANNEL_KEY
    from app.repository import ChannelRepository
    from app.services.radio_runtime import radio_runtime

    manager = radio_runtime.manager
    activity = await _channel_activity()
    channels = [c for c in await ChannelRepository.get_all() if c.membership == "adopted"]
    channels.sort(
        key=lambda c: (
            c.key.upper() != PUBLIC_CHANNEL_KEY,
            not c.favorite,
            not c.pinned,
            -activity.get(c.key.upper(), 0),
            c.name,
        )
    )
    warnings: list[str] = []
    written = 0
    capacity = max(0, int(manager.max_channels))
    for slot, channel in enumerate(channels[:capacity]):
        try:
            secret = bytes.fromhex(channel.key)
        except ValueError:
            warnings.append("channel_key_invalid")
            continue
        frame = (
            b"\x20"
            + bytes([slot])
            + utf8_prefix(channel.name, CHANNEL_NAME_MAX_BYTES).ljust(32, b"\x00")
            + secret
        )
        result = await mc.commands.send(frame, [EventType.OK, EventType.ERROR])
        if _rejected(result):
            warnings.append("channel_rejected")
            continue
        manager.note_channel_slot_loaded(channel.key, slot)
        written += 1
    # Empty the slots above: on the same radio they may still hold a channel
    # the user removed since (name "" and a zero secret, as setup clears them).
    for slot in range(min(len(channels), capacity), capacity):
        frame = b"\x20" + bytes([slot]) + bytes(32) + bytes(16)
        result = await mc.commands.send(frame, [EventType.OK, EventType.ERROR])
        if _rejected(result):
            warnings.append("channel_clear_failed")
        manager.forget_channel_slot(slot)
    if len(channels) > written and len(channels) > capacity:
        warnings.append("channels_over_radio_limit")
    return written, list(dict.fromkeys(warnings))


RADIO_CONTACT_FAVORITE = 0x01


@dataclass
class ContactPushOutcome:
    written: int = 0
    table_full: int = 0
    unreadable: bool = False
    warnings: list[str] = field(default_factory=list)


async def push_contacts_to_radio(mc) -> ContactPushOutcome:
    """Load the restored working set (favourites first, then recents, within the
    limits) onto the radio.

    The background contact reconcile is stopped for the push and always started
    again, even when the radio's table cannot be read or the push raises.
    """
    from app import radio_sync
    from app.repository import AppSettingsRepository

    autoevict = bool((await AppSettingsRepository.get()).load_with_autoevict)
    await radio_sync.stop_background_contact_reconciliation()
    radio_contacts: dict[str, dict] = {}
    try:
        return await _push_contacts(mc, autoevict, radio_contacts)
    finally:
        radio_sync.start_background_contact_reconciliation(
            initial_radio_contacts=radio_contacts, expected_mc=mc, autoevict=autoevict
        )


async def _push_contacts(
    mc, autoevict: bool, radio_contacts: dict[str, dict]
) -> ContactPushOutcome:
    """Fills ``radio_contacts`` with what the radio holds, as it goes."""
    from app import radio_sync
    from app.services.companion_cli import CONTACT_FLAG_REMOTE_CLI

    warnings: list[str] = []
    fetched = await mc.commands.get_contacts()
    if fetched is None or getattr(fetched, "type", None) != EventType.CONTACTS:
        return ContactPushOutcome(unreadable=True, warnings=["radio_contacts_unreadable"])
    radio_contacts.update(
        {
            str(key).lower(): value
            for key, value in (fetched.payload or {}).items()
            if isinstance(value, dict)
        }
    )
    selected = [
        c
        for c in await radio_sync.get_contacts_selected_for_radio_sync()
        if len(c.public_key) >= 64
    ]
    desired = {c.public_key.lower(): c for c in selected}

    if not autoevict:
        # Room first: a reflashed radio's table may already be full of adverts.
        for public_key in [key for key in radio_contacts if key not in desired]:
            await radio_sync.remove_radio_contact(
                mc, public_key, radio_contacts, context="backup restore"
            )

    written = 0
    table_full = 0
    for public_key, contact in desired.items():
        payload = contact.to_radio_dict()
        flags = (int(payload.get("flags") or 0) & ~CONTACT_FLAG_REMOTE_CLI) & 0xFF
        if autoevict:
            # Contacts Meshloom loads stay evictable by the radio.
            flags &= ~RADIO_CONTACT_FAVORITE
        elif contact.favorite:
            flags |= RADIO_CONTACT_FAVORITE
        payload["flags"] = flags
        present = public_key in radio_contacts
        if not present and table_full:
            table_full += 1
            continue
        try:
            if present:
                result = await mc.commands.update_contact(payload)
            else:
                result = await mc.commands.add_contact(payload)
        except Exception as exc:
            logger.warning(
                "Backup restore: loading contact %s failed: %s", public_key[:12], type(exc).__name__
            )
            warnings.append("contact_load_failed")
            continue
        if getattr(result, "type", None) == EventType.OK:
            radio_contacts[public_key] = payload
            written += 1
            continue
        reason = getattr(result, "payload", None)
        if isinstance(reason, dict) and reason.get("error_code") == 3:
            table_full += 1
        else:
            warnings.append("contact_load_failed")

    return ContactPushOutcome(
        written=written, table_full=table_full, warnings=list(dict.fromkeys(warnings))
    )


async def import_private_key_raw(mc, key: bytes) -> str:
    """Send CMD_IMPORT_PRIVATE_KEY and wait for OK, ERROR or DISABLED.

    meshcore's ``import_private_key()`` waits for OK/ERROR only, so a firmware
    built without key import (DISABLED) would read as a timeout. Returns
    "ok", "disabled", "rejected" or "timeout". The key is a registered secret
    for the exchange (the 0x18 frame is redacted structurally as well).
    """
    from app.log_redaction import secret_scope

    with secret_scope(key):
        result = await mc.commands.send(
            b"\x18" + key, [EventType.OK, EventType.ERROR, EventType.DISABLED], timeout=10
        )
    result_type = getattr(result, "type", None)
    if result_type == EventType.OK:
        return "ok"
    if result_type == EventType.DISABLED:
        return "disabled"
    if result_type == EventType.ERROR:
        payload = getattr(result, "payload", None)
        if isinstance(payload, dict) and payload.get("reason"):
            return "timeout"
        return "rejected"
    return "timeout"
