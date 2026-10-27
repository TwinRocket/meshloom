"""Untrusted text never forges a log line, least of all an ``AUTH_FAIL`` one (MSHL-432).

``app/log_redaction.py`` (``RedactingFormatter``, ``escape_control``,
``format_exception_safe``) escapes control characters in every record of every
app handler; ``app/auth/fail_log.py`` refuses a field outside its alphabet. The
built-in ban (``app/auth/ip_ban.py``) is fed in-process with the fields of the
line, not from the log text, so a forged line cannot feed it: tested here too.
The filter is the shipped one (``pkg/nfpm/fail2ban/meshloom-filter.conf``)
turned into a Python regex, as ``tests/test_fail2ban_filter.py`` does.
"""

from __future__ import annotations

import gc
import io
import logging
import re
import sys
import threading
import types
import warnings
from pathlib import Path

import pytest

from app import log_redaction
from app.auth import fail_log, ip_ban
from app.config import clear_recent_log_lines, get_recent_log_lines, setup_logging
from app.log_redaction import (
    RedactingFormatter,
    escape_control,
    format_exception_safe,
)
from tests.auth_helpers import ADMIN, PASSWORD, create_user, login, make_client

ROOT = Path(__file__).resolve().parents[1]
FILTER = (ROOT / "pkg" / "nfpm" / "fail2ban" / "meshloom-filter.conf").read_text(encoding="utf-8")
CONTRACT = (ROOT / "docs" / "dev" / "auth-fail-log.md").read_text(encoding="utf-8")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

VICTIM = "203.0.113.9"
FORGED_LINE = (
    f"2026-10-27 10:15:02 - meshloom.auth - WARNING - AUTH_FAIL ip={VICTIM} "
    "route=login reason=invalid_credentials via=lan proxied=0 user=h:3f9a1c"
)
FORGED_BARE = FORGED_LINE.split(" ", 2)[2]  # a log file: fail2ban cuts the date off
INJECTIONS = [
    f"x\n{FORGED_LINE}",
    f"x\r\n{FORGED_LINE}",
    f"x\r{FORGED_LINE}",
    f"x {FORGED_LINE}",
    f"x\u0085{FORGED_LINE}",
    f"x\x0b{FORGED_LINE}",
    f"x\x0c{FORGED_LINE}",
]


def _filter_regex() -> re.Pattern[str]:
    match = re.search(r"^failregex = (.+)$", FILTER, re.MULTILINE)
    assert match
    return re.compile(match.group(1).replace("<ADDR>", r"(?P<host>\S+)"))


def _contract_regex() -> re.Pattern[str]:
    match = re.search(r"^(\^\\d\{4\}.*AUTH_FAIL .*\$)$", CONTRACT, re.MULTILINE)
    assert match
    return re.compile(match.group(1))


def forged_matches(output: str) -> list[str]:
    """What fail2ban would take from ``output``: each text line, and each line a
    splitter on any separator would cut, in the three forms a filter sees (journal
    entry, log file, bare)."""
    patterns = (_filter_regex(), _contract_regex())
    found: list[str] = []
    for chunk in {*output.split("\n"), *output.splitlines()}:
        for form in (chunk, chunk.split(" ", 2)[-1], "host uvicorn[812]: " + chunk):
            for pattern in patterns:
                match = pattern.match(form)
                if match and match.group("host") == VICTIM:
                    found.append(form)
    return found


def _render(message: str, *args, exc_info=None, name: str = "meshloom.app", level=logging.INFO):
    record = logging.LogRecord(name, level, __file__, 1, message, args or None, exc_info)
    return RedactingFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT)).format(record)


def test_the_forged_line_is_matched_when_it_stands_alone():
    # The harness itself: an unescaped forged line IS what the filter matches.
    assert forged_matches(FORGED_LINE) and forged_matches(FORGED_BARE)
    assert forged_matches(f"2026-10-27 10:15:02 - x - INFO - hello\n{FORGED_LINE}")


def test_escape_control_covers_c0_c1_del_and_unicode_separators():
    assert escape_control("a\nb\rc\td") == "a\\nb\\rc\\td"
    assert escape_control("\x00\x1b\x7f\x85  ") == "\\x00\\x1b\\x7f\\x85\\u2028\\u2029"
    assert escape_control("plain é ✓") == "plain é ✓"
    for code in [*range(0x20), *range(0x7F, 0xA0), 0x2028, 0x2029]:
        assert len(escape_control(chr(code)).splitlines()) == 1
        assert not re.search("[\x00-\x1f\x7f-\x9f  ]", escape_control(chr(code)))


@pytest.mark.parametrize("injection", INJECTIONS)
def test_a_message_cannot_forge_a_line(injection):
    out = _render(injection)
    assert out.count("\n") == 0 and len(out.splitlines()) == 1
    assert forged_matches(out) == []


@pytest.mark.parametrize("injection", INJECTIONS)
def test_arguments_cannot_forge_a_line(injection):
    for out in (
        _render("Created group %s (id=%d)", injection, 3),
        _render("Created group %r %s", injection, injection),
        _render("user %s", [injection]),
        _render("user %s", Weird(injection)),
    ):
        assert len(out.splitlines()) == 1, out
        assert forged_matches(out) == []


class Weird:
    """A ``__str__`` that does not escape: ``%s`` of it carries the raw text."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text


def test_the_logger_name_and_a_dict_argument_are_covered_too():
    record = logging.LogRecord(
        f"app.x\n{FORGED_LINE}",
        logging.INFO,
        __file__,
        1,
        "%(k)s",
        ({"k": f"a\n{FORGED_LINE}"},),
        None,
    )
    out = RedactingFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT)).format(record)
    assert len(out.splitlines()) == 1 and forged_matches(out) == []


def _raise_in_chain(text: str) -> tuple:
    try:
        try:
            raise OSError(text)
        except OSError as inner:
            raise ValueError(text) from inner
    except ValueError:
        try:
            raise RuntimeError(text)
        except RuntimeError:
            return sys.exc_info()
    raise AssertionError


@pytest.mark.parametrize("injection", INJECTIONS)
def test_exception_text_cannot_forge_a_line(injection):
    exc_info = _raise_in_chain(injection)
    out = _render("failed", exc_info=exc_info)
    assert forged_matches(out) == []
    # The structure of a traceback is kept (frames on their own lines).
    assert "Traceback (most recent call last):" in out and "_raise_in_chain" in out
    assert "The above exception was the direct cause" in out
    assert "OSError" in out
    # Positive control: the plain traceback formatter lets the same text through.
    assert forged_matches(logging.Formatter().formatException(exc_info)) != []
    assert "\\n" in out or "\\r" in out or "\\x" in out or "\\u" in out


def test_exception_notes_and_groups_cannot_forge_a_line():
    try:
        error = ValueError(f"a\n{FORGED_LINE}")
        error.add_note(f"note\n{FORGED_LINE}")
        raise ExceptionGroup(f"g\n{FORGED_LINE}", [error, OSError(f"b\n{FORGED_LINE}")])
    except ExceptionGroup:
        exc_info = sys.exc_info()
        out = _render("failed", exc_info=exc_info)
        plain = logging.Formatter().formatException(exc_info)
    assert forged_matches(out) == []
    assert "ExceptionGroup" in out and "ValueError" in out
    # An exception message stays on one line, sub-groups included: every copy of
    # the injected text follows an escaped break, never a raw one.
    assert out.count(FORGED_LINE) == 4
    assert out.count("\\n" + FORGED_LINE) == 4
    assert out.count("\n" + FORGED_LINE) == 0
    # Not matchable either way (sub-group lines carry a "| " prefix), but the
    # contract says one line per message: the plain formatter breaks them, ours does not.
    for raw in ("g\n", "a\n", "b\n", "note\n"):
        assert raw in plain and raw not in out


def test_a_syntax_error_cannot_forge_a_line():
    try:
        compile(f"x = 1\n{FORGED_LINE}", "<user>", "exec")
    except SyntaxError:
        out = _render("failed", exc_info=sys.exc_info())
    assert forged_matches(out) == []


def test_a_traceback_cached_raw_on_the_record_is_not_trusted():
    # A plain Formatter earlier in the handler chain leaves the raw text in exc_text.
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", None, None)
    record.exc_text = f"Traceback (most recent call last):\nValueError: a\n{FORGED_LINE}"
    out = RedactingFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT)).format(record)
    assert forged_matches(out) == []
    assert forged_matches(record.exc_text) == []  # what a later unwrapped handler reuses

    try:
        raise ValueError(f"a\n{FORGED_LINE}")
    except ValueError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "f", None, sys.exc_info())
    logging.Formatter().format(record)  # plain formatter first: raw exc_text
    assert forged_matches(record.exc_text) != []
    out = RedactingFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT)).format(record)
    assert forged_matches(out) == []
    assert forged_matches(record.exc_text) == []


def test_format_exception_safe_keeps_a_normal_traceback_readable():
    try:
        raise ValueError("boom")
    except ValueError:
        text = format_exception_safe(sys.exc_info())
    assert text.startswith("Traceback (most recent call last):")
    assert text.endswith("ValueError: boom")
    assert text.count("\n") >= 3


# --- Through the real handlers --------------------------------------------------------


class _Stderr:
    """What the app's stream handlers wrote (they are pointed at one buffer)."""

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def readouterr(self):
        text = self.buffer.getvalue()
        self.buffer.seek(0)
        self.buffer.truncate()
        return types.SimpleNamespace(err=text)


@pytest.fixture
def real_logging(monkeypatch):
    import app.main  # noqa: F401  (it calls setup_logging() on first import)

    out = _Stderr()
    # StreamHandler binds sys.stderr when it is built.
    monkeypatch.setattr(sys, "stderr", out.buffer)
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    setup_logging()
    clear_recent_log_lines()
    yield out
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


@pytest.mark.parametrize("injection", INJECTIONS[:3])
def test_stderr_and_the_ring_buffer_never_hold_a_forged_line(real_logging, injection):
    logging.getLogger("meshloom.auth").warning("hello %s", injection)
    logging.getLogger("meshloom.app").info(injection)
    try:
        raise ValueError(injection)
    except ValueError:
        logging.getLogger("meshloom.app").exception("failed")
    err = real_logging.readouterr().err
    assert "hello x" in err
    ring = "\n".join(get_recent_log_lines())
    assert forged_matches(err) == [] and forged_matches(ring) == []


def test_the_access_log_cannot_forge_a_line(real_logging):
    # The decoded path is the real vector: /x%0A<line>%0A reaches the record as text.
    logger = logging.getLogger("uvicorn.access")
    logger.setLevel(logging.INFO)
    path = f"/x\n{FORGED_LINE}\n"
    args = ("1.2.3.4:5", "GET", path, "1.1", 200)
    logger.info('%s - "%s %s HTTP/%s" %d', *args)
    err = real_logging.readouterr().err
    assert "/x\\n2026" in err and forged_matches(err) == []
    # Positive control: through the same formatter without the escaping, it matches.
    handler = next(h for h in logger.handlers if isinstance(h.formatter, RedactingFormatter))
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None
    )
    assert forged_matches(handler.formatter.inner.format(record)) != []


# --- Realistic inputs ----------------------------------------------------------------


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_forged_username_at_login_writes_one_clean_line_and_counts_one_failure(
    real_logging,
):
    await create_user()
    async with make_client(client=("198.51.100.20", 50000)) as client:
        response = await client.post(
            "/api/auth/login", json={"username": f"x\n{FORGED_LINE}", "password": PASSWORD}
        )
    assert response.status_code == 401
    err = real_logging.readouterr().err
    ring = "\n".join(get_recent_log_lines())
    for text in (err, ring):
        assert forged_matches(text) == []
        auth_lines = [line for line in text.splitlines() if "AUTH_FAIL" in line]
        assert auth_lines, text
        assert len(auth_lines) == 1 and "ip=198.51.100.20 " in auth_lines[0]
        assert "user=h:" in auth_lines[0] and VICTIM not in text
    # The ban counts the real peer, once, and never the victim.
    assert list(ip_ban._failures) == ["198.51.100.20"]
    assert len(ip_ban._failures["198.51.100.20"]) == 1


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_name_logged_at_info_cannot_forge_a_line(real_logging):
    # app/routers/settings.py logs a blocked display name (a mesh node's) at INFO.
    await create_user()
    async with make_client() as client:
        await login(client, ADMIN, PASSWORD)
        response = await client.post(
            "/api/settings/blocked-names/toggle", json={"name": f"g\n{FORGED_LINE}"}
        )
    assert response.status_code == 200, response.text
    err = real_logging.readouterr().err
    assert "Toggling blocked name: g\\n2026" in err
    assert forged_matches(err) == []
    assert forged_matches("\n".join(get_recent_log_lines())) == []
    assert VICTIM not in list(ip_ban._failures)


def _scope(peer: str) -> dict:
    return {"type": "http", "client": (peer, 1), "headers": [], "state": {}}


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db")
@pytest.mark.parametrize(
    "peer",
    [
        f"2001:db8::1%eth0\n{FORGED_LINE}",
        f"2001:db8::1%x\r\n{FORGED_BARE}",
        f"{VICTIM}\n{FORGED_LINE}",
        f"{VICTIM} route=login\n",
        "2001:db8::1%25",
    ],
)
def test_a_hostile_client_address_writes_no_forged_line_and_feeds_no_ban(peer, caplog):
    # What a trusted proxy's X-Forwarded-For could hand over as the client host.
    caplog.set_level(logging.WARNING, logger="meshloom.auth")
    fail_log.log_auth_failure(_scope(peer), "bearer", "invalid_token")
    messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith("AUTH_FAIL ")]
    assert len(messages) == 1
    assert messages[0].startswith("AUTH_FAIL ip=-")
    out = RedactingFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT)).format(
        logging.LogRecord("meshloom.auth", logging.WARNING, __file__, 1, messages[0], None, None)
    )
    assert forged_matches(out) == []
    assert ip_ban._failures == {}


def test_ip_field_refuses_a_zone_id():
    assert fail_log.ip_field("fe80::1%eth0") == "-"
    assert fail_log.ip_field(f"2001:db8::1%x\n{FORGED_LINE}") == "-"
    assert fail_log.ip_field("2001:db8::1") == "2001:db8::1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("route", "login\nx"),
        ("route", "login x"),
        ("reason", "a\rb"),
        ("via", "lan\n" + FORGED_LINE),
        ("route", ""),
    ],
)
def test_format_line_refuses_a_field_outside_its_alphabet(field, value):
    fields = {
        "ip": "203.0.113.7",
        "route": "login",
        "reason": "invalid_credentials",
        "via": "lan",
        "proxied": False,
    }
    fields[field] = value
    with pytest.raises(ValueError):
        fail_log.format_line(username=None, **fields)  # type: ignore[arg-type]


def test_a_username_with_control_characters_only_reaches_the_line_as_a_hash():
    line = fail_log.format_line(
        ip="203.0.113.7",
        route="login",
        reason="invalid_credentials",
        via="lan",
        proxied=False,
        username=f"x\n{FORGED_LINE}\x00",
    )
    assert "\n" not in line and re.search(r"user=h:[0-9a-f]{6}$", line)


def test_a_meshcore_exception_cannot_forge_a_line():
    # The meshcore filter formats the traceback itself and caches it on the record.
    from app.log_redaction import install_meshcore_log_redaction

    install_meshcore_log_redaction()
    logger = logging.getLogger("meshcore")
    records: list[logging.LogRecord] = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Grab()
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        try:
            raise ValueError(f"radio said\n{FORGED_LINE}")
        except ValueError:
            logger.exception("handle_rx failed")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    [record] = records
    assert record.exc_text and forged_matches(record.exc_text) == []
    out = RedactingFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT)).format(record)
    assert forged_matches(out) == [] and "radio said\\n2026" in out


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db")
def test_the_ban_is_fed_even_when_the_line_cannot_be_formatted(monkeypatch, caplog):
    seen = []
    monkeypatch.setattr(ip_ban, "note_failure", lambda ip, **kw: seen.append((ip, kw)))
    monkeypatch.setattr(fail_log, "format_line", lambda **kw: (_ for _ in ()).throw(ValueError()))
    caplog.set_level(logging.WARNING, logger="meshloom.auth")
    fail_log.log_auth_failure(_scope("198.51.100.20"), "bearer", "invalid_token")
    assert [ip for ip, _ in seen] == ["198.51.100.20"]
    assert seen[0][1]["route"] == "bearer" and seen[0][1]["proxied"] is False
    assert not [r for r in caplog.records if r.getMessage().startswith("AUTH_FAIL ")]


# --- Long lines: journald and Docker split them (MSHL-435) ----------------------------

DOCKER_LINE_MAX = 16 * 1024  # the smallest split: Docker's log drivers; journald is 48 KiB


def _split_like_journald(output: str, line_max: int) -> list[str]:
    """The journal entries of ``output``: each line, cut every ``line_max`` bytes."""
    entries: list[str] = []
    for line in output.split("\n"):
        raw = line.encode("utf-8", "surrogatepass")
        for start in range(0, max(len(raw), 1), line_max):
            entries.append(raw[start : start + line_max].decode("utf-8", "replace"))
    return entries


def _aligned_injection(line_max: int) -> str:
    """A name that puts ``FORGED_LINE`` exactly at the start of the second entry."""
    head = _render("Toggling blocked name: %s", "")
    return "A" * (line_max - len(head.encode("utf-8"))) + FORGED_LINE


@pytest.mark.parametrize("line_max", [48 * 1024, 16 * 1024], ids=["journald", "docker"])
def test_a_line_long_enough_to_be_split_forges_no_entry(monkeypatch, line_max):
    name = _aligned_injection(line_max)
    out = _render("Toggling blocked name: %s", name)
    assert out.endswith(" more bytes]") and "[truncated: " in out
    assert all(len(line.encode()) < DOCKER_LINE_MAX for line in out.split("\n"))
    entries = _split_like_journald(out, line_max)
    assert len(entries) == 1 and forged_matches("\n".join(entries)) == []
    # Positive control: without the cap the second entry is the forged line.
    monkeypatch.setattr(log_redaction, "MAX_LINE_BYTES", 10**9)
    uncapped = _split_like_journald(_render("Toggling blocked name: %s", name), line_max)
    assert uncapped[1] == FORGED_LINE and forged_matches(uncapped[1])


def test_the_cap_counts_utf8_bytes_and_cuts_on_a_character():
    out = _render("name %s", "\U0001f600" * 3000 + "é" * 10)  # 12 000+ bytes, 3 000 characters
    raw = out.encode("utf-8")
    assert len(raw) <= log_redaction.MAX_LINE_BYTES < DOCKER_LINE_MAX
    assert "�" not in raw.decode("utf-8")
    assert out.endswith(" more bytes]")


def test_every_line_of_a_traceback_is_capped():
    try:
        raise ValueError("x" * 20000 + FORGED_LINE)
    except ValueError:
        out = _render("failed", exc_info=sys.exc_info())
    lines = out.split("\n")
    assert len(lines) > 2 and lines[-1].startswith("ValueError: xxx")
    assert all(len(line.encode()) < DOCKER_LINE_MAX for line in lines)
    assert forged_matches(out) == []


def test_a_short_line_is_left_alone():
    line = "y" * log_redaction.MAX_LINE_BYTES
    assert log_redaction.cap_line(line) == line
    assert log_redaction.cap_line(line + "y").endswith(" [truncated: 49 more bytes]")


def test_a_truncated_auth_fail_line_never_matches(monkeypatch):
    message = (
        f"AUTH_FAIL ip={VICTIM} route=login reason=invalid_credentials via=lan proxied=0 user=h:"
        + "a" * 9000
    )
    out = _render(message, name="meshloom.auth", level=logging.WARNING)
    assert "[truncated: " in out and forged_matches(out) == []
    # Positive control: the same record uncut is a match.
    monkeypatch.setattr(log_redaction, "MAX_LINE_BYTES", 10**9)
    assert forged_matches(_render(message, name="meshloom.auth", level=logging.WARNING))


def test_a_real_auth_fail_line_is_far_below_the_cap():
    line = fail_log.format_line(
        ip="2001:db8:ffff:ffff:ffff:ffff:ffff:ffff",
        route="login_2fa",
        reason="pending_invalid",
        via="relay",
        proxied=False,
        username="x" * 1000,
    )
    out = _render(line, name="meshloom.auth", level=logging.WARNING)
    assert len(out) < 512 and "truncated" not in out
    assert forged_matches(out.replace("2001:db8:ffff:ffff:ffff:ffff:ffff:ffff", VICTIM))


# --- What Python prints to stderr by itself (MSHL-435) --------------------------------


@pytest.fixture
def default_hooks(monkeypatch):
    """The interpreter's own hooks, as a server process has them (pytest installs its own)."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(sys, "unraisablehook", sys.__unraisablehook__)
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)


def test_install_replaces_only_the_default_hooks(default_hooks, monkeypatch):
    log_redaction.install_uncaught_handlers()
    assert sys.excepthook is log_redaction._excepthook
    assert sys.unraisablehook is log_redaction._unraisablehook
    assert threading.excepthook is log_redaction._thread_excepthook

    def mine(*_args):
        return None

    for module, name in ((sys, "excepthook"), (sys, "unraisablehook"), (threading, "excepthook")):
        monkeypatch.setattr(module, name, mine)
    log_redaction.install_uncaught_handlers()
    assert sys.excepthook is mine and sys.unraisablehook is mine and threading.excepthook is mine


@pytest.mark.parametrize("injection", INJECTIONS[:3])
def test_an_uncaught_exception_cannot_forge_a_line(default_hooks, real_logging, injection):
    try:
        raise ValueError(injection)
    except ValueError:
        sys.excepthook(*sys.exc_info())
    err = real_logging.readouterr().err
    assert "meshloom.uncaught - CRITICAL - Uncaught exception" in err
    assert "ValueError: x\\" in err and forged_matches(err) == []


@pytest.mark.parametrize("injection", INJECTIONS[:3])
def test_an_uncaught_exception_in_a_thread_cannot_forge_a_line(
    default_hooks, real_logging, injection
):
    def boom():
        raise ValueError(injection)

    worker = threading.Thread(target=boom, name="worker")
    worker.start()
    worker.join()
    err = real_logging.readouterr().err
    assert "Uncaught exception in thread worker" in err
    assert "ValueError: x\\" in err and forged_matches(err) == []


def test_an_unraisable_exception_cannot_forge_a_line(default_hooks, real_logging):
    class Leaky:
        def __del__(self):
            raise ValueError(f"x\n{FORGED_LINE}")

    Leaky()
    gc.collect()
    err = real_logging.readouterr().err
    assert "Exception ignored" in err and "ValueError: x\\n" in err
    assert forged_matches(err) == []


def test_a_warning_cannot_forge_a_line(real_logging):
    # logging remembers it already captured warnings; pytest restores showwarning
    # after each test, so start from a clean state.
    logging.captureWarnings(False)
    log_redaction.install_uncaught_handlers()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn(f"x\n{FORGED_LINE}", UserWarning, stacklevel=1)
    finally:
        logging.captureWarnings(False)
    err = real_logging.readouterr().err
    assert "py.warnings - WARNING - " in err and "UserWarning: x\\n" in err
    assert forged_matches(err) == []
