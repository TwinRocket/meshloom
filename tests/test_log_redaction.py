"""No Wi-Fi password or private key reaches a log line, at any level.

The scenarios run the real meshcore stack (tests/fixtures/meshcore_loopback.py)
with DEBUG on the root and ``meshcore`` loggers, and check every captured
record and every line of the ``/api/debug`` ring buffer.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import pytest
from meshcore import EventType
from pydantic import BaseModel, ConfigDict, Field, SecretStr

import app.log_redaction as log_redaction
from app.config import (
    _recent_log_handler,
    _RepeatSquelch,
    clear_recent_log_lines,
    get_recent_log_lines,
)
from app.log_redaction import (
    REDACTED,
    REDACTION_FAILED,
    MeshcoreSecretFilter,
    RedactingFormatter,
    install_meshcore_log_redaction,
    install_redacting_formatter,
    redact_text,
    registered_secret_count,
    secret_scope,
)

# Imported at module level: importing app.main runs setup_logging(), whose
# dictConfig would drop caplog's handler if it happened inside a test.
from app.main import log_server_errors, request_validation_error_handler
from tests.fixtures.meshcore_loopback import cli_reply, make_loopback

SECRET = "S3cr3t-Pw!"
# A private key with a quote byte, so repr() switches quote styles.
PRIVATE_KEY = bytes(range(0x20, 0x60))
assert len(PRIVATE_KEY) == 64 and b"'" in PRIVATE_KEY


def secret_forms(secret: str | bytes) -> list[str]:
    raw = secret.encode() if isinstance(secret, str) else secret
    forms = [raw.hex(), raw.hex().upper(), repr(raw)[2:-1]]
    if isinstance(secret, str):
        forms.append(secret)
    return forms


@pytest.fixture
def debug_logs(caplog):
    """DEBUG everywhere, the redaction installed, the ring buffer attached and wrapped."""
    install_meshcore_log_redaction()
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="meshcore")
    root = logging.getLogger()
    added = _recent_log_handler not in root.handlers
    if added:
        root.addHandler(_recent_log_handler)
    previous_level = _recent_log_handler.level
    previous_formatter = _recent_log_handler.formatter
    _recent_log_handler.setLevel(logging.DEBUG)
    if previous_formatter is None:
        _recent_log_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    install_redacting_formatter(_recent_log_handler)
    clear_recent_log_lines()
    yield caplog
    clear_recent_log_lines()
    _recent_log_handler.setLevel(previous_level)
    if added:
        root.removeHandler(_recent_log_handler)


def captured_text(caplog) -> list[str]:
    texts: list[str] = []
    for record in caplog.records:
        texts.append(record.getMessage())
        if record.exc_text:
            texts.append(record.exc_text)
        if record.stack_info:
            texts.append(record.stack_info)
    texts.extend(get_recent_log_lines())
    return texts


def assert_no_secret(caplog, *secrets: str | bytes) -> None:
    texts = captured_text(caplog)
    for secret in secrets or (SECRET,):
        for form in secret_forms(secret):
            for text in texts:
                assert form not in text, f"secret form {form!r} leaked in {text!r}"


def messages(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def find(caplog, prefix: str) -> list[str]:
    found = [message for message in messages(caplog) if message.startswith(prefix)]
    assert found, f"no record starting with {prefix!r}: the test would pass silently"
    return found


def cli_responder(reply: str):
    def respond(payload: bytes):
        if payload[:1] == b"\x42":
            return [cli_reply(reply)]
        return []

    return respond


# --------------------------------------------------------------------------
# Real meshcore stack
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["tcp", "serial"])
async def test_run_cli_command_set_password_is_redacted(debug_logs, kind):
    loop = await make_loopback(kind, cli_responder("> wifi.pwd updated (reboot to apply)"))
    try:
        result = await loop.mc.commands.run_cli_command(f"set wifi.pwd {SECRET}")
        await loop.settle()
    finally:
        await loop.close()
    assert result.type == EventType.CLI_REPLY
    assert find(debug_logs, "Sending cli command: ") == [f"Sending cli command: {REDACTED}"]
    # bytearray frame: meshcore logs its repr, plaintext without the filter.
    assert find(debug_logs, "Sending raw data: ") == [f"Sending raw data: 42...{REDACTED}"]
    assert find(debug_logs, "sending pkt : ") == ["sending pkt : [redacted frame 42]"]
    assert_no_secret(debug_logs)


async def test_incoming_cli_reply_frame_is_redacted(debug_logs):
    loop = await make_loopback()
    try:
        loop.feed(cli_reply(f"ab|> {SECRET}"))
        await loop.settle()
    finally:
        await loop.close()
    assert find(debug_logs, "Received data: ") == [f"Received data: {REDACTED}"]
    assert find(debug_logs, "Dispatching event: EventType.CLI_REPLY") == [
        f"Dispatching event: EventType.CLI_REPLY, {REDACTED}"
    ]
    assert_no_secret(debug_logs)


async def test_non_secret_frames_keep_their_debug_value(debug_logs):
    loop = await make_loopback()
    try:
        loop.feed(b"\x00")  # OK
        await loop.settle()
        await loop.mc.commands.send(b"\x16\x03")
        await loop.settle()
    finally:
        await loop.close()
    assert find(debug_logs, "Received data: ") == ["Received data: 00"]
    assert find(debug_logs, "Sending raw data: ") == ["Sending raw data: 1603"]
    assert find(debug_logs, "sending pkt : ") == ["sending pkt : b'<\\x02\\x00\\x16\\x03'"]


async def test_negative_control_without_filter_the_secret_leaks(debug_logs):
    meshcore_logger = logging.getLogger("meshcore")
    secret_filter = install_meshcore_log_redaction()
    meshcore_logger.removeFilter(secret_filter)
    try:
        loop = await make_loopback(responder=cli_responder("> ok"))
        try:
            await loop.mc.commands.run_cli_command(f"set wifi.pwd {SECRET}")
            loop.feed(cli_reply(f"ab|> {SECRET}"))
            await loop.settle()
        finally:
            await loop.close()
    finally:
        install_meshcore_log_redaction()
    joined = "\n".join(messages(debug_logs))
    assert SECRET in joined
    assert SECRET.encode().hex() in joined


async def test_subscriber_error_with_payload_is_redacted(debug_logs):
    loop = await make_loopback()

    def bad_subscriber(event):
        raise RuntimeError(f"cannot handle {event.payload['text']}")

    loop.mc.subscribe(EventType.CLI_REPLY, bad_subscriber)
    try:
        loop.feed(cli_reply(f"ab|> {SECRET}"))
        await loop.settle()
    finally:
        await loop.close()
    [record] = [
        r for r in debug_logs.records if r.getMessage().startswith("Error in event handler")
    ]
    assert record.getMessage() == f"Error in event handler for EventType.CLI_REPLY: {REDACTED}"
    assert record.exc_info is None and record.exc_text is None
    assert_no_secret(debug_logs)


async def test_handle_rx_parse_error_on_cli_frame_is_redacted(debug_logs):
    loop = await make_loopback()
    original = loop.mc.dispatcher.dispatch

    async def failing_dispatch(event):
        if event.type == EventType.CLI_REPLY:
            # The exception text quotes the payload and fakes a raw= field.
            raise ValueError(f"bad {event.payload['text']} | raw=00\n")
        await original(event)

    loop.mc._reader.dispatcher.dispatch = failing_dispatch
    try:
        loop.feed(cli_reply(f"ab|> {SECRET}"))
        await loop.settle()
    finally:
        await loop.close()
    [message] = find(debug_logs, "handle_rx parse error: ")
    assert message == f"handle_rx parse error: ValueError: {REDACTED}"
    assert_no_secret(debug_logs)


async def test_handle_rx_parse_error_on_plain_frame_is_kept(debug_logs):
    loop = await make_loopback()
    original = loop.mc.dispatcher.dispatch

    async def failing_dispatch(event):
        if event.type == EventType.OK:
            raise ValueError("bad frame")
        await original(event)

    loop.mc._reader.dispatcher.dispatch = failing_dispatch
    try:
        loop.feed(b"\x00")
        await loop.settle()
    finally:
        await loop.close()
    [message] = find(debug_logs, "handle_rx parse error: ")
    assert message.startswith("handle_rx parse error: ValueError: bad frame | raw=00\n")
    assert "Traceback" in message


def test_parse_error_reads_the_raw_hex_from_the_args(debug_logs):
    logger = logging.getLogger("meshcore")
    # A secret frame whose exception text carries a harmless-looking raw= field.
    logger.error(
        "handle_rx parse error: %s: %s | raw=%s\n%s",
        "ValueError",
        "oops | raw=00",
        cli_reply(f"ab|> {SECRET}").hex(),
        "Traceback: ...",
    )
    [message] = find(debug_logs, "handle_rx parse error: ")
    assert message == f"handle_rx parse error: ValueError: {REDACTED}"
    assert_no_secret(debug_logs)


def test_meshcore_record_with_exc_info_is_scrubbed(debug_logs):
    logger = logging.getLogger("meshcore")
    with secret_scope(SECRET):
        try:
            raise RuntimeError(f"failed while handling {SECRET}")
        except RuntimeError:
            logger.debug("Command error", exc_info=True)
    [record] = [r for r in debug_logs.records if r.getMessage() == "Command error"]
    assert record.exc_info is None
    assert record.exc_text and REDACTED in record.exc_text
    assert_no_secret(debug_logs)


def test_app_record_is_redacted_by_the_formatter(debug_logs):
    frame_hex = cli_reply(f"ab|> {SECRET}").hex()
    logging.getLogger("app.example").warning("relayed: Received data: %s", frame_hex)
    lines = [line for line in get_recent_log_lines() if "relayed:" in line]
    assert lines and lines[0].endswith(f"relayed: Received data: {REDACTED}")
    assert all(SECRET.encode().hex() not in line for line in get_recent_log_lines())


def test_exc_text_is_written_back_for_unwrapped_handlers(debug_logs):
    logger = logging.getLogger("app.example")
    with secret_scope(SECRET):
        try:
            raise RuntimeError(f"boom {SECRET}")
        except RuntimeError:
            logger.exception("failed")
    # caplog's handler is not wrapped: it reuses the exc_text the ring buffer's
    # redacting formatter wrote back.
    assert "boom" in debug_logs.text
    assert SECRET not in debug_logs.text
    assert_no_secret(debug_logs)


# --------------------------------------------------------------------------
# CompanionCli on the real stack
# --------------------------------------------------------------------------


async def _companion_cli(kind: str = "tcp"):
    from app.radio import RadioManager
    from app.services.companion_cli import CompanionCli
    from tests.fixtures.meshcore_loopback import FakeCompanionFirmware

    firmware = FakeCompanionFirmware()
    loop = await make_loopback(kind, firmware)
    manager = RadioManager()
    manager._meshcore = loop.mc
    manager.firmware_ver_code = 14
    return loop, firmware, manager, CompanionCli


@pytest.mark.parametrize("kind", ["tcp", "serial"])
async def test_companion_cli_password_set_and_read_are_redacted(debug_logs, kind):
    from app.services.companion_cli import mark_radio_lock_held

    loop, firmware, manager, CompanionCli = await _companion_cli(kind)
    try:
        async with manager.radio_operation("test") as mc:
            with mark_radio_lock_held():
                cli = CompanionCli(mc, manager=manager)
                await cli.set("wifi.pwd", SECRET, secret=True)
                reply = await cli.get("wifi.pwd", sensitive=True)
        await loop.settle()
    finally:
        await loop.close()
    assert firmware.stored_pwd == SECRET and reply.value == SECRET
    assert find(debug_logs, "Sending raw data: ") == [f"Sending raw data: 42...{REDACTED}"] * 2
    assert find(debug_logs, "sending pkt : ") == ["sending pkt : [redacted frame 42]"] * 2
    assert find(debug_logs, "Received data: ") == [f"Received data: {REDACTED}"] * 2
    assert "CLI set wifi.pwd -> [redacted]" in messages(debug_logs)
    assert "CLI get wifi.pwd -> [redacted]" in messages(debug_logs)
    assert_no_secret(debug_logs)


async def test_companion_cli_late_reply_after_timeout_is_redacted(debug_logs):
    from app.services.companion_cli import CliTimeoutError, mark_radio_lock_held

    loop, firmware, manager, CompanionCli = await _companion_cli()
    firmware.stored_pwd = SECRET
    firmware.silent.add("get wifi.pwd")
    try:
        async with manager.radio_operation("test") as mc:
            with mark_radio_lock_held():
                cli = CompanionCli(mc, manager=manager)
                with pytest.raises(CliTimeoutError):
                    await cli.run("get wifi.pwd", timeout=0.05)
        # The reply lands after the call returned: logged by the reader task,
        # outside any call, still redacted.
        loop.feed(cli_reply(f"{firmware.tags[-1]}|> {SECRET}"))
        await loop.settle()
    finally:
        await loop.close()
    assert find(debug_logs, "Dispatching event: EventType.CLI_REPLY") == [
        f"Dispatching event: EventType.CLI_REPLY, {REDACTED}"
    ]
    assert "Discarded a stale CLI reply" not in messages(debug_logs)
    assert_no_secret(debug_logs)


# --------------------------------------------------------------------------
# Private key export and import
# --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["tcp", "serial"])
async def test_private_key_export_is_redacted(debug_logs, kind):
    def respond(payload: bytes):
        if payload[:1] == b"\x17":
            return [b"\x0e" + PRIVATE_KEY]
        return []

    loop = await make_loopback(kind, respond)
    try:
        result = await loop.mc.commands.export_private_key()
        await loop.settle()
    finally:
        await loop.close()
    assert result.type == EventType.PRIVATE_KEY
    assert result.payload["private_key"] == PRIVATE_KEY
    assert find(debug_logs, "Received data: ") == [f"Received data: {REDACTED}"]
    assert find(debug_logs, "Received private key response: ") == [
        f"Received private key response: {REDACTED}"
    ]
    assert find(debug_logs, "Dispatching event: EventType.PRIVATE_KEY") == [
        f"Dispatching event: EventType.PRIVATE_KEY, {REDACTED}"
    ]
    assert_no_secret(debug_logs, PRIVATE_KEY)


@pytest.mark.parametrize("kind", ["tcp", "serial"])
async def test_private_key_import_is_redacted(debug_logs, kind):
    def respond(payload: bytes):
        if payload[:1] == b"\x18":
            return [b"\x00"]
        return []

    loop = await make_loopback(kind, respond)
    try:
        result = await loop.mc.commands.import_private_key(PRIVATE_KEY)
        # A bytearray-built frame logs its repr instead of hex.
        await loop.mc.commands.send(bytearray(b"\x18" + PRIVATE_KEY))
        await loop.settle()
    finally:
        await loop.close()
    assert result.type == EventType.OK
    assert find(debug_logs, "Sending raw data: ") == [f"Sending raw data: 18...{REDACTED}"] * 2
    assert find(debug_logs, "sending pkt : ") == ["sending pkt : [redacted frame 18]"] * 2
    assert_no_secret(debug_logs, PRIVATE_KEY)


async def test_import_service_scrubs_the_key_from_app_records(debug_logs):
    from app.services.radio_commands import import_private_key_and_refresh_keystore

    loop = await make_loopback(responder=lambda payload: [b"\x00"])

    async def export_fn(_mc):
        logging.getLogger("app.example").info("refreshed key %s", PRIVATE_KEY.hex())
        return True

    try:
        await import_private_key_and_refresh_keystore(
            loop.mc, PRIVATE_KEY, export_and_store_private_key_fn=export_fn
        )
        await loop.settle()
    finally:
        await loop.close()
    lines = [line for line in get_recent_log_lines() if "refreshed key" in line]
    assert lines and lines[0].endswith(f"refreshed key {REDACTED}")
    assert all(PRIVATE_KEY.hex() not in line for line in get_recent_log_lines())


def test_private_key_update_model_never_echoes_the_key(debug_logs):
    from pydantic import ValidationError

    from app.routers.radio import PrivateKeyUpdate

    secret = PRIVATE_KEY.hex()
    model = PrivateKeyUpdate(private_key=secret)
    assert secret not in repr(model) and secret not in str(model)
    try:
        PrivateKeyUpdate(private_key=[secret])  # type: ignore[arg-type]
    except ValidationError as exc:
        assert secret not in str(exc)
        # A service-side ValidationError is a 500, logged with its traceback.
        logging.getLogger("app.example").exception("Unhandled exception on PUT /x")
    else:  # pragma: no cover
        pytest.fail("expected a ValidationError")
    assert_no_secret(debug_logs, secret)


# --------------------------------------------------------------------------
# HTTP: 422 bodies and 500 logs
# --------------------------------------------------------------------------


class SampleCredentials(BaseModel):
    """The mandatory shape of a model with a secret field (app/AGENTS.md)."""

    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr = Field(max_length=8)


def _mini_app():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    mini = FastAPI()
    mini.middleware("http")(log_server_errors)
    mini.add_exception_handler(RequestValidationError, request_validation_error_handler)

    @mini.post("/body")
    async def body(credentials: SampleCredentials) -> dict:
        return {"ok": True}

    @mini.post("/service")
    async def service(payload: dict) -> dict:
        # A model built in service code: its ValidationError is a 500.
        SampleCredentials(password=payload["password"])
        return {"ok": True}

    return mini


def test_422_body_never_echoes_the_input():
    from fastapi.testclient import TestClient

    with TestClient(_mini_app()) as client:
        response = client.post("/body", json={"password": SECRET})
    assert response.status_code == 422
    assert SECRET not in response.text
    [error] = response.json()["detail"]
    assert set(error) == {"type", "loc", "msg"}
    assert error["loc"] == ["body", "password"]


def test_app_422_handler_is_registered():
    from fastapi.exceptions import RequestValidationError

    from app.main import app, request_validation_error_handler

    assert app.exception_handlers[RequestValidationError] is request_validation_error_handler


def test_500_from_service_validation_error_logs_no_secret(debug_logs):
    from fastapi.testclient import TestClient

    with TestClient(_mini_app(), raise_server_exceptions=False) as client:
        response = client.post("/service", json={"password": SECRET})
    assert response.status_code == 500
    assert any("Unhandled exception" in message for message in messages(debug_logs))
    assert_no_secret(debug_logs)


# --------------------------------------------------------------------------
# redact_text unit cases
# --------------------------------------------------------------------------

CLI_FRAME = b"\x42ab|set wifi.pwd " + SECRET.encode()


@pytest.mark.parametrize(
    "payload",
    [
        CLI_FRAME.hex(),
        repr(CLI_FRAME),
        repr(bytearray(CLI_FRAME)),
    ],
)
def test_sending_raw_data_forms(payload):
    assert redact_text(f"Sending raw data: {payload}") == f"Sending raw data: 42...{REDACTED}"


def test_sending_raw_data_unparsable_fails_closed():
    assert redact_text("Sending raw data: <object at 0x1>") == f"Sending raw data: {REDACTED}"
    assert redact_text("Sending raw data: zz") == f"Sending raw data: {REDACTED}"


def test_sending_pkt_has_no_length_header():
    packet = b"\x3c" + len(CLI_FRAME).to_bytes(2, "little") + CLI_FRAME
    assert redact_text(f"sending pkt : {packet!r}") == "sending pkt : [redacted frame 42]"
    assert redact_text("sending pkt : garbage") == f"sending pkt : {REDACTED}"


def test_quote_byte_in_frame_switches_repr_quotes():
    frame = b"\x42ab|set wifi.pwd it's" + SECRET.encode()
    assert repr(frame).startswith('b"')
    assert redact_text(f"Sending raw data: {frame!r}") == f"Sending raw data: 42...{REDACTED}"
    packet = b"\x3c" + len(frame).to_bytes(2, "little") + frame
    assert redact_text(f"sending pkt : {packet!r}") == "sending pkt : [redacted frame 42]"


def test_received_and_unhandled_rules():
    reply = cli_reply(f"ab|> {SECRET}")
    assert redact_text(f"Received data: {reply.hex()}") == f"Received data: {REDACTED}"
    assert redact_text("Received data: not-hex") == f"Received data: {REDACTED}"
    assert redact_text("Received data: 0a0b") == "Received data: 0a0b"
    assert (
        redact_text(f"Unhandled data received {bytearray(reply)!r}")
        == f"Unhandled data received {REDACTED}"
    )
    assert (
        redact_text("Unhandled data received bytearray(b'\\x7f\\x01')")
        == "Unhandled data received bytearray(b'\\x7f\\x01')"
    )


def test_private_key_rules():
    frame = b"\x18" + PRIVATE_KEY
    assert redact_text(f"Sending raw data: {frame.hex()}") == f"Sending raw data: 18...{REDACTED}"
    packet = b"\x3c" + len(frame).to_bytes(2, "little") + frame
    assert redact_text(f"sending pkt : {packet!r}") == "sending pkt : [redacted frame 18]"
    export = b"\x0e" + PRIVATE_KEY
    assert redact_text(f"Received data: {export.hex()}") == f"Received data: {REDACTED}"
    assert (
        redact_text(f"Received private key response: {export.hex()}")
        == f"Received private key response: {REDACTED}"
    )
    assert (
        redact_text(
            f"Dispatching event: EventType.PRIVATE_KEY, {{'private_key': {PRIVATE_KEY!r}}}, {{}}"
        )
        == f"Dispatching event: EventType.PRIVATE_KEY, {REDACTED}"
    )


def test_dispatch_and_cli_command_rules():
    assert (
        redact_text(f"Dispatching event: EventType.CLI_REPLY, {{'text': '{SECRET}'}}, {{}}")
        == f"Dispatching event: EventType.CLI_REPLY, {REDACTED}"
    )
    kept = "Dispatching event: EventType.OK, {}, {}"
    assert redact_text(kept) == kept
    assert redact_text(f"Sending cli command: set wifi.pwd {SECRET}") == (
        f"Sending cli command: {REDACTED}"
    )


def test_anchored_vs_unanchored():
    line = f"2026-01-01 00:00:00 - app.x - INFO - Sending cli command: set wifi.pwd {SECRET}"
    assert SECRET in redact_text(line)
    assert redact_text(line, unanchored=True).endswith(f"Sending cli command: {REDACTED}")


def test_multiline_rules_cover_the_traceback():
    text = (
        "2026 - meshcore - ERROR - Error in event handler for EventType.CLI_REPLY: "
        f"{SECRET}\nTraceback (most recent call last):\n  ValueError: {SECRET}"
    )
    assert redact_text(text, unanchored=True) == (
        f"2026 - meshcore - ERROR - Error in event handler for EventType.CLI_REPLY: {REDACTED}"
    )


# --------------------------------------------------------------------------
# secret_scope
# --------------------------------------------------------------------------


def test_secret_scope_refcount_and_forms():
    before = registered_secret_count()
    with secret_scope(SECRET):
        with secret_scope(SECRET):
            assert redact_text(f"x {SECRET} y") == f"x {REDACTED} y"
        assert redact_text(f"x {SECRET.encode().hex().upper()} y") == f"x {REDACTED} y"
    assert registered_secret_count() == before
    assert redact_text(f"x {SECRET} y") == f"x {SECRET} y"


def test_secret_scope_str_repr_forms_both_quotes():
    secret = "pä's\\x"
    with secret_scope(secret):
        # CPython picks double quotes for this value.
        assert redact_text(f"value={secret!r}") == f'value="{REDACTED}"'
        escaped_single = secret.replace("\\", "\\\\").replace("'", "\\'")
        assert redact_text(f"value='{escaped_single}'") == f"value='{REDACTED}'"
        assert REDACTED in redact_text(str({"k": secret}))
        raw = secret.encode()
        assert redact_text(f"data={raw!r}") == f'data=b"{REDACTED}"'
        assert redact_text(f"hex={raw.hex()}") == f"hex={REDACTED}"


def test_secret_scope_bytes_and_short_values():
    key = bytes(range(64))
    with secret_scope(key, "abc", ""):
        assert redact_text(f"k={key.hex()}") == f"k={REDACTED}"
        assert redact_text(f"k={key.hex().upper()}") == f"k={REDACTED}"
        assert redact_text(f"k={key!r}") == f"k=b'{REDACTED}'"
        # Under four bytes: never registered literally.
        assert redact_text("abc def") == "abc def"


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------


def test_filter_runs_before_repeat_squelch_and_install_is_idempotent(monkeypatch):
    from app.config import setup_logging

    setup_logging()
    setup_logging()
    install_meshcore_log_redaction()
    filters = logging.getLogger("meshcore").filters
    assert isinstance(filters[0], MeshcoreSecretFilter)
    assert sum(isinstance(f, MeshcoreSecretFilter) for f in filters) == 1
    assert any(isinstance(f, _RepeatSquelch) for f in filters[1:])
    handlers = [h for h in logging.getLogger().handlers if h.name == "default"]
    assert handlers and isinstance(handlers[0].formatter, RedactingFormatter)
    assert isinstance(_recent_log_handler.formatter, RedactingFormatter)
    access = logging.getLogger("uvicorn.access").handlers
    assert any(isinstance(h.formatter, RedactingFormatter) for h in access)
    install_redacting_formatter(_recent_log_handler)
    assert not isinstance(_recent_log_handler.formatter.inner, RedactingFormatter)


def test_filter_lands_on_existing_child_logger():
    child = logging.getLogger("meshcore.testchild")
    install_meshcore_log_redaction()
    assert isinstance(child.filters[0], MeshcoreSecretFilter)


def test_ble_loggers_are_clamped_after_setup_logging(monkeypatch):
    from app.config import settings, setup_logging

    monkeypatch.setattr(settings, "log_level", "DEBUG")
    try:
        setup_logging()
        for name in (
            "bleak.backends.bluezdbus.manager",
            "bleak.backends.bluezdbus.client",
            "bleak.backends.corebluetooth.client",
            "dbus_fast.message_bus",
        ):
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG)
    finally:
        monkeypatch.undo()
        setup_logging()


def test_ble_loggers_stay_clamped_when_root_is_lowered_later(caplog):
    logging.getLogger("bleak").setLevel(logging.NOTSET)
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    try:
        install_meshcore_log_redaction()
        caplog.set_level(logging.DEBUG)
        assert not logging.getLogger("bleak.backends.bluezdbus.manager").isEnabledFor(logging.DEBUG)
        assert not logging.getLogger("dbus_fast").isEnabledFor(logging.DEBUG)
    finally:
        root.setLevel(previous)


def test_bleak_logging_env_is_clamped_at_import():
    code = (
        "import logging, app.radio\n"
        "print(logging.getLogger('bleak.backends.bluezdbus.manager').isEnabledFor(logging.DEBUG),"
        " logging.getLogger('bleak.backends.corebluetooth.client').isEnabledFor(logging.DEBUG))\n"
    )
    env = {**os.environ, "BLEAK_LOGGING": "1", "MESHCORE_LOG_LEVEL": "DEBUG"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False False"


# --------------------------------------------------------------------------
# Totality
# --------------------------------------------------------------------------


def _raise(*args, **kwargs):
    raise RuntimeError("redaction bug")


async def test_redaction_failure_never_breaks_meshcore(debug_logs, monkeypatch, capsys):
    loop = await make_loopback()
    received = []
    loop.mc.subscribe(EventType.CLI_REPLY, lambda event: received.append(event))
    monkeypatch.setattr(log_redaction, "redact_text", _raise)
    try:
        loop.feed(cli_reply(f"ab|> {SECRET}"))
        await loop.settle()
        loop.feed(cli_reply("ab|> second"))
        await loop.settle()
        await loop.mc.commands.send(CLI_FRAME)
        await loop.settle()
    finally:
        monkeypatch.undo()
        await loop.close()
    # The dispatcher survived the "Dispatching event" record and kept going.
    assert len(received) == 2
    # The frame still reached the transport.
    assert loop.transport.sent_payloads == [CLI_FRAME]
    assert REDACTION_FAILED in messages(debug_logs)
    assert any(REDACTION_FAILED in line for line in get_recent_log_lines())
    assert_no_secret(debug_logs)
    err = capsys.readouterr().err
    assert SECRET not in err and SECRET.encode().hex() not in err


def test_formatter_whose_inner_raises_gives_the_placeholder(capsys):
    class Broken(logging.Formatter):
        def format(self, record):
            raise ValueError(f"inner {record.getMessage()}")

    formatter = RedactingFormatter(Broken())
    record = logging.LogRecord("app.x", logging.INFO, __file__, 1, SECRET, None, None)
    line = formatter.format(record)
    assert line.endswith(REDACTION_FAILED)
    assert SECRET not in line
    assert capsys.readouterr().err == ""
