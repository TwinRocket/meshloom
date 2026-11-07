"""Property-based fuzzing of the plugin RPC framing and parser (`meshloom_plugin/rpc.py`).

Both ends decode with `rpc.decode` behind a `rpc.LineDecoder`; the host also
dispatches what it parsed (`PluginProcess._handle_line`) and the child answers
it (the runner's `Channel`). Whatever a hostile peer sends (malformed JSON, huge
ids, deep nesting, invalid UTF-8, a line without its newline, a line over the
100 KiB cap), the decoders raise only their typed errors, the host never raises
and never holds more than the cap, and a real child either answers and keeps
serving or ends itself with exit 2 (which the host reports and restarts on).

Inputs are bounded (sizes, depths, example counts) and the runs are derandomized,
so CI is reproducible. Docs: docs/dev/plugins-security.md, "Tests".
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from meshloom_plugin import rpc

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the runner refuses Windows")

FUZZ = settings(
    max_examples=300,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
CODES = {rpc.PARSE_ERROR, rpc.INVALID_REQUEST}

# JSON values with the shapes that hurt parsers: huge integers, NaN and the
# infinities (not JSON: `json.dumps` writes them anyway), deep containers.
_scalars = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.integers(min_value=-(10**400), max_value=10**400)
    | st.floats(allow_nan=True, allow_infinity=True)
    | st.text(max_size=40)
)
json_values = st.recursive(
    _scalars,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=40,
)
methods = st.sampled_from(
    ["event.message", "event.health", "event.cancel", "http.fetch", "log.write", "x"]
) | st.text(max_size=20)


@st.composite
def envelopes(draw: Any) -> dict[str, Any]:
    """Something shaped like a JSON-RPC message, often wrong in one place."""
    msg: dict[str, Any] = {}
    if draw(st.booleans()):
        msg["jsonrpc"] = draw(st.sampled_from(["2.0", "2.0", "1.0", 2.0, None]))
    if draw(st.booleans()):
        msg["id"] = draw(json_values)
    kind = draw(st.sampled_from(["request", "result", "error", "none"]))
    if kind == "request":
        msg["method"] = draw(methods | json_values)
        if draw(st.booleans()):
            msg["params"] = draw(json_values)
    elif kind == "result":
        msg["result"] = draw(json_values)
    elif kind == "error":
        msg["error"] = draw(json_values)
    return msg


def _dump(value: Any) -> bytes:
    return json.dumps(value, allow_nan=True, ensure_ascii=False).encode("utf-8", "surrogatepass")


@st.composite
def hostile_lines(draw: Any) -> bytes:
    """One line without its newline: JSON-ish, corrupted, nested or random bytes."""
    shape = draw(st.sampled_from(["envelope", "corrupted", "nested", "bytes"]))
    if shape == "bytes":
        line = draw(st.binary(max_size=512))
    elif shape == "nested":
        depth = draw(st.integers(min_value=0, max_value=300))
        opener, closer = draw(st.sampled_from([(b"[", b"]"), (b'{"a":', b"}")]))
        inner = opener * depth + b"1" + closer * depth
        line = b'{"jsonrpc": "2.0", "id": 1, "method": "event.message", "params": [' + inner + b"]}"
    else:
        line = _dump(draw(envelopes()))
        if shape == "corrupted" and line:
            at = draw(st.integers(min_value=0, max_value=len(line)))
            junk = draw(st.sampled_from([b"\xff", b"\xc3", b"\xed\xa0\x80", b"\x00", b'"', b"]"]))
            line = line[:at] + junk + line[at:]
    return line.replace(b"\n", b" ")


# --- the parser ---------------------------------------------------------------------


def _is_json(value: Any) -> bool:
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, dict):
        return all(_is_json(v) for v in value.values())
    if isinstance(value, list):
        return all(_is_json(v) for v in value)
    return True


@FUZZ
@given(hostile_lines())
@example(b"")
@example(b"\xff\xfe")
@example(b'{"jsonrpc": "2.0", "id": 1, "result": NaN}')
@example(b'{"jsonrpc": "2.0", "id": ' + b"9" * 5000 + b', "result": 1}')
def test_decode_returns_a_message_or_a_typed_error(line: bytes) -> None:
    try:
        msg = rpc.decode(line)
    except rpc.ProtocolError as exc:
        assert exc.code in CODES
        return
    assert isinstance(msg, dict) and msg["jsonrpc"] == "2.0"
    assert _is_json(msg), msg  # no NaN or Infinity got through
    assert "method" in msg or "result" in msg or "error" in msg
    assert msg.get("id") is None or type(msg["id"]) in (int, str)
    if "method" in msg:
        assert isinstance(msg["method"], str)
        assert isinstance(msg.get("params", []), dict | list)


@FUZZ
@given(st.integers(min_value=0, max_value=200), st.sampled_from(["[", '{"k":']))
def test_nesting_is_refused_past_the_cap_and_only_there(depth: int, opener: str) -> None:
    closer = "]" if opener == "[" else "}"
    value = opener * depth + "0" + closer * depth
    line = f'{{"jsonrpc": "2.0", "id": 1, "result": {value}}}'.encode()
    # The envelope object is one level; brackets inside strings never count.
    if depth + 1 > rpc.MAX_NESTING_DEPTH:
        with pytest.raises(rpc.ProtocolError, match="nested deeper"):
            rpc.decode(line)
    else:
        assert rpc.decode(line)["id"] == 1
    text = f'{{"jsonrpc": "2.0", "id": 2, "result": "{opener[0] * depth}"}}'.encode()
    assert rpc.decode(text)["id"] == 2


ids = st.integers(min_value=-(2**63), max_value=2**63 - 1) | st.text(max_size=30)
plain_json = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=30),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=30,
)


@FUZZ
@given(ids, st.text(max_size=30), st.dictionaries(st.text(max_size=8), plain_json, max_size=5))
def test_what_encode_writes_decode_reads_back(msg_id: Any, method: str, params: Any) -> None:
    message = rpc.request(msg_id, method, params or None)
    try:
        line = rpc.encode(message)
    except ValueError:
        # Only over the cap (or nested past the parser's own limit).
        return
    assert line.endswith(b"\n") and b"\n" not in line[:-1]
    assert len(line) - 1 <= rpc.MAX_MESSAGE_BYTES
    try:
        assert rpc.decode(line[:-1]) == message
    except rpc.ProtocolError as exc:
        assert "nested deeper" in str(exc)


# --- the framing --------------------------------------------------------------------


def _small_decoder() -> rpc.LineDecoder:
    # The real decoder with small caps, so the fuzzer reaches every branch fast.
    return rpc.LineDecoder(
        max_bytes=64, large=rpc.LargeLines(160, lambda msg: msg.get("method") == "big")
    )


def _feed_all(decoder: rpc.LineDecoder, chunks: list[bytes]) -> list[bytes] | str:
    lines: list[bytes] = []
    for chunk in chunks:
        try:
            lines += decoder.feed(chunk)
        except rpc.FramingError:
            return "framing_error"
        # Never more buffered than the larger bound, line without newline included.
        assert len(decoder._buf) <= 160
    return lines


_line_bodies = st.binary(max_size=200).map(lambda b: b.replace(b"\n", b"")) | st.builds(
    lambda pad: b'{"jsonrpc": "2.0", "id": 1, "method": "big", "params": ["' + pad + b'"]}',
    st.binary(max_size=150).map(lambda b: b"x" * len(b)),
)


@FUZZ
@given(
    st.lists(_line_bodies, max_size=6),
    st.booleans(),
    st.lists(st.integers(min_value=0, max_value=2000), max_size=8),
)
def test_line_decoder_is_chunking_invariant_and_bounded(
    bodies: list[bytes], trailing_newline: bool, cuts: list[int]
) -> None:
    stream = b"".join(body + b"\n" for body in bodies)
    if bodies and not trailing_newline:
        stream = stream[:-1]
    points = sorted({min(c, len(stream)) for c in cuts})
    chunks = [stream[a:b] for a, b in zip([0, *points], [*points, len(stream)], strict=True)]
    whole = _feed_all(_small_decoder(), [stream])
    split = _feed_all(_small_decoder(), chunks)
    if whole == "framing_error" or split == "framing_error":
        # A refused stream is refused however it arrives, possibly earlier when
        # a chunk boundary leaves an over-long partial line.
        expected = _expected_lines(bodies, trailing_newline)
        assert expected == "framing_error" or split == "framing_error"
        return
    assert whole == split == _expected_lines(bodies, trailing_newline)


def _expected_lines(bodies: list[bytes], trailing_newline: bool) -> list[bytes] | str:
    complete = bodies if trailing_newline else bodies[:-1]
    for body in complete:
        if len(body) > 64:
            if len(body) > 160:
                return "framing_error"
            try:
                if rpc.decode(body).get("method") != "big":
                    return "framing_error"
            except rpc.ProtocolError:
                return "framing_error"
    if not trailing_newline and bodies and len(bodies[-1]) > 160:
        return "framing_error"
    return complete


@pytest.mark.parametrize(
    "size, method, accepted",
    [
        (rpc.MAX_MESSAGE_BYTES, "event.message", True),
        (rpc.MAX_MESSAGE_BYTES + 1, "event.message", False),
        (rpc.HTTP_FETCH_REQUEST_BYTES, "http.fetch", True),
        (rpc.HTTP_FETCH_REQUEST_BYTES + 1, "http.fetch", False),
    ],
)
def test_the_real_caps_hold_at_their_edges(size: int, method: str, accepted: bool) -> None:
    head = b'{"jsonrpc": "2.0", "id": 1, "method": "' + method.encode() + b'", "params": ["'
    tail = b'"]}'
    line = head + b"x" * (size - len(head) - len(tail)) + tail
    assert len(line) == size
    decoder = rpc.LineDecoder()
    if accepted:
        assert decoder.feed(line + b"\n") == [line]
    else:
        with pytest.raises(rpc.FramingError):
            decoder.feed(line + b"\n")
    # The same bytes without their newline: refused once past the larger bound.
    partial = rpc.LineDecoder()
    if size > rpc.HTTP_FETCH_REQUEST_BYTES:
        with pytest.raises(rpc.FramingError):
            partial.feed(line)
    else:
        assert partial.feed(line) == []


# --- the host end ---------------------------------------------------------------------


@FUZZ
@given(st.lists(hostile_lines(), min_size=1, max_size=8))
def test_the_host_survives_any_line_from_a_child(lines: list[bytes]) -> None:
    from app.plugins.host import LogRing, PluginProcess

    async def scenario() -> None:
        asked: list[str] = []

        async def on_request(method: str, params: Any) -> Any:
            asked.append(method)
            return None

        process = PluginProcess(Path("/nonexistent"), LogRing(4096, 256), on_request)
        decoder = rpc.LineDecoder()
        for line in lines:
            try:
                parsed = decoder.feed(line + b"\n")
            except rpc.FramingError:
                continue  # the real reader kills the child here
            for one in parsed:
                process._handle_line(one)
        # Answers go nowhere (no process): every task ends, nothing is raised.
        await asyncio.wait_for(asyncio.gather(*process._tasks), 5)
        assert len(asked) <= len(lines)

    asyncio.run(scenario())


# --- the child end --------------------------------------------------------------------

_ECHO = "def on_message(params):\n    return None\n"


class _Child:
    """One real runner reused across examples, replaced when it ends itself."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runner: Any = None
        self.count = 0

    def get(self) -> Any:
        from tests.test_plugin_runner import Runner

        if self.runner is None or self.runner.proc.poll() is not None:
            if self.runner is not None:
                self.runner.close()
            self.count += 1
            work = self.root / f"child-{self.count}"
            work.mkdir()
            plugin = work / "plugin"
            plugin.mkdir()
            (plugin / "manifest.json").write_text('{"entry": "plugin.py"}')
            (plugin / "plugin.py").write_text(_ECHO)
            self.runner = Runner(plugin, work)
        return self.runner

    def close(self) -> None:
        if self.runner is not None:
            self.runner.close()


def _wait_answer(runner: Any, msg_id: str) -> dict[str, Any]:
    while True:
        message = runner.read_message(timeout=10)
        if message.get("id") == msg_id and "method" not in message:
            return message


def test_a_real_child_answers_or_ends_itself_on_any_line(tmp_path_factory: Any) -> None:
    child = _Child(tmp_path_factory.mktemp("fuzz-child").resolve())
    pings = iter(range(10**9))

    @settings(FUZZ, max_examples=60)
    @given(
        hostile_lines() | st.integers(rpc.MAX_MESSAGE_BYTES - 64, 200_000).map(lambda n: b"y" * n)
    )
    def check(line: bytes) -> None:
        if b"plugin.shutdown" in line:
            assume(False)
        runner = child.get()
        runner.write(line + b"\n")
        if len(line) > rpc.HTTP_FETCH_RESULT_BYTES or (
            len(line) > rpc.MAX_MESSAGE_BYTES and not line.startswith(b'{"jsonrpc"')
        ):
            # Over the cap and not an answer: the child ends with the protocol exit.
            assert runner.proc.wait(timeout=10) == 2
            return
        ping = f"ping-{next(pings)}"
        runner.send(rpc.request(ping, "event.health", {"ok": True}))
        assert _wait_answer(runner, ping)["result"] is None

    try:
        check()
    finally:
        child.close()
