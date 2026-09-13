"""Round-trip companion codecs against meshcore_py's MessageReader."""

from __future__ import annotations

import asyncio

import pytest
from meshcore.events import EventDispatcher, EventType
from meshcore.packets import PacketType
from meshcore.reader import MessageReader

from app.radio_proxy.protocol import (
    PROXY_MODEL_PREFIX,
    FrameAssembler,
    encode_ack,
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
    make_instance_id,
    parse_proxy_instance_id,
    parse_send_chan,
    parse_send_txt,
    parse_set_channel,
    proxy_model_string,
)


async def _read_event(payload: bytes, event_type: EventType, timeout: float = 1.0):
    dispatcher = EventDispatcher()
    await dispatcher.start()
    reader = MessageReader(dispatcher)
    waiter = asyncio.create_task(dispatcher.wait_for_event(event_type, timeout=timeout))
    await asyncio.sleep(0)
    await reader.handle_rx(bytearray(payload))
    event = await waiter
    await dispatcher.stop()
    return event


@pytest.mark.asyncio
async def test_self_info_round_trip():
    pubkey = bytes(range(32))
    frame = encode_self_info(public_key=pubkey, name="PascalNode", tx_power=14, radio_freq=910.525)
    event = await _read_event(frame, EventType.SELF_INFO)
    assert event is not None
    assert event.payload["public_key"] == pubkey.hex()
    assert event.payload["name"] == "PascalNode"
    assert event.payload["tx_power"] == 14


@pytest.mark.asyncio
async def test_device_info_model_is_meshloom_proxy():
    instance_id = make_instance_id()
    frame = encode_device_info(
        instance_id=instance_id,
        max_channels=40,
        max_contacts=350,
        fw_build="meshloom",
        version="1.2.3",
        path_hash_mode=1,
    )
    event = await _read_event(frame, EventType.DEVICE_INFO)
    assert event is not None
    assert event.payload["fw ver"] == 10
    assert event.payload["max_channels"] == 40
    assert event.payload["path_hash_mode"] == 1
    model = event.payload["model"]
    assert model.startswith(PROXY_MODEL_PREFIX)
    assert parse_proxy_instance_id(model) == instance_id
    assert parse_proxy_instance_id(proxy_model_string(instance_id)) == instance_id


@pytest.mark.asyncio
async def test_contacts_stream_round_trip():
    dispatcher = EventDispatcher()
    await dispatcher.start()
    reader = MessageReader(dispatcher)
    pubkey = bytes(range(32))
    waiter = asyncio.create_task(dispatcher.wait_for_event(EventType.CONTACTS, timeout=1))
    await asyncio.sleep(0)
    await reader.handle_rx(bytearray(encode_contact_start(1)))
    await reader.handle_rx(
        bytearray(
            encode_contact(
                public_key=pubkey,
                contact_type=1,
                name="Alice",
                last_advert=100,
                lastmod=200,
            )
        )
    )
    await reader.handle_rx(bytearray(encode_contact_end(200)))
    event = await waiter
    await dispatcher.stop()
    assert event is not None
    assert pubkey.hex() in event.payload
    assert event.payload[pubkey.hex()]["adv_name"] == "Alice"


@pytest.mark.asyncio
async def test_channel_and_messages_round_trip():
    secret = bytes(range(16))
    chan = await _read_event(
        encode_channel_info(channel_idx=3, name="Public", secret=secret),
        EventType.CHANNEL_INFO,
    )
    assert chan is not None
    assert chan.payload["channel_idx"] == 3
    assert chan.payload["channel_name"] == "Public"
    assert chan.payload["channel_secret"] == secret

    dm = await _read_event(
        encode_contact_msg_recv(
            pubkey_prefix=bytes.fromhex("aabbccddeeff"),
            text="hello",
            sender_timestamp=1700000000,
        ),
        EventType.CONTACT_MSG_RECV,
    )
    assert dm is not None
    assert dm.payload["text"] == "hello"
    assert dm.payload["pubkey_prefix"] == "aabbccddeeff"

    ch = await _read_event(
        encode_channel_msg_recv(channel_idx=0, text="hi", sender_timestamp=1700000001),
        EventType.CHANNEL_MSG_RECV,
    )
    assert ch is not None
    assert ch.payload["channel_idx"] == 0
    assert ch.payload["text"] == "hi"


@pytest.mark.asyncio
async def test_control_frames_round_trip():
    assert (await _read_event(encode_ok(), EventType.OK)) is not None
    err = await _read_event(encode_error(1), EventType.ERROR)
    assert err is not None
    assert (await _read_event(encode_disabled(), EventType.DISABLED)) is not None
    assert (await _read_event(encode_no_more_msgs(), EventType.NO_MORE_MSGS)) is not None
    assert (await _read_event(encode_messages_waiting(), EventType.MESSAGES_WAITING)) is not None
    now = await _read_event(encode_current_time(12345), EventType.CURRENT_TIME)
    assert now is not None
    assert now.payload["time"] == 12345

    sent = await _read_event(
        encode_msg_sent(msg_type=0, expected_ack=b"\x11\x22\x33\x44", suggested_timeout_ms=2500),
        EventType.MSG_SENT,
    )
    assert sent is not None
    assert sent.payload["expected_ack"] == b"\x11\x22\x33\x44"
    assert sent.payload["suggested_timeout"] == 2500

    ack = await _read_event(
        encode_ack(ack_code=b"\x11\x22\x33\x44", trip_time_ms=12), EventType.ACK
    )
    assert ack is not None
    assert ack.payload["code"] == "11223344"

    log = await _read_event(
        encode_log_data(payload=b"\xaa\xbb", snr=3.0, rssi=-80),
        EventType.RX_LOG_DATA,
    )
    assert log is not None
    assert log.payload["payload"] == "aabb"


def test_frame_assembler_and_tx_marker():
    payload = encode_ok()
    wire = encode_tx_frame(payload)
    assert wire[0] == 0x3E
    size = int.from_bytes(wire[1:3], "little")
    assert wire[3:] == payload
    assert size == len(payload)

    assembler = FrameAssembler()
    client_frame = bytes([0x3C]) + len(payload).to_bytes(2, "little") + payload
    assert assembler.feed(client_frame[:2]) == []
    assert assembler.feed(client_frame[2:]) == [payload]


def test_parse_send_commands():
    dest = bytes.fromhex("aabbccddeeff")
    txt = bytes([2, 0, 0]) + (111).to_bytes(4, "little") + dest + b"hello"
    parsed = parse_send_txt(txt)
    assert parsed.txt_type == 0
    assert parsed.dest == dest
    assert parsed.text == "hello"

    chan = bytes([3, 0, 4]) + (222).to_bytes(4, "little") + b"body"
    parsed_chan = parse_send_chan(chan)
    assert parsed_chan.channel_idx == 4
    assert parsed_chan.text == "body"

    name = b"#room".ljust(32, b"\x00")
    secret = bytes(range(16))
    set_chan = bytes([0x20, 2]) + name + secret
    parsed_set = parse_set_channel(set_chan)
    assert parsed_set.channel_idx == 2
    assert parsed_set.name == "#room"
    assert parsed_set.secret == secret


def test_packet_type_disabled_is_15():
    assert PacketType.DISABLED.value == 15
