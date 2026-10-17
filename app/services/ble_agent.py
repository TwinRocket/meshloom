# ruff: noqa: F821
# pyright: reportUndefinedVariable=false
# dbus-fast reads D-Bus signatures from string annotations ("o", "u", ...), which
# linters take for undefined names. Do not add `from __future__ import annotations`:
# it would turn them into quoted strings dbus-fast cannot parse.
"""One-shot BlueZ pairing agent (org.bluez.Agent1) for a MeshCore BLE radio.

Linux only: imported lazily by `app.services.ble_pairing` once dbus-fast is known
to be importable. The agent answers only for the device it was created for and
only with the PIN saved in the UI. It is registered on the same D-Bus connection
that calls `Device1.Pair()`, so BlueZ uses it for that request only; it is never
made the default agent. See `docs/dev/ble-pairing.md`.

Each D-Bus method delegates to a plain helper: the `@dbus_method` wrapper drops
return values when called from Python, so unit tests call the helpers.
"""

import logging
import re

from dbus_fast import DBusError
from dbus_fast.service import ServiceInterface, dbus_method

logger = logging.getLogger(__name__)

AGENT_INTERFACE = "org.bluez.Agent1"
REJECTED = "org.bluez.Error.Rejected"
CANCELED = "org.bluez.Error.Canceled"

_PASSKEY_RE = re.compile(r"^\d{1,6}$")
_PIN_CODE_RE = re.compile(r"^[\x20-\x7e]{1,16}$")


class PairingAgent(ServiceInterface):
    """Answers BlueZ's passkey request for one device with the saved PIN."""

    def __init__(self, target_device_path: str, pin: str) -> None:
        super().__init__(AGENT_INTERFACE)
        self.target_device_path = target_device_path
        self._pin = pin
        # Flags read by ble_pairing to classify a failure. Never the PIN.
        self.passkey_requested = False
        self.invalid_pin = False
        self.unexpected_method: str | None = None
        self.cancelled = False
        self.released = False
        self.foreign_requests = 0

    def __repr__(self) -> str:
        return (
            f"PairingAgent(target={self.target_device_path!r}, "
            f"passkey_requested={self.passkey_requested}, invalid_pin={self.invalid_pin}, "
            f"unexpected_method={self.unexpected_method!r}, cancelled={self.cancelled})"
        )

    # Plain helpers (unit-tested directly) -------------------------------------

    def _check_target(self, device: str, method: str) -> None:
        if device != self.target_device_path:
            self.foreign_requests += 1
            logger.debug("BLE agent: %s for another device (%s) rejected", method, device)
            raise DBusError(REJECTED, "Not the device being paired")
        logger.debug("BLE agent: %s for %s", method, device)

    def _passkey_for(self, device: str) -> int:
        self._check_target(device, "RequestPasskey")
        self.passkey_requested = True
        if not _PASSKEY_RE.match(self._pin):
            self.invalid_pin = True
            raise DBusError(REJECTED, "The saved PIN is not a 1-6 digit passkey")
        return int(self._pin)

    def _pin_code_for(self, device: str) -> str:
        self._check_target(device, "RequestPinCode")
        self.passkey_requested = True
        if not _PIN_CODE_RE.match(self._pin):
            self.invalid_pin = True
            raise DBusError(REJECTED, "The saved PIN is not a valid PIN code")
        return self._pin

    def _reject_method(self, device: str, method: str) -> None:
        self._check_target(device, method)
        # A random comparison value or a remote-initiated Just Works request is
        # never the static PIN of a MeshCore companion: refuse it.
        self.unexpected_method = method
        raise DBusError(REJECTED, f"{method} is not supported")

    def _authorize_service(self, device: str) -> None:
        self._check_target(device, "AuthorizeService")
        raise DBusError(REJECTED, "Service authorization is not supported")

    # org.bluez.Agent1 ----------------------------------------------------------

    @dbus_method()
    def Release(self) -> None:  # noqa: N802
        self.released = True
        logger.debug("BLE agent released")

    @dbus_method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: N802
        return self._pin_code_for(device)

    @dbus_method()
    def DisplayPinCode(self, device: "o", pincode: "s") -> None:  # noqa: N802
        # Never happens with KeyboardOnly. The value is not logged.
        self._check_target(device, "DisplayPinCode")
        raise DBusError(REJECTED, "DisplayPinCode is not supported")

    @dbus_method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: N802
        return self._passkey_for(device)

    @dbus_method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q") -> None:  # noqa: N802
        # Never happens with KeyboardOnly. Informational only: nothing to answer.
        logger.debug("BLE agent: DisplayPasskey for %s ignored", device)

    @dbus_method()
    def RequestConfirmation(self, device: "o", passkey: "u") -> None:  # noqa: N802
        self._reject_method(device, "RequestConfirmation")

    @dbus_method()
    def RequestAuthorization(self, device: "o") -> None:  # noqa: N802
        self._reject_method(device, "RequestAuthorization")

    @dbus_method()
    def AuthorizeService(self, device: "o", uuid: "s") -> None:  # noqa: N802
        self._authorize_service(device)

    @dbus_method()
    def Cancel(self) -> None:  # noqa: N802
        self.cancelled = True
        logger.debug("BLE agent: request cancelled by BlueZ")
