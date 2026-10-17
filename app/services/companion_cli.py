"""The MeshCore companion CLI (firmware v1.18, companion protocol 14+).

``CMD_RUN_CLI_COMMAND`` (66) runs a text command on the radio and the firmware
answers one ``RESP_CODE_CLI_REPLY`` (29) frame. Replies carry no request id, so
this module adds the guard the protocol lacks:

- **Serialized.** Every call runs inside ``companion_cli_session()`` (the radio
  lock, polling paused, auto-fetch suspended) and checks that the calling task
  holds the lock itself; a task spawned inside a session inherits the context
  variable but is refused.
- **Tagged.** The firmware echoes a 3-byte ``"XY|"`` prefix on the reply when
  the command starts with one (``handleCommand()``, ``MyMesh.cpp``). Each call
  takes the next tag of the connection's ``CliLinkState`` and accepts only a
  reply with that tag; a reply with another tag is stale and dropped. A call
  that ends without its reply (timeout, cancellation, error) quarantines its
  tag for the rest of the connection, so a late ``get wifi.pwd`` answer can
  never be taken as the answer to a later command.
- **Secret-aware.** The helper classifies commands itself (fail closed): a
  ``set`` of a name containing ``pwd``/``pass``/``key``/``secret``/``pin`` is
  secret (its value is registered with ``secret_scope()``), a ``get`` of one
  is a sensitive read. No ``set`` argument and no sensitive reply is ever
  logged, and no exception carries command text.

Never call ``mc.commands.run_cli_command`` directly, and never wrap a CLI call
in ``asyncio.wait_for``/``shield``/``gather``: on Python 3.11 they run it in a
new task, which fails the owner-task check. Use ``run(timeout=...)`` or
``async with asyncio.timeout(...)``.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import secrets as _secrets
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from meshcore import EventType

from app.log_redaction import REDACTED, secret_scope

if TYPE_CHECKING:
    from meshcore import MeshCore

logger = logging.getLogger(__name__)

CLI_COMMAND_CODE = 66
CLI_MIN_FIRMWARE_VER_CODE = 14
CLI_REPLY_TIMEOUT_SECONDS = 5.0
CLI_MAX_FRAME_BYTES = 160  # the firmware frame buffer is 172; leave a margin
# Commands that never reply or reboot: reboots go through mc.commands.reboot()
# plus RadioManager.note_expected_reboot().
CLI_FORBIDDEN_VERBS = frozenset({"reboot", "poweroff", "shutdown", "erase", "start"})
CLI_TAG_ALPHABET = "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) != "|")
CLI_MAX_QUARANTINED_TAGS = 64  # beyond this the link is declared unreliable
CLI_PROBE_BACKOFF_SECONDS = 30.0
CLI_MAX_PROBES_PER_CONNECTION = 5
CLI_PROBE_ERROR_GRACE_SECONDS = 1.0
TAGGED_REPLY = re.compile(r"^[\x21-\x7e]{2}\|")

# (verb, name) pairs that are always secret / sensitive, on top of the name
# markers below. The single place to extend.
SENSITIVE_COMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("set", "wifi.pwd"),
        ("get", "wifi.pwd"),
    }
)
_SECRET_NAME_MARKERS = ("pwd", "pass", "key", "secret", "pin")
# The firmware copies everything after this exact prefix verbatim.
_SET_WIFI_PWD_PREFIX = "set wifi.pwd "

CapabilityReason = Literal[
    "ok",
    "firmware_too_old",
    "firmware_unknown",
    "proxy_link",
    "untagged",
    "unsupported_cmd",
    "probe_failed",
    "link_unreliable",
]


class CliError(RuntimeError):
    """A companion CLI failure. ``code`` is stable and never contains a secret."""

    def __init__(self, code: str, *, verb: str | None = None, name: str | None = None) -> None:
        self.code = code
        self.verb = verb
        self.name = name
        where = " ".join(part for part in (verb, name) if part)
        super().__init__(f"{code} ({where})" if where else code)


class CliUnsupportedError(CliError):
    """The connected radio has no usable companion CLI (``code`` is the reason)."""


class CliUnknownCommandError(CliError):
    """The firmware answered "Unknown command" (for example no Wi-Fi interface)."""


class CliTimeoutError(CliError):
    """No tagged reply before the deadline."""


class CliRejectedError(CliError):
    """The reply starts with "Error"/"ERROR". ``reply_text`` is None for sensitive commands."""

    def __init__(
        self,
        code: str,
        *,
        verb: str | None = None,
        name: str | None = None,
        reply_text: str | None = None,
    ) -> None:
        super().__init__(code, verb=verb, name=name)
        self.reply_text = reply_text


class CliLockError(RuntimeError):
    """A CLI call outside a radio operation held by the calling task (programming error)."""


class CliCommandError(ValueError):
    """A command that fails validation. ``code`` only, never the command text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class CliReply:
    text: str  # reply after the "XY|" tag, untouched
    value: str  # text minus exactly one leading "> " (never stripped)
    sensitive: bool

    def __repr__(self) -> str:
        if self.sensitive:
            return "CliReply(<redacted>)"
        return f"CliReply(text={self.text!r})"

    __str__ = __repr__


@dataclass
class CliCapabilities:
    supported: bool
    reason: CapabilityReason
    wifi: bool
    firmware_ver_code: int | None


@dataclass
class CliLinkState:
    """Per-connection CLI state, owned by ``RadioManager.cli_link``."""

    next_tag: int
    quarantined: set[str] = field(default_factory=set)
    capabilities: CliCapabilities | None = None  # probe results only
    probe_failures: int = 0
    probe_retry_after: float = 0.0


_randbelow = _secrets.randbelow


def new_cli_link_state() -> CliLinkState:
    """A fresh link state with a random tag start (bytes buffered across a
    reconnect then rarely collide with the new connection's first tags)."""
    return CliLinkState(next_tag=_randbelow(len(CLI_TAG_ALPHABET) ** 2))


RADIO_LOCK_HELD: contextvars.ContextVar[asyncio.Task[Any] | None] = contextvars.ContextVar(
    "RADIO_LOCK_HELD", default=None
)


@contextmanager
def mark_radio_lock_held() -> Iterator[None]:
    """Record that the current task holds the radio lock for the block.

    Used by ``companion_cli_session`` and by code that already runs inside
    ``radio_operation()`` or setup and builds ``CompanionCli`` directly.
    """
    token = RADIO_LOCK_HELD.set(asyncio.current_task())
    try:
        yield
    finally:
        RADIO_LOCK_HELD.reset(token)


@dataclass(frozen=True)
class _Classified:
    verb: str
    name: str
    secret: bool
    sensitive: bool
    secret_values: tuple[str, ...]


def _classify(command: str) -> _Classified:
    tokens = command.split()
    verb = tokens[0].lower()
    name = tokens[1].lower() if len(tokens) > 1 else ""
    marked = any(marker in token for token in (verb, name) for marker in _SECRET_NAME_MARKERS)
    secret = marked or (verb, name) in SENSITIVE_COMMANDS
    values: list[str] = []
    if secret:
        if command.startswith(_SET_WIFI_PWD_PREFIX):
            values.append(command[len(_SET_WIFI_PWD_PREFIX) :])
        # Whatever follows the first two tokens, raw and stripped: covers case
        # and spacing variants the firmware would not even recognise.
        match = re.match(r"\S+\s+\S+\s(.*)\Z", command, re.DOTALL)
        if match:
            values.extend((match.group(1), match.group(1).strip()))
        if any(marker in verb for marker in _SECRET_NAME_MARKERS):
            # A one-word secret verb: everything after it.
            rest = command.split(None, 1)
            if len(rest) == 2:
                values.append(rest[1])
    return _Classified(
        verb=verb,
        name=name,
        secret=secret,
        sensitive=secret,
        secret_values=tuple(value for value in values if value),
    )


def _validate_command(command: str) -> bytes:
    if not isinstance(command, str) or len(command) < 2:
        raise CliCommandError("cli_command_too_short")
    if command[0].isspace():
        raise CliCommandError("cli_command_leading_space")
    if any(char in command for char in "\r\n\0"):
        raise CliCommandError("cli_command_bad_character")
    if command.split()[0].lower() in CLI_FORBIDDEN_VERBS:
        raise CliCommandError("cli_command_forbidden")
    # "XY|" tag plus the command code.
    encoded = command.encode("utf-8")
    if 1 + 3 + len(encoded) > CLI_MAX_FRAME_BYTES:
        raise CliCommandError("cli_command_too_long")
    return encoded


def _value_of(text: str) -> str:
    return text[2:] if text.startswith("> ") else text


_ExchangeOutcome = Literal["reply", "untagged", "unsupported_cmd", "timeout"]


class CompanionCli:
    """CLI calls for one radio operation. Every instance of a connection shares
    the manager's single ``CliLinkState`` (tags, quarantine, capabilities)."""

    def __init__(self, mc: MeshCore, *, manager: Any) -> None:
        self._mc = mc
        self._manager = manager

    @property
    def link(self) -> CliLinkState:
        return self._manager.cli_link

    # -- lock -------------------------------------------------------------

    def _check_lock(self) -> None:
        owner = RADIO_LOCK_HELD.get()
        lock = getattr(self._manager, "_operation_lock", None)
        current = asyncio.current_task()
        if owner is None or owner is not current or lock is None or not lock.locked():
            raise CliLockError(
                "Companion CLI calls must run inside companion_cli_session() "
                "in the task that holds the radio lock"
            )

    # -- tags -------------------------------------------------------------

    def _mark_unreliable(self, link: CliLinkState) -> CliUnsupportedError:
        link.capabilities = CliCapabilities(
            supported=False,
            reason="link_unreliable",
            wifi=False,
            firmware_ver_code=getattr(self._manager, "firmware_ver_code", None),
        )
        logger.warning(
            "Companion CLI: %d replies never arrived on this connection; "
            "CLI disabled until reconnect",
            len(link.quarantined),
        )
        return CliUnsupportedError("link_unreliable")

    def _allocate_tag(self, link: CliLinkState) -> str:
        if len(link.quarantined) > CLI_MAX_QUARANTINED_TAGS:
            raise self._mark_unreliable(link)
        size = len(CLI_TAG_ALPHABET)
        total = size * size
        # Pigeonhole: with fewer quarantined tags than slots, one of the next
        # len(quarantined) + 1 tags is free. Bounded, no await, never a loop
        # holding the radio lock.
        for _ in range(min(total, len(link.quarantined) + 1)):
            index = link.next_tag % total
            link.next_tag = (index + 1) % total
            tag = CLI_TAG_ALPHABET[index // size] + CLI_TAG_ALPHABET[index % size]
            if tag not in link.quarantined:
                return tag
        raise self._mark_unreliable(link)

    # -- exchange ---------------------------------------------------------

    async def _exchange(
        self,
        link: CliLinkState,
        tag: str,
        encoded_command: bytes,
        *,
        timeout: float,
        probe: bool,
    ) -> tuple[_ExchangeOutcome, str | None]:
        """Send one tagged command and wait for its reply.

        Returns ("reply", text after the tag), or, during a probe only,
        ("untagged", None) / ("unsupported_cmd", None); ("timeout", None) when
        the deadline passes. Any exit without our reply quarantines the tag.
        """
        loop = asyncio.get_running_loop()
        frame = bytes([CLI_COMMAND_CODE]) + tag.encode("ascii") + b"|" + encoded_command
        prefix = tag + "|"
        reply: asyncio.Future[tuple[_ExchangeOutcome, str | None]] = loop.create_future()
        refused: asyncio.Future[float] = loop.create_future()

        def on_reply(event: Any) -> None:
            # Synchronous and total: never raises, never logs reply text.
            try:
                payload = event.payload
                text = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(text, str) or reply.done():
                    return
                if TAGGED_REPLY.match(text):
                    if text.startswith(prefix):
                        reply.set_result(("reply", text[len(prefix) :]))
                    else:
                        logger.debug("Discarded a stale CLI reply")
                elif probe:
                    reply.set_result(("untagged", None))
                else:
                    logger.debug("Discarded an untagged CLI reply")
            except Exception:
                pass

        def on_error(event: Any) -> None:
            try:
                payload = event.payload
                code = payload.get("error_code") if isinstance(payload, dict) else None
                if code == 1 and not refused.done():
                    refused.set_result(loop.time())
            except Exception:
                pass

        subscriptions = [self._mc.subscribe(EventType.CLI_REPLY, on_reply)]
        if probe:
            # ERR_CODE_UNSUPPORTED_CMD may be our answer (no CLI), or a late
            # answer to an earlier command: only conclusive after a grace.
            subscriptions.append(self._mc.subscribe(EventType.ERROR, on_error))
        matched = False
        try:
            await self._mc.commands.send(frame)
            deadline = loop.time() + timeout
            while True:
                remaining = deadline - loop.time()
                if refused.done():
                    remaining = min(
                        remaining, refused.result() + CLI_PROBE_ERROR_GRACE_SECONDS - loop.time()
                    )
                if reply.done():
                    break
                if remaining <= 0:
                    if refused.done() and probe:
                        return ("unsupported_cmd", None)
                    return ("timeout", None)
                waiters: set[asyncio.Future[Any]] = {reply}
                if probe and not refused.done():
                    waiters.add(refused)
                await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            outcome = reply.result()
            matched = outcome[0] == "reply"
            return outcome
        finally:
            for subscription in subscriptions:
                try:
                    subscription.unsubscribe()
                except Exception:
                    pass
            if not matched:
                link.quarantined.add(tag)

    # -- support detection ------------------------------------------------

    async def capabilities(self, *, refresh: bool = False) -> CliCapabilities:
        """Whether the connected radio has a usable companion CLI, and Wi-Fi.

        The gate reasons (``proxy_link``, ``firmware_unknown``,
        ``firmware_too_old``) are computed on every call and send nothing. Only
        probe results are cached in the link state, until reconnect: ``untagged``,
        ``unsupported_cmd`` and ``link_unreliable`` stick; ``probe_failed`` is
        retried after ``CLI_PROBE_BACKOFF_SECONDS`` (``refresh`` skips the backoff)
        and sticks after ``CLI_MAX_PROBES_PER_CONNECTION`` failed probes.
        """
        from app.radio_proxy.protocol import PROXY_MODEL_PREFIX

        manager = self._manager
        version = getattr(manager, "firmware_ver_code", None)
        model = getattr(manager, "device_model", None) or ""
        if model.startswith(PROXY_MODEL_PREFIX):
            # A Meshloom radio proxy virtualizes DEVICE_QUERY (fw ver 10) and
            # answers ERROR to command 66: never probe through it.
            return CliCapabilities(False, "proxy_link", False, version)
        if version is None:
            return CliCapabilities(False, "firmware_unknown", False, version)
        if version < CLI_MIN_FIRMWARE_VER_CODE:
            return CliCapabilities(False, "firmware_too_old", False, version)

        link = self.link
        cached = link.capabilities
        if cached is not None:
            if cached.reason == "probe_failed":
                if link.probe_failures >= CLI_MAX_PROBES_PER_CONNECTION:
                    return cached
                if not refresh and asyncio.get_running_loop().time() < link.probe_retry_after:
                    return cached
            elif not refresh or not cached.supported:
                return cached
        self._check_lock()
        return await self._probe(link, version)

    async def _probe(self, link: CliLinkState, version: int) -> CliCapabilities:
        result: CliCapabilities | None = None
        for _attempt in range(2):
            try:
                tag = self._allocate_tag(link)
            except CliUnsupportedError:
                assert link.capabilities is not None
                return link.capabilities
            outcome, text = await self._exchange(
                link, tag, b"get wifi.status", timeout=CLI_REPLY_TIMEOUT_SECONDS, probe=True
            )
            if outcome == "timeout":
                continue  # once more, with a new tag
            if outcome == "reply":
                wifi = _value_of(text or "") in ("connected", "disconnected")
                result = CliCapabilities(True, "ok", wifi, version)
            elif outcome == "untagged":
                # No untagged mode: without the tag echo a late reply could be
                # taken for the answer to the next command.
                result = CliCapabilities(False, "untagged", False, version)
            else:
                result = CliCapabilities(False, "unsupported_cmd", False, version)
            break
        if result is None:
            link.probe_failures += 1
            link.probe_retry_after = asyncio.get_running_loop().time() + CLI_PROBE_BACKOFF_SECONDS
            result = CliCapabilities(False, "probe_failed", False, version)
        link.capabilities = result
        logger.info(
            "Companion CLI probe: supported=%s reason=%s wifi=%s",
            result.supported,
            result.reason,
            result.wifi,
        )
        return result

    # -- public calls -----------------------------------------------------

    async def run(
        self,
        command: str,
        *,
        secrets: Sequence[str] = (),
        sensitive_reply: bool = False,
        timeout: float = CLI_REPLY_TIMEOUT_SECONDS,
    ) -> CliReply:
        """Run one CLI command and return its reply.

        Raises ``CliLockError`` outside a session of this task, ``CliCommandError``
        (a ``ValueError``) for an invalid command, ``CliUnsupportedError`` when the
        link was declared unreliable, ``CliTimeoutError``,
        ``CliUnknownCommandError`` and ``CliRejectedError``.
        """
        self._check_lock()
        encoded = _validate_command(command)
        classified = _classify(command)
        sensitive = sensitive_reply or classified.sensitive
        hidden = classified.secret or sensitive
        link = self.link
        cached = link.capabilities
        if cached is not None and cached.reason == "link_unreliable":
            raise CliUnsupportedError("link_unreliable")
        tag = self._allocate_tag(link)
        with secret_scope(*classified.secret_values, *secrets):
            outcome, text = await self._exchange(link, tag, encoded, timeout=timeout, probe=False)
        verb, name = classified.verb, classified.name or None
        if outcome != "reply" or text is None:
            self._log(classified, "timeout")
            raise CliTimeoutError("cli_timeout", verb=verb, name=name)
        if text == "Unknown command":
            self._log(classified, "unknown command")
            raise CliUnknownCommandError("cli_unknown_command", verb=verb, name=name)
        if text.startswith(("Error", "ERROR")):
            self._log(classified, "rejected" if hidden else text)
            raise CliRejectedError(
                "cli_rejected", verb=verb, name=name, reply_text=None if hidden else text
            )
        result = CliReply(text=text, value=_value_of(text), sensitive=sensitive)
        if hidden:
            self._log(classified, REDACTED)
        elif classified.verb == "set":
            self._log(classified, "ok")
        else:
            logger.debug("CLI %s -> %s", command, text)
        return result

    @staticmethod
    def _log(classified: _Classified, outcome: str) -> None:
        # Verb and name only: never a set argument, never a sensitive reply.
        logger.debug("CLI %s %s -> %s", classified.verb, classified.name, outcome)

    async def get(self, name: str, *, sensitive: bool = False) -> CliReply:
        """``get <name>``."""
        _validate_name(name)
        return await self.run(f"get {name}", sensitive_reply=sensitive)

    async def set(self, name: str, value: str, *, secret: bool = False) -> CliReply:
        """``set <name> <value>`` (the value is sent verbatim, never trimmed)."""
        _validate_name(name)
        return await self.run(f"set {name} {value}", secrets=(value,) if secret else ())


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name or any(char.isspace() for char in name):
        raise CliCommandError("cli_name_invalid")


@asynccontextmanager
async def companion_cli_session(
    name: str, *, blocking: bool = True, require_setup: bool = True
) -> AsyncIterator[CompanionCli]:
    """Take the radio lock (polling paused, auto-fetch suspended) and yield a
    ``CompanionCli`` bound to the manager's ``CliLinkState``.

    With ``require_setup`` (default) it raises ``CliError("setup_incomplete")``
    before taking the lock while setup is not complete: ``radio_operation(
    suspend_auto_fetch=True)`` always restarts auto-fetch on exit, so a session
    opened before setup registered the event handlers would pull queued messages
    off the radio and lose them.
    """
    from app.services.radio_runtime import radio_runtime

    manager = radio_runtime.manager
    if require_setup and not manager.is_setup_complete:
        raise CliError("setup_incomplete")
    async with radio_runtime.radio_operation(
        name, pause_polling=True, suspend_auto_fetch=True, blocking=blocking
    ) as mc:
        with mark_radio_lock_held():
            yield CompanionCli(mc, manager=manager)
