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
- ``secret_scope()`` registers literal secrets for the duration of a call; every
  registered form (text, hex, repr escapes) is replaced wherever it appears.

The ``bleak`` and ``dbus_fast`` loggers print every BLE frame at DEBUG, so their
level is clamped to INFO whatever ``MESHCORE_LOG_LEVEL`` says.

Both the filter and the formatter are total: a redaction bug must never kill
the meshcore dispatcher task or let ``Handler.handleError`` print the raw record.
``tests/test_meshcore_log_sites.py`` pins the meshcore log calls these rules
were written against: review this module whenever that test fails.
"""

from __future__ import annotations

import ast
import logging
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager

REDACTED = "[redacted]"
REDACTION_FAILED = "[log record dropped: redaction error]"
UNFORMATTABLE_RECORD = "[unformattable meshcore record]"
QUIET_THIRD_PARTY_LOGGERS = ("bleak", "dbus_fast")
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
            formatted = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact_text(formatted, unanchored=True)
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
            exc_text = record.exc_text
            if record.exc_info and not exc_text:
                exc_text = self.inner.formatException(record.exc_info)
            if exc_text:
                # Written back, only once redacted, so a handler that is not
                # wrapped reuses it.
                record.exc_text = redact_text(exc_text, unanchored=True)
            if record.stack_info:
                record.stack_info = redact_text(record.stack_info, unanchored=True)
            return redact_text(self.inner.format(record), unanchored=True)
        except Exception:
            # Never leave a traceback behind that later handlers would reuse.
            record.exc_info = None
            record.exc_text = REDACTION_FAILED if had_traceback else None
            record.stack_info = None
            return self._placeholder(record)

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
    """Keep the BLE libraries' frame dumps off, whatever the root level is.

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
