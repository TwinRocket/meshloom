"""A small fake of bluetoothd's D-Bus API for the BLE pairing tests.

Runs on a private dbus-daemon (see `tests/test_ble_pairing_dbus.py`), owns the
name `org.bluez` and answers with a raw message handler so it sees each caller's
unique name, like BlueZ does:

- ObjectManager.GetManagedObjects, Properties.Get/GetAll/Set;
- Adapter1.SetDiscoveryFilter/StartDiscovery/StopDiscovery/RemoveDevice;
- AgentManager1.RegisterAgent/UnregisterAgent/RequestDefaultAgent (one agent per
  sender; Release() is sent to the agent after UnregisterAgent's reply);
- Device1.Pair/CancelPairing/Disconnect. Pair() asks RequestPasskey on the agent
  registered by the same sender; no agent, an agent error or a wrong value
  answer `org.bluez.Error.AuthenticationFailed` (real BlueZ behaviour).

It watches NameOwnerChanged: when a client's connection closes, its agent,
discovery and pending Pair() are dropped, and later calls are recorded.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus

ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
AGENT_MANAGER = "org.bluez.AgentManager1"
PROPS = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

_SIGNATURES = {
    "Adapter": "o",
    "Roles": "as",
    "UUIDs": "as",
    "RSSI": "n",
}


def _variant(name: str, value: Any) -> Variant:
    if name in _SIGNATURES:
        return Variant(_SIGNATURES[name], value)
    if isinstance(value, bool):
        return Variant("b", value)
    if isinstance(value, int):
        return Variant("i", value)
    return Variant("s", str(value))


def _wrap(props: dict[str, Any]) -> dict[str, Variant]:
    return {key: _variant(key, value) for key, value in props.items()}


def device_path(adapter: str, address: str) -> str:
    return f"{adapter}/dev_{address.upper().replace(':', '_')}"


@dataclass
class Call:
    member: str
    path: str
    sender: str
    body: list[Any] = field(default_factory=list)


class FakeBlueZ:
    def __init__(self, bus_address: str) -> None:
        self.bus_address = bus_address
        self.bus: MessageBus | None = None
        self.adapters: dict[str, dict[str, Any]] = {}
        self.devices: dict[str, dict[str, Any]] = {}
        # Devices that appear once discovery has run for `delay` seconds (None: never).
        self.hidden: dict[str, tuple[dict[str, Any], float | None]] = {}
        self.passkeys: dict[str, int] = {}
        self.calls: list[Call] = []
        self.agents: dict[str, tuple[str, str]] = {}
        self.discovering: dict[str, set[str]] = {}
        self.lost_names: list[str] = []
        self.calls_after_lost: list[Call] = []
        self.bonded_property = True
        self.pair_delay = 0.0
        self.pair_error: str | None = None
        self.disconnect_delay = 0.0
        self.passkey_requests: list[str] = []
        # Calls the fake made to agents: (member, reply message type name or None).
        self.agent_calls: list[tuple[str, str | None]] = []
        self._pending_pairs: dict[str, tuple[Message, asyncio.Task]] = {}
        self._tasks: set[asyncio.Task] = set()

    # Setup --------------------------------------------------------------------

    def add_adapter(
        self,
        name: str = "hci0",
        *,
        powered: bool = True,
        roles: list[str] | None = None,
        power_state: str | None = None,
    ) -> str:
        path = f"/org/bluez/{name}"
        props: dict[str, Any] = {"Address": "00:11:22:33:44:55", "Powered": powered}
        if roles is not None:
            props["Roles"] = roles
        else:
            props["Roles"] = ["central", "peripheral"]
        if power_state is not None:
            props["PowerState"] = power_state
        self.adapters[path] = props
        return path

    def _device_props(self, adapter: str, address: str, **extra: Any) -> dict[str, Any]:
        props: dict[str, Any] = {
            "Address": address.upper(),
            "Adapter": adapter,
            "Name": "MeshCore-Test",
            "Paired": False,
            "Trusted": False,
            "Connected": False,
            "Blocked": False,
        }
        if self.bonded_property:
            props["Bonded"] = False
        props.update(extra)
        return props

    def add_device(self, adapter: str, address: str, *, passkey: int = 123456, **extra: Any) -> str:
        path = device_path(adapter, address)
        self.devices[path] = self._device_props(adapter, address, **extra)
        self.passkeys[path] = passkey
        return path

    def add_hidden_device(
        self,
        adapter: str,
        address: str,
        *,
        delay: float | None = 0.1,
        passkey: int = 123456,
    ) -> str:
        path = device_path(adapter, address)
        self.hidden[path] = (self._device_props(adapter, address), delay)
        self.passkeys[path] = passkey
        return path

    async def start(self) -> None:
        self.bus = await MessageBus(bus_address=self.bus_address).connect()
        self.bus.add_message_handler(self._handle)
        await self.bus.request_name("org.bluez")
        await self.bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=["type='signal',sender='org.freedesktop.DBus',member='NameOwnerChanged'"],
            )
        )

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task
        if self.bus is not None:
            self.bus.disconnect()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.bus.wait_for_disconnect(), 2)

    def members(self) -> list[str]:
        return [call.member for call in self.calls]

    def calls_to(self, member: str) -> list[Call]:
        return [call for call in self.calls if call.member == member]

    # Dispatch -----------------------------------------------------------------

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _handle(self, msg: Message) -> Message | bool | None:
        if msg.message_type == MessageType.SIGNAL:
            if (
                msg.member == "NameOwnerChanged"
                and msg.body[2] == ""
                and msg.body[0] == msg.body[1]
            ):
                self._client_lost(msg.body[0])
            return None
        if msg.message_type != MessageType.METHOD_CALL:
            return None
        if msg.destination != "org.bluez" and not (msg.destination or "").startswith(":"):
            return None
        call = Call(msg.member or "", msg.path or "", msg.sender or "", list(msg.body))
        self.calls.append(call)
        if call.sender in self.lost_names:
            self.calls_after_lost.append(call)
        handler = getattr(self, f"_on_{msg.interface}_{msg.member}".replace(".", "_"), None)
        if handler is None:
            return Message.new_error(
                msg, "org.freedesktop.DBus.Error.UnknownMethod", f"{msg.member} unknown"
            )
        return handler(msg)

    def _client_lost(self, name: str) -> None:
        self.lost_names.append(name)
        self.agents.pop(name, None)
        for senders in self.discovering.values():
            senders.discard(name)
        for path, (msg, task) in list(self._pending_pairs.items()):
            if msg.sender == name:
                task.cancel()
                self._pending_pairs.pop(path, None)

    # ObjectManager / Properties ------------------------------------------------

    def _on_org_freedesktop_DBus_ObjectManager_GetManagedObjects(self, msg: Message) -> Message:  # noqa: N802
        objects: dict[str, dict[str, dict[str, Variant]]] = {
            "/org/bluez": {AGENT_MANAGER: {}},
        }
        for path, props in self.adapters.items():
            objects[path] = {ADAPTER: _wrap(props)}
        for path, props in self.devices.items():
            objects[path] = {DEVICE: _wrap(props)}
        return Message.new_method_return(msg, "a{oa{sa{sv}}}", [objects])

    def _unknown_object(self, msg: Message) -> Message:
        return Message.new_error(
            msg, "org.freedesktop.DBus.Error.UnknownObject", f"No object at {msg.path}"
        )

    def _on_org_freedesktop_DBus_Properties_GetAll(self, msg: Message) -> Message:  # noqa: N802
        props = self.devices.get(msg.path or "") if msg.body[0] == DEVICE else None
        if props is None:
            props = self.adapters.get(msg.path or "") if msg.body[0] == ADAPTER else None
        if props is None:
            return self._unknown_object(msg)
        return Message.new_method_return(msg, "a{sv}", [_wrap(props)])

    def _on_org_freedesktop_DBus_Properties_Get(self, msg: Message) -> Message:  # noqa: N802
        props = self.devices.get(msg.path or "")
        if props is None or msg.body[1] not in props:
            return self._unknown_object(msg)
        return Message.new_method_return(msg, "v", [_variant(msg.body[1], props[msg.body[1]])])

    def _on_org_freedesktop_DBus_Properties_Set(self, msg: Message) -> Message:  # noqa: N802
        props = self.devices.get(msg.path or "")
        if props is None:
            return self._unknown_object(msg)
        props[msg.body[1]] = msg.body[2].value
        return Message.new_method_return(msg)

    # Adapter1 ----------------------------------------------------------------

    def _on_org_bluez_Adapter1_SetDiscoveryFilter(self, msg: Message) -> Message:  # noqa: N802
        return Message.new_method_return(msg)

    def _on_org_bluez_Adapter1_StartDiscovery(self, msg: Message) -> Message:  # noqa: N802
        adapter = msg.path or ""
        props = self.adapters.get(adapter)
        if props is None:
            return self._unknown_object(msg)
        if not props.get("Powered"):
            return Message.new_error(msg, "org.bluez.Error.NotReady", "Resource Not Ready")
        senders = self.discovering.setdefault(adapter, set())
        if msg.sender in senders:
            return Message.new_error(
                msg, "org.bluez.Error.InProgress", "Operation already in progress"
            )
        senders.add(msg.sender or "")
        for path, (dev_props, delay) in list(self.hidden.items()):
            if dev_props["Adapter"] == adapter and delay is not None:
                self._spawn(self._reveal(path, dev_props, delay))
        return Message.new_method_return(msg)

    async def _reveal(self, path: str, props: dict[str, Any], delay: float) -> None:
        await asyncio.sleep(delay)
        self.hidden.pop(path, None)
        self.devices[path] = props

    def _on_org_bluez_Adapter1_StopDiscovery(self, msg: Message) -> Message:  # noqa: N802
        senders = self.discovering.get(msg.path or "", set())
        if msg.sender not in senders:
            return Message.new_error(msg, "org.bluez.Error.Failed", "No discovery started")
        senders.discard(msg.sender or "")
        return Message.new_method_return(msg)

    def _on_org_bluez_Adapter1_RemoveDevice(self, msg: Message) -> Message:  # noqa: N802
        path = msg.body[0]
        if path not in self.devices:
            return Message.new_error(msg, "org.bluez.Error.DoesNotExist", "Does Not Exist")
        del self.devices[path]
        return Message.new_method_return(msg)

    # AgentManager1 -------------------------------------------------------------

    def _on_org_bluez_AgentManager1_RegisterAgent(self, msg: Message) -> Message:  # noqa: N802
        if msg.sender in self.agents:
            return Message.new_error(msg, "org.bluez.Error.AlreadyExists", "Already Exists")
        self.agents[msg.sender or ""] = (msg.body[0], msg.body[1])
        return Message.new_method_return(msg)

    def _on_org_bluez_AgentManager1_UnregisterAgent(self, msg: Message) -> bool:  # noqa: N802
        registered = self.agents.get(msg.sender or "")
        if registered is None or registered[0] != msg.body[0]:
            assert self.bus is not None
            self.bus.send(Message.new_error(msg, "org.bluez.Error.DoesNotExist", "No agent"))
            return True
        del self.agents[msg.sender or ""]
        assert self.bus is not None
        self.bus.send(Message.new_method_return(msg))
        # BlueZ releases the agent after replying; the client may have unexported it.
        self._spawn(self._call_agent(msg.sender or "", registered[0], "Release", "", []))
        return True

    def _on_org_bluez_AgentManager1_RequestDefaultAgent(self, msg: Message) -> Message:  # noqa: N802
        return Message.new_method_return(msg)

    async def _call_agent(
        self, sender: str, agent_path: str, member: str, signature: str, body: list[Any]
    ) -> Message | None:
        assert self.bus is not None
        try:
            reply = await self.bus.call(
                Message(
                    destination=sender,
                    path=agent_path,
                    interface="org.bluez.Agent1",
                    member=member,
                    signature=signature,
                    body=body,
                )
            )
        except Exception:
            self.agent_calls.append((member, None))
            return None
        self.agent_calls.append((member, reply.message_type.name))
        return reply

    # Device1 -------------------------------------------------------------------

    def _on_org_bluez_Device1_Pair(self, msg: Message) -> bool | Message:  # noqa: N802
        path = msg.path or ""
        if path not in self.devices:
            return self._unknown_object(msg)
        if path in self._pending_pairs:
            return Message.new_error(msg, "org.bluez.Error.InProgress", "In Progress")
        task = asyncio.ensure_future(self._pair(msg, path))
        self._pending_pairs[path] = (msg, task)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _pair(self, msg: Message, path: str) -> None:
        assert self.bus is not None
        props = self.devices[path]
        try:
            props["Connected"] = True
            if self.pair_delay:
                await asyncio.sleep(self.pair_delay)
            if self.pair_error:
                self.bus.send(Message.new_error(msg, self.pair_error, "injected"))
                return
            agent = self.agents.get(msg.sender or "")
            if agent is None:
                self.bus.send(
                    Message.new_error(msg, "org.bluez.Error.AuthenticationFailed", "No agent")
                )
                return
            self.passkey_requests.append(path)
            reply = await self._call_agent(
                msg.sender or "", agent[0], "RequestPasskey", "o", [path]
            )
            if (
                reply is None
                or reply.message_type == MessageType.ERROR
                or reply.body[0] != self.passkeys.get(path)
            ):
                self.bus.send(
                    Message.new_error(msg, "org.bluez.Error.AuthenticationFailed", "Failed")
                )
                return
            props["Paired"] = True
            if self.bonded_property:
                props["Bonded"] = True
            self.bus.send(Message.new_method_return(msg))
        except asyncio.CancelledError:
            if msg.sender not in self.lost_names:
                self.bus.send(
                    Message.new_error(msg, "org.bluez.Error.AuthenticationCanceled", "Canceled")
                )
            raise
        finally:
            self._pending_pairs.pop(path, None)

    def _on_org_bluez_Device1_CancelPairing(self, msg: Message) -> Message:  # noqa: N802
        pending = self._pending_pairs.get(msg.path or "")
        if pending is None:
            return Message.new_error(msg, "org.bluez.Error.DoesNotExist", "No pairing")
        pending[1].cancel()
        return Message.new_method_return(msg)

    def _on_org_bluez_Device1_Disconnect(self, msg: Message) -> Message:  # noqa: N802
        props = self.devices.get(msg.path or "")
        if props is None:
            return self._unknown_object(msg)
        if self.disconnect_delay:
            self._spawn(self._delayed_disconnect(props))
        else:
            props["Connected"] = False
        return Message.new_method_return(msg)

    async def _delayed_disconnect(self, props: dict[str, Any]) -> None:
        await asyncio.sleep(self.disconnect_delay)
        props["Connected"] = False
