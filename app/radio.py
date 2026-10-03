"""RadioManager transport and session state management.

Provides backward-compatible single-radio access by subclassing RadioInstance
and registering the default instance into RadioRegistry.
"""

from __future__ import annotations

import asyncio

from meshcore import MeshCore
from serial.serialutil import SerialException
from serial.tools import list_ports

from app.config import settings
from app.keystore import clear_keys
from app.models import RadioTransportSnapshot
from app.services.radio_instance import (
    _SERIAL_PORT_ERROR_RE,
    LIFECYCLE_LOCK_TIMEOUT_SECONDS,
    MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS,
    RadioDisconnectedError,
    RadioInstance,
    RadioOperationBusyError,
    RadioOperationError,
    _extract_serial_port_from_error,
    _format_reconnect_failure,
    detect_serial_devices,
    find_radio_port,
    test_serial_device,
)
from app.services.radio_registry import radio_registry
from app.services.radio_transport import get_transport, is_tcp

__all__ = [
    "LIFECYCLE_LOCK_TIMEOUT_SECONDS",
    "MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS",
    "MeshCore",
    "RadioDisconnectedError",
    "RadioInstance",
    "RadioManager",
    "RadioOperationBusyError",
    "RadioOperationError",
    "RadioTransportSnapshot",
    "SerialException",
    "_SERIAL_PORT_ERROR_RE",
    "_extract_serial_port_from_error",
    "_format_reconnect_failure",
    "asyncio",
    "clear_keys",
    "detect_serial_devices",
    "find_radio_port",
    "get_transport",
    "is_tcp",
    "list_ports",
    "radio_manager",
    "radio_registry",
    "settings",
    "test_serial_device",
]


class RadioManager(RadioInstance):
    """Manages the MeshCore radio connection for single-radio and default workflows.

    Inherits from RadioInstance to provide complete domain behavior while maintaining
    full backward compatibility for existing single-radio code and tests.
    """

    def __init__(self, radio_id: str = "default", name: str = "Primary Radio") -> None:
        super().__init__(radio_id=radio_id, name=name)


radio_manager = RadioManager()
radio_manager._is_app_radio_singleton = True
radio_registry.register(radio_manager)
