"""Wi-Fi settings of the connected radio (companion CLI, firmware v1.18+).

Validation, the 423 check and the HTTP error mapping only: the service
(``app/services/radio_wifi.py``) owns the CLI session. No route ever returns a
password, there is no ``set wifi.clear`` route, and the remote CLI over the
mesh is never used.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models import (
    RadioWifiChangeResult,
    RadioWifiCredentialsRequest,
    RadioWifiEnabledRequest,
    RadioWifiPromptDismissRequest,
    RadioWifiState,
)
from app.services import radio_wifi
from app.services.companion_cli import (
    CliError,
    CliRejectedError,
    CliTimeoutError,
    CliUnknownCommandError,
    CliUnsupportedError,
)
from app.services.radio_runtime import radio_runtime
from app.services.radio_wifi import WifiRequestError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/radio/wifi", tags=["radio"])

_MESSAGES: dict[str, str] = {
    "radio_not_connected": "Radio not connected",
    "wifi_unsupported": "This radio's firmware has no companion CLI or no Wi-Fi interface",
    "wifi_cli_timeout": "The radio did not answer the Wi-Fi command in time",
    "wifi_cli_rejected": "The radio refused the Wi-Fi command",
    "wifi_cli_error": "The radio gave an unexpected answer to the Wi-Fi command",
    "wifi_invalid_ssid": "Invalid Wi-Fi network name",
    "wifi_invalid_password": "Invalid Wi-Fi password",
    "wifi_store_unverified": (
        "The Wi-Fi credentials could not be verified; the previous values were restored"
    ),
    "wifi_store_partial": (
        "Storing the Wi-Fi credentials was interrupted; the radio may hold incomplete ones"
    ),
    "wifi_radio_changed": "The connected radio changed; this question was for another radio",
}


def _error(status: int, code: str) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": _MESSAGES.get(code, code)}
    )


def _map_error(exc: Exception) -> HTTPException:
    """Coded HTTP error for a service failure (never carries command text)."""
    if isinstance(exc, WifiRequestError):
        return _error(exc.status, exc.code)
    if isinstance(exc, (CliUnsupportedError, CliUnknownCommandError)):
        return _error(409, "wifi_unsupported")
    if isinstance(exc, CliTimeoutError):
        return _error(422, "wifi_cli_timeout")
    if isinstance(exc, CliRejectedError):
        return _error(422, "wifi_cli_rejected")
    if isinstance(exc, CliError):
        if exc.code == "setup_incomplete":
            return _error(423, "radio_not_connected")
        if exc.code in ("wifi_store_unverified", "wifi_store_partial"):
            return _error(422, exc.code)
        return _error(422, "wifi_cli_error")
    raise exc


def _require_connected() -> None:
    if not radio_runtime.is_connected or radio_runtime.meshcore is None:
        raise _error(423, "radio_not_connected")


async def _connected_key() -> str:
    _require_connected()
    if not radio_runtime.is_setup_complete:
        raise _error(423, "radio_not_connected")
    key = await radio_wifi.connected_radio_public_key()
    if key is None:
        raise _error(423, "radio_not_connected")
    return key


@router.get("", response_model=RadioWifiState)
async def get_radio_wifi(refresh: bool = False) -> RadioWifiState:
    """The cached Wi-Fi state (``check: "pending"`` until the first check).

    ``refresh=true``: a live read without the password when a check completed on
    this connection, else the full check (password read, prompt computed).
    """
    _require_connected()
    try:
        return await radio_wifi.get_wifi_state(refresh=refresh)
    except (CliError, WifiRequestError) as exc:
        raise _map_error(exc) from None


@router.put("/credentials", response_model=RadioWifiChangeResult)
async def put_radio_wifi_credentials(
    request: RadioWifiCredentialsRequest,
) -> RadioWifiChangeResult:
    """Store Wi-Fi credentials in the radio (verified). Reboots only with
    ``reboot: true`` and when the change needs one."""
    public_key = await _connected_key()
    try:
        return await radio_wifi.apply_wifi_credentials(request, public_key=public_key)
    except (CliError, WifiRequestError) as exc:
        raise _map_error(exc) from None


@router.put("/enabled", response_model=RadioWifiChangeResult)
async def put_radio_wifi_enabled(request: RadioWifiEnabledRequest) -> RadioWifiChangeResult:
    public_key = await _connected_key()
    try:
        return await radio_wifi.apply_wifi_enabled(request, public_key=public_key)
    except (CliError, WifiRequestError) as exc:
        raise _map_error(exc) from None


@router.post("/reboot", response_model=RadioWifiChangeResult)
async def post_radio_wifi_reboot() -> RadioWifiChangeResult:
    """Reboot the radio to apply stored Wi-Fi settings, as a planned reboot
    (``wifi_config``): never use the generic ``POST /radio/reboot`` for this."""
    await _connected_key()
    try:
        return await radio_wifi.reboot_for_wifi_config()
    except (CliError, WifiRequestError) as exc:
        raise _map_error(exc) from None


@router.post("/prompt/dismiss", response_model=RadioWifiState)
async def post_radio_wifi_prompt_dismiss(
    request: RadioWifiPromptDismissRequest,
) -> RadioWifiState:
    """Never ask again for this radio (409 ``wifi_radio_changed`` when the
    connected radio is not the one the question was shown for)."""
    await _connected_key()
    try:
        return await radio_wifi.dismiss_wifi_prompt(request.radio_public_key)
    except (CliError, WifiRequestError) as exc:
        raise _map_error(exc) from None
