import asyncio
import glob
import logging
import platform
import re
from collections import OrderedDict
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path

from meshcore import MeshCore
from serial.serialutil import SerialException
from serial.tools import list_ports

from app.config import settings
from app.keystore import clear_keys
from app.models import RadioTransportSnapshot
from app.services.radio_transport import get_transport, is_tcp

logger = logging.getLogger(__name__)
MAX_FRONTEND_RECONNECT_ERROR_BROADCASTS = 3
LIFECYCLE_LOCK_TIMEOUT_SECONDS = 20
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

    try:
        for info in list_ports.comports():
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
    mc = None
    try:
        logger.debug("Testing serial device %s", port)
        mc = await asyncio.wait_for(
            MeshCore.create_serial(port=port, baudrate=baudrate),
            timeout=timeout,
        )

        # Check if we got valid self_info (indicates successful communication)
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
    devices = detect_serial_devices()

    if not devices:
        logger.warning("No serial devices found")
        return None

    logger.info("Found %d serial device(s), testing for MeshCore radio...", len(devices))

    for device in devices:
        if await test_serial_device(device, baudrate):
            logger.info("Found MeshCore radio at %s", device)
            return device

    logger.warning("No MeshCore radio found on any serial device")
    return None


class RadioManager:
    """Manages the MeshCore radio connection."""

    def __init__(self):
        self._meshcore: MeshCore | None = None
        self._connection_info: str | None = None
        self._transport_snapshot: RadioTransportSnapshot | None = None
        self._connection_desired: bool = True
        self._reconnect_task: asyncio.Task | None = None
        self._last_connected: bool = False
        self._reconnect_lock: asyncio.Lock | None = None
        self._operation_lock: asyncio.Lock | None = None
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
        self._channel_slot_by_key: OrderedDict[str, int] = OrderedDict()
        self._channel_key_by_slot: dict[int, str] = {}
        self._pending_message_channel_key_by_slot: dict[int, str] = {}

    async def _acquire_operation_lock(
        self,
        name: str,
        *,
        blocking: bool,
    ) -> None:
        """Acquire the shared radio operation lock."""

        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()

        if not blocking:
            if self._operation_lock.locked():
                raise RadioOperationBusyError(f"Radio is busy (operation: {name})")
            # In single-threaded asyncio the lock cannot be acquired between the
            # check above and the await below (no other coroutine runs until we
            # yield). The await returns immediately for an uncontested lock.
            await self._operation_lock.acquire()
        else:
            await self._operation_lock.acquire()

        logger.debug("Acquired radio operation lock (%s)", name)

    def _release_operation_lock(self, name: str) -> None:
        """Release the shared radio operation lock."""
        if self._operation_lock and self._operation_lock.locked():
            self._operation_lock.release()
            logger.debug("Released radio operation lock (%s)", name)
        else:
            logger.error("Attempted to release unlocked radio operation lock (%s)", name)

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
        yields it.  Callers get a fresh reference via ``async with ... as mc:``,
        avoiding stale-reference bugs when a reconnect swaps ``_meshcore``
        between the pre-check and the lock acquisition.

        Args:
            name: Human-readable operation name for logs/errors.
            pause_polling: Pause fallback message polling while held.
            suspend_auto_fetch: Stop MeshCore auto message fetching while held.
            blocking: If False, fail immediately when lock is held.

        Raises:
            RadioDisconnectedError: If the radio disconnected before the lock
                was acquired (``_meshcore`` is ``None``).
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
                        logger.warning("Failed to restart auto message fetching (%s): %s", name, e)
            finally:
                self._release_operation_lock(name)

    async def post_connect_setup(self) -> None:
        """Run shared post-connection orchestration after transport setup succeeds."""
        from app.services.radio_lifecycle import run_post_connect_setup

        await run_post_connect_setup(self)

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
        if settings.force_channel_slot_reconfigure:
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

    def resume_connection(self) -> None:
        """Allow connection monitor and manual reconnects to establish transport again."""
        self._connection_desired = True

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
        from app.services.radio_ingest_gate import deny_ingest

        deny_ingest()
        self._setup_complete = False

    def _install_library_reconnect_gate(self, mc: MeshCore) -> None:
        """Deny ingest on the library disconnect callback, not DISCONNECTED.

        meshcore_py only emits DISCONNECTED when auto_reconnect is off or
        exhausted. With auto_reconnect=True the drop goes through
        handle_disconnect() and never reaches our event handler.
        """
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
        snapshot = await get_transport()
        self._transport_snapshot = snapshot
        return snapshot

    async def connect(self) -> None:
        """Connect to the radio using the configured transport."""
        if self._meshcore is not None:
            await self.disconnect()

        snapshot = await self._load_transport()
        if snapshot.transport is None:
            raise RuntimeError(
                "Radio transport is not configured. Configure it in Meshloom before connecting."
            )
        if snapshot.transport == "tcp":
            await self._connect_tcp()
        elif snapshot.transport == "ble":
            await self._connect_ble()
        else:
            await self._connect_serial()
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
            logger.info("No serial port specified, auto-detecting...")
            port = await find_radio_port(baudrate)
            if not port:
                raise RuntimeError("No MeshCore radio found. Please specify a serial port.")

        logger.debug("Connecting to radio at %s (baud %d)", port, baudrate)
        mc = await MeshCore.create_serial(
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
        logger.debug("Serial connection established")

    async def _connect_tcp(self) -> None:
        """Connect to the radio over TCP."""
        snapshot = await self._load_transport()
        if snapshot.transport is None:
            raise RuntimeError(
                "Radio transport is not configured. Configure it in Meshloom before connecting."
            )
        host = snapshot.tcp_host
        port = snapshot.tcp_port

        logger.debug("Connecting to radio at %s:%d (TCP)", host, port)
        from app.radio_proxy.manager import radio_proxy_manager

        if radio_proxy_manager.would_loop_transport(host, port):
            raise RuntimeError("TCP radio target points at this Meshloom radio proxy")
        mc = await MeshCore.create_tcp(
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
        logger.debug("TCP connection established")

    async def _connect_ble(self) -> None:
        """Connect to the radio over BLE."""
        snapshot = await self._load_transport()
        if snapshot.transport is None:
            raise RuntimeError(
                "Radio transport is not configured. Configure it in Meshloom before connecting."
            )
        address = snapshot.ble_address
        pin = snapshot.ble_pin

        logger.debug("Connecting to radio at %s (BLE)", address)
        mc = await MeshCore.create_ble(
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
        logger.debug("BLE connection established")

    async def disconnect(self) -> None:
        """Disconnect from the radio."""
        from app.radio_sync import stop_background_contact_reconciliation

        clear_keys()
        self._reset_reconnect_error_broadcasts()
        if self._meshcore is None:
            return

        await stop_background_contact_reconciliation()
        from app.event_handlers import unregister_event_handlers
        from app.services.radio_ingest_gate import deny_ingest

        deny_ingest()
        unregister_event_handlers()
        await self._acquire_operation_lock("disconnect", blocking=True)
        try:
            mc = self._meshcore
            if mc is None:
                return

            logger.debug("Disconnecting from radio")
            await self._disable_meshcore_auto_reconnect(mc)
            try:
                await mc.disconnect()
            finally:
                await self._disable_meshcore_auto_reconnect(mc)

            if self._meshcore is mc:
                self._meshcore = None
            self._reset_connected_runtime_state()
            logger.debug("Radio disconnected")
        finally:
            self._release_operation_lock("disconnect")

    async def reconnect(self, *, broadcast_on_success: bool = True) -> bool:
        """Attempt to reconnect to the radio.

        Returns True if reconnection was successful, False otherwise.
        Uses a lock to prevent concurrent reconnection attempts.
        """
        from app.websocket import broadcast_health

        # Lazily initialize lock (can't create in __init__ before event loop exists)
        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()

        async with self._reconnect_lock:
            if not self._connection_desired:
                logger.info("Reconnect skipped because connection is paused by operator")
                return False

            # If we became connected while waiting for the lock (another
            # reconnect succeeded ahead of us), skip the redundant attempt.
            if self.is_connected:
                logger.debug("Already connected after acquiring lock, skipping reconnect")
                return True

            logger.info("Attempting to reconnect to radio...")

            try:
                # Disconnect if we have a stale connection
                if self._meshcore is not None:
                    try:
                        await self.disconnect()
                    except Exception:
                        pass

                # Try to connect (serial auto-detects only when transport is serial)
                await self.connect()

                if not self._connection_desired:
                    logger.info("Reconnect completed after pause request; disconnecting transport")
                    await self.disconnect()
                    return False

                if self.is_connected:
                    logger.info("Radio reconnected successfully at %s", self._connection_info)
                    self._reset_reconnect_error_broadcasts()
                    if broadcast_on_success:
                        broadcast_health(True, self._connection_info)
                    return True
                else:
                    logger.warning("Reconnection failed: not connected after connect()")
                    return False

            except Exception as e:
                log_message, frontend_detail, include_traceback = _format_reconnect_failure(
                    e,
                    snapshot=self._transport_snapshot,
                    connection_info=self._connection_info,
                )
                logger.warning(log_message, exc_info=include_traceback)
                self._broadcast_reconnect_error_if_needed(frontend_detail)
                return False

    @asynccontextmanager
    async def lifecycle_transition(self, name: str):
        """Serialize transport/identity changes outside radio_operation/_setup_lock.

        Sequence for callers: stop monitor + close ingest, then mutate, then
        the monitor is restarted on exit. Timed acquisition fails with 503.
        """
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        try:
            await asyncio.wait_for(
                self._lifecycle_lock.acquire(),
                timeout=LIFECYCLE_LOCK_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise RadioOperationBusyError(f"Radio lifecycle is busy ({name})") from exc
        from app.services.radio_ingest_gate import deny_ingest

        await self.stop_connection_monitor()
        deny_ingest()
        try:
            yield
        finally:
            try:
                await self.start_connection_monitor()
            finally:
                self._lifecycle_lock.release()

    async def start_connection_monitor(self) -> None:
        """Start background task to monitor connection and auto-reconnect."""
        from app.services.radio_lifecycle import connection_monitor_loop

        if self._reconnect_task is not None:
            return

        self._reconnect_task = asyncio.create_task(connection_monitor_loop(self))
        logger.info("Radio connection monitor started")

    async def stop_connection_monitor(self) -> None:
        """Stop the connection monitor task."""
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None
            logger.info("Radio connection monitor stopped")


radio_manager = RadioManager()
