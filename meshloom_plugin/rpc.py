"""JSON-RPC 2.0 over NDJSON: framing and message helpers for both ends.

One JSON object per line, UTF-8, at most MAX_MESSAGE_BYTES per line (newline
excluded). Batches are not supported. Standard library only: the child imports
this before its import surface is frozen, the server imports it as is.
"""

import json
import re
from typing import Any

MAX_MESSAGE_BYTES = 100 * 1024
# Arrays and objects nested deeper than this are refused before parsing, so a
# hostile peer never relies on the parser's recursion limit (both ends decode
# with this function).
MAX_NESTING_DEPTH = 64

_JSON_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_JSON_BRACKET = re.compile(r"[\[\]{}]")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HANDLER_ERROR = -32000
OVERLOADED = -32001
CANCELLED = -32800


class FramingError(ValueError):
    """The stream broke the framing rules (a line over the cap): the peer is dropped."""


class ProtocolError(ValueError):
    """A line that is not a valid JSON-RPC object. `code` is the JSON-RPC error code."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class LineDecoder:
    """Splits a byte stream into lines, refusing any line longer than the cap."""

    def __init__(self, max_bytes: int = MAX_MESSAGE_BYTES):
        self._max = max_bytes
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        lines: list[bytes] = []
        start = 0
        while True:
            end = self._buf.find(b"\n", start)
            if end < 0:
                break
            if end - start > self._max:
                raise FramingError(f"message over {self._max} bytes")
            lines.append(bytes(self._buf[start:end]))
            start = end + 1
        del self._buf[:start]
        if len(self._buf) > self._max:
            raise FramingError(f"message over {self._max} bytes")
        return lines


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _nesting_depth(text: str) -> int:
    depth = deepest = 0
    for bracket in _JSON_BRACKET.findall(_JSON_STRING.sub("", text)):
        if bracket in "[{":
            depth += 1
            deepest = max(deepest, depth)
        else:
            depth -= 1
    return deepest


def decode(line: bytes) -> dict[str, Any]:
    """Parse one line into a JSON-RPC message object (request, notification or response)."""
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(PARSE_ERROR, "parse error: not UTF-8") from None
    if _nesting_depth(text) > MAX_NESTING_DEPTH:
        raise ProtocolError(PARSE_ERROR, f"parse error: nested deeper than {MAX_NESTING_DEPTH}")
    try:
        obj = json.loads(text, parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError(PARSE_ERROR, f"parse error: {type(exc).__name__}") from None
    if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
        raise ProtocolError(INVALID_REQUEST, "not a JSON-RPC 2.0 object")
    if "method" in obj:
        if not isinstance(obj["method"], str):
            raise ProtocolError(INVALID_REQUEST, "method must be a string")
        if "params" in obj and not isinstance(obj["params"], dict | list):
            raise ProtocolError(INVALID_REQUEST, "params must be an object or an array")
    elif "result" not in obj and "error" not in obj:
        raise ProtocolError(INVALID_REQUEST, "neither a request nor a response")
    msg_id = obj.get("id")
    if isinstance(msg_id, bool) or not (msg_id is None or isinstance(msg_id, str | int)):
        raise ProtocolError(INVALID_REQUEST, "id must be a string, an integer or null")
    return obj


def encode(message: dict[str, Any]) -> bytes:
    """Serialise one message to a line; ValueError when it is over the cap or not JSON."""
    try:
        text = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"not JSON: {type(exc).__name__}: {exc}") from None
    data = text.encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError(f"message over {MAX_MESSAGE_BYTES} bytes")
    return data + b"\n"


def request(msg_id: int | str, method: str, params: Any = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def notification(method: str, params: Any = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def result(msg_id: int | str | None, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": value}


def error(msg_id: int | str | None, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}
