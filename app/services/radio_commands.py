import logging
from collections.abc import Awaitable, Callable
from typing import Any

from meshcore import EventType

from app.log_redaction import secret_scope

logger = logging.getLogger(__name__)


class RadioCommandServiceError(RuntimeError):
    """Base error for reusable radio command workflows."""


class PathHashModeUnsupportedError(RadioCommandServiceError):
    """Raised when firmware does not support path hash mode updates."""


class RadioCommandRejectedError(RadioCommandServiceError):
    """Raised when the radio reports an error for a command. ``code`` names the
    setting (``name_rejected``, ``radio_params_rejected``...), never a value."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class KeystoreRefreshError(RadioCommandServiceError):
    """Raised when server-side keystore refresh fails after import."""


def _rejected(result) -> bool:
    return result is not None and getattr(result, "type", None) == EventType.ERROR


def _refused_by_firmware(result) -> bool:
    """An ERROR frame the radio sent, not meshcore's own timeout or send failure.

    meshcore reports a timeout as ERROR ``{"reason": ...}`` and a send failure as
    ERROR ``{"error": ...}``; the firmware's ERROR carries ``error_code`` or nothing.
    """
    if not _rejected(result):
        return False
    payload = getattr(result, "payload", None)
    return not (isinstance(payload, dict) and ("reason" in payload or "error" in payload))


def device_info_client_repeat(result) -> bool | None:
    """The client-repeat flag from a DEVICE_INFO reply (firmware 9+), else None."""
    if result is None or getattr(result, "type", None) != EventType.DEVICE_INFO:
        return None
    payload = getattr(result, "payload", None)
    if not isinstance(payload, dict):
        return None
    repeat = payload.get("repeat")
    return repeat if isinstance(repeat, bool) else None


async def apply_radio_config_update(
    mc,
    update,
    *,
    path_hash_mode_supported: bool,
    set_path_hash_mode: Callable[[int], None],
    sync_radio_time_fn: Callable[[Any], Awaitable[Any]],
    client_repeat: bool | None = None,
) -> list[str]:
    """Apply a validated radio-config update to the connected radio.

    ``client_repeat`` is the repeat byte sent with the radio parameters: the
    firmware reads a missing byte as "repeat off", so callers pass the radio's
    current flag (from a fresh DEVICE_INFO) to keep it. The firmware refuses the
    whole frame when repeat is on and the new frequency is outside the bands
    where repeat is allowed: the parameters are then sent once more with repeat
    off, and the returned warnings hold ``client_repeat_not_allowed``. A command
    the radio answers with ERROR raises ``RadioCommandRejectedError``; its
    message names the setting, never the value.
    """
    warnings: list[str] = []
    if update.advert_location_source is not None:
        advert_loc_policy = 0 if update.advert_location_source == "off" else 1
        logger.info(
            "Setting advert location policy to %s",
            update.advert_location_source,
        )
        result = await mc.commands.set_advert_loc_policy(advert_loc_policy)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set advert location policy: {result.payload}"
            )

    if update.multi_acks_enabled is not None:
        multi_acks = 1 if update.multi_acks_enabled else 0
        logger.info("Setting multi ACKs to %d", multi_acks)
        result = await mc.commands.set_multi_acks(multi_acks)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(f"Failed to set multi ACKs: {result.payload}")

    if update.telemetry_mode_base is not None:
        logger.info("Setting telemetry_mode_base to %d", update.telemetry_mode_base)
        result = await mc.commands.set_telemetry_mode_base(update.telemetry_mode_base)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set telemetry mode (base): {result.payload}"
            )

    if update.telemetry_mode_loc is not None:
        logger.info("Setting telemetry_mode_loc to %d", update.telemetry_mode_loc)
        result = await mc.commands.set_telemetry_mode_loc(update.telemetry_mode_loc)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set telemetry mode (location): {result.payload}"
            )

    if update.telemetry_mode_env is not None:
        logger.info("Setting telemetry_mode_env to %d", update.telemetry_mode_env)
        result = await mc.commands.set_telemetry_mode_env(update.telemetry_mode_env)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(
                f"Failed to set telemetry mode (environment): {result.payload}"
            )

    if update.name is not None:
        logger.info("Setting radio name to %s", update.name)
        result = await mc.commands.set_name(update.name)
        if _rejected(result):
            raise RadioCommandRejectedError("The radio rejected the name", code="name_rejected")

    if update.lat is not None or update.lon is not None:
        current_info = mc.self_info
        lat = update.lat if update.lat is not None else current_info.get("adv_lat", 0.0)
        lon = update.lon if update.lon is not None else current_info.get("adv_lon", 0.0)
        logger.info("Setting radio coordinates to %f, %f", lat, lon)
        result = await mc.commands.set_coords(lat=lat, lon=lon)
        if _rejected(result):
            raise RadioCommandRejectedError(
                "The radio rejected the coordinates", code="coords_rejected"
            )

    if update.tx_power is not None:
        logger.info("Setting TX power to %d dBm", update.tx_power)
        result = await mc.commands.set_tx_power(val=update.tx_power)
        if _rejected(result):
            raise RadioCommandRejectedError(
                "The radio rejected the TX power", code="tx_power_rejected"
            )

    if update.radio is not None:
        logger.info(
            "Setting radio params: freq=%f MHz, bw=%f kHz, sf=%d, cr=%d, repeat=%s",
            update.radio.freq,
            update.radio.bw,
            update.radio.sf,
            update.radio.cr,
            client_repeat,
        )
        radio_kwargs: dict[str, Any] = {
            "freq": update.radio.freq,
            "bw": update.radio.bw,
            "sf": update.radio.sf,
            "cr": update.radio.cr,
        }
        if client_repeat is not None:
            radio_kwargs["repeat"] = 1 if client_repeat else 0
        result = await mc.commands.set_radio(**radio_kwargs)
        # Only a refusal from the radio means repeat is not allowed here: a
        # timeout says nothing about it, and resending without repeat would
        # report the firmware as dropping it.
        if client_repeat and _refused_by_firmware(result):
            logger.info("The radio refused repeat on these parameters: retrying without it")
            result = await mc.commands.set_radio(**{**radio_kwargs, "repeat": 0})
            if not _rejected(result):
                warnings.append("client_repeat_not_allowed")
        if _rejected(result):
            raise RadioCommandRejectedError(
                "The radio rejected the radio parameters", code="radio_params_rejected"
            )

    if update.path_hash_mode is not None:
        if not path_hash_mode_supported:
            raise PathHashModeUnsupportedError("Firmware does not support path hash mode setting")

        logger.info("Setting path hash mode to %d", update.path_hash_mode)
        result = await mc.commands.set_path_hash_mode(update.path_hash_mode)
        if result is not None and result.type == EventType.ERROR:
            raise RadioCommandRejectedError(f"Failed to set path hash mode: {result.payload}")
        set_path_hash_mode(update.path_hash_mode)

    await sync_radio_time_fn(mc)

    # Commands like set_name() write to flash but don't update cached self_info.
    # send_appstart() forces a fresh SELF_INFO so the response reflects changes.
    await mc.commands.send_appstart()
    return warnings


async def import_private_key_and_refresh_keystore(
    mc,
    key_bytes: bytes,
    *,
    export_and_store_private_key_fn: Callable[[Any], Awaitable[bool]],
) -> None:
    """Import a private key and refresh the in-memory keystore immediately.

    The key is registered as a literal secret for the whole exchange, so no form
    of it (hex, repr) reaches a log line, on top of the structural rules that
    already redact the import (0x18) and export (0x0E) frames.
    """
    with secret_scope(key_bytes):
        result = await mc.commands.import_private_key(key_bytes)
        if result.type == EventType.ERROR:
            raise RadioCommandRejectedError(f"Failed to import private key: {result.payload}")

        keystore_refreshed = await export_and_store_private_key_fn(mc)
        if not keystore_refreshed:
            logger.warning("Keystore refresh failed after import, retrying once")
            keystore_refreshed = await export_and_store_private_key_fn(mc)

    if not keystore_refreshed:
        raise KeystoreRefreshError(
            "Private key imported on radio, but server-side keystore refresh failed. "
            "Reconnect to apply the new key for DM decryption."
        )
