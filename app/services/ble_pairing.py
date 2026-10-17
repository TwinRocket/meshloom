"""Pair a MeshCore BLE radio through BlueZ before meshcore connects to it.

bleak's ``pair()`` registers no BlueZ agent, so a PIN-protected companion fails
with ``AuthenticationFailed`` on a headless Linux host. Before handing over to
meshcore, ``ensure_paired()`` pairs the radio itself over D-Bus:

1. pick the adapter (powered, central role; deterministic order) and look the
   device up on that adapter only, discovering it when BlueZ does not know it;
2. already bonded there: trust it, release an orphan link, done (no agent);
3. otherwise export a one-shot ``org.bluez.Agent1`` (``ble_agent.PairingAgent``),
   ``RegisterAgent`` it as ``KeyboardOnly`` and call ``Device1.Pair()`` on the
   **same** D-Bus connection, so BlueZ uses it for this request only (never
   ``RequestDefaultAgent``: HA or a desktop agent keeps working);
4. set ``Trusted``, then **disconnect**: bleak's ``BleakClient(address)`` scans
   first and a connected companion does not advertise. The caller hands meshcore
   the BlueZ device object instead (adapter pinned, ``pin=None``).

Environmental failures (no D-Bus, no bluetoothd, permission, no adapter, not
Linux) carry ``fallback=True``: the caller keeps today's path. Authentication
failures are sticky for one (address, PIN); transient ones back off. Importable
on every platform; dbus-fast is imported lazily. The PIN is never logged.
Design notes, error table and hardware checklist: ``docs/dev/ble-pairing.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import logging
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.models import RadioBlePairingStatus

if TYPE_CHECKING:
    from app.services.ble_agent import PairingAgent

logger = logging.getLogger(__name__)

# Timeouts in seconds. Module level so tests can shrink them.
BUS_CONNECT_TIMEOUT = 5.0
FIRST_CALL_TIMEOUT = 10.0  # bluetoothd may be D-Bus activated on the first call
CALL_TIMEOUT = 5.0
DISCOVERY_TIMEOUT = 10.0
DISCOVERY_POLL = 0.5
PAIR_TIMEOUT = 25.0  # the passkey is typed by the agent, no human in the loop
DISCONNECT_WAIT = 3.0
DISCONNECT_POLL = 0.2
OVERALL_TIMEOUT = 45.0
CLEANUP_BUDGET = 4.0  # outside OVERALL_TIMEOUT: 45 + 4 stays under a 60 s proxy timeout
STATUS_TIMEOUT = 2.0
STATUS_CACHE_SECONDS = 1.0
BACKOFF_STEPS = (5, 10, 20, 40, 60)
STALE_BOND_THRESHOLD = 3
AGENT_CAPABILITY = "KeyboardOnly"
AGENT_PATH_PREFIX = "/org/meshloom/ble_agent"

BLUEZ_SERVICE = "org.bluez"
BLUEZ_ROOT = "/org/bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"

_MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", re.IGNORECASE)
_PIN_RE = re.compile(r"^\d{1,6}$")

_UNREACHABLE_TEXTS = (
    "le-connection-abort-by-local",
    "software caused connection abort",
    "page timeout",
    "host is down",
)
_ABSENT_OBJECT_ERRORS = frozenset(
    {
        "org.freedesktop.DBus.Error.UnknownObject",
        "org.freedesktop.DBus.Error.UnknownMethod",
        "org.freedesktop.DBus.Error.UnknownInterface",
        "org.freedesktop.DBus.Error.InvalidArgs",
        "org.bluez.Error.DoesNotExist",
    }
)

Retry = Literal["sticky", "backoff", "none"]

# code -> (HTTP status, fallback, retry policy, English message)
_CODES: dict[str, tuple[int, bool, Retry, str]] = {
    "ble_pair_invalid_pin": (422, False, "sticky", "The Bluetooth PIN must be 1 to 6 digits"),
    "ble_pair_dbus_unavailable": (
        503,
        True,
        "none",
        "The system D-Bus (Bluetooth service) cannot be reached",
    ),
    "ble_pair_bluez_unavailable": (
        503,
        True,
        "none",
        "The Bluetooth service (bluetoothd) is not running",
    ),
    "ble_pair_permission_denied": (
        503,
        True,
        "none",
        "Not allowed to pair through the Bluetooth service",
    ),
    "ble_pair_no_adapter": (503, True, "none", "No Bluetooth adapter"),
    "ble_pair_adapter_blocked": (503, False, "backoff", "Bluetooth is blocked (rfkill)"),
    "ble_pair_adapter_off": (503, False, "backoff", "The Bluetooth adapter is turned off"),
    "ble_pair_device_not_found": (404, False, "backoff", "Radio not found over Bluetooth"),
    "ble_pair_device_blocked": (
        409,
        False,
        "sticky",
        "The radio is blocked in this machine's Bluetooth settings",
    ),
    "ble_pair_wrong_pin": (422, False, "sticky", "Bluetooth pairing failed: wrong PIN"),
    "ble_pair_rejected": (422, False, "sticky", "The radio refused the pairing"),
    "ble_pair_unreachable": (503, False, "backoff", "The radio did not answer over Bluetooth"),
    "ble_pair_timeout": (504, False, "backoff", "Bluetooth pairing timed out"),
    "ble_pair_in_progress": (409, False, "none", "A Bluetooth pairing is already in progress"),
    "ble_pair_failed": (502, False, "backoff", "Bluetooth pairing failed"),
    "ble_pair_unsupported": (
        503,
        True,
        "none",
        "Automatic Bluetooth pairing is not available on this system",
    ),
}


class BlePairingError(RuntimeError):
    """A coded pairing failure. ``message`` is English and never contains the PIN."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        http_status: int | None = None,
        fallback: bool | None = None,
        retry: Retry | None = None,
        params: dict[str, str] | None = None,
        dbus_name: str | None = None,
        already_reported: bool = False,
    ) -> None:
        default_status, default_fallback, default_retry, default_message = _CODES.get(
            code, (502, False, "backoff", "Bluetooth pairing failed")
        )
        self.code = code
        self.message = message or default_message
        self.http_status = default_status if http_status is None else http_status
        self.fallback = default_fallback if fallback is None else fallback
        self.retry: Retry = default_retry if retry is None else retry
        self.params: dict[str, str] = dict(params or {})
        self.dbus_name = dbus_name
        self.already_reported = already_reported
        super().__init__(f"{code}: {self.message}")

    def copy(self, *, already_reported: bool) -> BlePairingError:
        return BlePairingError(
            self.code,
            self.message,
            http_status=self.http_status,
            fallback=self.fallback,
            retry=self.retry,
            params=self.params,
            dbus_name=self.dbus_name,
            already_reported=already_reported,
        )


@dataclass(frozen=True)
class PairOutcome:
    state: Literal["paired", "already_paired"]
    address: str  # normalised upper-case MAC
    adapter_path: str
    device_path: str
    device_props: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Failure:
    fingerprint: str
    error: BlePairingError
    at: float  # wall clock, for the UI
    attempts: int = 1
    retry_at: float | None = None  # monotonic, backoff only
    reported: bool = False

    @property
    def sticky(self) -> bool:
        return self.error.retry == "sticky"

    def __repr__(self) -> str:  # the fingerprint is a hash; no PIN anywhere
        return (
            f"_Failure(code={self.error.code!r}, sticky={self.sticky}, "
            f"attempts={self.attempts}, retry_at={self.retry_at})"
        )


@dataclass
class _StatusRead:
    at: float
    adapter: str = ""
    device: dict[str, Any] | None = None
    error: BlePairingError | None = None


# Module state (one radio per process). Reset by reset_for_tests().
_lock: asyncio.Lock | None = None
_in_progress = False
_last_failure: _Failure | None = None
_already_paired_failures: dict[str, int] = {}
_stale_bond_reported: set[str] = set()
_status_cache: dict[str, _StatusRead] = {}
_last_fallback_code: str | None = None
_multi_adapter_warned = False

_monotonic = time.monotonic
_wall_clock = time.time


# Public helpers -----------------------------------------------------------------


def pairing_supported() -> tuple[bool, str | None]:
    """Whether this process can pair through BlueZ (Linux with dbus-fast)."""
    if not sys.platform.startswith("linux"):
        return False, "ble_pair_unsupported"
    try:
        if importlib.util.find_spec("dbus_fast") is None:
            return False, "ble_pair_unsupported"
    except (ImportError, ValueError):
        return False, "ble_pair_unsupported"
    return True, None


def is_mac_address(address: str) -> bool:
    return bool(_MAC_RE.match((address or "").strip()))


def is_valid_pin(pin: str) -> bool:
    return bool(_PIN_RE.match(pin or ""))


def should_prepair(address: str) -> bool:
    return pairing_supported()[0] and is_mac_address(address)


def in_progress() -> bool:
    return _in_progress


def _normalise(address: str) -> str:
    return (address or "").strip().upper()


def _fingerprint(address: str, pin: str) -> str:
    return hashlib.sha256(f"{_normalise(address)}\0{pin}".encode()).hexdigest()


def _failure_for(address: str, pin: str) -> _Failure | None:
    failure = _last_failure
    if failure is None or failure.fingerprint != _fingerprint(address, pin):
        return None
    return failure


def auto_pair_blocked(address: str, pin: str) -> bool:
    failure = _failure_for(address, pin)
    return failure is not None and failure.sticky


def _retry_in(failure: _Failure) -> int | None:
    if failure.retry_at is None:
        return None
    remaining = failure.retry_at - _monotonic()
    return max(0, int(remaining + 0.999)) if remaining > 0 else None


def _clear_failure_state(address: str | None = None) -> None:
    global _last_failure
    _last_failure = None
    if address is not None:
        _already_paired_failures.pop(address, None)
        _stale_bond_reported.discard(address)


def _raise_if_blocked(fingerprint: str) -> None:
    failure = _last_failure
    if failure is None or failure.fingerprint != fingerprint:
        return
    if failure.sticky:
        raise failure.error.copy(already_reported=True)
    if failure.retry_at is not None and _monotonic() < failure.retry_at:
        raise failure.error.copy(already_reported=True)


def _record_failure(fingerprint: str, error: BlePairingError) -> BlePairingError:
    """Store a sticky/backoff failure; return the error to raise."""
    global _last_failure
    previous = _last_failure if _last_failure and _last_failure.fingerprint == fingerprint else None
    attempts = previous.attempts + 1 if previous is not None else 1
    retry_at = None
    if error.retry == "backoff":
        step = BACKOFF_STEPS[min(attempts - 1, len(BACKOFF_STEPS) - 1)]
        retry_at = _monotonic() + step
    # One toast per (address, PIN) and code: a retry failing the same way stays quiet.
    already = bool(previous and previous.reported and previous.error.code == error.code)
    _last_failure = _Failure(
        fingerprint=fingerprint,
        error=error,
        at=_wall_clock(),
        attempts=attempts,
        retry_at=retry_at,
        reported=True,
    )
    return error.copy(already_reported=already) if already else error


def log_fallback(exc: BlePairingError) -> None:
    """Log an environmental fallback once per distinct code, then at DEBUG."""
    global _last_fallback_code
    if exc.code != _last_fallback_code:
        _last_fallback_code = exc.code
        logger.warning(
            "Automatic BLE pairing unavailable (%s: %s); connecting without it. "
            "Pair the radio with the system tools if it asks for a PIN.",
            exc.code,
            exc.message,
        )
    else:
        logger.debug("Automatic BLE pairing still unavailable (%s)", exc.code)


def note_connect_result(address: str, *, ok: bool, after_already_paired: bool) -> None:
    """Count connection failures on an existing bond (a radio reset leaves it stale).

    Never removes the bond: past the threshold the UI suggests Forget pairing.
    """
    address = _normalise(address)
    if ok:
        _already_paired_failures.pop(address, None)
        _stale_bond_reported.discard(address)
        return
    if not after_already_paired:
        return
    count = _already_paired_failures.get(address, 0) + 1
    _already_paired_failures[address] = count
    if count >= STALE_BOND_THRESHOLD and address not in _stale_bond_reported:
        _stale_bond_reported.add(address)
        logger.warning(
            "BLE radio %s refused %d connections on its existing pairing; "
            "if it was reset, use Forget pairing",
            address,
            count,
        )
        from app.websocket import broadcast_error

        broadcast_error(
            "The radio refuses this machine's saved pairing",
            "If it was reset or re-flashed, press Forget pairing in Settings > Radio.",
            code="ble_pair_stale_bond",
        )


def reset_for_tests() -> None:
    global _lock, _in_progress, _last_failure, _last_fallback_code, _multi_adapter_warned
    _lock = None
    _in_progress = False
    _last_failure = None
    _already_paired_failures.clear()
    _stale_bond_reported.clear()
    _status_cache.clear()
    _last_fallback_code = None
    _multi_adapter_warned = False


# D-Bus plumbing -----------------------------------------------------------------


def _authenticator() -> Any:
    """Let bleak decide (it honours BLEAK_DBUS_AUTH_UID); no Meshloom variable."""
    try:
        from bleak.backends.bluezdbus.utils import get_dbus_authenticator
    except ImportError:
        return None
    try:
        return get_dbus_authenticator()
    except Exception:
        return None


async def _connect_bus(bus_address: str | None = None, *, system: bool = True) -> Any:
    """Connect a dbus-fast MessageBus, mapping failures to coded errors."""
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
        from dbus_fast.errors import AuthError
    except ImportError as exc:
        raise BlePairingError("ble_pair_unsupported") from exc

    bus = None
    try:
        bus = MessageBus(
            bus_address=bus_address,
            bus_type=BusType.SYSTEM if system else BusType.SESSION,
            auth=_authenticator(),
        )
        return await asyncio.wait_for(bus.connect(), BUS_CONNECT_TIMEOUT)
    except (AuthError, PermissionError) as exc:
        _quiet_disconnect(bus)
        raise BlePairingError(
            "ble_pair_permission_denied", params={"error": type(exc).__name__}
        ) from None
    except TimeoutError:
        _quiet_disconnect(bus)
        raise BlePairingError("ble_pair_dbus_unavailable", params={"error": "timeout"}) from None
    except Exception as exc:
        _quiet_disconnect(bus)
        raise BlePairingError(
            "ble_pair_dbus_unavailable", params={"error": type(exc).__name__}
        ) from None


async def _open_bus() -> Any:
    """The only place that connects to the system bus (tests replace it)."""
    return await _connect_bus()


def _quiet_disconnect(bus: Any) -> None:
    if bus is None:
        return
    try:
        bus.disconnect()
    except Exception:
        logger.debug("D-Bus disconnect failed", exc_info=True)


def _unpack(value: Any) -> Any:
    """Turn dbus-fast Variants (recursively) into plain Python values."""
    try:
        from dbus_fast import Variant
    except ImportError:  # pragma: no cover - only reached with plain test data
        Variant = None  # noqa: N806
    if Variant is not None and isinstance(value, Variant):
        return _unpack(value.value)
    if isinstance(value, dict):
        return {key: _unpack(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    return value


def _map_dbus_error(
    name: str,
    text: str = "",
    *,
    context: str = "any",
    agent: PairingAgent | None = None,
) -> BlePairingError:
    """Classify a D-Bus error reply. See the table in docs/dev/ble-pairing.md."""
    lowered = (text or "").lower()
    if agent is not None and agent.invalid_pin:
        return BlePairingError("ble_pair_invalid_pin", dbus_name=name)
    if context == "pair" and agent is not None and agent.unexpected_method:
        logger.warning(
            "BLE pairing aborted: BlueZ asked for %s, which a MeshCore companion never "
            "needs with a PIN",
            agent.unexpected_method,
        )
        return BlePairingError(
            "ble_pair_failed",
            "Bluetooth pairing failed (unexpected pairing method)",
            retry="sticky",
            params={"error": "unexpected pairing method"},
            dbus_name=name,
        )
    if name in (
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
    ):
        return BlePairingError("ble_pair_bluez_unavailable", dbus_name=name)
    if name in ("org.freedesktop.DBus.Error.AccessDenied", "org.freedesktop.DBus.Error.AuthFailed"):
        return BlePairingError("ble_pair_permission_denied", dbus_name=name)
    if context == "register":
        if name in (
            "org.freedesktop.DBus.Error.UnknownObject",
            "org.freedesktop.DBus.Error.UnknownMethod",
            "org.freedesktop.DBus.Error.UnknownInterface",
        ):
            return BlePairingError("ble_pair_bluez_unavailable", dbus_name=name)
        if name in ("org.bluez.Error.InvalidArguments", "org.bluez.Error.AlreadyExists"):
            return BlePairingError("ble_pair_failed", params={"error": name}, dbus_name=name)
    if name == "org.bluez.Error.NotReady":
        return BlePairingError("ble_pair_adapter_off", dbus_name=name)
    if name == "org.bluez.Error.Blocked":
        return BlePairingError("ble_pair_device_blocked", dbus_name=name)
    if name == "org.bluez.Error.DoesNotExist" or (
        name == "org.freedesktop.DBus.Error.UnknownObject" and context in ("pair", "lookup")
    ):
        return BlePairingError("ble_pair_device_not_found", dbus_name=name)
    if name in ("org.bluez.Error.AuthenticationFailed", "org.bluez.Error.AuthenticationRejected"):
        if agent is not None and agent.passkey_requested:
            return BlePairingError("ble_pair_wrong_pin", dbus_name=name)
        logger.info(
            "BLE pairing refused before any PIN was asked (%s). If the radio was reset, use "
            "Forget pairing; on AppArmor hosts (Ubuntu, HA OS) check that the container may "
            "receive calls from bluetoothd",
            name,
        )
        return BlePairingError("ble_pair_rejected", dbus_name=name)
    if name in (
        "org.bluez.Error.AuthenticationCanceled",
        "org.bluez.Error.ConnectionAttemptFailed",
        "org.bluez.Error.NotAvailable",
    ):
        return BlePairingError("ble_pair_unreachable", dbus_name=name)
    if name == "org.bluez.Error.Failed" and any(item in lowered for item in _UNREACHABLE_TEXTS):
        return BlePairingError("ble_pair_unreachable", dbus_name=name)
    if name in (
        "org.bluez.Error.AuthenticationTimeout",
        "org.freedesktop.DBus.Error.NoReply",
        "org.freedesktop.DBus.Error.Timeout",
        "org.freedesktop.DBus.Error.TimedOut",
    ):
        return BlePairingError("ble_pair_timeout", dbus_name=name)
    if name == "org.bluez.Error.InProgress":
        return BlePairingError("ble_pair_in_progress", dbus_name=name)
    return BlePairingError(
        "ble_pair_failed",
        f"Bluetooth pairing failed ({name})",
        params={"error": name},
        dbus_name=name,
    )


def _device_path(adapter_path: str, address: str) -> str:
    return f"{adapter_path}/dev_{_normalise(address).replace(':', '_')}"


def _adapter_name(adapter_path: str) -> str:
    return adapter_path.rsplit("/", 1)[-1] if adapter_path else ""


def is_bonded(props: dict[str, Any]) -> bool:
    """``Bonded`` when BlueZ exposes it (>= 5.70), else ``Paired``."""
    if "Bonded" in props:
        return bool(props.get("Bonded"))
    return bool(props.get("Paired"))


def _adapters(objects: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    return {
        path: interfaces[ADAPTER_IFACE]
        for path, interfaces in objects.items()
        if ADAPTER_IFACE in interfaces
    }


def pick_adapter(objects: dict[str, dict[str, dict[str, Any]]]) -> str:
    """Powered adapters with the central role (or no Roles: old BlueZ), first by path."""
    global _multi_adapter_warned
    adapters = _adapters(objects)
    if not adapters:
        raise BlePairingError("ble_pair_no_adapter")
    eligible = sorted(
        path
        for path, props in adapters.items()
        if props.get("Powered") and ("Roles" not in props or "central" in props.get("Roles", []))
    )
    if eligible:
        if len(eligible) > 1 and not _multi_adapter_warned:
            _multi_adapter_warned = True
            logger.warning(
                "Several Bluetooth adapters can reach the radio (%s); Meshloom pairs and "
                "connects through %s",
                ", ".join(_adapter_name(path) for path in eligible),
                _adapter_name(eligible[0]),
            )
        return eligible[0]
    if any(props.get("Powered") for props in adapters.values()):
        raise BlePairingError("ble_pair_no_adapter")
    if any(props.get("PowerState") == "off-blocked" for props in adapters.values()):
        raise BlePairingError("ble_pair_adapter_blocked")
    raise BlePairingError("ble_pair_adapter_off")


def device_on(
    adapter_path: str, address: str, objects: dict[str, dict[str, dict[str, Any]]]
) -> tuple[str, dict[str, Any]] | None:
    """The device object on that adapter only (a bond elsewhere does not count)."""
    path = _device_path(adapter_path, address)
    props = objects.get(path, {}).get(DEVICE_IFACE)
    if props is None:
        return None
    if str(props.get("Address", "")).upper() != _normalise(address):
        return None
    if props.get("Adapter", adapter_path) != adapter_path:
        return None
    return path, props


class _BlueZ:
    """Calls to bluetoothd on one bus connection."""

    def __init__(self, bus: Any) -> None:
        self.bus = bus

    async def call_raw(
        self,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        from dbus_fast import Message

        message = Message(
            destination=BLUEZ_SERVICE,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        )
        try:
            return await asyncio.wait_for(
                self.bus.call(message), CALL_TIMEOUT if timeout is None else timeout
            )
        except TimeoutError:
            raise BlePairingError(
                "ble_pair_timeout", params={"error": f"{member} timed out"}
            ) from None
        except BlePairingError:
            raise
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise BlePairingError(
                "ble_pair_dbus_unavailable", params={"error": type(exc).__name__}
            ) from None

    async def call(
        self,
        path: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        *,
        timeout: float | None = None,
        context: str = "any",
        agent: PairingAgent | None = None,
    ) -> list[Any]:
        from dbus_fast import MessageType

        reply = await self.call_raw(path, interface, member, signature, body, timeout=timeout)
        if reply.message_type == MessageType.ERROR:
            text = reply.body[0] if reply.body and isinstance(reply.body[0], str) else ""
            raise _map_dbus_error(reply.error_name or "", text, context=context, agent=agent)
        return list(reply.body or [])

    async def managed_objects(self, timeout: float) -> dict[str, dict[str, dict[str, Any]]]:
        body = await self.call(
            "/", OBJECT_MANAGER_IFACE, "GetManagedObjects", timeout=timeout, context="lookup"
        )
        return _unpack(body[0]) if body else {}

    async def device_props(self, device_path: str) -> dict[str, Any] | None:
        """Device1 properties, or None when BlueZ does not know that object."""
        from dbus_fast import MessageType

        reply = await self.call_raw(device_path, PROPERTIES_IFACE, "GetAll", "s", [DEVICE_IFACE])
        if reply.message_type == MessageType.ERROR:
            if reply.error_name in _ABSENT_OBJECT_ERRORS:
                return None
            text = reply.body[0] if reply.body and isinstance(reply.body[0], str) else ""
            raise _map_dbus_error(reply.error_name or "", text, context="lookup")
        return _unpack(reply.body[0]) if reply.body else {}

    async def set_trusted(self, device_path: str) -> None:
        from dbus_fast import Variant

        try:
            await self.call(
                device_path,
                PROPERTIES_IFACE,
                "Set",
                "ssv",
                [DEVICE_IFACE, "Trusted", Variant("b", True)],
            )
        except BlePairingError as exc:
            logger.debug("Could not mark the BLE radio trusted: %s", exc.code)

    async def disconnect_and_wait(self, device_path: str) -> None:
        """Release the LE link; never fatal."""
        try:
            await self.call(device_path, DEVICE_IFACE, "Disconnect")
        except BlePairingError as exc:
            logger.debug("BLE Disconnect failed (%s)", exc.code)
        deadline = _monotonic() + DISCONNECT_WAIT
        while True:
            try:
                props = await self.device_props(device_path)
            except BlePairingError as exc:
                logger.debug("Could not read BLE link state (%s)", exc.code)
                return
            if not props or not props.get("Connected"):
                return
            if _monotonic() >= deadline:
                logger.debug("BLE link still up after %.1f s", DISCONNECT_WAIT)
                return
            await asyncio.sleep(DISCONNECT_POLL)


class _PairSession:
    """One pairing attempt on one bus connection; tracks what cleanup must undo."""

    def __init__(self, bluez: _BlueZ, address: str, pin: str) -> None:
        self.bluez = bluez
        self.address = address
        self._pin = pin
        self.adapter_path = ""
        self.device_path = ""
        self.agent: PairingAgent | None = None
        self.agent_path: str | None = None
        self.agent_registered = False
        self.discovery_started = False
        self.need_cancel_pairing = False

    def __repr__(self) -> str:
        return f"_PairSession(address={self.address!r}, device={self.device_path!r})"

    async def run(self) -> PairOutcome:
        bluez = self.bluez
        objects = await bluez.managed_objects(FIRST_CALL_TIMEOUT)
        self.adapter_path = pick_adapter(objects)
        found = device_on(self.adapter_path, self.address, objects)
        if found is not None:
            self.device_path, props = found
            if props.get("Blocked"):
                raise BlePairingError("ble_pair_device_blocked")
            if is_bonded(props):
                return await self._already_paired(props)
            # Not paired, or a session-only pairing (Paired without Bonded): pair
            # directly. A radio that is not advertising fails Pair() as unreachable.
        else:
            self.device_path = await self._discover()
        return await self._pair()

    async def _already_paired(self, props: dict[str, Any]) -> PairOutcome:
        bluez = self.bluez
        if not props.get("Trusted"):
            await bluez.set_trusted(self.device_path)
        if props.get("Connected"):
            # meshcore is never running here: a live link is an orphan (crash,
            # earlier attempt) and would make bleak's connect skip or fail.
            await bluez.disconnect_and_wait(self.device_path)
        fresh = await self._props_or(props)
        return PairOutcome(
            "already_paired", self.address, self.adapter_path, self.device_path, fresh
        )

    async def _props_or(self, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            fresh = await self.bluez.device_props(self.device_path)
        except BlePairingError:
            fresh = None
        return fresh if fresh is not None else fallback

    async def _discover(self) -> str:
        from dbus_fast import Variant

        bluez = self.bluez
        adapter = self.adapter_path
        path = _device_path(adapter, self.address)
        try:
            await bluez.call(
                adapter,
                ADAPTER_IFACE,
                "SetDiscoveryFilter",
                "a{sv}",
                [{"Transport": Variant("s", "le")}],
            )
        except BlePairingError as exc:
            logger.debug("SetDiscoveryFilter failed (%s)", exc.code)
        try:
            await bluez.call(adapter, ADAPTER_IFACE, "StartDiscovery", context="lookup")
            self.discovery_started = True
        except BlePairingError as exc:
            if exc.dbus_name != "org.bluez.Error.InProgress":
                raise
        deadline = _monotonic() + DISCOVERY_TIMEOUT
        found = False
        while True:
            props = await bluez.device_props(path)
            if props is not None and str(props.get("Address", "")).upper() == self.address:
                if props.get("Blocked"):
                    raise BlePairingError("ble_pair_device_blocked")
                found = True
                break
            if _monotonic() >= deadline:
                break
            await asyncio.sleep(DISCOVERY_POLL)
        await self._stop_discovery()
        if not found:
            raise BlePairingError("ble_pair_device_not_found")
        return path

    async def _stop_discovery(self) -> None:
        if not self.discovery_started:
            return
        self.discovery_started = False
        try:
            await self.bluez.call(self.adapter_path, ADAPTER_IFACE, "StopDiscovery")
        except BlePairingError as exc:
            # "No discovery started" and friends: nothing of ours left to stop.
            logger.debug("StopDiscovery failed (%s)", exc.code)

    async def _pair(self) -> PairOutcome:
        from app.services.ble_agent import PairingAgent

        bluez = self.bluez
        agent = PairingAgent(self.device_path, self._pin)
        agent_path = f"{AGENT_PATH_PREFIX}/{secrets.token_hex(8)}"
        bluez.bus.export(agent_path, agent)
        self.agent, self.agent_path = agent, agent_path
        await bluez.call(
            BLUEZ_ROOT,
            AGENT_MANAGER_IFACE,
            "RegisterAgent",
            "os",
            [agent_path, AGENT_CAPABILITY],
            context="register",
        )
        self.agent_registered = True
        logger.info("Pairing BLE radio %s through %s", self.address, self.adapter_path)
        from dbus_fast import MessageType

        reply = await self._pair_call(agent)
        if reply.message_type == MessageType.ERROR:
            name = reply.error_name or ""
            if name != "org.bluez.Error.AlreadyExists":
                text = reply.body[0] if reply.body and isinstance(reply.body[0], str) else ""
                raise _map_dbus_error(name, text, context="pair", agent=agent)
        await bluez.set_trusted(self.device_path)
        # Mandatory: a link left up stops the companion advertising and the next
        # connect would not find it (docs/dev/ble-pairing.md).
        await bluez.disconnect_and_wait(self.device_path)
        props = await self._props_or({"Address": self.address})
        logger.info("BLE radio %s paired", self.address)
        return PairOutcome("paired", self.address, self.adapter_path, self.device_path, props)

    async def _pair_call(self, agent: PairingAgent) -> Any:
        try:
            return await self.bluez.call_raw(
                self.device_path, DEVICE_IFACE, "Pair", timeout=PAIR_TIMEOUT
            )
        except BlePairingError as exc:
            if exc.code == "ble_pair_timeout":
                self.need_cancel_pairing = True
            if agent.invalid_pin:
                raise BlePairingError("ble_pair_invalid_pin") from None
            raise

    async def cleanup(self, *, failed: bool) -> None:
        """Best-effort undo, in order. Never called on cancellation."""
        bluez = self.bluez
        if self.need_cancel_pairing and self.device_path:
            try:
                await bluez.call(self.device_path, DEVICE_IFACE, "CancelPairing")
            except BlePairingError as exc:
                logger.debug("CancelPairing failed (%s)", exc.code)
        if self.agent_registered and self.agent_path:
            try:
                await bluez.call(
                    BLUEZ_ROOT, AGENT_MANAGER_IFACE, "UnregisterAgent", "o", [self.agent_path]
                )
            except BlePairingError as exc:
                logger.debug("UnregisterAgent failed (%s)", exc.code)
            self.agent_registered = False
        if self.agent_path:
            # Only after UnregisterAgent's reply: a late Release() gets UnknownMethod.
            try:
                bluez.bus.unexport(self.agent_path)
            except Exception:
                logger.debug("Agent unexport failed", exc_info=True)
            self.agent_path = None
        await self._stop_discovery()
        if failed and self.device_path:
            try:
                props = await bluez.device_props(self.device_path)
            except BlePairingError:
                props = None
            if props and props.get("Connected"):
                await bluez.disconnect_and_wait(self.device_path)


# Lock ------------------------------------------------------------------------------


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


class _Locked:
    """``async with _locked():`` that rebinds a lock left on a dead event loop (tests)."""

    def __init__(self) -> None:
        self._held: asyncio.Lock | None = None

    async def __aenter__(self) -> None:
        global _lock
        lock = _get_lock()
        try:
            await lock.acquire()
        except RuntimeError as exc:
            if "different event loop" not in str(exc):
                raise
            _lock = lock = asyncio.Lock()
            await lock.acquire()
        self._held = lock

    async def __aexit__(self, *exc_info: object) -> None:
        if self._held is not None and self._held.locked():
            self._held.release()
        self._held = None


# Entry points ------------------------------------------------------------------------


async def ensure_paired(address: str, pin: str, *, explicit: bool = False) -> PairOutcome:
    """Make sure the radio is bonded on the adapter meshcore will use.

    Raises ``BlePairingError``. ``explicit`` (the Pair button) lifts a sticky
    failure or a backoff window first.
    """
    global _in_progress
    address = _normalise(address)
    async with _Locked():
        _in_progress = True
        _status_cache.clear()
        try:
            return await _ensure_paired_locked(address, pin, explicit=explicit)
        finally:
            _in_progress = False
            _status_cache.clear()


async def _ensure_paired_locked(address: str, pin: str, *, explicit: bool) -> PairOutcome:
    global _last_fallback_code
    fingerprint = _fingerprint(address, pin)
    if explicit:
        _clear_failure_state()
    else:
        _raise_if_blocked(fingerprint)
    if not is_valid_pin(pin):
        raise _record_failure(fingerprint, BlePairingError("ble_pair_invalid_pin"))
    try:
        outcome = await _pair_session(address, pin)
    except BlePairingError as exc:
        if exc.retry in ("sticky", "backoff"):
            raise _record_failure(fingerprint, exc) from None
        raise
    _clear_failure_state()
    _last_fallback_code = None
    if outcome.state == "paired":
        _already_paired_failures.pop(address, None)
        _stale_bond_reported.discard(address)
    return outcome


async def _pair_session(address: str, pin: str) -> PairOutcome:
    bus = await _open_bus()
    session = _PairSession(_BlueZ(bus), address, pin)
    cancelled = False
    failed = True
    try:
        try:
            async with asyncio.timeout(OVERALL_TIMEOUT):
                outcome = await session.run()
        except TimeoutError:
            session.need_cancel_pairing = session.agent_registered
            raise BlePairingError("ble_pair_timeout", params={"error": "overall timeout"}) from None
        failed = False
        return outcome
    except asyncio.CancelledError:
        # No D-Bus round trip: closing the connection makes BlueZ drop our agent,
        # our discovery session and a pending bonding (NameOwnerChanged).
        cancelled = True
        raise
    finally:
        try:
            if not cancelled:
                try:
                    async with asyncio.timeout(CLEANUP_BUDGET):
                        await session.cleanup(failed=failed)
                except TimeoutError:
                    logger.debug("BLE pairing cleanup ran out of time")
                except Exception:
                    logger.debug("BLE pairing cleanup failed", exc_info=True)
        finally:
            _quiet_disconnect(bus)


async def forget(address: str) -> int:
    """Remove the radio's bond from every adapter. Returns how many held it."""
    address = _normalise(address)
    if not should_prepair(address):
        raise BlePairingError("ble_pair_unsupported")
    async with _Locked():
        _status_cache.clear()
        bus = await _open_bus()
        try:
            async with asyncio.timeout(OVERALL_TIMEOUT):
                bluez = _BlueZ(bus)
                objects = await bluez.managed_objects(FIRST_CALL_TIMEOUT)
                adapters = sorted(_adapters(objects))
                try:
                    first = pick_adapter(objects)
                    adapters.remove(first)
                    adapters.insert(0, first)
                except (BlePairingError, ValueError):
                    pass
                removed = 0
                for adapter in adapters:
                    found = device_on(adapter, address, objects)
                    if found is None:
                        continue
                    device_path, props = found
                    if props.get("Connected"):
                        await bluez.disconnect_and_wait(device_path)
                    await bluez.call(
                        adapter, ADAPTER_IFACE, "RemoveDevice", "o", [device_path], context="forget"
                    )
                    removed += 1
        except TimeoutError:
            raise BlePairingError("ble_pair_timeout", params={"error": "overall timeout"}) from None
        finally:
            _quiet_disconnect(bus)
            _status_cache.clear()
        _clear_failure_state(address)
        logger.info("Forgot BLE pairing of %s on %d adapter(s)", address, removed)
        return removed


async def _read_status(address: str) -> _StatusRead:
    cached = _status_cache.get(address)
    now = _monotonic()
    if cached is not None and now - cached.at < STATUS_CACHE_SECONDS:
        return cached
    read = _StatusRead(at=now)
    bus = None
    try:
        async with asyncio.timeout(STATUS_TIMEOUT):
            bus = await _open_bus()
            bluez = _BlueZ(bus)
            objects = await bluez.managed_objects(STATUS_TIMEOUT)
            adapter = pick_adapter(objects)
            read.adapter = _adapter_name(adapter)
            found = device_on(adapter, address, objects)
            read.device = found[1] if found else None
    except TimeoutError:
        read.error = BlePairingError("ble_pair_bluez_unavailable", params={"error": "timeout"})
    except BlePairingError as exc:
        read.error = exc
    finally:
        _quiet_disconnect(bus)
    _status_cache[address] = read
    return read


async def pairing_status(address: str, pin: str, transport_is_ble: bool) -> RadioBlePairingStatus:
    """Pairing state for the UI. Never raises for environmental problems."""
    supported, _reason = pairing_supported()
    address = _normalise(address)
    if not transport_is_ble or not address or not pin:
        return RadioBlePairingStatus(supported=supported, state="not_configured", address=address)
    if not supported or not is_mac_address(address):
        return RadioBlePairingStatus(
            supported=False,
            state="unsupported",
            address=address,
            error_code="ble_pair_unsupported",
        )
    hint: Literal["stale_bond_suspected"] | None = (
        "stale_bond_suspected"
        if _already_paired_failures.get(address, 0) >= STALE_BOND_THRESHOLD
        else None
    )
    if _in_progress:
        return RadioBlePairingStatus(supported=True, state="pairing", address=address, hint=hint)

    failure = _failure_for(address, pin)
    read = await _read_status(address)
    base: dict[str, Any] = {"supported": True, "address": address, "adapter": read.adapter}
    if read.device is not None:
        base.update(
            paired=is_bonded(read.device),
            trusted=bool(read.device.get("Trusted")),
            connected=bool(read.device.get("Connected")),
        )
    if read.device is not None and is_bonded(read.device):
        return RadioBlePairingStatus(state="paired", hint=hint, **base)
    if failure is not None:
        return RadioBlePairingStatus(
            state="failed",
            error_code=failure.error.code,
            error_at=int(failure.at),
            auto_pair_blocked=failure.sticky,
            retry_in=_retry_in(failure),
            hint=hint,
            **base,
        )
    if read.error is not None:
        return RadioBlePairingStatus(
            state="unavailable", error_code=read.error.code, hint=hint, **base
        )
    if read.device is None:
        return RadioBlePairingStatus(state="unknown_device", hint=hint, **base)
    return RadioBlePairingStatus(state="not_paired", hint=hint, **base)
