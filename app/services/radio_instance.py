"""Domain abstraction representing a single MeshCore radio instance.

Encapsulates transport connection, locks, channel send slot cache, ingest gate,
ephemeral keystore, stats, and background sync task handles for a single radio.
"""

from __future__ import annotations

import asyncio
import glob
import inspect
import logging
import platform
import random
import re
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from meshcore import MeshCore
from serial.serialutil import SerialException
from serial.tools import list_ports as serial_list_ports

from app.config import settings
from app.decoder import derive_public_key
from app.models import RadioTransportSnapshot
from app.services.radio_transport import get_transport, is_tcp

logger = logging.getLogger(__name__)

MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS = 3
LIFECYCLE_LOCK_TIMEOUT_SECONDS = 20
MAX_NOISE_FLOOR_SAMPLES = 1500
REMOTE_BACKOFF_BASE_SECONDS = 2.0
REMOTE_BACKOFF_MAX_SECONDS = 60.0
REMOTE_BACKOFF_JITTER_RATIO = 0.20
_SERIAL_PORT_ERROR_RE = re.compile(r"could not open port (?P<port>.+?):")


class RadioOperationError(RuntimeError):
    """Base class for shared radio operation lock errors."""


class RadioOperationBusyError(RadioOperationError):
    """Raised when a non-blocking radio operation cannot acquire the lock."""


class RadioDisconnectedError(RadioOperationError):
    """Raised when the radio disconnects between pre-check and lock acquisition."""


def detect_serial_devices() -> list[str]:
    """Detect available serial devices, including Windows COM ports."""
    devices: list[str] = []
    seen: set[str] = set()
    resolved_paths: set[str] = set()

    def _add(device: str) -> None:
        if not device or device in seen:
            return
        try:
            resolved = str(Path(device).resolve())
        except OSError:
            resolved = device
        if resolved in resolved_paths:
            return
        seen.add(device)
        resolved_paths.add(resolved)
        devices.append(device)

    import sys

    radio_mod = sys.modules.get("app.radio")
    list_ports_mod = getattr(radio_mod, "list_ports", serial_list_ports)

    try:
        for info in list_ports_mod.comports():
            device = getattr(info, "device", None)
            if device:
                _add(device)
    except Exception:
        logger.debug("list_ports.comports() failed", exc_info=True)

    system = platform.system()
    if system == "Darwin":
        for pattern in (
            "/dev/cu.usb*",
            "/dev/cu.wchusbserial*",
            "/dev/cu.SLAB_USBtoUART*",
        ):
            for device in glob.glob(pattern):
                _add(device)
    else:
        by_id_path = Path("/dev/serial/by-id")
        if by_id_path.is_dir():
            for path in by_id_path.iterdir():
                _add(str(path))
        for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
            for device in glob.glob(pattern):
                _add(device)

    devices.sort()
    return devices


def _extract_serial_port_from_error(exc: Exception) -> str | None:
    """Best-effort extraction of a serial port path from a pyserial error."""
    message = str(exc)
    match = _SERIAL_PORT_ERROR_RE.search(message)
    if match:
        return match.group("port")
    return None


def _format_reconnect_failure(
    exc: Exception,
    *,
    snapshot: RadioTransportSnapshot | None = None,
    connection_info: str | None = None,
) -> tuple[str, str, bool]:
    """Return log message, frontend detail, and whether to log a traceback."""
    connection_type = None
    serial_port = ""
    if snapshot is not None and snapshot.configured:
        connection_type = snapshot.connection_type
        serial_port = snapshot.serial_port
    elif connection_info:
        if connection_info.startswith("Serial:"):
            connection_type = "serial"
            serial_port = connection_info.split(":", 1)[1].strip()
        elif connection_info.startswith("TCP:"):
            connection_type = "tcp"
        elif connection_info.startswith("BLE:"):
            connection_type = "ble"

    if connection_type == "serial":
        if isinstance(exc, RuntimeError) and str(exc).startswith("No MeshCore radio found"):
            message = (
                "Could not find a MeshCore radio on any serial port. "
                "Did the radio get disconnected or change serial ports?"
            )
            return (message, message, False)

        if isinstance(exc, SerialException):
            port = serial_port or _extract_serial_port_from_error(exc) or "the serial port"
            message = (
                f"Could not connect to serial port {port}. "
                "Did the radio get disconnected or change serial ports?"
            )
            return (message, message, False)

    return (f"Reconnection failed: {exc}", str(exc), True)


async def test_serial_device(port: str, baudrate: int, timeout: float = 3.0) -> bool:
    """Test if a MeshCore radio responds on the given serial port."""
    import sys

    radio_mod = sys.modules.get("app.radio")
    meshcore_cls = getattr(radio_mod, "MeshCore", MeshCore)

    mc = None
    try:
        logger.debug("Testing serial device %s", port)
        mc = await asyncio.wait_for(
            meshcore_cls.create_serial(port=port, baudrate=baudrate),
            timeout=timeout,
        )

        if mc.is_connected and mc.self_info:
            logger.debug("Device %s responded with valid self_info", port)
            return True

        return False
    except TimeoutError:
        logger.debug("Device %s timed out", port)
        return False
    except Exception as e:
        logger.debug("Device %s failed: %s", port, e)
        return False
    finally:
        if mc is not None:
            try:
                await mc.disconnect()
            except Exception:
                pass


async def find_radio_port(baudrate: int) -> str | None:
    """Find the first serial port with a responding MeshCore radio."""
    import sys

    radio_mod = sys.modules.get("app.radio")
    detect_fn = getattr(radio_mod, "detect_serial_devices", detect_serial_devices)
    test_fn = getattr(radio_mod, "test_serial_device", test_serial_device)

    devices = detect_fn()

    if not devices:
        logger.warning("No serial devices found")
        return None

    logger.info("Found %d serial device(s), testing for MeshCore radio...", len(devices))

    for device in devices:
        if await test_fn(device, baudrate):
            logger.info("Found MeshCore radio at %s", device)
            return device

    logger.warning("No MeshCore radio found on any serial device")
    return None


class RadioInstance:
    """Domain abstraction representing a single physical or virtual MeshCore radio instance."""

    def __init__(
        self,
        radio_id: str = "default",
        name: str = "Primary Radio",
        transport_snapshot: RadioTransportSnapshot | None = None,
    ) -> None:
        self.radio_id: str = radio_id
        self.name: str = name
        self._meshcore: MeshCore | None = None
        self._connection_info: str | None = None
        self._transport_snapshot: RadioTransportSnapshot | None = transport_snapshot
        self._connection_desired: bool = True
        self.auto_connect: bool = True
        self._last_error: str | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._last_connected: bool = False

        # Lazy loop-bound locks.
        # NEVER create asyncio.Lock in module scope or __init__ before the event loop runs.
        self._operation_lock: asyncio.Lock | None = None
        self._reconnect_lock: asyncio.Lock | None = None
        self._setup_lock: asyncio.Lock | None = None
        self._lifecycle_lock: asyncio.Lock | None = None

        self._setup_in_progress: bool = False
        self._setup_complete: bool = False
        self._frontend_reconnect_error_broadcasts: int = 0
        self.device_info_loaded: bool = False
        self.max_contacts: int | None = None
        self.device_model: str | None = None
        self.firmware_build: str | None = None
        self.firmware_version: str | None = None
        # Companion protocol version (FIRMWARE_VER_CODE). Gates version-dependent
        # host commands such as the mode-1 unscoped flood-scope frame (ver 12+).
        self.firmware_ver_code: int | None = None
        self.max_channels: int = 40
        self.path_hash_mode: int = 0
        self.path_hash_mode_supported: bool = False

        # Channel send slot cache
        self._channel_slot_by_key: OrderedDict[str, int] = OrderedDict()
        self._channel_key_by_slot: dict[int, str] = {}
        self._pending_message_channel_key_by_slot: dict[int, str] = {}

        # Ingest gate state
        self._ingest_allowed: bool = False
        self._session_generation: int = 0

        # In-memory keystore for direct messages
        self._private_key: bytes | None = None
        self._public_key: bytes | None = None

        # Local stats & noise floor deque
        self._noise_floor_samples: deque[tuple[int, int]] = deque(maxlen=MAX_NOISE_FLOOR_SAMPLES)
        self._latest_stats: dict[str, Any] = {}
        self._stats_task: asyncio.Task | None = None

        # Background sync task handles
        self._sync_task: asyncio.Task | None = None
        self._advert_task: asyncio.Task | None = None
        self._telemetry_collect_task: asyncio.Task | None = None
        self._message_poll_task: asyncio.Task | None = None
        self._contact_reconcile_task: asyncio.Task | None = None
        self._subscriptions: list[Any] = []

        # Connection backoff state for remote transports (TCP/BLE)
        self._reconnect_failures: int = 0
        self._next_reconnect_time: float = 0.0

        # Sync throttling/state variables
        self._polling_pause_count: int = 0
        self._clock_reboot_attempted: bool = False
        self._last_contact_sync: float = 0.0

    @property
    def _meshcore_cls(self):
        import sys

        radio_mod = sys.modules.get("app.radio")
        return getattr(radio_mod, "MeshCore", MeshCore)

    @property
    def _find_radio_port_fn(self):
        import sys

        radio_mod = sys.modules.get("app.radio")
        return getattr(radio_mod, "find_radio_port", find_radio_port)

    @property
    def _settings(self):
        import sys

        radio_mod = sys.modules.get("app.radio")
        return getattr(radio_mod, "settings", settings)

    # ── Operation Lock Management ────────────────────────────────────────────

    async def _acquire_operation_lock(
        self,
        name: str,
        *,
        blocking: bool,
    ) -> None:
        """Acquire the shared radio operation lock."""
        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()

        try:
            if not blocking:
                if self._operation_lock.locked():
                    raise RadioOperationBusyError(f"Radio is busy (operation: {name})")
                await self._operation_lock.acquire()
            else:
                await self._operation_lock.acquire()
        except RuntimeError as exc:
            # pytest-asyncio gives each test its own loop. A leftover lock from
            # another test in the same xdist worker is bound to a dead loop.
            if "different event loop" not in str(exc):
                raise
            logger.debug(
                "[radio:%s] Rebound radio operation lock to current event loop (%s)",
                self.radio_id,
                name,
            )
            self._operation_lock = asyncio.Lock()
            await self._operation_lock.acquire()

        logger.debug("[radio:%s] Acquired radio operation lock (%s)", self.radio_id, name)

    def _release_operation_lock(self, name: str) -> None:
        """Release the shared radio operation lock."""
        if self._operation_lock and self._operation_lock.locked():
            self._operation_lock.release()
            logger.debug("[radio:%s] Released radio operation lock (%s)", self.radio_id, name)
        else:
            logger.error(
                "[radio:%s] Attempted to release unlocked radio operation lock (%s)",
                self.radio_id,
                name,
            )

    def _reset_connected_runtime_state(self) -> None:
        """Clear cached runtime state after a transport teardown completes."""
        self._setup_complete = False
        self.device_info_loaded = False
        self.max_contacts = None
        self.device_model = None
        self.firmware_build = None
        self.firmware_version = None
        self.firmware_ver_code = None
        self.max_channels = 40
        self.path_hash_mode = 0
        self.path_hash_mode_supported = False
        self.reset_channel_send_cache()
        self.clear_pending_message_channel_slots()

    @asynccontextmanager
    async def radio_operation(
        self,
        name: str,
        *,
        pause_polling: bool = False,
        suspend_auto_fetch: bool = False,
        blocking: bool = True,
    ):
        """Acquire shared radio lock and optionally pause polling / auto-fetch.

        After acquiring the lock, resolves the current MeshCore instance and
        yields it. Callers get a fresh reference via ``async with ... as mc:``,
        avoiding stale-reference bugs when a reconnect swaps ``_meshcore``
        between the pre-check and the lock acquisition.
        """
        await self._acquire_operation_lock(name, blocking=blocking)

        mc = self._meshcore
        if mc is None:
            self._release_operation_lock(name)
            raise RadioDisconnectedError("Radio disconnected")

        poll_context = nullcontext()
        if pause_polling:
            from app.radio_sync import pause_polling as pause_polling_context

            poll_context = pause_polling_context()

        auto_fetch_paused = False

        try:
            async with poll_context:
                if suspend_auto_fetch:
                    await mc.stop_auto_message_fetching()
                    auto_fetch_paused = True
                yield mc
        finally:
            try:
                if auto_fetch_paused:
                    try:
                        await mc.start_auto_message_fetching()
                    except Exception as e:
                        logger.warning(
                            "[radio:%s] Failed to restart auto message fetching (%s): %s",
                            self.radio_id,
                            name,
                            e,
                        )
            finally:
                self._release_operation_lock(name)

    async def post_connect_setup(self) -> None:
        """Run shared post-connection orchestration after transport setup succeeds."""
        from app.services.radio_lifecycle import run_post_connect_setup

        await run_post_connect_setup(self)

    # ── Channel Send Slot Cache ──────────────────────────────────────────────

    def reset_channel_send_cache(self) -> None:
        """Forget any session-local channel-slot reuse state."""
        self._channel_slot_by_key.clear()
        self._channel_key_by_slot.clear()

    def remember_pending_message_channel_slot(self, channel_key: str, slot: int) -> None:
        """Remember a channel key for later queued-message recovery."""
        self._pending_message_channel_key_by_slot[slot] = channel_key.upper()

    def get_pending_message_channel_key(self, slot: int) -> str | None:
        """Return the last remembered channel key for a radio slot."""
        return self._pending_message_channel_key_by_slot.get(slot)

    def clear_pending_message_channel_slots(self) -> None:
        """Drop any queued-message recovery slot metadata."""
        self._pending_message_channel_key_by_slot.clear()

    def channel_slot_reuse_enabled(self) -> bool:
        """Return whether this transport can safely reuse cached channel slots."""
        if self._settings.force_channel_slot_reconfigure:
            return False
        if self._connection_info:
            return not self._connection_info.startswith("TCP:")
        return not is_tcp(self._transport_snapshot)

    def get_channel_send_cache_capacity(self) -> int:
        """Return the app-managed channel cache capacity for the current session."""
        try:
            return max(1, int(self.max_channels))
        except (TypeError, ValueError):
            return 1

    def get_cached_channel_slot(self, channel_key: str) -> int | None:
        """Return the cached radio slot for a channel key, if present."""
        return self._channel_slot_by_key.get(channel_key.upper())

    def plan_channel_send_slot(
        self,
        channel_key: str,
        *,
        preferred_slot: int = 0,
    ) -> tuple[int, bool, str | None]:
        """Choose a radio slot for a channel send.

        Returns `(slot, needs_configure, evicted_channel_key)`.
        """
        if not self.channel_slot_reuse_enabled():
            return preferred_slot, True, None

        normalized_key = channel_key.upper()
        cached_slot = self._channel_slot_by_key.get(normalized_key)
        if cached_slot is not None:
            return cached_slot, False, None

        capacity = self.get_channel_send_cache_capacity()
        if len(self._channel_slot_by_key) < capacity:
            slot = self._find_first_free_channel_slot(capacity, preferred_slot)
            return slot, True, None

        evicted_key, slot = next(iter(self._channel_slot_by_key.items()))
        return slot, True, evicted_key

    def note_channel_slot_loaded(self, channel_key: str, slot: int) -> None:
        """Record that a channel is now resident in the given radio slot."""
        if not self.channel_slot_reuse_enabled():
            return

        normalized_key = channel_key.upper()
        previous_slot = self._channel_slot_by_key.pop(normalized_key, None)
        if previous_slot is not None and previous_slot != slot:
            self._channel_key_by_slot.pop(previous_slot, None)

        displaced_key = self._channel_key_by_slot.get(slot)
        if displaced_key is not None and displaced_key != normalized_key:
            self._channel_slot_by_key.pop(displaced_key, None)

        self._channel_key_by_slot[slot] = normalized_key
        self._channel_slot_by_key[normalized_key] = slot

    def note_channel_slot_used(self, channel_key: str) -> None:
        """Refresh LRU order for a previously loaded channel slot."""
        if not self.channel_slot_reuse_enabled():
            return

        normalized_key = channel_key.upper()
        slot = self._channel_slot_by_key.get(normalized_key)
        if slot is None:
            return
        self._channel_slot_by_key.move_to_end(normalized_key)
        self._channel_key_by_slot[slot] = normalized_key

    def invalidate_cached_channel_slot(self, channel_key: str) -> None:
        """Drop any cached slot assignment for a channel key."""
        normalized_key = channel_key.upper()
        slot = self._channel_slot_by_key.pop(normalized_key, None)
        if slot is None:
            return
        if self._channel_key_by_slot.get(slot) == normalized_key:
            self._channel_key_by_slot.pop(slot, None)

    def get_channel_send_cache_snapshot(self) -> list[tuple[str, int]]:
        """Return the current channel send cache contents in LRU order."""
        return list(self._channel_slot_by_key.items())

    def _find_first_free_channel_slot(self, capacity: int, preferred_slot: int) -> int:
        """Pick the first unclaimed app-managed slot, preferring the requested slot."""
        if preferred_slot < capacity and preferred_slot not in self._channel_key_by_slot:
            return preferred_slot

        for slot in range(capacity):
            if slot not in self._channel_key_by_slot:
                return slot

        return preferred_slot

    # ── Status Properties ────────────────────────────────────────────────────

    @property
    def meshcore(self) -> MeshCore | None:
        return self._meshcore

    @property
    def connection_info(self) -> str | None:
        return self._connection_info

    @property
    def is_connected(self) -> bool:
        return self._meshcore is not None and self._meshcore.is_connected

    @property
    def is_reconnecting(self) -> bool:
        return self._reconnect_lock is not None and self._reconnect_lock.locked()

    @property
    def is_setup_in_progress(self) -> bool:
        return self._setup_in_progress

    @property
    def is_setup_complete(self) -> bool:
        return self._setup_complete

    @property
    def connection_desired(self) -> bool:
        return self._connection_desired

    @connection_desired.setter
    def connection_desired(self, value: bool) -> None:
        self._connection_desired = bool(value)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @last_error.setter
    def last_error(self, value: str | None) -> None:
        self._last_error = value

    def resume_connection(self) -> None:
        """Allow connection monitor and manual reconnects to establish transport again."""
        self._connection_desired = True
        self._reset_reconnect_backoff()

    @property
    def is_remote(self) -> bool:
        """Return True if this radio is configured with a remote transport (TCP or BLE)."""
        snapshot = self._transport_snapshot
        if snapshot is not None and snapshot.transport in ("tcp", "ble"):
            return True
        return bool(
            self._connection_info
            and any(proto in self._connection_info for proto in ("TCP:", "BLE:"))
        )

    def compute_reconnect_delay(self) -> float:
        """Compute reconnection delay with exponential backoff and +/- 20% jitter.

        Exponential backoff: base * (2 ** min(failures, 5)), capped at max_seconds.
        Jitter: uniformly distributed within +/- 20% of the computed backoff.
        Non-remote (serial) connections return 0.0 (no backoff).
        """
        if not self.is_remote:
            return 0.0

        failures = max(0, self._reconnect_failures)
        raw_backoff = min(
            REMOTE_BACKOFF_BASE_SECONDS * (2 ** min(failures, 5)),
            REMOTE_BACKOFF_MAX_SECONDS,
        )
        jitter = random.uniform(-REMOTE_BACKOFF_JITTER_RATIO, REMOTE_BACKOFF_JITTER_RATIO)
        return max(1.0, raw_backoff * (1.0 + jitter))

    def can_attempt_reconnect(self) -> bool:
        """Return True if the reconnection backoff cooldown window has elapsed."""
        if not self._connection_desired:
            return False
        if not self.is_remote:
            return True
        return time.monotonic() >= self._next_reconnect_time

    def _record_reconnect_failure(self) -> float:
        """Record a failed reconnection attempt and update the backoff timer."""
        delay = self.compute_reconnect_delay()
        self._reconnect_failures += 1
        self._next_reconnect_time = time.monotonic() + delay
        if self.is_remote:
            logger.info(
                "[radio:%s] Connection backoff: waiting %.1fs before next attempt (failure #%d)",
                self.radio_id,
                delay,
                self._reconnect_failures,
            )
        return delay

    def _reset_reconnect_backoff(self) -> None:
        """Reset the reconnection backoff tracking after a successful connection."""
        self._reconnect_failures = 0
        self._next_reconnect_time = 0.0

    async def pause_connection(self) -> None:
        """Stop automatic reconnect attempts and tear down any current transport."""
        self._connection_desired = False
        self._last_connected = False
        await self.disconnect()

    def _reset_reconnect_error_broadcasts(self) -> None:
        self._frontend_reconnect_error_broadcasts = 0

    def _broadcast_reconnect_error_if_needed(self, details: str) -> None:
        from app.websocket import broadcast_error

        self._frontend_reconnect_error_broadcasts += 1
        if self._frontend_reconnect_error_broadcasts > MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS:
            return

        if self._frontend_reconnect_error_broadcasts == MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS:
            details = f"{details} Further reconnect failures will be logged only until a connection succeeds."

        broadcast_error("Reconnection failed", details, code="reconnection_failed")

    def _note_library_transport_lost(self) -> None:
        """Close ingest as soon as meshcore_py detects a transport drop."""
        self.deny_ingest()
        self._setup_complete = False

    def _install_library_reconnect_gate(self, mc: MeshCore) -> None:
        """Deny ingest on the library disconnect callback, not DISCONNECTED."""
        connection_manager = getattr(mc, "connection_manager", None)
        if connection_manager is None or getattr(
            connection_manager, "_meshloom_disconnect_gated", False
        ):
            return

        original = connection_manager.handle_disconnect

        async def _gated_handle_disconnect(reason: str = "unknown"):
            self._note_library_transport_lost()
            return await original(reason)

        connection_manager.handle_disconnect = _gated_handle_disconnect
        connection_manager._meshloom_disconnect_gated = True
        transport_cx = getattr(connection_manager, "connection", None)
        if transport_cx is not None and hasattr(transport_cx, "set_disconnect_callback"):
            transport_cx.set_disconnect_callback(connection_manager.handle_disconnect)

    async def _disable_meshcore_auto_reconnect(self, mc: MeshCore) -> None:
        """Disable library-managed reconnects so manual teardown fully releases transport."""
        connection_manager = getattr(mc, "connection_manager", None)
        if connection_manager is None:
            return

        if hasattr(connection_manager, "auto_reconnect"):
            connection_manager.auto_reconnect = False

        reconnect_task = getattr(connection_manager, "_reconnect_task", None)
        if reconnect_task is None or not isinstance(reconnect_task, asyncio.Task | asyncio.Future):
            return

        reconnect_task.cancel()
        try:
            await reconnect_task
        except asyncio.CancelledError:
            pass
        finally:
            connection_manager._reconnect_task = None

    async def _load_transport(self) -> RadioTransportSnapshot:
        """Read and cache the UX-owned transport snapshot."""
        if self._transport_snapshot is not None and self.radio_id != "default":
            return self._transport_snapshot
        import sys

        radio_mod = sys.modules.get("app.radio")
        get_transport_fn = getattr(radio_mod, "get_transport", get_transport)
        snapshot = await get_transport_fn()
        self._transport_snapshot = snapshot
        return snapshot

    # ── Connection Lifecycle ─────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to the radio using the configured transport."""
        if self._meshcore is not None:
            await self.disconnect()

        snapshot = await self._load_transport()
        if snapshot.transport is None:
            err = "Radio transport is not configured. Configure it in Meshloom before connecting."
            self._last_error = err
            raise RuntimeError(err)
        try:
            if snapshot.transport == "tcp":
                await self._connect_tcp()
            elif snapshot.transport == "ble":
                await self._connect_ble()
            else:
                await self._connect_serial()
            self._last_error = None
        except Exception as exc:
            self._last_error = str(exc)
            raise
        if self._meshcore is not None:
            self._install_library_reconnect_gate(self._meshcore)

    async def _connect_serial(self) -> None:
        """Connect to the radio over serial."""
        snapshot = await self._load_transport()
        if snapshot.transport is None:
            raise RuntimeError(
                "Radio transport is not configured. Configure it in Meshloom before connecting."
            )
        port = snapshot.serial_port
        baudrate = snapshot.serial_baudrate

        if not port:
            logger.info("[radio:%s] No serial port specified, auto-detecting...", self.radio_id)
            port = await self._find_radio_port_fn(baudrate)
            if not port:
                raise RuntimeError("No MeshCore radio found. Please specify a serial port.")

        logger.debug(
            "[radio:%s] Connecting to radio at %s (baud %d)", self.radio_id, port, baudrate
        )
        mc = await self._meshcore_cls.create_serial(
            port=port,
            baudrate=baudrate,
            auto_reconnect=True,
            max_reconnect_attempts=10,
        )
        if mc is None:
            raise RuntimeError(f"Failed to open serial connection to {port}")
        self._meshcore = mc
        self._connection_info = f"Serial: {port}"
        self._last_connected = True
        self._setup_complete = False
        self._reset_reconnect_backoff()
        logger.debug("[radio:%s] Serial connection established", self.radio_id)

    async def _connect_tcp(self) -> None:
        """Connect to the radio over TCP."""
        snapshot = await self._load_transport()
        if snapshot.transport is None:
            raise RuntimeError(
                "Radio transport is not configured. Configure it in Meshloom before connecting."
            )
        host = snapshot.tcp_host
        port = snapshot.tcp_port

        logger.debug("[radio:%s] Connecting to radio at %s:%d (TCP)", self.radio_id, host, port)
        from app.radio_proxy.manager import radio_proxy_manager

        if radio_proxy_manager.would_loop_transport(host, port):
            raise RuntimeError("TCP radio target points at this Meshloom radio proxy")
        mc = await self._meshcore_cls.create_tcp(
            host=host,
            port=port,
            auto_reconnect=True,
            max_reconnect_attempts=10,
        )
        if mc is None:
            raise RuntimeError(f"Failed to open TCP connection to {host}:{port}")
        self._meshcore = mc
        self._connection_info = f"TCP: {host}:{port}"
        self._last_connected = True
        self._setup_complete = False
        self._reset_reconnect_backoff()
        self._tune_tcp_socket(mc)
        logger.debug("[radio:%s] TCP connection established", self.radio_id)

    def _tune_tcp_socket(self, mc: Any) -> None:
        """Enable and tune TCP keepalive to detect half-open sockets on flaky cellular/VPN links."""
        try:
            import socket

            connection_mgr = getattr(mc, "connection_manager", None)
            connection_obj: Any = (
                getattr(connection_mgr, "connection", None) if connection_mgr else None
            )
            sock = None
            if connection_obj is not None:
                if hasattr(connection_obj, "get_extra_info"):
                    sock = connection_obj.get_extra_info("socket")
                else:
                    transport: Any = getattr(connection_obj, "_transport", None)
                    writer: Any = getattr(connection_obj, "_writer", None)
                    if transport is not None and hasattr(transport, "get_extra_info"):
                        sock = transport.get_extra_info("socket")
                    elif writer is not None and hasattr(writer, "get_extra_info"):
                        sock = writer.get_extra_info("socket")
                    elif hasattr(connection_obj, "socket"):
                        sock = getattr(connection_obj, "socket", None)

            if sock is not None:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, "TCP_KEEPIDLE"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                    sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 15000, 5000))
                logger.debug(
                    "[radio:%s] TCP keepalive configured (15s idle, 5s interval, 3 probes)",
                    self.radio_id,
                )
        except Exception as e:
            logger.debug("[radio:%s] Could not tune TCP keepalive: %s", self.radio_id, e)

    async def _connect_ble(self) -> None:
        """Connect to the radio over BLE."""
        snapshot = await self._load_transport()
        if snapshot.transport is None:
            raise RuntimeError(
                "Radio transport is not configured. Configure it in Meshloom before connecting."
            )
        address = snapshot.ble_address
        pin = snapshot.ble_pin

        logger.debug("[radio:%s] Connecting to radio at %s (BLE)", self.radio_id, address)
        mc = await self._meshcore_cls.create_ble(
            address=address,
            pin=pin,
            auto_reconnect=True,
            max_reconnect_attempts=15,
        )
        if mc is None:
            raise RuntimeError(f"Failed to open BLE connection to {address}")
        self._meshcore = mc
        self._connection_info = f"BLE: {address}"
        self._last_connected = True
        self._setup_complete = False
        self._reset_reconnect_backoff()
        logger.debug("[radio:%s] BLE connection established", self.radio_id)

    async def disconnect(self) -> None:
        """Disconnect from the radio."""
        from app.radio_sync import stop_background_contact_reconciliation

        self.clear_keys()
        self._reset_reconnect_error_broadcasts()
        if self._meshcore is None:
            return

        await stop_background_contact_reconciliation()
        from app.event_handlers import unregister_event_handlers

        self.deny_ingest()
        unregister_event_handlers(self)
        await self._acquire_operation_lock("disconnect", blocking=True)
        try:
            mc = self._meshcore
            if mc is None:
                return

            logger.debug("[radio:%s] Disconnecting from radio", self.radio_id)
            await self._disable_meshcore_auto_reconnect(mc)
            try:
                disc = mc.disconnect()
                if inspect.isawaitable(disc):
                    await disc
            finally:
                await self._disable_meshcore_auto_reconnect(mc)

            if self._meshcore is mc:
                self._meshcore = None
            self._reset_connected_runtime_state()
            logger.debug("[radio:%s] Radio disconnected", self.radio_id)
        finally:
            self._release_operation_lock("disconnect")

    async def reconnect(
        self,
        *,
        broadcast_on_success: bool = True,
        force: bool = False,
    ) -> bool:
        """Attempt to reconnect to the radio.

        Returns True if reconnection was successful, False otherwise.
        Uses a lock to prevent concurrent reconnection attempts.
        """
        from app.websocket import broadcast_health

        if not self._connection_desired:
            logger.info(
                "[radio:%s] Reconnect skipped because connection is paused by operator",
                self.radio_id,
            )
            return False

        if not force and not self.can_attempt_reconnect():
            remaining = max(0.0, self._next_reconnect_time - time.monotonic())
            logger.debug(
                "[radio:%s] Reconnect skipped; in backoff window (%.1fs remaining)",
                self.radio_id,
                remaining,
            )
            return False

        # Lazily initialize lock (can't create in __init__ before event loop exists)
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()

        try:
            await self._reconnect_lock.acquire()
        except RuntimeError as exc:
            if "different event loop" not in str(exc):
                raise
            logger.debug("[radio:%s] Rebound reconnect lock to current event loop", self.radio_id)
            self._reconnect_lock = asyncio.Lock()
            await self._reconnect_lock.acquire()

        try:
            if not self._connection_desired:
                logger.info(
                    "[radio:%s] Reconnect skipped because connection is paused by operator",
                    self.radio_id,
                )
                return False

            if self.is_connected:
                logger.debug(
                    "[radio:%s] Already connected after acquiring lock, skipping reconnect",
                    self.radio_id,
                )
                self._reset_reconnect_backoff()
                return True

            logger.info("[radio:%s] Attempting to reconnect to radio...", self.radio_id)

            try:
                if self._meshcore is not None:
                    try:
                        await self.disconnect()
                    except Exception:
                        pass

                await self.connect()

                if not self._connection_desired:
                    logger.info(
                        "[radio:%s] Reconnect completed after pause request; disconnecting transport",
                        self.radio_id,
                    )
                    await self.disconnect()
                    return False

                if self.is_connected:
                    logger.info(
                        "[radio:%s] Radio reconnected successfully at %s",
                        self.radio_id,
                        self._connection_info,
                    )
                    self._reset_reconnect_error_broadcasts()
                    self._reset_reconnect_backoff()
                    self._last_error = None
                    if broadcast_on_success:
                        broadcast_health(True, self._connection_info)
                    return True
                else:
                    logger.warning(
                        "[radio:%s] Reconnection failed: not connected after connect()",
                        self.radio_id,
                    )
                    self._record_reconnect_failure()
                    self._last_error = "Not connected after connect()"
                    return False

            except Exception as e:
                self._record_reconnect_failure()
                log_message, frontend_detail, include_traceback = _format_reconnect_failure(
                    e,
                    snapshot=self._transport_snapshot,
                    connection_info=self._connection_info,
                )
                logger.warning(
                    "[radio:%s] %s", self.radio_id, log_message, exc_info=include_traceback
                )
                self._last_error = frontend_detail
                self._broadcast_reconnect_error_if_needed(frontend_detail)
                return False
        finally:
            if self._reconnect_lock.locked():
                self._reconnect_lock.release()

    @asynccontextmanager
    async def setup_lock_context(self):
        """Acquire post-connect setup lock with event loop rebind safety."""
        if self._setup_lock is None:
            self._setup_lock = asyncio.Lock()
        try:
            await self._setup_lock.acquire()
        except RuntimeError as exc:
            if "different event loop" not in str(exc):
                raise
            logger.debug("Rebound setup lock to current event loop (%s)", self.radio_id)
            self._setup_lock = asyncio.Lock()
            await self._setup_lock.acquire()
        try:
            yield
        finally:
            if self._setup_lock and self._setup_lock.locked():
                self._setup_lock.release()

    @asynccontextmanager
    async def lifecycle_transition(self, name: str):
        """Serialize transport/identity changes outside radio_operation/_setup_lock."""
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        try:
            await asyncio.wait_for(
                self._lifecycle_lock.acquire(),
                timeout=LIFECYCLE_LOCK_TIMEOUT_SECONDS,
            )
        except RuntimeError as exc:
            if "different event loop" not in str(exc):
                raise
            logger.debug("Rebound lifecycle lock to current event loop (%s)", name)
            self._lifecycle_lock = asyncio.Lock()
            await asyncio.wait_for(
                self._lifecycle_lock.acquire(),
                timeout=LIFECYCLE_LOCK_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise RadioOperationBusyError(f"Radio lifecycle is busy ({name})") from exc

        await self.stop_connection_monitor()
        self.deny_ingest()
        try:
            yield
        finally:
            try:
                await self.start_connection_monitor()
            finally:
                if self._lifecycle_lock and self._lifecycle_lock.locked():
                    self._lifecycle_lock.release()

    async def start_connection_monitor(self) -> None:
        """Start background task to monitor connection and auto-reconnect."""
        from app.services.radio_lifecycle import connection_monitor_loop

        if self._reconnect_task is not None:
            return

        self._reconnect_task = asyncio.create_task(connection_monitor_loop(self))
        logger.info("[radio:%s] Radio connection monitor started", self.radio_id)

    async def stop_connection_monitor(self) -> None:
        """Stop the connection monitor task."""
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(
                    "[radio:%s] Exception while stopping connection monitor: %s", self.radio_id, e
                )
            self._reconnect_task = None
            logger.info("[radio:%s] Radio connection monitor stopped", self.radio_id)

    def require_connected(self) -> MeshCore:
        """Return MeshCore when available, mirroring existing HTTP semantics."""
        if self.is_setup_in_progress:
            raise HTTPException(
                status_code=423,
                detail={"code": "radio_initializing", "message": "Radio is initializing"},
            )
        if not self.is_connected:
            raise HTTPException(
                status_code=423,
                detail={"code": "radio_not_connected", "message": "Radio not connected"},
            )
        mc = self.meshcore
        if mc is None:
            raise HTTPException(
                status_code=423,
                detail={"code": "radio_not_connected", "message": "Radio not connected"},
            )
        return mc

    # ── Ingest Gate Helpers ───────────────────────────────────────────────────

    def begin_connection_session(self) -> int:
        """Close ingest and bump the session id. Return the new generation."""
        self._ingest_allowed = False
        self._session_generation += 1
        logger.debug(
            "[radio:%s] Ingest closed (session %s)",
            self.radio_id,
            self._session_generation,
        )
        if self.radio_id == "default":
            import app.services.radio_ingest_gate as gate_mod

            gate_mod._ingest_allowed = False
            gate_mod._session_generation = self._session_generation
        return self._session_generation

    def allow_ingest(self, session: int | None = None) -> None:
        """Allow ingest for this radio instance (optionally verifying session)."""
        if session is not None and session != self._session_generation:
            return
        self._ingest_allowed = True
        logger.debug(
            "[radio:%s] Ingest allowed (session %s)",
            self.radio_id,
            self._session_generation,
        )
        if self.radio_id == "default":
            import app.services.radio_ingest_gate as gate_mod

            gate_mod._ingest_allowed = True

    def deny_ingest(self) -> None:
        """Deny ingest for this radio instance."""
        self._ingest_allowed = False
        logger.debug("[radio:%s] Ingest denied", self.radio_id)
        if self.radio_id == "default":
            import app.services.radio_ingest_gate as gate_mod

            gate_mod._ingest_allowed = False

    def ingest_allowed(self) -> bool:
        """Return whether ingest is allowed for this radio instance."""
        return self._ingest_allowed

    @property
    def current_session(self) -> int:
        """Return current session generation."""
        return self._session_generation

    # ── In-Memory Keystore Helpers ───────────────────────────────────────────

    def set_private_key(self, key: bytes) -> None:
        """Store the private key in memory and derive the public key."""
        if len(key) != 64:
            raise ValueError(f"Private key must be 64 bytes, got {len(key)}")
        self._private_key = key
        self._public_key = derive_public_key(key)
        logger.info(
            "[radio:%s] Private key stored (public key: %s...)",
            self.radio_id,
            self._public_key.hex()[:12],
        )
        if self.radio_id == "default":
            import app.keystore as ks

            ks._private_key = key
            ks._public_key = self._public_key

    def get_private_key(self) -> bytes | None:
        """Get the stored private key."""
        return self._private_key

    def get_public_key(self) -> bytes | None:
        """Get the derived public key."""
        return self._public_key

    def has_private_key(self) -> bool:
        """Check if a private key is stored."""
        return self._private_key is not None

    def clear_keys(self) -> None:
        """Clear any stored private/public key material from memory."""
        had_key = self._private_key is not None or self._public_key is not None
        self._private_key = None
        self._public_key = None
        if had_key:
            logger.info("[radio:%s] Cleared in-memory keystore", self.radio_id)
        if self.radio_id == "default":
            from app.keystore import clear_keys as ks_clear_keys

            ks_clear_keys()

    # ── Local Stats & Noise Floor ────────────────────────────────────────────

    def get_noise_floor_history(self) -> dict:
        """Return the current 24-hour in-memory noise floor history snapshot."""
        now = int(time.time())
        cutoff = now - (24 * 60 * 60)

        samples = [
            {"timestamp": timestamp, "noise_floor_dbm": noise_floor_dbm}
            for timestamp, noise_floor_dbm in self._noise_floor_samples
            if timestamp >= cutoff
        ]

        latest = samples[-1] if samples else None
        oldest_timestamp = samples[0]["timestamp"] if samples else None
        coverage_seconds = 0 if oldest_timestamp is None else max(0, now - oldest_timestamp)

        return {
            "sample_interval_seconds": 60,
            "coverage_seconds": coverage_seconds,
            "latest_noise_floor_dbm": latest["noise_floor_dbm"] if latest else None,
            "latest_timestamp": latest["timestamp"] if latest else None,
            "samples": samples,
        }

    def get_latest_radio_stats(self) -> dict[str, Any]:
        """Return the most recent radio stats snapshot."""
        return dict(self._latest_stats)

    def clear_latest_radio_stats(self) -> None:
        """Drop in-memory local-radio samples."""
        self._latest_stats = {}
        self._noise_floor_samples.clear()

    # ── Background Sync Task Helpers ─────────────────────────────────────────

    def is_polling_paused(self) -> bool:
        """Check if polling is currently paused for this radio instance."""
        return self._polling_pause_count > 0

    def reset_contact_sync_throttle(self) -> None:
        """Clear the contact-sync throttle timestamp."""
        self._last_contact_sync = 0.0

    async def stop_sync_tasks(self) -> None:
        """Cancel and clean up all background sync tasks for this instance."""
        tasks = [
            self._sync_task,
            self._advert_task,
            self._telemetry_collect_task,
            self._message_poll_task,
            self._contact_reconcile_task,
        ]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._sync_task = None
        self._advert_task = None
        self._telemetry_collect_task = None
        self._message_poll_task = None
        self._contact_reconcile_task = None
