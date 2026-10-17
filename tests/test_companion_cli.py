"""Companion CLI helper: serialization, tagged replies, secrets, validation."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import pytest

import app.radio as radio_module
import app.services.companion_cli as cli_module
from app.radio import RadioManager
from app.services.companion_cli import (
    CLI_FORBIDDEN_VERBS,
    CLI_MAX_QUARANTINED_TAGS,
    CLI_TAG_ALPHABET,
    CliCommandError,
    CliLockError,
    CliRejectedError,
    CliReply,
    CliTimeoutError,
    CliUnknownCommandError,
    CliUnsupportedError,
    CompanionCli,
    companion_cli_session,
    mark_radio_lock_held,
    new_cli_link_state,
)
from tests.fixtures.meshcore_loopback import FakeCompanionFirmware, cli_reply, make_loopback

SECRET = "S3cr3t-Pw!"
SHORT_TIMEOUT = 0.1


@pytest.fixture
async def radio():
    firmware = FakeCompanionFirmware()
    loop = await make_loopback(responder=firmware)
    manager = RadioManager()
    manager._meshcore = loop.mc
    manager.firmware_ver_code = 14
    manager.device_model = "Heltec V3"
    manager._setup_complete = True
    yield loop, firmware, manager
    await loop.close()


@asynccontextmanager
async def held(manager: RadioManager):
    async with manager.radio_operation("test") as mc:
        with mark_radio_lock_held():
            yield CompanionCli(mc, manager=manager)


def all_log_text(caplog) -> str:
    return "\n".join(
        [record.getMessage() for record in caplog.records]
        + [record.exc_text or "" for record in caplog.records]
    )


# --------------------------------------------------------------------------
# Calls and tags
# --------------------------------------------------------------------------


async def test_run_sends_a_tagged_frame_and_parses_the_reply(radio):
    loop, firmware, manager = radio
    firmware.stored_ssid = "Home"
    async with held(manager) as cli:
        reply = await cli.get("wifi.ssid")
    assert reply == CliReply(text="> Home", value="Home", sensitive=False)
    [payload] = firmware.raw_payloads
    tag = firmware.tags[0]
    assert payload == b"\x42" + f"{tag}|get wifi.ssid".encode()
    assert len(tag) == 2 and all(char in CLI_TAG_ALPHABET for char in tag)
    assert "|" not in CLI_TAG_ALPHABET and " " not in CLI_TAG_ALPHABET
    assert len(CLI_TAG_ALPHABET) == 93


async def test_reply_value_strips_exactly_one_prompt(radio):
    loop, firmware, manager = radio
    firmware.overrides["get a.b"] = ">   a b  "
    firmware.overrides["get empty"] = "> "
    firmware.overrides["get raw"] = "OK"
    async with held(manager) as cli:
        assert (await cli.get("a.b")).value == "  a b  "
        assert (await cli.get("empty")).value == ""
        assert (await cli.get("raw")).value == "OK"


async def test_unknown_and_rejected_replies(radio):
    loop, firmware, manager = radio
    firmware.wifi = False
    async with held(manager) as cli:
        with pytest.raises(CliUnknownCommandError) as unknown:
            await cli.get("wifi.status")
        with pytest.raises(CliRejectedError) as rejected:
            await cli.set("tz.offset", "20")
    assert unknown.value.code == "cli_unknown_command"
    assert rejected.value.reply_text == "Error, must be from -12 to +14"


async def test_rejected_sensitive_reply_text_is_not_kept(radio):
    loop, firmware, manager = radio
    firmware.overrides[f"set wifi.pwd {SECRET}"] = f"ERROR: bad {SECRET}"
    async with held(manager) as cli:
        with pytest.raises(CliRejectedError) as rejected:
            await cli.set("wifi.pwd", SECRET, secret=True)
    assert rejected.value.reply_text is None
    assert SECRET not in str(rejected.value) and SECRET not in repr(rejected.value)


async def test_error_frame_during_a_normal_call_is_ignored(radio):
    loop, firmware, manager = radio
    firmware.extra_frames.append(b"\x01\x01")  # ERROR(ERR_CODE_UNSUPPORTED_CMD)
    async with held(manager) as cli:
        reply = await cli.get("wifi.status")
    assert reply.value == "disconnected"


async def test_stale_reply_within_a_session_is_discarded(radio):
    loop, firmware, manager = radio
    firmware.stored_pwd = SECRET
    firmware.silent.add("get wifi.pwd")
    async with held(manager) as cli:
        with pytest.raises(CliTimeoutError):
            await cli.run("get wifi.pwd", timeout=SHORT_TIMEOUT)
        stale_tag = firmware.tags[-1]
        # The late password reply arrives right before the status reply.
        firmware.extra_frames.append(cli_reply(f"{stale_tag}|> {SECRET}"))
        reply = await cli.get("wifi.status")
    assert reply.value == "disconnected"
    assert stale_tag in manager.cli_link.quarantined


async def test_stale_reply_across_sessions_is_discarded(radio):
    loop, firmware, manager = radio
    firmware.silent.add("get wifi.pwd")
    async with held(manager) as cli:
        with pytest.raises(CliTimeoutError):
            await cli.run("get wifi.pwd", timeout=SHORT_TIMEOUT)
    stale_tag = firmware.tags[-1]
    firmware.silent.clear()
    firmware.overrides["get wifi.pwd"] = lambda _command: None
    async with held(manager) as cli:
        loop.feed(cli_reply(f"{stale_tag}|> {SECRET}"))
        with pytest.raises(CliTimeoutError):
            await cli.run("get wifi.pwd", timeout=SHORT_TIMEOUT)
        firmware.overrides.clear()
        firmware.extra_frames.append(cli_reply(f"{stale_tag}|> {SECRET}"))
        reply = await cli.get("wifi.status")
    assert reply.value == "disconnected"


async def test_tag_is_quarantined_after_cancellation(radio):
    loop, firmware, manager = radio
    firmware.silent.add("get wifi.status")
    async with held(manager) as cli:
        # Cancelled by a timeout scope in this task (asyncio.timeout, not wait_for).
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(SHORT_TIMEOUT):
                await cli.run("get wifi.status", timeout=5)
    assert firmware.tags[-1] in manager.cli_link.quarantined


async def test_tag_exhaustion_declares_the_link_unreliable(radio):
    loop, firmware, manager = radio
    link = manager.cli_link
    link.quarantined.update(f"q{index}" for index in range(CLI_MAX_QUARANTINED_TAGS + 1))
    async with held(manager) as cli:
        with pytest.raises(CliUnsupportedError) as error:
            await cli.get("wifi.status")
        assert error.value.code == "link_unreliable"
        with pytest.raises(CliUnsupportedError):
            await cli.get("wifi.status")
    assert firmware.commands == []
    assert link.capabilities is not None and link.capabilities.reason == "link_unreliable"


async def test_every_tag_quarantined_never_hangs(radio):
    loop, firmware, manager = radio
    link = manager.cli_link
    link.quarantined.update(a + b for a in CLI_TAG_ALPHABET for b in CLI_TAG_ALPHABET)
    assert len(link.quarantined) == len(CLI_TAG_ALPHABET) ** 2
    async with held(manager) as cli:
        with pytest.raises(CliUnsupportedError):
            await cli.get("wifi.status")
    assert firmware.commands == []


def test_allocator_with_a_full_quarantine_below_the_cap_raises(monkeypatch):
    # Even if the cap were lifted, a full scan never loops forever.
    monkeypatch.setattr(cli_module, "CLI_MAX_QUARANTINED_TAGS", 10**6)
    link = new_cli_link_state()
    link.quarantined.update(a + b for a in CLI_TAG_ALPHABET for b in CLI_TAG_ALPHABET)
    cli = CompanionCli(object(), manager=object())  # type: ignore[arg-type]
    with pytest.raises(CliUnsupportedError):
        cli._allocate_tag(link)


def test_allocator_skips_quarantined_tags():
    link = new_cli_link_state()
    link.next_tag = 0
    first = CLI_TAG_ALPHABET[0] + CLI_TAG_ALPHABET[0]
    second = CLI_TAG_ALPHABET[0] + CLI_TAG_ALPHABET[1]
    link.quarantined.add(first)
    cli = CompanionCli(object(), manager=object())  # type: ignore[arg-type]
    assert cli._allocate_tag(link) == second


def test_tag_start_is_random_per_connection(monkeypatch):
    values = iter([17, 4242])
    monkeypatch.setattr(cli_module, "_randbelow", lambda bound: next(values))
    assert new_cli_link_state().next_tag != new_cli_link_state().next_tag


def test_link_state_is_reset_with_the_connection():
    manager = RadioManager()
    manager.cli_link.quarantined.add("ab")
    manager.cli_link.probe_failures = 3
    manager._reset_connected_runtime_state()
    assert manager.cli_link.quarantined == set() and manager.cli_link.probe_failures == 0
    manager.cli_link.quarantined.add("cd")
    manager.reset_cli_link()
    assert manager.cli_link.quarantined == set()


async def test_setup_drops_the_probe_cache_but_keeps_the_quarantine(radio, fast_probe):
    loop, firmware, manager = radio
    firmware.silent.add("get wifi.status")
    async with held(manager) as cli:
        await cli.capabilities()
    link = manager.cli_link
    quarantined = set(link.quarantined)
    next_tag = link.next_tag
    assert quarantined and link.probe_failures == 1
    manager.reset_cli_probe()
    assert manager.cli_link is link
    assert link.capabilities is None and link.probe_failures == 0
    assert link.probe_retry_after == 0.0
    assert link.quarantined == quarantined and link.next_tag == next_tag


# --------------------------------------------------------------------------
# Lock ownership
# --------------------------------------------------------------------------


async def test_refused_without_marking_the_lock(radio):
    loop, firmware, manager = radio
    async with manager.radio_operation("test") as mc:
        cli = CompanionCli(mc, manager=manager)
        with pytest.raises(CliLockError):
            await cli.get("wifi.status")
    assert firmware.commands == []


async def test_refused_when_another_task_holds_the_lock(radio):
    loop, firmware, manager = radio
    holding = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with manager.radio_operation("other"):
            holding.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await holding.wait()
    try:
        assert manager._operation_lock is not None and manager._operation_lock.locked()
        cli = CompanionCli(loop.mc, manager=manager)
        with pytest.raises(CliLockError):
            await cli.get("wifi.status")
    finally:
        release.set()
        await task
    assert firmware.commands == []


async def test_refused_in_a_task_spawned_inside_a_session(radio):
    loop, firmware, manager = radio
    async with held(manager) as cli:
        child = asyncio.create_task(cli.get("wifi.status"))
        with pytest.raises(CliLockError):
            await child
    assert firmware.commands == []


async def test_refused_before_any_lock_was_created():
    manager = RadioManager()
    assert manager._operation_lock is None
    cli = CompanionCli(object(), manager=manager)  # type: ignore[arg-type]
    with mark_radio_lock_held(), pytest.raises(CliLockError):
        await cli.get("wifi.status")


async def test_wait_for_around_a_call(radio):
    """Documented: on 3.11 wait_for runs the call in a new task and is refused."""
    loop, firmware, manager = radio
    async with held(manager) as cli:
        assert (await cli.run("get wifi.status", timeout=1)).value == "disconnected"
        if sys.version_info < (3, 12):
            with pytest.raises(CliLockError):
                await asyncio.wait_for(cli.get("wifi.status"), 1)
        else:
            assert (await asyncio.wait_for(cli.get("wifi.status"), 1)).value == "disconnected"


async def test_session_refuses_before_setup_completes(radio, monkeypatch):
    loop, firmware, manager = radio
    manager._setup_complete = False
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    with pytest.raises(cli_module.CliError) as error:
        async with companion_cli_session("test"):
            pass
    assert error.value.code == "setup_incomplete"
    assert manager._operation_lock is None
    assert firmware.commands == [] and loop.transport.writes == []


async def test_session_runs_calls_under_the_radio_operation(radio, monkeypatch):
    loop, firmware, manager = radio
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    async with companion_cli_session("test") as cli:
        assert manager._operation_lock is not None and manager._operation_lock.locked()
        assert (await cli.get("wifi.status")).value == "disconnected"
    assert not manager._operation_lock.locked()


async def test_session_rechecks_setup_once_the_lock_is_held(radio, monkeypatch):
    """A reconnect while the session waits resets setup: refuse before touching
    auto-fetch, so nothing restarts it ahead of the new setup."""
    loop, firmware, manager = radio
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    calls: list[str] = []

    async def stop() -> None:
        calls.append("stop")

    async def start() -> None:
        calls.append("start")

    monkeypatch.setattr(loop.mc, "stop_auto_message_fetching", stop)
    monkeypatch.setattr(loop.mc, "start_auto_message_fetching", start)

    async def open_session() -> None:
        async with companion_cli_session("test"):
            pass

    async with manager.radio_operation("holder"):
        waiter = asyncio.create_task(open_session())
        await asyncio.sleep(0)  # passes the first check, waits for the lock
        manager._setup_complete = False
    with pytest.raises(cli_module.CliError) as error:
        await waiter
    assert error.value.code == "setup_incomplete"
    assert calls == []
    assert not manager._operation_lock.locked()

    manager._setup_complete = True
    await open_session()
    assert calls == ["stop", "start"]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["x", " get wifi.ssid", "get wifi.ssid\nreboot", "get\rx", "get\0x"]
    + sorted(CLI_FORBIDDEN_VERBS)
    + ["REBOOT now", "x" * 200],
)
async def test_invalid_commands_are_refused(radio, command):
    loop, firmware, manager = radio
    async with held(manager) as cli:
        with pytest.raises(CliCommandError) as error:
            await cli.run(command)
    assert isinstance(error.value, ValueError)
    assert command.strip() not in str(error.value) or len(command.strip()) < 2
    assert firmware.commands == []


async def test_names_with_spaces_are_refused(radio):
    loop, firmware, manager = radio
    async with held(manager) as cli:
        with pytest.raises(CliCommandError):
            await cli.get("wifi ssid")


# --------------------------------------------------------------------------
# Secrets never reach a log line or an exception
# --------------------------------------------------------------------------


def assert_secret_absent(text: str) -> None:
    """Case-insensitive: the helper lowercases verbs and names."""
    lowered = text.lower()
    assert SECRET.lower() not in lowered
    assert SECRET.encode().hex() not in lowered


def assert_error_carries_no_secret(exc: cli_module.CliError) -> None:
    assert_secret_absent(str(exc))
    assert_secret_absent(repr(exc))
    assert_secret_absent(exc.name or "")
    assert_secret_absent(exc.verb or "")


SECRET_COMMANDS = [
    f"set wifi.pwd {SECRET}",
    f"SET  WIFI.PWD {SECRET}",
    f"set foo.key {SECRET}",
    f"set my.secret {SECRET}",
    f"password {SECRET}",
    f"Password {SECRET} extra",
    f"set key={SECRET}",
    f"set wifi.pwd={SECRET}",
    f"pwd={SECRET}",
]


@pytest.mark.parametrize("command", SECRET_COMMANDS)
async def test_raw_secret_commands_never_log_their_value(radio, caplog, command):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="meshcore")
    loop, firmware, manager = radio
    async with held(manager) as cli:
        try:
            reply = await cli.run(command)
        except CliUnknownCommandError as exc:
            assert_error_carries_no_secret(exc)
        else:
            assert reply.sensitive
    await loop.settle()
    assert firmware.commands  # the frame went out
    assert_secret_absent(all_log_text(caplog))


@pytest.mark.parametrize("command", SECRET_COMMANDS)
async def test_raw_secret_commands_time_out_without_their_value(radio, caplog, command):
    caplog.set_level(logging.DEBUG)
    loop, firmware, manager = radio
    firmware.silent.add(command)
    async with held(manager) as cli:
        with pytest.raises(CliTimeoutError) as error:
            await cli.run(command, timeout=SHORT_TIMEOUT)
    assert_error_carries_no_secret(error.value)
    assert_secret_absent(all_log_text(caplog))


def test_one_word_secret_verb_never_shows_its_argument():
    classified = cli_module._classify(f"password {SECRET}")
    assert (classified.shown_verb, classified.shown_name) == ("password", "")
    assert cli_module._classify(f"set key={SECRET}").shown_name == "[redacted]"
    assert cli_module._classify("set foo.key x").shown_name == "foo.key"
    assert cli_module._classify("get wifi.pwd").shown_name == "wifi.pwd"
    assert cli_module._classify("set a=b c").shown_name == "a=b"  # not secret
    # A value glued to a name that looks like one is still caught when known.
    glued = cli_module._classify("set s3cr3tpw x", extra_secrets=("S3cr3tPw",))
    assert glued.secret and glued.shown_name == "[redacted]"


async def test_explicit_secret_on_an_unmarked_name_is_hidden(radio, caplog):
    caplog.set_level(logging.DEBUG)
    loop, firmware, manager = radio
    firmware.overrides[f"set foo {SECRET}"] = f"> foo is now {SECRET}"
    firmware.overrides["set foo bad"] = f"Error: bad value {SECRET}"
    async with held(manager) as cli:
        reply = await cli.set("foo", SECRET, secret=True)
        assert reply.sensitive and SECRET not in repr(reply)
        firmware.overrides[f"set foo {SECRET}"] = f"Error: bad value {SECRET}"
        with pytest.raises(CliRejectedError) as rejected:
            await cli.set("foo", SECRET, secret=True)
    assert rejected.value.reply_text is None
    assert rejected.value.name == "foo"
    assert_error_carries_no_secret(rejected.value)
    text = all_log_text(caplog)
    assert "CLI set foo -> [redacted]" in text and "CLI set foo -> rejected" in text
    assert_secret_absent(text)


async def test_run_with_explicit_secrets_hides_command_and_reply(radio, caplog):
    caplog.set_level(logging.DEBUG)
    loop, firmware, manager = radio
    firmware.overrides[f"ver {SECRET}"] = f"> fine {SECRET}"
    async with held(manager) as cli:
        reply = await cli.run(f"ver {SECRET}", secrets=(SECRET,))
    assert reply.sensitive and reply.value == f"fine {SECRET}"
    text = all_log_text(caplog)
    assert "CLI ver [redacted] -> [redacted]" in text
    assert_secret_absent(text)


async def test_get_wifi_pwd_is_always_a_sensitive_read(radio, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="meshcore")
    loop, firmware, manager = radio
    firmware.stored_pwd = SECRET
    async with held(manager) as cli:
        reply = await cli.run("get wifi.pwd")
    await loop.settle()
    assert reply.sensitive and reply.value == SECRET
    assert SECRET not in repr(reply) and SECRET not in str(reply)
    assert "CLI get wifi.pwd -> [redacted]" in all_log_text(caplog)
    assert SECRET not in all_log_text(caplog)


async def test_set_logs_only_the_name(radio, caplog):
    caplog.set_level(logging.DEBUG, logger="app.services.companion_cli")
    loop, firmware, manager = radio
    async with held(manager) as cli:
        await cli.set("wifi.ssid", "MyNet")
    text = all_log_text(caplog)
    assert "CLI set wifi.ssid -> ok" in text
    assert "MyNet" not in text


async def test_get_of_a_plain_value_is_logged(radio, caplog):
    caplog.set_level(logging.DEBUG, logger="app.services.companion_cli")
    loop, firmware, manager = radio
    async with held(manager) as cli:
        await cli.get("wifi.status")
    assert "CLI get wifi.status -> > disconnected" in all_log_text(caplog)


async def test_timeout_error_carries_no_command_text(radio):
    loop, firmware, manager = radio
    firmware.silent.add(f"set wifi.pwd {SECRET}")
    async with held(manager) as cli:
        with pytest.raises(CliTimeoutError) as error:
            await cli.run(f"set wifi.pwd {SECRET}", timeout=SHORT_TIMEOUT)
    assert SECRET not in str(error.value)
    assert error.value.verb == "set" and error.value.name == "wifi.pwd"


# --------------------------------------------------------------------------
# Support detection
# --------------------------------------------------------------------------


@pytest.fixture
def fast_probe(monkeypatch):
    monkeypatch.setattr(cli_module, "CLI_REPLY_TIMEOUT_SECONDS", SHORT_TIMEOUT)
    monkeypatch.setattr(cli_module, "CLI_PROBE_ERROR_GRACE_SECONDS", 0.05)


@pytest.mark.parametrize(
    ("version", "model", "reason"),
    [
        (None, "Heltec V3", "firmware_unknown"),
        (13, "Heltec V3", "firmware_too_old"),
        (14, "MeshloomProxy/0123456789ab", "proxy_link"),
        (10, "MeshloomProxy/0123456789ab", "proxy_link"),
    ],
)
async def test_gate_sends_nothing_and_is_not_cached(radio, version, model, reason):
    loop, firmware, manager = radio
    manager.firmware_ver_code = version
    manager.device_model = model
    async with held(manager) as cli:
        caps = await cli.capabilities()
        assert (caps.supported, caps.reason, caps.wifi) == (False, reason, False)
        assert firmware.commands == [] and manager.cli_link.capabilities is None
        # Once the device query fills the version, the next call probes.
        manager.firmware_ver_code = 14
        manager.device_model = "Heltec V3"
        caps = await cli.capabilities()
    assert caps.supported and caps.reason == "ok"
    assert firmware.commands == ["get wifi.status"]


@pytest.mark.parametrize(
    ("version", "model", "reason"),
    [
        (None, "Heltec V3", "firmware_unknown"),
        (13, "Heltec V3", "firmware_too_old"),
        (14, "MeshloomProxy/0123456789ab", "proxy_link"),
    ],
)
async def test_run_applies_the_gate(radio, version, model, reason):
    loop, firmware, manager = radio
    manager.firmware_ver_code = version
    manager.device_model = model
    async with held(manager) as cli:
        with pytest.raises(CliUnsupportedError) as error:
            await cli.get("wifi.status")
    assert error.value.code == reason
    assert firmware.commands == [] and loop.transport.writes == []
    assert manager.cli_link.quarantined == set()


@pytest.mark.parametrize("reason", ["untagged", "unsupported_cmd", "link_unreliable"])
async def test_run_refuses_after_a_sticky_probe_result(radio, reason):
    loop, firmware, manager = radio
    manager.cli_link.capabilities = cli_module.CliCapabilities(False, reason, False, 14)  # type: ignore[arg-type]
    async with held(manager) as cli:
        with pytest.raises(CliUnsupportedError) as error:
            await cli.get("wifi.status")
    assert error.value.code == reason
    assert firmware.commands == [] and loop.transport.writes == []


async def test_run_still_works_after_a_failed_probe(radio):
    loop, firmware, manager = radio
    manager.cli_link.capabilities = cli_module.CliCapabilities(False, "probe_failed", False, 14)
    async with held(manager) as cli:
        assert (await cli.get("wifi.status")).value == "disconnected"


async def test_gate_needs_no_lock(radio):
    loop, firmware, manager = radio
    manager.firmware_ver_code = 12
    cli = CompanionCli(loop.mc, manager=manager)
    assert (await cli.capabilities()).reason == "firmware_too_old"


async def test_probe_detects_cli_with_wifi_and_caches_it(radio, caplog):
    caplog.set_level(logging.INFO, logger="app.services.companion_cli")
    loop, firmware, manager = radio
    firmware.connected = True
    async with held(manager) as cli:
        caps = await cli.capabilities()
        again = await cli.capabilities()
    assert (caps.supported, caps.reason, caps.wifi, caps.firmware_ver_code) == (
        True,
        "ok",
        True,
        14,
    )
    assert again is caps
    assert firmware.commands == ["get wifi.status"]
    assert "Companion CLI probe: supported=True reason=ok wifi=True" in all_log_text(caplog)


async def test_probe_cli_without_wifi(radio):
    loop, firmware, manager = radio
    firmware.wifi = False
    async with held(manager) as cli:
        caps = await cli.capabilities()
    assert (caps.supported, caps.wifi) == (True, False)


async def test_refresh_reprobes_a_supported_link(radio):
    loop, firmware, manager = radio
    async with held(manager) as cli:
        await cli.capabilities()
        await cli.capabilities(refresh=True)
    assert firmware.commands == ["get wifi.status", "get wifi.status"]


async def test_untagged_reply_fails_closed_and_sticks(radio):
    loop, firmware, manager = radio
    firmware.echo_tags = False
    async with held(manager) as cli:
        caps = await cli.capabilities()
        await cli.capabilities(refresh=True)
    assert (caps.supported, caps.reason) == (False, "untagged")
    assert len(firmware.commands) == 1


async def test_error_unsupported_then_no_reply_is_unsupported_cmd(radio, fast_probe):
    loop, firmware, manager = radio
    firmware.overrides["get wifi.status"] = lambda _command: None
    firmware.extra_frames.append(b"\x01\x01")
    async with held(manager) as cli:
        caps = await cli.capabilities()
        assert (caps.supported, caps.reason) == (False, "unsupported_cmd")
        again = await cli.capabilities(refresh=True)
    assert again.reason == "unsupported_cmd"
    assert len(firmware.commands) == 1


async def test_error_unsupported_followed_by_our_reply_is_supported(radio, fast_probe):
    loop, firmware, manager = radio
    # A late ERROR(1) to an earlier command arrives first, then our tagged reply.
    firmware.extra_frames.append(b"\x01\x01")
    async with held(manager) as cli:
        caps = await cli.capabilities()
    assert (caps.supported, caps.reason, caps.wifi) == (True, "ok", True)


async def test_other_error_codes_are_ignored_during_the_probe(radio, fast_probe):
    loop, firmware, manager = radio
    firmware.extra_frames.append(b"\x01\x02")
    async with held(manager) as cli:
        caps = await cli.capabilities()
    assert caps.supported


async def test_late_reply_of_attempt_one_is_stale_during_attempt_two(radio, fast_probe):
    loop, firmware, manager = radio
    answers = iter([None, "> disconnected"])
    firmware.overrides["get wifi.status"] = lambda _command: next(answers)

    async with held(manager) as cli:
        original_call = firmware.__call__

        def responder(payload):
            frames = original_call(payload)
            if len(firmware.tags) == 2:
                # Attempt 1's reply, tagged with attempt 1's tag, lands first.
                frames.insert(0, cli_reply(f"{firmware.tags[0]}|> disconnected"))
            return frames

        loop.set_responder(responder)
        caps = await cli.capabilities()
    assert (caps.supported, caps.reason) == (True, "ok")
    assert len(firmware.commands) == 2
    assert firmware.tags[0] in manager.cli_link.quarantined


async def test_probe_timeouts_back_off_and_cap(radio, fast_probe, monkeypatch):
    loop, firmware, manager = radio
    firmware.silent.add("get wifi.status")
    link = manager.cli_link

    async def probe(cli, **kwargs):
        # Unanswered sends trip meshcore's TCP disconnect heuristic: forget them.
        loop.balance()
        return await cli.capabilities(**kwargs)

    async with held(manager) as cli:
        caps = await probe(cli)
        assert (caps.supported, caps.reason) == (False, "probe_failed")
        assert len(firmware.commands) == 2  # retried once with a new tag
        assert len(set(firmware.tags)) == 2
        # Within the backoff: nothing is sent.
        assert (await probe(cli)).reason == "probe_failed"
        assert len(firmware.commands) == 2
        # After the backoff: a new probe.
        link.probe_retry_after = 0.0
        await probe(cli)
        assert len(firmware.commands) == 4
        # refresh skips the backoff, not the cap.
        for _ in range(3):
            await probe(cli, refresh=True)
        assert link.probe_failures == 5
        assert len(firmware.commands) == 10
        await probe(cli, refresh=True)
        link.probe_retry_after = 0.0
        await probe(cli)
    assert len(firmware.commands) == 10


async def test_probe_counters_reset_with_the_connection(radio, fast_probe):
    loop, firmware, manager = radio
    firmware.silent.add("get wifi.status")
    async with held(manager) as cli:
        await cli.capabilities()
    assert manager.cli_link.probe_failures == 1
    manager.reset_cli_link()
    assert manager.cli_link.capabilities is None and manager.cli_link.probe_failures == 0
    manager.cli_link.probe_failures = 2
    manager._reset_connected_runtime_state()
    assert manager.cli_link.probe_failures == 0


async def test_probe_is_refused_outside_a_session(radio):
    loop, firmware, manager = radio
    cli = CompanionCli(loop.mc, manager=manager)
    with pytest.raises(CliLockError):
        await cli.capabilities()
    assert firmware.commands == []


async def test_debug_snapshot_reports_the_probe(radio, monkeypatch):
    from app.routers.debug import _build_companion_cli

    loop, firmware, manager = radio
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    assert _build_companion_cli() is None
    firmware.echo_tags = False
    async with held(manager) as cli:
        await cli.capabilities()
    block = _build_companion_cli()
    assert block is not None
    assert (block.supported, block.reason, block.wifi) == (False, "untagged", False)


@pytest.mark.parametrize(
    ("version", "model", "reason"),
    [
        (None, "Heltec V3", "firmware_unknown"),
        (13, "Heltec V3", "firmware_too_old"),
        (10, "MeshloomProxy/0123456789ab", "proxy_link"),
    ],
)
async def test_debug_snapshot_reports_the_gate_without_probing(
    radio, monkeypatch, version, model, reason
):
    from app.routers.debug import _build_companion_cli

    loop, firmware, manager = radio
    monkeypatch.setattr(radio_module, "radio_manager", manager)
    manager.firmware_ver_code = version
    manager.device_model = model
    block = _build_companion_cli()
    assert block is not None
    assert (block.supported, block.reason, block.wifi) == (False, reason, False)
    assert firmware.commands == [] and manager.cli_link.capabilities is None


# --------------------------------------------------------------------------
# Typed helpers
# --------------------------------------------------------------------------


async def test_wifi_reads(radio):
    from app.services.companion_cli import (
        WifiLinkStatus,
        wifi_credentials_stored,
        wifi_enabled,
        wifi_link_status,
        wifi_ssid,
        wifi_stored_password,
    )

    loop, firmware, manager = radio
    async with held(manager) as cli:
        assert await wifi_ssid(cli) == "FlasherNet"  # compile-time fallback
        assert await wifi_credentials_stored(cli) is False
        assert await wifi_link_status(cli) == WifiLinkStatus(connected=False, ip=None)
        assert await wifi_enabled(cli) is True
        firmware.fallback_ssid = None
        assert await wifi_ssid(cli) is None
        firmware.stored_ssid, firmware.stored_pwd = "Home", "  p w  "
        firmware.connected, firmware.ip = True, "192.168.1.42"
        assert await wifi_ssid(cli) == "Home"
        assert (await wifi_stored_password(cli)).get_secret_value() == "  p w  "
        assert await wifi_credentials_stored(cli) is True
        assert await wifi_link_status(cli) == WifiLinkStatus(connected=True, ip="192.168.1.42")
        firmware.ip = "garbage"
        assert (await wifi_link_status(cli)).ip is None


async def test_set_wifi_enabled(radio):
    from app.services.companion_cli import set_wifi_enabled, wifi_enabled

    loop, firmware, manager = radio
    async with held(manager) as cli:
        await set_wifi_enabled(cli, False)
        assert firmware.enabled is False
        assert await wifi_enabled(cli) is False
        await set_wifi_enabled(cli, True)
    assert firmware.commands[0] == "set wifi.enabled 0"


async def test_wifi_reads_without_wifi_raise_unknown_command(radio):
    from app.services.companion_cli import wifi_ssid

    loop, firmware, manager = radio
    firmware.wifi = False
    async with held(manager) as cli:
        with pytest.raises(CliUnknownCommandError):
            await wifi_ssid(cli)


async def test_store_credentials_ssid_then_password_and_verifies(radio, caplog):
    from pydantic import SecretStr

    from app.services.companion_cli import WifiStoreOutcome, store_wifi_credentials

    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="meshcore")
    loop, firmware, manager = radio
    async with held(manager) as cli:
        outcome = await store_wifi_credentials(cli, "FlasherNet", SecretStr(SECRET))
    await loop.settle()
    assert outcome == WifiStoreOutcome(
        previous_ssid="FlasherNet",
        previously_stored=False,
        ssid_changed=False,
        password_changed=True,
    )
    assert (firmware.stored_ssid, firmware.stored_pwd) == ("FlasherNet", SECRET)
    sets = [command for command in firmware.commands if command.startswith("set ")]
    assert sets == ["set wifi.ssid FlasherNet", f"set wifi.pwd {SECRET}"]
    # The password is read once before and once after, no more.
    assert firmware.commands.count("get wifi.pwd") == 2
    assert SECRET not in all_log_text(caplog)
    assert SECRET not in repr(outcome)


async def test_store_open_network_sends_an_empty_password(radio):
    from pydantic import SecretStr

    from app.services.companion_cli import store_wifi_credentials

    loop, firmware, manager = radio
    async with held(manager) as cli:
        outcome = await store_wifi_credentials(cli, "Cafe Wifi ", SecretStr(""))
    assert firmware.raw_payloads[-3].endswith(b"|set wifi.pwd ")
    assert (firmware.stored_ssid, firmware.stored_pwd) == ("Cafe Wifi ", "")
    assert outcome.password_changed is False and outcome.ssid_changed is True


async def test_store_mismatch_rolls_back_to_clear(radio):
    from pydantic import SecretStr

    from app.services.companion_cli import CliError, store_wifi_credentials

    loop, firmware, manager = radio
    reads = {"count": 0}

    def pwd_reader(command):
        reads["count"] += 1
        return "> " if reads["count"] == 1 else "> something-else"

    firmware.overrides["get wifi.pwd"] = pwd_reader
    async with held(manager) as cli:
        with pytest.raises(CliError) as error:
            await store_wifi_credentials(cli, "Home", SecretStr(SECRET))
    assert error.value.code == "wifi_store_unverified"
    assert firmware.commands[-1] == "set wifi.clear"
    assert (firmware.stored_ssid, firmware.stored_pwd) == ("", "")


async def test_store_mismatch_restores_previous_credentials(radio):
    from pydantic import SecretStr

    from app.services.companion_cli import CliError, store_wifi_credentials

    loop, firmware, manager = radio
    firmware.stored_ssid, firmware.stored_pwd = "Old", "old-password"
    firmware.overrides["get wifi.ssid"] = lambda _c: (
        "> Old" if firmware.commands.count("get wifi.ssid") == 1 else "> Wrong"
    )
    async with held(manager) as cli:
        with pytest.raises(CliError) as error:
            await store_wifi_credentials(cli, "New", SecretStr(SECRET))
    assert error.value.code == "wifi_store_unverified"
    assert (firmware.stored_ssid, firmware.stored_pwd) == ("Old", "old-password")


async def test_store_password_timeout_is_retried_once_then_rolled_back(radio, fast_probe):
    from pydantic import SecretStr

    from app.services.companion_cli import CliError, store_wifi_credentials

    loop, firmware, manager = radio
    firmware.silent.add(f"set wifi.pwd {SECRET}")
    async with held(manager) as cli:
        with pytest.raises(CliError) as error:
            await store_wifi_credentials(cli, "Home", SecretStr(SECRET))
    assert error.value.code == "wifi_store_unverified"
    assert firmware.commands.count(f"set wifi.pwd {SECRET}") == 2
    assert firmware.commands[-1] == "set wifi.clear"
    assert (firmware.stored_ssid, firmware.stored_pwd) == ("", "")


async def test_store_link_lost_after_ssid_is_partial(radio, fast_probe):
    from pydantic import SecretStr

    from app.services.companion_cli import CliError, store_wifi_credentials

    loop, firmware, manager = radio

    def store_then_drop_the_link(command):
        reply = firmware.handle(command)
        firmware.silent.update({f"set wifi.pwd {SECRET}", "set wifi.clear"})
        return reply

    firmware.overrides["set wifi.ssid Home"] = store_then_drop_the_link
    async with held(manager) as cli:
        with pytest.raises(CliError) as error:
            await store_wifi_credentials(cli, "Home", SecretStr(SECRET))
    assert error.value.code == "wifi_store_partial"
    assert firmware.stored_ssid == "Home" and firmware.stored_pwd == ""


@pytest.mark.parametrize(
    ("ssid", "password", "code"),
    [
        ("x" * 33, "password", "wifi_ssid_length"),
        ("é" * 17, "password", "wifi_ssid_length"),
        ("", "password", "wifi_ssid_length"),
        ("(not set)", "password", "wifi_ssid_reserved"),
        ("Net\x07", "password", "wifi_ssid_control_character"),
        ("Net", "p" * 64, "wifi_password_length"),
        ("Net", "pass\nword", "wifi_password_control_character"),
    ],
)
async def test_store_validation(radio, ssid, password, code):
    from pydantic import SecretStr

    from app.services.companion_cli import store_wifi_credentials

    loop, firmware, manager = radio
    async with held(manager) as cli:
        with pytest.raises(CliCommandError) as error:
            await store_wifi_credentials(cli, ssid, SecretStr(password))
    assert error.value.code == code
    assert password not in str(error.value)
    assert firmware.commands == []


def test_validators_accept_short_and_untrimmed_values():
    from app.services.companion_cli import validate_wifi_password, validate_wifi_ssid

    assert validate_wifi_password("abcde") == "abcde"  # WEP-40
    assert validate_wifi_password("abc", typed=False) == "abc"
    assert validate_wifi_password("") == ""
    assert validate_wifi_password(" spaced ") == " spaced "
    assert validate_wifi_ssid(" My Net ") == " My Net "
    assert validate_wifi_ssid("x" * 32) == "x" * 32


async def test_read_cli_node_prefs_skips_unknown(radio):
    from app.services.companion_cli import read_cli_node_prefs

    loop, firmware, manager = radio
    async with held(manager) as cli:
        prefs = await read_cli_node_prefs(cli)
    assert prefs == {"tz.offset": "0", "cad": "off", "int.thresh": "14", "txdelay": "0.5"}


async def test_write_cli_node_pref(radio):
    from app.services.companion_cli import write_cli_node_pref

    loop, firmware, manager = radio
    async with held(manager) as cli:
        await write_cli_node_pref(cli, "tz.offset", "2")
        with pytest.raises(CliRejectedError):
            await write_cli_node_pref(cli, "tz.offset", "20")
        with pytest.raises(CliCommandError):
            await write_cli_node_pref(cli, "wifi.pwd", "x")
        with pytest.raises(CliCommandError):
            await write_cli_node_pref(cli, "cad", "on off")
    assert firmware.prefs["tz.offset"] == "2"


def test_remote_cli_flag_constant():
    from app.services.companion_cli import CONTACT_FLAG_REMOTE_CLI

    assert CONTACT_FLAG_REMOTE_CLI == 0x10


@pytest.mark.parametrize(
    ("command", "tail"),
    [
        ("set wifi.pwd ", ""),
        ("set wifi.pwd  x y ", " x y "),
        ("set  wifi.pwd abc", "abc"),
        ("set\twifi.pwd\nabc", "abc"),
        ("get wifi.ssid", None),
        ("reboot", None),
        ("", None),
    ],
)
def test_after_two_tokens_matches_the_former_regex(command, tail):
    from app.services.companion_cli import _after_two_tokens

    assert _after_two_tokens(command) == tail


def test_after_two_tokens_is_linear_on_long_input():
    from app.services.companion_cli import _after_two_tokens

    # CodeQL py/polynomial-redos: the former regex backtracked on this shape.
    assert _after_two_tokens("a " + "!" * 200_000) is None
