"""JSON-RPC 2.0 over NDJSON: framing and message helpers for both ends.

One JSON object per line, UTF-8, at most MAX_MESSAGE_BYTES per line (newline
excluded), except the two lines of `http.fetch`: its request (child to host) may
reach HTTP_FETCH_REQUEST_BYTES and a response the child reads may reach
HTTP_FETCH_RESULT_BYTES (`LargeLines`, per direction). Batches are not supported. Standard library only: the child imports
this before its import surface is frozen, the server imports it as is.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_MESSAGE_BYTES = 100 * 1024
# `http.fetch` carries a request body of up to 256 KiB (JSON-escaped) and an
# answer of up to 1 MiB (base64 when it is not text): only those lines may be longer.
HTTP_FETCH_METHOD = "http.fetch"
HTTP_FETCH_REQUEST_BYTES = 384 * 1024
HTTP_FETCH_RESULT_BYTES = 1536 * 1024
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


@dataclass(frozen=True)
class LargeLines:
    """The lines of one direction that may exceed the cap: up to `max_bytes`, and
    only when the parsed message passes `allows`."""

    max_bytes: int
    allows: Callable[[dict[str, Any]], bool]


def _is_http_fetch_request(msg: dict[str, Any]) -> bool:
    return msg.get("method") == HTTP_FETCH_METHOD and "id" in msg


def _is_response(msg: dict[str, Any]) -> bool:
    return "method" not in msg


# What the host reads from a child: only an `http.fetch` request may be long.
HOST_INBOUND = LargeLines(HTTP_FETCH_REQUEST_BYTES, _is_http_fetch_request)
# What a child reads from the host: only an answer (the host is the trusted end,
# and only `http.fetch` answers are built that long, see `encode`).
CHILD_INBOUND = LargeLines(HTTP_FETCH_RESULT_BYTES, _is_response)


class LineDecoder:
    """Splits a byte stream into lines, refusing any line longer than the cap.

    A line over `max_bytes` is accepted only within `large` (parsed once more to
    check its kind); a partial line is refused once it passes the larger bound.
    """

    def __init__(self, max_bytes: int = MAX_MESSAGE_BYTES, large: LargeLines | None = HOST_INBOUND):
        self._max = max_bytes
        self._large = large if large is not None and large.max_bytes > max_bytes else None
        self._buf = bytearray()

    def _bound(self) -> int:
        return self._large.max_bytes if self._large is not None else self._max

    def _check(self, line: bytes) -> None:
        if len(line) <= self._max:
            return
        if self._large is None or len(line) > self._large.max_bytes:
            raise FramingError(f"message over {self._bound()} bytes")
        try:
            msg = decode(line)
        except ProtocolError:
            raise FramingError(f"message over {self._max} bytes") from None
        if not self._large.allows(msg):
            raise FramingError(f"message over {self._max} bytes")

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        lines: list[bytes] = []
        start = 0
        while True:
            end = self._buf.find(b"\n", start)
            if end < 0:
                break
            line = bytes(self._buf[start:end])
            self._check(line)
            lines.append(line)
            start = end + 1
        del self._buf[:start]
        if len(self._buf) > self._bound():
            raise FramingError(f"message over {self._bound()} bytes")
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


def result_limit(method: str) -> int:
    """The cap of the line answering a request for `method` (host side)."""
    return HTTP_FETCH_RESULT_BYTES if method == HTTP_FETCH_METHOD else MAX_MESSAGE_BYTES


def encode(message: dict[str, Any], max_bytes: int | None = None) -> bytes:
    """Serialise one message to a line; ValueError when it is over the cap or not JSON.

    The cap is `max_bytes`, else HTTP_FETCH_REQUEST_BYTES for an `http.fetch`
    request, else MAX_MESSAGE_BYTES.
    """
    if max_bytes is None:
        max_bytes = (
            HTTP_FETCH_REQUEST_BYTES if _is_http_fetch_request(message) else MAX_MESSAGE_BYTES
        )
    try:
        text = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"not JSON: {type(exc).__name__}: {exc}") from None
    data = text.encode("utf-8")
    if len(data) > max_bytes:
        raise ValueError(f"message over {max_bytes} bytes")
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
