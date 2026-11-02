"""The relay frame codec against the golden vectors (MSHL-87).

The vectors are ``tests/fixtures/remote_frames_vectors.json``, the byte-for-byte copy
pinned by sha256 in ``tests/test_remote_identity.py``. Every frame is encoded and
decoded, every multi-frame message splits into its frames, and every invalid message
gives the contract's verdict. The instance HELLO the relay client builds follows the
instance role rules (``hello_roles``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services import remote_frames as rf
from app.services import remote_relay

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "remote_frames_vectors.json").read_text()
)
FRAMES = {frame["id"]: frame for frame in VECTORS["frames"]}


def _payload(spec: dict[str, Any]) -> bytes:
    if "json" in spec:
        return rf.json_payload(spec["json"])
    if "utf8" in spec:
        return spec["utf8"].encode()
    if "hex" in spec:
        return bytes.fromhex(spec["hex"])
    if "close" in spec:
        code, reason = spec["close"]
        return rf.close_payload(code, reason)
    if "u32" in spec:
        return rf.credit_payload(spec["u32"])
    raise AssertionError(spec)


def _flags(names: list[str]) -> int:
    return sum(rf.FLAGS[name] for name in names)


def test_constants_match_the_vectors() -> None:
    assert VECTORS["protocol"] == rf.PROTOCOL
    assert VECTORS["layout"]["header_bytes"] == rf.HEADER_BYTES
    assert VECTORS["types"] == rf.TYPES
    assert {rf.TYPES[name] for name in VECTORS["json_payload_types"]} == rf.JSON_TYPES
    assert VECTORS["flags"] == rf.FLAGS
    limits = VECTORS["limits"]
    for name, (default, maximum, floor) in rf.LIMITS.items():
        assert limits[name] == {"default": default, "max": maximum, "min": floor}
    assert limits["max_header_bytes"] == rf.MAX_HEADER_BYTES
    assert limits["max_message_bytes"] == rf.MAX_MESSAGE_BYTES
    assert limits["max_ws_message_bytes"] == rf.MAX_WS_MESSAGE_BYTES
    assert limits["max_credit_window"] == rf.MAX_CREDIT_WINDOW
    assert limits["close_reason_max_bytes"] == rf.CLOSE_REASON_MAX_BYTES
    assert limits["hello_timeout_s"] == remote_relay.HELLO_TIMEOUT_S
    assert limits["ws_close_linger_s"] == remote_relay.WS_CLOSE_LINGER_S
    window = limits["batch_window_ms"]
    assert window["min"] <= remote_relay.BATCH_WINDOW_S * 1000 <= window["max"]
    assert {
        name: limits[name]["default"] for name in ("max_streams", "max_chunk", "initial_credit")
    } == remote_relay.WANTED_LIMITS


@pytest.mark.parametrize("frame", VECTORS["frames"], ids=lambda f: f["id"])
def test_every_frame_encodes_and_decodes(frame: dict[str, Any]) -> None:
    payload = _payload(frame["payload"])
    flags = _flags(frame["flags"])
    encoded = rf.encode_frame(rf.TYPES[frame["type"]], frame["stream_id"], flags, payload)
    assert encoded.hex() == frame["frame_hex"]
    decoded = rf.decode_message(bytes.fromhex(frame["frame_hex"]))
    assert decoded.too_large == [] and decoded.unknown_types == 0
    assert decoded.frames == [rf.Frame(rf.TYPES[frame["type"]], frame["stream_id"], flags, payload)]
    if "json" in frame["payload"]:
        assert decoded.frames[0].json() == frame["payload"]["json"]
    if "close" in frame["payload"]:
        assert rf.parse_close(payload) == tuple(frame["payload"]["close"])
    if "u32" in frame["payload"]:
        assert rf.parse_credit(payload) == frame["payload"]["u32"]


def test_an_empty_close_is_1005() -> None:
    assert rf.parse_close(_payload(FRAMES["ws_close_empty"]["payload"])) == (1005, "")
    assert rf.close_payload(1005) == b""


@pytest.mark.parametrize("message", VECTORS["messages"], ids=lambda m: m["id"])
def test_messages_split_into_their_frames(message: dict[str, Any]) -> None:
    joined = b"".join(bytes.fromhex(FRAMES[i]["frame_hex"]) for i in message["frames"])
    assert joined.hex() == message["message_hex"]
    decoded = rf.decode_message(bytes.fromhex(message["message_hex"]))
    assert [
        rf.encode_frame(f.type, f.stream_id, f.flags, f.payload).hex() for f in decoded.frames
    ] == [FRAMES[i]["frame_hex"] for i in message["frames"]]


def _message(parts: list[dict[str, Any]]) -> bytes:
    out = b""
    for part in parts:
        out += (
            bytes.fromhex(part["hex"])
            if "hex" in part
            else bytes.fromhex(part["fill"]) * part["count"]
        )
    return out


@pytest.mark.parametrize("case", VECTORS["invalid_messages"], ids=lambda c: c["id"])
def test_invalid_messages(case: dict[str, Any]) -> None:
    data = _message(case["parts"])
    if case["expect"] in ("cancel", "ignore"):
        decoded = rf.decode_message(data)
        if case["expect"] == "cancel":
            assert decoded.too_large == case["cancel_streams"]
            assert len(decoded.frames) == case["frames_after"]
        else:
            assert decoded.unknown_types == 1 and len(decoded.frames) == 1
        return
    with pytest.raises(rf.FrameError) as exc:
        rf.decode_message(data)
    assert exc.value.reason == case["expect"]


def test_effective_limits_clamp_to_the_contract() -> None:
    assert rf.effective_limits(None) == {
        "max_streams": 64,
        "max_chunk": 262144,
        "initial_credit": 1048576,
    }
    assert rf.effective_limits({"max_streams": 0, "max_chunk": 1, "initial_credit": 10**9}) == {
        "max_streams": 1,
        "max_chunk": 16384,
        "initial_credit": 16777216,
    }
    # initial_credit never below max_chunk.
    assert (
        rf.effective_limits({"max_chunk": 262144, "initial_credit": 16384})["initial_credit"]
        == 262144
    )


def test_relay_hello_vector_validates() -> None:
    assert remote_relay.parse_relay_hello(FRAMES["hello_relay"]["payload"]["json"]) is not None
    for broken in ({"role": "instance"}, {"protocol": 2}, {"server_time": "x"}):
        assert (
            remote_relay.parse_relay_hello({**FRAMES["hello_relay"]["payload"]["json"], **broken})
            is None
        )


async def test_the_instance_hello_follows_the_role_rules(test_db) -> None:
    hello = await remote_relay.build_hello()
    rules = VECTORS["hello_roles"]["instance"]
    assert set(rules["required"]) <= set(hello)
    assert hello["protocol"] == 1 and hello["role"] == "instance"
    install_kinds = rules["properties"]["instance"]["properties"]["install_kind"]["enum"]
    assert hello["instance"]["install_kind"] in install_kinds and hello["instance"]["version"]
    assert set(hello["plugins"]) == {"enabled", "isolation"}
    assert isinstance(hello["plugins"]["enabled"], bool)
    assert hello["plugins"]["isolation"] in ("none", "setuid", "bwrap", None)
    assert "bots_enabled" not in hello
    assert hello["gate"] == {"gen": 0, "revoked": []}
    assert hello["features"] == []
