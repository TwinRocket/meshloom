"""Keep secrets out of every log line, at every level.

The pinned ``meshcore`` library logs whole frames at DEBUG: the outgoing
command bytes (``Sending raw data``, ``sending pkt``), the incoming ones
(``Received data``), the dispatched event payloads and, on a parse failure, the
raw frame at ERROR. A companion CLI frame can carry a Wi-Fi password (``set
wifi.pwd`` out, the ``get wifi.pwd`` reply in) and the private key travels in
the export (on every connect) and import frames, so those records are redacted
here before any handler sees them.

Three layers, all always on:

- ``MeshcoreSecretFilter`` sits at index 0 of the ``meshcore`` logger's filters
  (before ``_RepeatSquelch`` in ``app.config``, which reads and may rewrite the
  message). Its structural rules key on the exact meshcore message prefixes and
  on the frame code, so a reply that arrives after its call timed out (logged by
  the reader task, outside any call) is still redacted.
- ``RedactingFormatter`` wraps the formatters of the app's handlers. It runs the
  same rules on the fully formatted output, traceback included, and writes the
  redacted traceback back to ``record.exc_text`` so a handler that is not wrapped
  reuses the redacted text.
- ``RedactingFormatter`` also keeps untrusted text from forging a log line
  (MSHL-432): CR, LF and every other control character in the message, its
  arguments and the exception text come out as visible escapes (``\\n``), so a
  record is always one line plus the structural lines of its traceback. See
  ``escape_control()`` and ``format_exception_safe()``. Each of those lines is
  then capped at ``MAX_LINE_BYTES`` (MSHL-435, ``cap_lines()``): journald cuts a
  line longer than its ``LineMax`` (48 KiB) into several entries, Docker at
  16 KiB, and the start of such a continuation entry is text the client chose.
- ``install_uncaught_handlers()`` sends what Python would otherwise print raw to
  standard error (warnings, an uncaught exception in the main thread or another
  thread, an unraisable one) through logging, so through that formatter too.
  asyncio's default exception handler already logs (logger ``asyncio``).
- ``secret_scope()`` registers literal secrets for the duration of a call; every
  registered form (text, hex, repr escapes) is replaced wherever it appears.

The ``bleak`` and ``dbus_fast`` loggers print every BLE frame at DEBUG, and
``aiosqlite`` prints every statement with its parameters (password and token
hashes, VAPID and fanout secrets), and ``urllib3.connectionpool`` prints every
request line (the Web Push endpoint path is the subscription's token, Apprise
and webhook URLs carry theirs), so their level is clamped to INFO whatever
``MESHCORE_LOG_LEVEL`` says.

Both the filter and the formatter are total: a redaction bug must never kill
the meshcore dispatcher task or let ``Handler.handleError`` print the raw record.
``tests/test_meshcore_log_sites.py`` pins the meshcore log calls these rules
were written against: review this module whenever that test fails.
"""

from __future__ import annotations

import ast
import copy
import logging
import re
import sys
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager

REDACTED = "[redacted]"
REDACTION_FAILED = "[log record dropped: redaction error]"
UNFORMATTABLE_RECORD = "[unformattable meshcore record]"
QUIET_THIRD_PARTY_LOGGERS = ("bleak", "dbus_fast", "aiosqlite", "urllib3")
SECRET_MIN_LITERAL_BYTES = 4

# Companion frame codes whose payload is secret. Outgoing: command codes.
# Incoming: response codes.
SECRET_OUT: frozenset[int] = frozenset(
    {
        0x42,  # CMD_RUN_CLI_COMMAND ("set wifi.pwd ...")
        0x18,  # CMD_IMPORT_PRIVATE_KEY
    }
)
SECRET_IN: frozenset[int] = frozenset(
    {
        0x1D,  # RESP_CODE_CLI_REPLY ("get wifi.pwd" answer)
        0x0E,  # RESP_CODE_PRIVATE_KEY (export, on every connect)
    }
)

_SECRET_EVENT_TYPES = ("EventType.CLI_REPLY", "EventType.PRIVATE_KEY")

# --------------------------------------------------------------------------
# Log-line forging (MSHL-432)
# --------------------------------------------------------------------------

# Every C0 and C1 control character (CR, LF, tab, ESC, NEL...), DEL, and the
# Unicode line and paragraph separators: anything a log consumer, a terminal or
# ``str.splitlines()`` may treat as a line break or an escape sequence.
_CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_NAMED_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
_EXC_SAFE_ATTR = "_meshloom_exc_safe"


def _escape_one(match: re.Match[str]) -> str:
    char = match.group(0)
    named = _NAMED_ESCAPES.get(char)
    if named:
        return named
    code = ord(char)
    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


def escape_control(text: str) -> str:
    """``text`` with every control character written as a visible escape.

    ``\\n``, ``\\r`` and ``\\t`` by name, ``\\xNN`` or ``\\uNNNN`` for the others. A
    backslash is left alone: the point is that no input can end a line, not
    that the escape is reversible.
    """
    return _CONTROL_RE.sub(_escape_one, text)


class _SafeTracebackException(traceback.TracebackException):
    """A traceback whose exception messages (the only attacker-reachable lines) are one line each."""

    def format_exception_only(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        text = "".join(super().format_exception_only(*args, **kwargs))
        yield escape_control(text.rstrip("\n")) + "\n"


def _harden_chain(root: traceback.TracebackException) -> None:
    seen: set[int] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        current.__class__ = _SafeTracebackException
        for child in (current.__cause__, current.__context__, *(current.exceptions or ())):
            if child is not None:
                stack.append(child)


def format_exception_safe(exc_info: tuple) -> str:  # type: ignore[type-arg]
    """The traceback text with the structural lines of a traceback and nothing forged.

    The frames (file names, our own source lines) keep their newlines; the
    message of every exception in the chain, notes and groups included, is
    escaped by ``escape_control()``, so it stays on one line.
    """
    exc_type, exc, tb = exc_info
    root = traceback.TracebackException(exc_type, exc, tb)
    _harden_chain(root)
    return "".join(root.format()).rstrip("\n")


# Longest line, in UTF-8 bytes, any handler writes (MSHL-435). Well under the
# 16 KiB at which Docker's log drivers split a line and journald's 48 KiB
# ``LineMax``: a line split there would start a new journal entry with text the
# client chose, such as a whole forged ``AUTH_FAIL`` line. The marker starts
# with a space, so a cut line never ends in a ``user=<token>`` a filter could take.
MAX_LINE_BYTES = 8192
TRUNCATED_MARKER = " [truncated: {} more bytes]"
_MARKER_RESERVE = 48  # bytes kept for the marker


def cap_line(line: str) -> str:
    """``line``, or its head and ``TRUNCATED_MARKER`` when longer than ``MAX_LINE_BYTES``."""
    if len(line) * 4 <= MAX_LINE_BYTES:  # UTF-8 never takes more than 4 bytes a character
        return line
    raw = line.encode("utf-8", "surrogatepass")
    if len(raw) <= MAX_LINE_BYTES:
        return line
    # Cut on a character boundary: an incomplete sequence at the end is dropped.
    head = raw[: MAX_LINE_BYTES - _MARKER_RESERVE].decode("utf-8", "ignore")
    kept = len(head.encode("utf-8", "surrogatepass"))
    return head + TRUNCATED_MARKER.format(len(raw) - kept)


def cap_lines(text: str) -> str:
    """Every line of ``text`` through ``cap_line()``."""
    if len(text) * 4 <= MAX_LINE_BYTES:
        return text
    return "\n".join(cap_line(line) for line in text.split("\n"))


# --------------------------------------------------------------------------
# Literal secrets (secret_scope)
# --------------------------------------------------------------------------

_literal_lock = threading.Lock()
_literal_counts: dict[str, int] = {}
_literal_snapshot: tuple[str, ...] = ()


def _bytes_repr_body(data: bytes, quote: str) -> str:
    """Body of ``repr(data)`` as CPython writes it inside ``quote``."""
    out: list[str] = []
    for byte in data:
        char = chr(byte)
        if char == "\\" or char == quote:
            out.append("\\" + char)
        elif char == "\t":
            out.append("\\t")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif 0x20 <= byte < 0x7F:
            out.append(char)
        else:
            out.append(f"\\x{byte:02x}")
    return "".join(out)


def _str_repr_body(text: str, quote: str) -> str:
    """Body of ``repr(text)`` as CPython writes it inside ``quote``."""
    out: list[str] = []
    for char in text:
        if char == "\\" or char == quote:
            out.append("\\" + char)
        elif char in "'\"":
            out.append(char)
        else:
            out.append(repr(char)[1:-1])
    return "".join(out)


def _secret_forms(secret: str | bytes) -> set[str]:
    forms: set[str] = set()
    if isinstance(secret, str):
        text: str | None = secret
        raw = secret.encode("utf-8")
    else:
        raw = bytes(secret)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if len(raw) < SECRET_MIN_LITERAL_BYTES:
        return forms
    forms.add(raw.hex())
    forms.add(raw.hex().upper())
    for quote in ("'", '"'):
        forms.add(_bytes_repr_body(raw, quote))
    if text is not None:
        forms.add(text)
        for quote in ("'", '"'):
            forms.add(_str_repr_body(text, quote))
    return {form for form in forms if form}


def _refresh_snapshot() -> None:
    global _literal_snapshot
    # Longest first, so a form that contains another is replaced whole.
    _literal_snapshot = tuple(sorted(_literal_counts, key=len, reverse=True))


@contextmanager
def secret_scope(*secrets: str | bytes) -> Iterator[None]:
    """Register literal secrets for the duration of the block.

    Refcounted and thread-safe. A ``str`` is registered as itself, its UTF-8 hex
    (lower and upper case), the repr escapes of its UTF-8 bytes and its own str
    repr body, in both quote styles. ``bytes`` are registered as their hex and
    repr escapes, and as text when they decode as UTF-8. Values shorter than
    ``SECRET_MIN_LITERAL_BYTES`` are not registered literally (they would mangle
    every line); the structural rules still cover the meshcore frame sites.
    """
    forms: set[str] = set()
    for secret in secrets:
        if isinstance(secret, str | bytes | bytearray):
            forms |= _secret_forms(secret)
    with _literal_lock:
        for form in forms:
            _literal_counts[form] = _literal_counts.get(form, 0) + 1
        _refresh_snapshot()
    try:
        yield
    finally:
        with _literal_lock:
            for form in forms:
                count = _literal_counts.get(form, 0) - 1
                if count <= 0:
                    _literal_counts.pop(form, None)
                else:
                    _literal_counts[form] = count
            _refresh_snapshot()


def registered_secret_count() -> int:
    """Number of literal forms currently registered (tests and diagnostics)."""
    with _literal_lock:
        return len(_literal_counts)


def _scrub_literals(text: str) -> str:
    for form in _literal_snapshot:
        if form in text:
            text = text.replace(form, REDACTED)
    return text


# --------------------------------------------------------------------------
# Payload parsers (each one fails closed: None means "cannot tell")
# --------------------------------------------------------------------------

_HEX_RE = re.compile(r"[0-9a-fA-F]+")


def _parse_hex(payload: str) -> bytes | None:
    payload = payload.strip()
    if not payload or len(payload) % 2 or not _HEX_RE.fullmatch(payload):
        return None
    try:
        return bytes.fromhex(payload)
    except ValueError:
        return None


def _parse_bytes_literal(payload: str) -> bytes | None:
    """Parse ``b'...'`` / ``b"..."`` / ``bytearray(b'...')`` without evaluating code."""
    payload = payload.strip()
    if payload.startswith("bytearray(") and payload.endswith(")"):
        payload = payload[len("bytearray(") : -1]
    if not payload.startswith(("b'", 'b"')):
        return None
    try:
        value = ast.literal_eval(payload)
    except Exception:
        return None
    if isinstance(value, bytes | bytearray):
        return bytes(value)
    return None


def _parse_frame(payload: str) -> bytes | None:
    return _parse_hex(payload) or _parse_bytes_literal(payload)


# --------------------------------------------------------------------------
# Structural rules
# --------------------------------------------------------------------------


def _rule_cli_command(rest: str) -> str | None:
    return REDACTED


def _rule_raw_data(rest: str) -> str | None:
    frame = _parse_frame(rest)
    if frame is None or not frame:
        return REDACTED
    if frame[0] in SECRET_OUT:
        # Opcode only: the byte count would give away the password length.
        return f"{frame[0]:02x}...{REDACTED}"
    return None


def _rule_sending_pkt(rest: str) -> str | None:
    frame = _parse_bytes_literal(rest)
    if frame is None or len(frame) < 4:
        return REDACTED
    if frame[3] in SECRET_OUT:
        # The 2-byte length header is dropped too, for the same reason.
        return f"[redacted frame {frame[3]:02x}]"
    return None


def _rule_received_hex(rest: str) -> str | None:
    frame = _parse_hex(rest)
    if frame is None or frame[0] in SECRET_IN:
        return REDACTED
    return None


def _rule_received_private_key(rest: str) -> str | None:
    return REDACTED


def _rule_unhandled(rest: str) -> str | None:
    frame = _parse_frame(rest)
    if frame is None or not frame or frame[0] in SECRET_IN:
        return REDACTED
    return None


def _rule_dispatch(rest: str) -> str | None:
    for event_type in _SECRET_EVENT_TYPES:
        if rest.startswith(event_type + ","):
            return f"{event_type}, {REDACTED}"
    return None


# (prefix, rule). The rule receives what follows the prefix on the same line and
# returns a replacement for it, or None to keep it.
_LINE_RULES = (
    ("Sending cli command: ", _rule_cli_command),
    ("Sending raw data: ", _rule_raw_data),
    ("sending pkt : ", _rule_sending_pkt),
    ("Received data: ", _rule_received_hex),
    ("Received private key response: ", _rule_received_private_key),
    ("Unhandled data received ", _rule_unhandled),
    ("Dispatching event: ", _rule_dispatch),
)

_PARSE_ERROR_RE = re.compile(r"handle_rx parse error: (?P<type>[A-Za-z_][\w.]*): ")
_PARSE_ERROR_RAW_RE = re.compile(r" \| raw=([0-9a-fA-F]*)(?=\n|$)")
_HANDLER_ERROR_RE = re.compile(
    r"Error in event handler for (?P<type>" + "|".join(map(re.escape, _SECRET_EVENT_TYPES)) + r"): "
)


def parse_error_frame_is_safe(raw_hex: object) -> bool:
    """True when a ``handle_rx parse error`` raw hex is parseable and not secret."""
    if not isinstance(raw_hex, str):
        return False
    frame = _parse_hex(raw_hex)
    return frame is not None and frame[0] not in SECRET_IN


def _apply_multiline_rules(text: str, *, unanchored: bool) -> str:
    match = _HANDLER_ERROR_RE.search(text) if unanchored else _HANDLER_ERROR_RE.match(text)
    if match:
        text = text[: match.end()] + REDACTED
    match = _PARSE_ERROR_RE.search(text) if unanchored else _PARSE_ERROR_RE.match(text)
    if match:
        tail = text[match.end() :]
        raws = _PARSE_ERROR_RAW_RE.findall(tail)
        # Exactly one raw= field, parseable and not a secret frame, else redact
        # everything after the type name: the exception text, the raw hex and
        # the traceback appended to the message.
        if len(raws) != 1 or not parse_error_frame_is_safe(raws[0]):
            text = text[: match.end()] + REDACTED
    return text


def _apply_line_rules(line: str, *, unanchored: bool) -> str:
    for prefix, rule in _LINE_RULES:
        index = line.find(prefix) if unanchored else (0 if line.startswith(prefix) else -1)
        if index < 0:
            continue
        start = index + len(prefix)
        replacement = rule(line[start:])
        if replacement is not None:
            return line[:start] + replacement
    return line


def redact_text(message: str, *, unanchored: bool = False) -> str:
    """Return ``message`` with every secret-bearing part redacted.

    Anchored (the filter, on ``record.getMessage()``): the rules match at the
    start of the message or of a line. Unanchored (the formatter, on a formatted
    line that begins with the time/name/level prefix): the rules are searched
    anywhere in a line. Multi-line rules run on the whole text first, then the
    single-line rules per line, then the literal scrub.
    """
    text = _apply_multiline_rules(message, unanchored=unanchored)
    if any(prefix in text for prefix, _ in _LINE_RULES):
        text = "\n".join(
            _apply_line_rules(line, unanchored=unanchored) for line in text.split("\n")
        )
    return _scrub_literals(text)


# --------------------------------------------------------------------------
# Filter and formatter
# --------------------------------------------------------------------------


def _drop_record_payload(record: logging.LogRecord, message: str) -> None:
    record.msg = message
    record.args = None
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None


class MeshcoreSecretFilter(logging.Filter):
    """Redact secret-bearing meshcore records. Never drops a record, never raises."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._redact(record)
        except Exception:
            try:
                _drop_record_payload(record, REDACTION_FAILED)
            except Exception:
                pass
        return True

    def _redact(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            record.msg = UNFORMATTABLE_RECORD
            record.args = None
            message = UNFORMATTABLE_RECORD

        drop_exc = False
        if message.startswith("handle_rx parse error: "):
            args = record.args
            # reader.py logs (type name, exception, raw hex, traceback): read the
            # raw hex from the args, never from the merged text, where the
            # exception text comes first and could itself contain "raw=".
            raw_hex = args[2] if isinstance(args, tuple) and len(args) == 4 else None
            if not parse_error_frame_is_safe(raw_hex):
                match = _PARSE_ERROR_RE.match(message)
                type_name = match.group("type") if match else "Exception"
                message = f"handle_rx parse error: {type_name}: {REDACTED}"
                record.msg = message
                record.args = None
                drop_exc = True
        if _HANDLER_ERROR_RE.match(message):
            drop_exc = True

        new = redact_text(message)
        if new != message:
            record.msg = new
            record.args = None

        if drop_exc:
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return
        if record.exc_info:
            formatted = format_exception_safe(record.exc_info)
            record.exc_text = redact_text(formatted, unanchored=True)
            setattr(record, _EXC_SAFE_ATTR, record.exc_text)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text, unanchored=True)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info, unanchored=True)


class RedactingFormatter(logging.Formatter):
    """Wrap another formatter and redact its whole output. Never raises."""

    def __init__(self, inner: logging.Formatter) -> None:
        super().__init__()
        self.inner = inner

    def format(self, record: logging.LogRecord) -> str:
        had_traceback = bool(record.exc_info or record.exc_text)
        try:
            exc_text = self._safe_exc_text(record)
            if exc_text:
                # Written back, only once redacted, so a handler that is not
                # wrapped reuses it.
                exc_text = redact_text(exc_text, unanchored=True)
                record.exc_text = exc_text
                setattr(record, _EXC_SAFE_ATTR, exc_text)
            if record.stack_info:
                record.stack_info = redact_text(record.stack_info, unanchored=True)
            # The inner formatter sees the record without its traceback, so its
            # whole output (prefix, message, arguments, however they were
            # formatted) is one escaped line; the traceback is added after.
            bare = copy.copy(record)
            bare.exc_info = None
            bare.exc_text = None
            bare.stack_info = None
            parts = [escape_control(self.inner.format(bare))]
            if exc_text:
                parts.append(exc_text)
            if record.stack_info:
                parts.append(record.stack_info)
            # Capped last: redaction first sees the whole text, and nothing it
            # writes can make a line longer again.
            return cap_lines(redact_text("\n".join(parts), unanchored=True))
        except Exception:
            # Never leave a traceback behind that later handlers would reuse.
            record.exc_info = None
            record.exc_text = REDACTION_FAILED if had_traceback else None
            record.stack_info = None
            return self._placeholder(record)

    def _safe_exc_text(self, record: logging.LogRecord) -> str | None:
        """The traceback with one line per exception message.

        An ``exc_text`` cached on the record is trusted only when this module
        wrote it: a plain ``Formatter`` upstream leaves the raw text there.
        """
        if record.exc_text and record.exc_text == getattr(record, _EXC_SAFE_ATTR, None):
            return record.exc_text
        if record.exc_info:
            return format_exception_safe(record.exc_info)
        if record.exc_text:
            # Pre-rendered by someone else: no way to tell its structure.
            return escape_control(record.exc_text)
        return None

    def _placeholder(self, record: logging.LogRecord) -> str:
        try:
            when = self.formatTime(record, getattr(self.inner, "datefmt", None))
            return f"{when} - {record.name} - {record.levelname} - {REDACTION_FAILED}"
        except Exception:
            return REDACTION_FAILED


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------

_install_lock = threading.Lock()
_meshcore_filter: MeshcoreSecretFilter | None = None


def clamp_quiet_third_party_loggers() -> None:
    """Keep the BLE frame dumps and aiosqlite's statement dumps off, whatever
    the root level is.

    ``BLEAK_LOGGING`` sets ``bleak`` to DEBUG when bleak is imported, so this
    runs again after that import (``app/radio.py``) and at the end of
    ``setup_logging()``.
    """
    manager = logging.Logger.manager
    for base in QUIET_THIRD_PARTY_LOGGERS:
        targets = [logging.getLogger(base)]
        targets += [
            logger
            for name, logger in list(manager.loggerDict.items())
            if name.startswith(base + ".") and isinstance(logger, logging.Logger)
        ]
        for logger in targets:
            explicit = logger is targets[0] or logger.level != logging.NOTSET
            if explicit and logger.level < logging.INFO:
                logger.setLevel(logging.INFO)


def install_meshcore_log_redaction() -> MeshcoreSecretFilter:
    """Install the meshcore filter at index 0 and clamp the BLE loggers. Idempotent.

    The filter goes on ``meshcore`` and on every existing ``meshcore.*`` logger
    (a logger filter never sees a child logger's records; the log-site test also
    pins every meshcore ``getLogger`` argument to ``"meshcore"``).
    """
    global _meshcore_filter
    with _install_lock:
        if _meshcore_filter is None:
            _meshcore_filter = MeshcoreSecretFilter()
        secret_filter = _meshcore_filter
        targets = [logging.getLogger("meshcore")]
        targets += [
            logger
            for name, logger in list(logging.Logger.manager.loggerDict.items())
            if name.startswith("meshcore.") and isinstance(logger, logging.Logger)
        ]
        for logger in targets:
            if secret_filter in logger.filters:
                if logger.filters[0] is secret_filter:
                    continue
                logger.filters.remove(secret_filter)
            logger.filters.insert(0, secret_filter)
    clamp_quiet_third_party_loggers()
    return secret_filter


def install_redacting_formatter(handler: logging.Handler) -> None:
    """Wrap ``handler``'s formatter in ``RedactingFormatter``. Idempotent."""
    current = handler.formatter
    if isinstance(current, RedactingFormatter):
        return
    handler.setFormatter(RedactingFormatter(current or logging.Formatter()))


# --------------------------------------------------------------------------
# What Python prints to standard error by itself (MSHL-435)
# --------------------------------------------------------------------------

UNCAUGHT_LOGGER = "meshloom.uncaught"


def _excepthook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
    if issubclass(exc_type, KeyboardInterrupt):
        # Ctrl-C at a shell: Python's own short message, nothing untrusted in it.
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger(UNCAUGHT_LOGGER).critical("Uncaught exception", exc_info=(exc_type, exc, tb))


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:  # Python ignores it too
        return
    name = args.thread.name if args.thread is not None else "<unknown>"
    exc = args.exc_value
    logging.getLogger(UNCAUGHT_LOGGER).error(
        "Uncaught exception in thread %s: %s",
        name,
        args.exc_type.__name__,
        exc_info=(args.exc_type, exc, args.exc_traceback) if exc is not None else None,
    )


def _unraisablehook(unraisable) -> None:  # type: ignore[no-untyped-def]
    try:
        where = repr(unraisable.object) if unraisable.object is not None else ""
    except Exception:
        where = "<object repr() failed>"
    exc = unraisable.exc_value
    logging.getLogger(UNCAUGHT_LOGGER).error(
        "%s: %s",
        unraisable.err_msg or "Exception ignored in",
        where,
        exc_info=(unraisable.exc_type, exc, unraisable.exc_traceback) if exc is not None else None,
    )


def install_uncaught_handlers() -> None:
    """Route warnings and uncaught exceptions through logging. Idempotent.

    Python's default hooks print the exception message raw to standard error,
    where a message with a line break would forge a journal line. A hook someone
    else installed (a test runner, a debugger) is left in place.
    """
    logging.captureWarnings(True)
    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = _excepthook
    if threading.excepthook is threading.__excepthook__:
        threading.excepthook = _thread_excepthook
    if sys.unraisablehook is sys.__unraisablehook__:
        sys.unraisablehook = _unraisablehook
