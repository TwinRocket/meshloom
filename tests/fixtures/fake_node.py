"""A fake v1.18 companion radio for the real meshcore stack (loopback).

``FakeNode`` extends ``FakeCompanionFirmware`` (the CLI and its Wi-Fi prefs)
with the binary companion commands the full backup reads and writes: SELF_INFO,
DEVICE_INFO, tuning, custom vars, default flood scope, auto-add, radio params,
other params, BLE PIN, channels, contacts and the private key. Every command
is recorded in ``log`` (name, detail) in arrival order, so tests can check the
order the restore writes in.

``connected_node`` wires one to the process-wide radio manager, setup complete.
"""

from __future__ import annotations

import struct
from contextlib import asynccontextmanager
from typing import Any

from app.decoder import derive_public_key
from tests.fixtures.meshcore_loopback import FakeCompanionFirmware, make_loopback

OK = b"\x00"


def error(code: int = 1) -> bytes:
    return bytes([1, code])


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("utf-8", "ignore")


class FakeNode(FakeCompanionFirmware):
    def __init__(self, *, private_key: bytes | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.private_key = private_key or bytes(range(1, 65))
        self.export_disabled = False
        self.import_disabled = False
        self.name = "Base"
        self.lat, self.lon = 48.85, 2.35
        self.tx_power, self.max_tx_power = 20, 22
        self.freq, self.bw, self.sf, self.cr = 869.525, 250.0, 11, 5
        self.repeat = False
        self.repeat_allowed = True
        self.other = {
            "manual_add_contacts": 0,
            "telemetry": 0,
            "adv_loc_policy": 0,
            "multi_acks": 0,
        }
        self.tuning = (1000, 1000)
        self.ble_pin = 123456
        self.path_hash_mode = 0
        self.fw_ver = 14
        self.max_contacts = 100
        self.max_channels = 8
        self.custom_vars: dict[str, str] = {"gps": "0"}
        self.default_scope: tuple[str, bytes] | None = None
        self.autoadd = (0, 0)
        self.channels: dict[int, tuple[str, bytes]] = {}
        self.contacts: dict[str, dict[str, Any]] = {}
        self.reject: set[int] = set()  # command codes answered with ERROR
        self.log: list[tuple[str, Any]] = []

    @property
    def public_key(self) -> str:
        return derive_public_key(self.private_key).hex()

    # -- frames -------------------------------------------------------------

    def self_info_frame(self) -> bytes:
        telemetry = self.other["telemetry"]
        return (
            b"\x05"
            + bytes([1, self.tx_power, self.max_tx_power])
            + bytes.fromhex(self.public_key)
            + struct.pack("<ii", int(self.lat * 1e6), int(self.lon * 1e6))
            + bytes(
                [
                    self.other["multi_acks"],
                    self.other["adv_loc_policy"],
                    telemetry,
                    self.other["manual_add_contacts"],
                ]
            )
            + struct.pack("<II", int(round(self.freq * 1000)), int(round(self.bw * 1000)))
            + bytes([self.sf, self.cr])
            + self.name.encode()
        )

    def device_info_frame(self) -> bytes:
        return (
            b"\x0d"
            + bytes([self.fw_ver, self.max_contacts // 2, self.max_channels])
            + struct.pack("<I", self.ble_pin)
            + b"01 Jan 2026".ljust(12, b"\x00")
            + b"Heltec V3".ljust(40, b"\x00")
            + b"v1.18.0".ljust(20, b"\x00")
            + bytes([1 if self.repeat else 0, self.path_hash_mode])
        )

    def contact_frame(self, contact: dict[str, Any]) -> bytes:
        return (
            b"\x03"
            + bytes.fromhex(contact["public_key"])
            + bytes([contact["type"], contact["flags"], contact["out_path_len_byte"]])
            + contact["out_path"].ljust(64, b"\x00")
            + contact["name"].encode()[:32].ljust(32, b"\x00")
            + struct.pack("<Iii", contact["last_advert"], 0, 0)
            + struct.pack("<I", 0)
        )

    def add_contact(self, public_key: str, *, flags: int = 0, name: str = "") -> None:
        self.contacts[public_key] = {
            "public_key": public_key,
            "type": 1,
            "flags": flags,
            "out_path_len_byte": 255,
            "out_path": b"",
            "name": name,
            "last_advert": 0,
        }

    # -- responder ------------------------------------------------------------

    def __call__(self, payload: bytes) -> list[bytes]:
        code = payload[0]
        if code == 0x42:
            self.log.append(("cli", payload[4:].decode("utf-8", "ignore")))
            return super().__call__(payload)
        if code == 0x0A:  # SYNC_NEXT_MESSAGE
            return [b"\x0a"]
        if code in self.reject:
            self.log.append(("rejected", code))
            return [error(6)]
        handler = getattr(self, f"_cmd_{code:02x}", None)
        if handler is None:
            return [error(1)]
        return handler(payload[1:])

    def _cmd_01(self, body: bytes) -> list[bytes]:  # APP_START
        return [self.self_info_frame()]

    def _cmd_16(self, body: bytes) -> list[bytes]:  # DEVICE_QUERY
        return [self.device_info_frame()]

    def _cmd_2b(self, body: bytes) -> list[bytes]:  # GET_TUNING
        return [b"\x17" + struct.pack("<II", *self.tuning)]

    def _cmd_15(self, body: bytes) -> list[bytes]:  # SET_TUNING
        self.tuning = struct.unpack("<II", body[:8])
        self.log.append(("set_tuning", self.tuning))
        return [OK]

    def _cmd_28(self, body: bytes) -> list[bytes]:  # GET_CUSTOM_VARS
        text = ",".join(f"{k}:{v}" for k, v in self.custom_vars.items())
        return [b"\x15" + text.encode()]

    def _cmd_29(self, body: bytes) -> list[bytes]:  # SET_CUSTOM_VAR
        key, _, value = body.decode().partition(":")
        self.custom_vars[key] = value
        self.log.append(("set_custom_var", (key, value)))
        return [OK]

    def _cmd_40(self, body: bytes) -> list[bytes]:  # GET_DEFAULT_FLOOD_SCOPE
        if self.default_scope is None:
            return [b"\x1c"]
        name, key = self.default_scope
        return [b"\x1c" + name.encode().ljust(31, b"\x00") + key]

    def _cmd_3f(self, body: bytes) -> list[bytes]:  # SET_DEFAULT_FLOOD_SCOPE
        self.default_scope = None if not body else (_cstr(body[:31]), body[31:47])
        self.log.append(("set_default_scope", self.default_scope))
        return [OK]

    def _cmd_3b(self, body: bytes) -> list[bytes]:  # GET_AUTOADD_CONFIG
        return [bytes([0x19, *self.autoadd])]

    def _cmd_3a(self, body: bytes) -> list[bytes]:  # SET_AUTOADD_CONFIG
        self.autoadd = (body[0], body[1] if len(body) > 1 else 0)
        self.log.append(("set_autoadd", self.autoadd))
        return [OK]

    def _cmd_08(self, body: bytes) -> list[bytes]:  # SET_ADVERT_NAME
        self.name = body.decode()
        self.log.append(("set_name", self.name))
        return [OK]

    def _cmd_0e(self, body: bytes) -> list[bytes]:  # SET_ADVERT_LATLON
        lat, lon = struct.unpack("<ii", body[:8])
        self.lat, self.lon = lat / 1e6, lon / 1e6
        self.log.append(("set_coords", (self.lat, self.lon)))
        return [OK]

    def _cmd_0c(self, body: bytes) -> list[bytes]:  # SET_RADIO_TX_POWER
        power = struct.unpack("<i", body[:4])[0]
        if power > self.max_tx_power:
            return [error(6)]
        self.tx_power = power
        self.log.append(("set_tx_power", power))
        return [OK]

    def _cmd_0b(self, body: bytes) -> list[bytes]:  # SET_RADIO_PARAMS
        freq, bw = struct.unpack("<II", body[:8])
        sf, cr = body[8], body[9]
        repeat = len(body) > 10 and body[10] != 0
        if repeat and not self.repeat_allowed:
            self.log.append(("set_radio_rejected", repeat))
            return [error(6)]
        self.freq, self.bw, self.sf, self.cr = freq / 1000, bw / 1000, sf, cr
        self.repeat = repeat
        self.log.append(("set_radio", (self.freq, self.bw, sf, cr, repeat)))
        return [OK]

    def _cmd_26(self, body: bytes) -> list[bytes]:  # SET_OTHER_PARAMS
        self.other = {
            "manual_add_contacts": body[0],
            "telemetry": body[1],
            "adv_loc_policy": body[2],
            "multi_acks": body[3] if len(body) > 3 else 0,
        }
        self.log.append(("set_other", dict(self.other)))
        return [OK]

    def _cmd_3d(self, body: bytes) -> list[bytes]:  # SET_PATH_HASH_MODE
        self.path_hash_mode = body[1]
        self.log.append(("set_path_hash_mode", self.path_hash_mode))
        return [OK]

    def _cmd_25(self, body: bytes) -> list[bytes]:  # SET_DEVICE_PIN
        self.ble_pin = struct.unpack("<I", body[:4])[0]
        self.log.append(("set_pin", self.ble_pin))
        return [OK]

    def _cmd_20(self, body: bytes) -> list[bytes]:  # SET_CHANNEL
        idx = body[0]
        self.channels[idx] = (_cstr(body[1:33]), body[33:49])
        self.log.append(("set_channel", (idx, body[1:33], body[33:49])))
        return [OK]

    def _cmd_04(self, body: bytes) -> list[bytes]:  # GET_CONTACTS
        frames = [b"\x02" + struct.pack("<I", len(self.contacts))]
        frames += [self.contact_frame(c) for c in self.contacts.values()]
        frames.append(b"\x04" + struct.pack("<I", 0))
        return frames

    def _cmd_09(self, body: bytes) -> list[bytes]:  # ADD_UPDATE_CONTACT
        public_key = body[:32].hex()
        if public_key not in self.contacts and len(self.contacts) >= self.max_contacts:
            self.log.append(("add_contact_full", public_key))
            return [error(3)]
        update = public_key in self.contacts
        self.contacts[public_key] = {
            "public_key": public_key,
            "type": body[32],
            "flags": body[33],
            "out_path_len_byte": body[34],
            "out_path": body[35:99].rstrip(b"\x00"),
            "name": _cstr(body[99:131]),
            "last_advert": struct.unpack("<I", body[131:135])[0],
        }
        self.log.append(("update_contact" if update else "add_contact", (public_key, body[33])))
        return [OK]

    def _cmd_0f(self, body: bytes) -> list[bytes]:  # REMOVE_CONTACT
        public_key = body[:32].hex()
        if self.contacts.pop(public_key, None) is None:
            return [error(2)]
        self.log.append(("remove_contact", public_key))
        return [OK]

    def _cmd_17(self, body: bytes) -> list[bytes]:  # EXPORT_PRIVATE_KEY
        if self.export_disabled:
            return [b"\x0f"]
        return [b"\x0e" + self.private_key]

    def _cmd_18(self, body: bytes) -> list[bytes]:  # IMPORT_PRIVATE_KEY
        if self.import_disabled:
            self.log.append(("import_key_disabled", None))
            return [b"\x0f"]
        if len(body) != 64:
            return [error(6)]
        self.private_key = bytes(body)
        self.log.append(("import_key", derive_public_key(self.private_key).hex()))
        return [OK]

    def _cmd_13(self, body: bytes) -> list[bytes]:  # REBOOT
        self.log.append(("reboot", None))
        return []

    def names(self) -> list[str]:
        return [name for name, _detail in self.log]


@asynccontextmanager
async def connected_node(node: FakeNode | None = None, *, kind: str = "tcp"):
    """A FakeNode behind the real meshcore stack, set as the connected radio of
    the process-wide manager with setup complete. Restores the manager after."""
    from app.radio import radio_manager

    node = node or FakeNode()
    loop = await make_loopback(kind, responder=node)
    loop.mc.connection_manager._is_connected = True
    saved = {
        name: getattr(radio_manager, name)
        for name in (
            "_meshcore",
            "_setup_complete",
            "firmware_ver_code",
            "device_model",
            "device_info_loaded",
            "max_channels",
            "max_contacts",
            "path_hash_mode_supported",
            "path_hash_mode",
            "_connection_desired",
            "_last_connected",
        )
    }
    radio_manager._meshcore = loop.mc
    radio_manager._setup_complete = True
    radio_manager.firmware_ver_code = node.fw_ver
    radio_manager.device_model = "Heltec V3"
    radio_manager.device_info_loaded = True
    radio_manager.max_channels = node.max_channels
    radio_manager.max_contacts = node.max_contacts
    radio_manager.path_hash_mode_supported = True
    radio_manager.reset_cli_link()
    try:
        yield loop, node
    finally:
        from app import radio_sync

        await radio_sync.stop_background_contact_reconciliation()
        await radio_manager.stop_connection_monitor()
        loop.balance()
        await loop.close()
        for name, value in saved.items():
            setattr(radio_manager, name, value)
        radio_manager.reset_cli_link()
