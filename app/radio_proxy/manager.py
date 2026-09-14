"""TCP companion server that presents Meshloom as a virtual radio."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from meshcore.packets import CommandType

from app.channel_constants import is_public_channel_key
from app.loop_lock import LoopBoundLock
from app.radio_proxy.policy import CommandDisposition, classify_command, send_txt_is_plain
from app.radio_proxy.protocol import (
    FrameAssembler,
    encode_ack,
    encode_battery,
    encode_channel_info,
    encode_channel_msg_recv,
    encode_contact,
    encode_contact_end,
    encode_contact_msg_recv,
    encode_contact_start,
    encode_current_time,
    encode_device_info,
    encode_disabled,
    encode_error,
    encode_log_data,
    encode_messages_waiting,
    encode_msg_sent,
    encode_no_more_msgs,
    encode_ok,
    encode_self_info,
    encode_tx_frame,
    is_empty_channel_secret,
    make_instance_id,
    parse_add_contact,
    parse_get_channel_idx,
    parse_get_contacts_since,
    parse_remove_contact_key,
    parse_send_chan,
    parse_send_txt,
    parse_set_channel,
    proxy_model_string,
)
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

MESSAGE_QUEUE_MAX = 64
LOG_QUEUE_MAX = 256
ACK_TTL_SECONDS = 300.0
DEFAULT_BIND = "0.0.0.0"
DEFAULT_PORT = 5001
DEFAULT_MAX_CLIENTS = 8
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"})


def _put_drop_oldest(queue: asyncio.Queue[bytes], item: bytes) -> bool:
    try:
        queue.put_nowait(item)
        return False
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            return True
        return True


def hosts_overlap(left: str, right: str) -> bool:
    a = left.strip().lower()
    b = right.strip().lower()
    if a == b:
        return True
    return a in LOOPBACK_HOSTS and b in LOOPBACK_HOSTS


def targets_this_proxy(host: str, port: int, *, bind: str, listen_port: int) -> bool:
    if int(port) != int(listen_port):
        return False
    bind_n = bind.strip().lower()
    host_n = host.strip().lower()
    if bind_n in {"0.0.0.0", "::"}:
        return host_n in LOOPBACK_HOSTS
    return hosts_overlap(host, bind_n)


def is_local_proxy_target(
    host: str,
    port: int,
    *,
    bind: str,
    listen_port: int,
    listening: bool,
) -> bool:
    if not listening:
        return False
    return targets_this_proxy(host, port, bind=bind, listen_port=listen_port)


def _hex_key(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return value.hex().lower()
    return value.strip().lower()


def _key_bytes(hex_key: str) -> bytes:
    raw = bytes.fromhex(hex_key)
    if len(raw) != 32:
        raise ValueError("public key must be 32 bytes")
    return raw


def _channel_secret(hex_key: str) -> bytes:
    raw = bytes.fromhex(hex_key)
    if len(raw) != 16:
        raise ValueError("channel key must be 16 bytes")
    return raw


def _channel_key_from_secret(secret: bytes) -> str:
    return secret.hex().upper()


def project_host_channels(channels: list[Any]) -> list[Any]:
    public = [ch for ch in channels if is_public_channel_key(ch.key)]
    rest = sorted(
        (ch for ch in channels if not is_public_channel_key(ch.key)),
        key=lambda ch: ch.key.upper(),
    )
    if public:
        return [public[0], *rest]
    return rest


@dataclass
class OverlayContact:
    public_key: str
    name: str
    contact_type: int


@dataclass
class QueuedMessage:
    kind: str
    frame: bytes
    channel_key: str | None = None
    text: str | None = None
    sender_timestamp: int | None = None


@dataclass
class ProxySettings:
    enabled: bool = False
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    max_clients: int = DEFAULT_MAX_CLIENTS


class ProxySession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.slots: dict[int, str] = {}
        self.cleared: set[int] = set()
        self.added: dict[str, OverlayContact] = {}
        self.hidden: set[str] = set()
        self.message_queue: asyncio.Queue[QueuedMessage] = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAX)
        self.log_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=LOG_QUEUE_MAX)
        self.dropped_messages = 0
        self.dropped_logs = 0
        self._closed = False
        self._write_lock = asyncio.Lock()
        self._writer_task: asyncio.Task[None] | None = None

    def start_writer(self) -> None:
        self._writer_task = asyncio.create_task(self._writer_loop())

    async def _writer_loop(self) -> None:
        try:
            while not self._closed:
                frame = await self.log_queue.get()
                await self._write_frame(frame)
        except (ConnectionError, asyncio.CancelledError, RuntimeError):
            return
        except Exception:
            logger.debug("Proxy session writer failed", exc_info=True)

    async def _write_frame(self, payload: bytes) -> None:
        if self._closed:
            return
        async with self._write_lock:
            if self._closed:
                return
            self.writer.write(encode_tx_frame(payload))
            await self.writer.drain()

    async def write_response(self, payload: bytes) -> None:
        await self._write_frame(payload)

    def push_log(self, payload: bytes) -> None:
        if _put_drop_oldest(self.log_queue, payload):
            self.dropped_logs += 1

    def enqueue_message(self, item: QueuedMessage) -> None:
        try:
            self.message_queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self.message_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.message_queue.put_nowait(item)
            except asyncio.QueueFull:
                self.dropped_messages += 1
                return
            self.dropped_messages += 1
        self.push_log(encode_messages_waiting())

    def dequeue_message(self) -> QueuedMessage | None:
        try:
            item = self.message_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        if item.kind == "chan" and item.channel_key:
            idx = next(
                (
                    slot
                    for slot, key in self.slots.items()
                    if key.upper() == item.channel_key.upper()
                ),
                None,
            )
            if idx is None:
                return self.dequeue_message()
            if item.text is not None:
                return QueuedMessage(
                    kind=item.kind,
                    frame=encode_channel_msg_recv(
                        channel_idx=idx,
                        text=item.text,
                        sender_timestamp=int(item.sender_timestamp or 0),
                    ),
                    channel_key=item.channel_key,
                    text=item.text,
                    sender_timestamp=item.sender_timestamp,
                )
        return item

    async def close(self) -> None:
        self._closed = True
        if self._writer_task:
            self._writer_task.cancel()
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


class RadioProxyManager:
    def __init__(self) -> None:
        self.instance_id = make_instance_id()
        self._settings = ProxySettings()
        self._server: asyncio.AbstractServer | None = None
        self._sessions: set[ProxySession] = set()
        self._pending_acks: dict[str, tuple[ProxySession, float]] = {}
        self._last_error: str | None = None
        self._lock = LoopBoundLock()

    @property
    def settings(self) -> ProxySettings:
        return self._settings

    @property
    def listening(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def listen_port(self) -> int | None:
        server = self._server
        sockets = getattr(server, "sockets", None)
        if not sockets:
            return None
        return int(sockets[0].getsockname()[1])

    @property
    def client_count(self) -> int:
        return len(self._sessions)

    @property
    def dropped_messages(self) -> int:
        return sum(session.dropped_messages for session in self._sessions)

    @property
    def dropped_logs(self) -> int:
        return sum(session.dropped_logs for session in self._sessions)

    def status_dict(self) -> dict[str, Any]:
        return {
            "enabled": self._settings.enabled,
            "bind": self._settings.bind,
            "port": self._settings.port,
            "max_clients": self._settings.max_clients,
            "listening": self.listening,
            "client_count": self.client_count,
            "dropped_messages": self.dropped_messages,
            "dropped_logs": self.dropped_logs,
            "last_error": self._last_error,
            "instance_id": self.instance_id,
            "model": proxy_model_string(self.instance_id),
        }

    def would_loop_transport(self, host: str, port: int) -> bool:
        listen = self.listen_port if self.listening else self._settings.port
        return is_local_proxy_target(
            host,
            port,
            bind=self._settings.bind,
            listen_port=listen or self._settings.port,
            listening=self.listening,
        )

    async def apply_settings(self, settings: ProxySettings) -> dict[str, Any]:
        async with self._lock:
            self._settings = settings
            await self._restart_locked()
        return self.status_dict()

    async def start_from_db(self) -> None:
        from app.repository.radio_proxy import RadioProxyRepository

        stored = await RadioProxyRepository.get()
        async with self._lock:
            self._settings = stored
            await self._restart_locked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _restart_locked(self) -> None:
        await self._stop_locked()
        if not self._settings.enabled:
            return
        try:
            self._server = await asyncio.start_server(
                self._on_client,
                host=self._settings.bind,
                port=self._settings.port,
            )
            self._last_error = None
            logger.info(
                "Radio proxy listening on %s:%d (model=%s)",
                self._settings.bind,
                self._settings.port,
                proxy_model_string(self.instance_id),
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("Failed to start radio proxy")

    async def _stop_locked(self) -> None:
        server = self._server
        self._server = None
        sessions = list(self._sessions)
        self._sessions.clear()
        self._pending_acks.clear()
        for session in sessions:
            await session.close()
        if server is not None:
            server.close()
            await server.wait_closed()

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._sessions) >= self._settings.max_clients:
            writer.close()
            await writer.wait_closed()
            return
        session = ProxySession(reader, writer)
        self._sessions.add(session)
        session.start_writer()
        try:
            await self._session_loop(session)
        finally:
            self._sessions.discard(session)
            await session.close()

    async def _session_loop(self, session: ProxySession) -> None:
        assembler = FrameAssembler()
        while not session._closed:
            try:
                chunk = await session.reader.read(4096)
            except (ConnectionError, asyncio.CancelledError):
                return
            if not chunk:
                return
            for payload in assembler.feed(chunk):
                await self._handle_command(session, payload)

    async def _handle_command(self, session: ProxySession, payload: bytes) -> None:
        if not payload:
            await session.write_response(encode_error())
            return
        code = payload[0]
        disposition = classify_command(code)
        try:
            if disposition is CommandDisposition.DISABLED:
                await session.write_response(encode_disabled())
                return
            if disposition is CommandDisposition.ERROR:
                await session.write_response(encode_error())
                return
            if disposition is CommandDisposition.SEND:
                await self._handle_send(session, payload)
                return
            await self._handle_virtual(session, payload)
        except Exception:
            logger.exception("Radio proxy command %s failed", code)
            await session.write_response(encode_error())

    async def _handle_virtual(self, session: ProxySession, payload: bytes) -> None:
        code = payload[0]
        if code == CommandType.APP_START.value:
            await session.write_response(await self._self_info_frame())
            return
        if code == CommandType.DEVICE_QEURY.value:
            await session.write_response(self._device_info_frame())
            return
        if code == CommandType.GET_DEVICE_TIME.value:
            await session.write_response(encode_current_time(int(time.time())))
            return
        if code == CommandType.SET_DEVICE_TIME.value:
            await session.write_response(encode_ok())
            return
        if code == CommandType.GET_BATT_AND_STORAGE.value:
            await session.write_response(self._battery_frame())
            return
        if code == CommandType.HAS_CONNECTION.value:
            from app.services.radio_runtime import radio_runtime

            if radio_runtime.is_connected:
                await session.write_response(encode_ok())
            else:
                await session.write_response(encode_error())
            return
        if code == CommandType.RESET_PATH.value:
            await session.write_response(encode_ok())
            return
        if code == CommandType.GET_CONTACTS.value:
            since = parse_get_contacts_since(payload)
            await self._write_contacts(session, since=since)
            return
        if code == CommandType.GET_CONTACT_BY_KEY.value:
            await self._write_contact_by_key(session, payload)
            return
        if code == CommandType.ADD_UPDATE_CONTACT.value:
            parsed = parse_add_contact(payload)
            key = _hex_key(parsed.public_key)
            session.hidden.discard(key)
            session.added[key] = OverlayContact(
                public_key=key,
                name=parsed.name,
                contact_type=parsed.contact_type,
            )
            await session.write_response(encode_ok())
            return
        if code == CommandType.REMOVE_CONTACT.value:
            key = _hex_key(parse_remove_contact_key(payload))
            session.added.pop(key, None)
            session.hidden.add(key)
            await session.write_response(encode_ok())
            return
        if code == CommandType.GET_CHANNEL.value:
            await session.write_response(
                await self._channel_info(session, parse_get_channel_idx(payload))
            )
            return
        if code == CommandType.SET_CHANNEL.value:
            await self._set_channel(session, parse_set_channel(payload))
            return
        if code == CommandType.SYNC_NEXT_MESSAGE.value:
            await self._sync_next_message(session)
            return
        await session.write_response(encode_error())

    async def _handle_send(self, session: ProxySession, payload: bytes) -> None:
        from app.services.radio_runtime import radio_runtime

        if not radio_runtime.is_connected:
            self._last_error = "radio disconnected"
            await session.write_response(encode_error())
            return
        code = payload[0]
        if code == CommandType.SEND_TXT_MSG.value:
            parsed = parse_send_txt(payload)
            if not send_txt_is_plain(parsed.txt_type):
                await session.write_response(encode_error())
                return
            await self._send_direct(session, parsed)
            return
        if code == CommandType.SEND_CHANNEL_TXT_MSG.value:
            parsed = parse_send_chan(payload)
            await self._send_channel(session, parsed)
            return
        await session.write_response(encode_error())

    async def _self_info_frame(self) -> bytes:
        from app.keystore import get_public_key
        from app.services.radio_runtime import radio_runtime

        mc = getattr(radio_runtime, "meshcore", None)
        info = getattr(mc, "self_info", None) or {}
        pubkey_hex = info.get("public_key") or ""
        pubkey = (
            bytes.fromhex(pubkey_hex)
            if len(pubkey_hex) == 64
            else (get_public_key() or b"\x00" * 32)
        )
        if len(pubkey) != 32:
            pubkey = (pubkey + b"\x00" * 32)[:32]
        telemetry = (
            ((info.get("telemetry_mode_env") or 0) << 4)
            | ((info.get("telemetry_mode_loc") or 0) << 2)
            | (info.get("telemetry_mode_base") or 0)
        )
        return encode_self_info(
            public_key=pubkey,
            name=str(info.get("name") or "Meshloom"),
            adv_type=int(info.get("adv_type") or 1),
            tx_power=int(info.get("tx_power") or 0),
            max_tx_power=int(info.get("max_tx_power") or 22),
            lat=float(info.get("adv_lat") or 0.0),
            lon=float(info.get("adv_lon") or 0.0),
            multi_acks=int(info.get("multi_acks") or 0),
            adv_loc_policy=int(info.get("adv_loc_policy") or 0),
            telemetry_mode=telemetry,
            manual_add_contacts=bool(info.get("manual_add_contacts")),
            radio_freq=float(info.get("radio_freq") or 0.0),
            radio_bw=float(info.get("radio_bw") or 0.0),
            radio_sf=int(info.get("radio_sf") or 0),
            radio_cr=int(info.get("radio_cr") or 0),
        )

    def _device_info_frame(self) -> bytes:
        from app.services.radio_runtime import radio_runtime

        build = get_app_build_info()
        max_channels = getattr(radio_runtime, "max_channels", None) or 40
        path_hash_mode = getattr(radio_runtime, "path_hash_mode", 0) or 0
        return encode_device_info(
            instance_id=self.instance_id,
            max_contacts=510,
            max_channels=int(max_channels),
            fw_build=(build.version or "meshloom")[:12],
            version=(build.version or "")[:20],
            path_hash_mode=int(path_hash_mode),
        )

    def _battery_frame(self) -> bytes:
        from app.services.radio_stats import get_latest_radio_stats

        stats = get_latest_radio_stats() or {}
        return encode_battery(level_mv=int(stats.get("battery_mv") or 0))

    async def _visible_contacts(self, session: ProxySession) -> list[Any]:
        from app.repository import ContactRepository

        host = await ContactRepository.get_all(limit=10000)
        visible: dict[str, Any] = {}
        for contact in host:
            key = contact.public_key.lower()
            if len(key) != 64 or key in session.hidden:
                continue
            visible[key] = contact
        extras: list[OverlayContact] = []
        for key, overlay in session.added.items():
            if key in session.hidden:
                continue
            if key not in visible:
                extras.append(overlay)
        return [*visible.values(), *extras]

    async def _write_contacts(self, session: ProxySession, *, since: int) -> None:
        contacts = await self._visible_contacts(session)
        encoded: list[bytes] = []
        latest = 0
        for contact in contacts:
            if isinstance(contact, OverlayContact):
                lastmod = 0
                if since and lastmod < since:
                    continue
                encoded.append(
                    encode_contact(
                        public_key=_key_bytes(contact.public_key),
                        contact_type=contact.contact_type,
                        name=contact.name,
                    )
                )
                continue
            lastmod = int(contact.last_seen or contact.last_advert or 0)
            if since and lastmod < since:
                continue
            latest = max(latest, lastmod)
            path = bytes.fromhex(contact.direct_path or "") if contact.direct_path else b""
            encoded.append(
                encode_contact(
                    public_key=_key_bytes(contact.public_key),
                    contact_type=contact.type,
                    flags=contact.flags,
                    out_path=path,
                    out_path_len=contact.direct_path_len,
                    out_path_hash_mode=max(contact.direct_path_hash_mode, 0),
                    name=contact.name or "",
                    last_advert=int(contact.last_advert or 0),
                    lat=float(contact.lat or 0.0),
                    lon=float(contact.lon or 0.0),
                    lastmod=lastmod,
                )
            )
        await session.write_response(encode_contact_start(len(encoded)))
        for frame in encoded:
            await session.write_response(frame)
        await session.write_response(encode_contact_end(latest))

    async def _write_contact_by_key(self, session: ProxySession, payload: bytes) -> None:
        if len(payload) < 33:
            await session.write_response(encode_error())
            return
        key = _hex_key(payload[1:33])
        contacts = await self._visible_contacts(session)
        for contact in contacts:
            public_key = (
                contact.public_key
                if not isinstance(contact, OverlayContact)
                else contact.public_key
            )
            if public_key.lower() != key:
                continue
            if isinstance(contact, OverlayContact):
                await session.write_response(
                    encode_contact(
                        public_key=_key_bytes(contact.public_key),
                        contact_type=contact.contact_type,
                        name=contact.name,
                    )
                )
                return
            path = bytes.fromhex(contact.direct_path or "") if contact.direct_path else b""
            await session.write_response(
                encode_contact(
                    public_key=_key_bytes(contact.public_key),
                    contact_type=contact.type,
                    flags=contact.flags,
                    out_path=path,
                    out_path_len=contact.direct_path_len,
                    out_path_hash_mode=max(contact.direct_path_hash_mode, 0),
                    name=contact.name or "",
                    last_advert=int(contact.last_advert or 0),
                    lat=float(contact.lat or 0.0),
                    lon=float(contact.lon or 0.0),
                    lastmod=int(contact.last_seen or contact.last_advert or 0),
                )
            )
            return
        await session.write_response(encode_error())

    async def _channel_info(self, session: ProxySession, idx: int) -> bytes:
        from app.repository import ChannelRepository

        if idx in session.cleared:
            return encode_channel_info(channel_idx=idx)
        if idx in session.slots:
            channel = await ChannelRepository.get_by_key(session.slots[idx])
            if channel is None:
                return encode_channel_info(channel_idx=idx)
            return encode_channel_info(
                channel_idx=idx,
                name=channel.name,
                secret=_channel_secret(channel.key),
            )
        channels = project_host_channels(await ChannelRepository.get_all())
        if 0 <= idx < len(channels):
            channel = channels[idx]
            session.slots[idx] = channel.key.upper()
            return encode_channel_info(
                channel_idx=idx,
                name=channel.name,
                secret=_channel_secret(channel.key),
            )
        return encode_channel_info(channel_idx=idx)

    async def _set_channel(self, session: ProxySession, parsed) -> None:
        from app.repository import ChannelRepository

        empty = not parsed.name.strip() or is_empty_channel_secret(parsed.secret)
        if empty:
            session.slots.pop(parsed.channel_idx, None)
            session.cleared.add(parsed.channel_idx)
            await session.write_response(encode_ok())
            return
        key = _channel_key_from_secret(parsed.secret)
        session.cleared.discard(parsed.channel_idx)
        session.slots[parsed.channel_idx] = key
        is_hashtag = parsed.name.startswith("#")
        await ChannelRepository.upsert_name(key, parsed.name, is_hashtag=is_hashtag)
        await session.write_response(encode_ok())

    async def _sync_next_message(self, session: ProxySession) -> None:
        item = session.dequeue_message()
        if item is None:
            await session.write_response(encode_no_more_msgs())
            return
        await session.write_response(item.frame)

    async def _resolve_dest(self, session: ProxySession, dest: bytes) -> Any | None:
        from app.repository import AmbiguousPublicKeyPrefixError, ContactRepository

        prefix = dest.hex().lower()
        overlay_hits = [
            item
            for item in session.added.values()
            if item.public_key.startswith(prefix) and item.public_key not in session.hidden
        ]
        try:
            host = await ContactRepository.get_by_key_or_prefix(prefix)
        except AmbiguousPublicKeyPrefixError:
            return None
        if host and host.public_key.lower() in session.hidden:
            host = None
        if overlay_hits and host and overlay_hits[0].public_key != host.public_key.lower():
            return None
        if len(overlay_hits) > 1:
            return None
        if host:
            return host
        return overlay_hits[0] if overlay_hits else None

    async def _send_direct(self, session: ProxySession, parsed) -> None:
        from app.repository import ContactRepository, MessageRepository
        from app.services.dm_ack_tracker import track_pending_ack
        from app.services.message_send import send_direct_message_to_contact
        from app.services.radio_runtime import radio_runtime
        from app.websocket import broadcast_event

        dest = await self._resolve_dest(session, parsed.dest)
        if dest is None or isinstance(dest, OverlayContact):
            await session.write_response(encode_error())
            return
        try:
            result = await send_direct_message_to_contact(
                contact=dest,
                text=parsed.text,
                radio_manager=radio_runtime,
                broadcast_fn=broadcast_event,
                track_pending_ack_fn=track_pending_ack,
                now_fn=time.time,
                arm_retries=False,
                message_repository=MessageRepository,
                contact_repository=ContactRepository,
            )
        except HTTPException:
            await session.write_response(encode_error())
            return
        ack = bytes.fromhex(result.expected_ack) if result.expected_ack else b"\x00" * 4
        if result.expected_ack:
            self._pending_acks[result.expected_ack.lower()] = (
                session,
                time.monotonic() + ACK_TTL_SECONDS,
            )
            if result.message.acked:
                self.notify_ack(result.expected_ack)
        await session.write_response(
            encode_msg_sent(
                msg_type=0,
                expected_ack=ack,
                suggested_timeout_ms=result.suggested_timeout_ms,
            )
        )

    def _strip_radio_prefix(self, text: str) -> str:
        from app.services.radio_runtime import radio_runtime

        mc = getattr(radio_runtime, "meshcore", None)
        info = getattr(mc, "self_info", None) or {}
        name = str(info.get("name") or "")
        prefix = f"{name}: "
        if name and text.startswith(prefix):
            return text[len(prefix) :]
        return text

    async def _send_channel(self, session: ProxySession, parsed) -> None:
        from app.repository import ChannelRepository, MessageRepository
        from app.services.message_send import send_channel_message_to_channel
        from app.services.radio_runtime import radio_runtime
        from app.websocket import broadcast_error, broadcast_event

        key = session.slots.get(parsed.channel_idx)
        if not key:
            await session.write_response(encode_error())
            return
        channel = await ChannelRepository.get_by_key(key)
        if channel is None:
            await session.write_response(encode_error())
            return
        body = self._strip_radio_prefix(parsed.text)
        try:
            await send_channel_message_to_channel(
                channel=channel,
                channel_key_upper=channel.key.upper(),
                key_bytes=_channel_secret(channel.key),
                text=body,
                radio_manager=radio_runtime,
                broadcast_fn=broadcast_event,
                error_broadcast_fn=broadcast_error,
                now_fn=time.time,
                temp_radio_slot=0,
                message_repository=MessageRepository,
            )
        except HTTPException:
            await session.write_response(encode_error())
            return
        await session.write_response(encode_ok())

    def notify_ack(self, ack_code: str) -> None:
        self._expire_acks()
        item = self._pending_acks.pop(ack_code.lower(), None)
        if item is None:
            return
        session, _expires = item
        if session._closed:
            return
        try:
            session.push_log(encode_ack(ack_code=bytes.fromhex(ack_code)))
        except ValueError:
            return

    def notify_broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        if event_type == "message":
            self._fanout_message(data)
        elif event_type == "raw_packet":
            self._fanout_raw(data)

    def _fanout_message(self, data: dict[str, Any]) -> None:
        # Outgoing messages are not relayed. The companion protocol can say "this
        # contact sent you this"; it has no frame for "this node sent this from
        # somewhere else", so relaying one arrives as an incoming message and the
        # client files the operator's own words under the contact — with no RF
        # metadata, since it never crossed the air. A client that sent it already
        # knows; a client that did not cannot be told truthfully.
        if data.get("outgoing"):
            return
        msg_type = data.get("type")
        text = str(data.get("text") or "")
        timestamp = int(data.get("sender_timestamp") or data.get("received_at") or time.time())
        if msg_type == "PRIV":
            sender = str(data.get("sender_key") or data.get("conversation_key") or "")
            prefix = bytes.fromhex(sender[:12]) if len(sender) >= 12 else b"\x00" * 6
            frame = encode_contact_msg_recv(
                pubkey_prefix=prefix,
                text=text,
                sender_timestamp=timestamp,
            )
            for session in list(self._sessions):
                session.enqueue_message(QueuedMessage(kind="dm", frame=frame))
            return
        if msg_type != "CHAN":
            return
        channel_key = str(data.get("conversation_key") or "").upper()
        for session in list(self._sessions):
            idx = next(
                (slot for slot, key in session.slots.items() if key.upper() == channel_key),
                None,
            )
            if idx is None:
                continue
            session.enqueue_message(
                QueuedMessage(
                    kind="chan",
                    frame=encode_channel_msg_recv(
                        channel_idx=idx,
                        text=text,
                        sender_timestamp=timestamp,
                    ),
                    channel_key=channel_key,
                    text=text,
                    sender_timestamp=timestamp,
                )
            )

    def _fanout_raw(self, data: dict[str, Any]) -> None:
        # Every payload type, as the radio reports it. The firmware emits RX_LOG_DATA
        # for everything it hears, so a client on the companion port sees adverts,
        # paths, acks and traces; forwarding only GROUP_TEXT made the proxy a
        # narrower radio than the one it stands in for, and left every view built on
        # observed traffic — the visualiser, the packet feed, the node map — empty
        # behind it. This is the receive path: it carries no command authority, and
        # what a client may ask the radio to do is still decided by classify_command.
        hex_data = str(data.get("data") or "")
        if not hex_data:
            return
        try:
            payload = bytes.fromhex(hex_data)
        except ValueError:
            return
        frame = encode_log_data(
            payload=payload,
            snr=float(data.get("snr") or 0.0),
            rssi=int(data.get("rssi") or 0),
        )
        for session in list(self._sessions):
            session.push_log(frame)

    def _expire_acks(self) -> None:
        now = time.monotonic()
        expired = [
            code for code, (_session, expires) in self._pending_acks.items() if expires <= now
        ]
        for code in expired:
            self._pending_acks.pop(code, None)


radio_proxy_manager = RadioProxyManager()
