"""Plugin host: one supervised child process per enabled plugin instance.

`PluginManager` (the `plugin_manager` singleton) owns a `PluginInstance` per
running instance; each instance runs one `PluginProcess` at a time, the child
launched by `app/plugins/launch.py` (`meshloom_plugin.runner`), and restarts it
with backoff when it fails. Behaviour and budgets: docs/dev/plugins.md,
"Supervisor".

- `plugin.init` must answer within `Budgets.init_timeout` (20 s).
- Events wait in a bounded inbox per instance (100, the oldest dropped and
  counted); at most `policy.MAX_CONCURRENT_HANDLERS` are in flight in the child,
  so the queue builds here, where it is bounded, and every in-flight event is
  actually running. An event gets `Budgets.event_timeout` (30 s), then
  `event.cancel`, then the process group is killed `Budgets.cancel_grace` (5 s)
  later.
- Everything pending when a process ends (in flight or queued) is dropped and
  counted, never replayed.
- A failure restarts the child after 1, 2, 4 ... 60 s; `Budgets.crash_loop_after`
  (5) consecutive failures stop the instance with `crash_loop` until a manual
  restart. A run that handled an event, or lived `Budgets.stable_after`, resets
  the count.
- CPU: a rolling budget (20 CPU-seconds per 60 s) read from `/proc/<pid>/stat`
  of the child and its descendants on Linux; the runner's hard `RLIMIT_CPU`
  (3600 s) is the portable backstop.
- The number of running instances is capped from the machine's memory
  (`instance_cap()`), and the instances of `load_from_db()` start 500 ms apart.

Isolation: every child is launched through the OS backend of
`app/plugins/isolation/` (`bwrap`, `setuid` or `none`, detected once per
process); `isolation_level()` is what health and `plugin.init` report.

What a child may ask (`messages.send`, `storage.*`, `timer.set`...) and which
events reach it are the capability layer's (`app/plugins/capabilities/`): each
instance holds its effective permissions, and every request goes through
`capabilities.dispatch()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import itertools
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.auth.ws_events import WsEvent
from app.message_limits import DM_HARD_LIMIT, channel_text_allowance
from app.plugins import bot_conversion, isolation, launch
from app.plugins.errors import HostMethodError
from app.plugins.quotas import effective_send_limits, send_guard
from app.secret_fields import scrub_error
from meshloom_plugin import policy, rpc

logger = logging.getLogger(__name__)

PluginStatus = Literal[
    "installed",
    "disabled",
    "starting",
    "running",
    "error",
    "crash_loop",
    "needs_attention",
    "incompatible",
    "revoked",
    # Display only, never stored: an enabled instance held by the global pause.
    "paused",
]
DisabledSource = Literal["env", "platform", "isolation"]

HOOKS = ("message", "raw_packet", "contact", "telemetry", "health", "timer")
# Persisted statuses that keep an enabled instance stopped at start-up until
# someone acts (a manual restart, a review, an update).
# `last_error` of a bundled instance whose code on disk is not its row's version
# (`PluginManager.code_matches`); also the `PluginHostError` code of its start.
CODE_MISMATCH = "plugin_code_mismatch"
STICKY_STATUSES = frozenset({"crash_loop", "needs_attention", "incompatible", "revoked"})
# What a disabled instance keeps showing: it waits for the user, not for a start.
ATTENTION_STATUSES = STICKY_STATUSES - {"crash_loop"}
API_VERSION = 1
LANGUAGES = ("en", "fr")
DEFAULT_LANGUAGE = "en"

MEM_PER_INSTANCE = 128 * 1024 * 1024
INSTANCE_CAP_MIN = 3
INSTANCE_CAP_MAX = 20

_DROP_LOG_INTERVAL = 10.0
# How long the host drains a child's stdout after killing it for a line over the
# cap, before it closes the pipe itself (`PluginProcess._discard_stdout`).
STDOUT_DRAIN_SECONDS = 5.0
# How long the host waits for a dead child's pipes to close before it closes them
# itself (`PluginProcess._wait_exit`; Python 3.11 only, gh-119710).
PIPE_CLOSE_SECONDS = 5.0
# Requests a child may have pending at the host at once; past it: -32001.
MAX_CHILD_REQUESTS = 32
# What a test event may capture of its `messages.send` requests; past either
# bound the child gets -32001 (`test_sends_truncated`) and the result says so.
MAX_TEST_SENDS = 32
MAX_TEST_SEND_BYTES = 64 * 1024
# `last_error` is a stable code plus a short message the host writes; the full
# detail (child text, paths) goes to the server log only.
LAST_ERROR_MAX_CHARS = 200
_DETAIL_LOG_MAX_CHARS = 2000
_PATH_RE = re.compile(r"(?<![\w.])(?:/[^\s/:'\"]+)+/?")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
_RUNNER_CODE_RE = re.compile(r"^[a-z][a-z_]{0,39}$")


@dataclass(frozen=True)
class Budgets:
    """Every time and size bound of the supervisor (seconds, events, bytes)."""

    init_timeout: float = 20.0
    event_timeout: float = 30.0
    cancel_grace: float = 5.0
    shutdown_grace: float = 5.0
    # The whole stop of one instance (shutdown message, grace, SIGTERM, SIGKILL, reap).
    stop_timeout: float = 10.0
    backoff_initial: float = 1.0
    backoff_max: float = 60.0
    crash_loop_after: int = 5
    stable_after: float = 60.0
    inbox_size: int = 100
    max_in_flight: int = policy.MAX_CONCURRENT_HANDLERS
    cpu_budget: float = 20.0
    cpu_window: float = 60.0
    cpu_sample_interval: float = 5.0
    stderr_ring_bytes: int = 1024 * 1024
    stderr_line_bytes: int = 2048
    stagger: float = 0.5
    message_settle: float = 2.0
    # Overrides of `policy.DEFAULT_LIMITS` passed to the runner (tests lower them).
    runner_limits: dict[str, int] = field(default_factory=dict)

    def backoff(self, failures: int) -> float:
        return min(self.backoff_max, self.backoff_initial * 2 ** max(0, failures - 1))


class PluginHostError(Exception):
    """A typed refusal of the supervisor. `code` is stable; the message never holds a secret."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PluginCapacityError(PluginHostError):
    """Starting one more instance would pass the hard cap (`instance_cap()`)."""

    def __init__(self, cap: int):
        super().__init__("instance_cap_reached", f"at most {cap} plugin instances can run")
        self.cap = cap


class ProcessEnded(Exception):
    """The child is gone; whatever waited on it is dropped."""


class RpcError(Exception):
    """The child answered a request with a JSON-RPC error."""

    def __init__(self, error: Any):
        self.error = error if isinstance(error, dict) else {}
        self.code = self.error.get("code")
        super().__init__(str(self.error.get("message") or "error"))


def stored_attention(instance_id: str) -> dict[str, Any] | None:
    """The instance's stored `attention` (the repository's cache), None when none."""
    from app.repository.plugins import _instances_cache

    row = _instances_cache.get(instance_id)
    return row.get("attention") if row else None


def sanitize_error(text: str | None) -> str | None:
    """`last_error` as WebSocket and health show it: no control characters, no
    filesystem path, at most `LAST_ERROR_MAX_CHARS`."""
    if text is None:
        return None
    text = _CONTROL_RE.sub(" ", text)
    text = _PATH_RE.sub("<path>", text)
    text = " ".join(text.split())
    if len(text) > LAST_ERROR_MAX_CHARS:
        text = text[: LAST_ERROR_MAX_CHARS - 1] + "\u2026"
    return text


def _identifier(value: Any, fallback: str = "Exception") -> str:
    """A child-supplied name (an exception type) kept only when it is a short identifier."""
    return value if isinstance(value, str) and _IDENTIFIER_RE.match(value) else fallback


def _error_type(exc: RpcError) -> str:
    data = exc.error.get("data")
    return _identifier(data.get("type") if isinstance(data, dict) else None)


def _detail(text: Any) -> str:
    """Child-controlled text for the server log: one line, capped."""
    line = " ".join(_CONTROL_RE.sub(" ", str(text)).split())
    return line[:_DETAIL_LOG_MAX_CHARS]


# --- platform -------------------------------------------------------------------


def platform_supported() -> bool:
    """The runner needs `resource` (rlimits): no `resource`, no plugins (Windows)."""
    return importlib.util.find_spec("resource") is not None


def isolation_level() -> str:
    """The OS isolation every child gets (`app/plugins/isolation/`, detected once)."""
    return isolation.current().level


def message_bytes_limit() -> int:
    """The smallest text allowance a plugin may meet: a channel message's, after
    the `"<radio name>: "` prefix (10-byte fallback when the name is unknown)."""
    name = ""
    try:
        from app.services.radio_runtime import radio_runtime

        mc = radio_runtime.meshcore
        name = (mc.self_info or {}).get("name", "") if mc is not None else ""
    except Exception:
        name = ""
    return min(DM_HARD_LIMIT, channel_text_allowance(name or None))


def read_proc_cpu_seconds(pid: int) -> float | None:
    """utime + stime of `pid` from `/proc/<pid>/stat`, in seconds; None when unreadable."""
    try:
        data = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    # `comm` (field 2) may hold spaces and parentheses: fields restart after the last ')'.
    fields = data[data.rfind(b")") + 2 :].split()
    try:
        ticks = int(fields[11]) + int(fields[12])  # fields 14 and 15
        return ticks / os.sysconf("SC_CLK_TCK")
    except (IndexError, ValueError, OSError):
        return None


def read_proc_tree_cpu_seconds(pid: int) -> float | None:
    """CPU of `pid` and every descendant, reaped ones included (`cutime`, `cstime`).

    Under bwrap the supervisor's child is bwrap and the runner a grandchild (the
    runner's seccomp filter and `RLIMIT_NPROC` 0 keep it from starting more, but
    the budget covers the whole tree anyway).
    None when `pid` is unreadable.
    """
    total = 0
    tick = os.sysconf("SC_CLK_TCK")
    todo, seen = [pid], set()
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            data = Path(f"/proc/{current}/stat").read_bytes()
        except OSError:
            if current == pid:
                return None
            continue
        fields = data[data.rfind(b")") + 2 :].split()
        try:
            total += sum(int(fields[i]) for i in (11, 12, 13, 14))  # fields 14 to 17
        except (IndexError, ValueError):
            continue
        with contextlib.suppress(OSError):
            for task in os.listdir(f"/proc/{current}/task"):
                with contextlib.suppress(OSError, ValueError):
                    children = Path(f"/proc/{current}/task/{task}/children").read_text()
                    todo.extend(int(child) for child in children.split())
    return total / tick


def default_cpu_reader() -> Callable[[int], float | None] | None:
    return read_proc_tree_cpu_seconds if sys.platform.startswith("linux") else None


def mem_total_bytes() -> int | None:
    """`MemTotal` from `/proc/meminfo`, else the physical pages sysconf reports."""
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def instance_cap(mem_bytes: int | None) -> int:
    """One running instance per 128 MB of memory, between 3 and 20 (a Pi 3: 7)."""
    if not mem_bytes:
        return INSTANCE_CAP_MIN
    return max(INSTANCE_CAP_MIN, min(INSTANCE_CAP_MAX, mem_bytes // MEM_PER_INSTANCE))


# --- stderr ring ----------------------------------------------------------------


class LogRing:
    """The child's stderr: complete lines, each cut at `line_bytes`, `max_bytes` in all."""

    def __init__(self, max_bytes: int, line_bytes: int):
        self._max = max_bytes
        self._line_max = line_bytes
        self._lines: deque[str] = deque()
        self._size = 0
        self._partial = bytearray()
        self._partial_cut = False
        self.runner_error: dict[str, str] | None = None

    def feed(self, data: bytes) -> None:
        start = 0
        while True:
            end = data.find(b"\n", start)
            if end < 0:
                self._extend(data[start:])
                return
            self._extend(data[start:end])
            self._commit()
            start = end + 1

    def flush(self) -> None:
        if self._partial or self._partial_cut:
            self._commit()

    def _extend(self, chunk: bytes) -> None:
        room = self._line_max - len(self._partial)
        if len(chunk) > room:
            self._partial_cut = True
            chunk = chunk[: max(0, room)]
        self._partial += chunk

    def _commit(self) -> None:
        raw = bytes(self._partial)
        self._partial.clear()
        cut = self._partial_cut
        self._partial_cut = False
        if raw.startswith(b'{"runner_error"') and not cut:
            with contextlib.suppress(ValueError):
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    self.runner_error = {
                        "code": str(parsed.get("runner_error")),
                        "message": str(parsed.get("message", "")),
                    }
        line = raw.decode("utf-8", "replace") + (" [truncated]" if cut else "")
        self._lines.append(line)
        self._size += len(line)
        while self._size > self._max and self._lines:
            self._size -= len(self._lines.popleft())

    def lines(self) -> list[str]:
        return list(self._lines)


# --- one child process ----------------------------------------------------------

RequestHandler = Callable[[str, Any], Awaitable[Any]]
NotificationHandler = Callable[[str, Any], None]


class PluginProcess:
    """One child: spawn, JSON-RPC both ways, stderr capture, kill of its process group."""

    def __init__(
        self,
        plugin_dir: Path,
        log: LogRing,
        on_request: RequestHandler,
        write_timeout: float = 30.0,
        on_notify: NotificationHandler | None = None,
        backend: isolation.IsolationBackend | None = None,
    ):
        self._plugin_dir = plugin_dir
        # The instance's backend (bound to its uid under `setuid`); the process-wide
        # one when None, resolved at `start()`.
        self._backend = backend
        self._write_timeout = write_timeout
        self._log = log
        self._on_request = on_request
        self._on_notify = on_notify
        self._proc: asyncio.subprocess.Process | None = None
        self.work_dir: Path | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._write_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._readers: list[asyncio.Task[Any]] = []
        self._exited = asyncio.Event()
        self.kill_reason: str | None = None
        self.started_at = 0.0
        self._wrapped = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def alive(self) -> bool:
        return self._proc is not None and not self._exited.is_set()

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    async def start(self, params_line: Callable[[Path], bytes]) -> None:
        backend = self._backend or isolation.current().backend
        self._backend = backend
        self._wrapped = backend.level == "bwrap"
        backend.prepare_plugin_dir(self._plugin_dir)
        self.work_dir = backend.prepare_work_dir(Path(tempfile.mkdtemp(prefix="meshloom-plugin-")))
        argv, kwargs = backend.launch(self._plugin_dir, self.work_dir)
        self._proc = await asyncio.create_subprocess_exec(
            *argv, stderr=asyncio.subprocess.PIPE, **kwargs
        )
        self.started_at = time.monotonic()
        self._readers = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
            asyncio.create_task(self._wait_exit()),
        ]
        await self._write(params_line(self.work_dir))

    async def wait(self) -> None:
        await self._exited.wait()

    def kill(self, reason: str, sig: int = signal.SIGKILL) -> None:
        """Signal the child's process group (its own session). The first reason wins."""
        if self._proc is None or self._proc.returncode is not None:
            return
        if self.kill_reason is None:
            self.kill_reason = reason
        self._signal_group(sig)

    def _signal_group(self, sig: int) -> None:
        """Through the backend: without `CAP_KILL`, the `setuid` level signals the
        group from a helper running as the instance uid."""
        if self._proc is None:
            return
        backend = self._backend or isolation.current().backend
        backend.signal_group(self._proc.pid, sig)

    def exit_reason(self) -> str:
        if self.kill_reason is not None:
            return self.kill_reason
        code = self.returncode
        if code is None:
            return "process still running"
        # bwrap reports a child killed by a signal as 128 + the signal.
        if code < 0 or (self._wrapped and code > 128):
            sig = -code if code < 0 else code - 128
            if sig == getattr(signal, "SIGXCPU", None):
                return "cpu_rlimit: CPU time limit reached (RLIMIT_CPU)"
            with contextlib.suppress(ValueError):
                return f"killed: killed by {signal.Signals(sig).name}"
            return f"killed: killed by signal {sig}"
        if code == 3 and self._log.runner_error is not None:
            err = self._log.runner_error
            runner_code = err["code"] if _RUNNER_CODE_RE.match(err["code"]) else "unknown"
            logger.warning(
                "Plugin runner refused to start (%s): %s", runner_code, _detail(err["message"])
            )
            return f"start_refused: the runner refused to start ({runner_code})"
        if code == 2:
            return "protocol_violation: the plugin broke the protocol (exit 2)"
        return f"crash: exited unexpectedly (code {code})"

    # Requests and notifications to the child.

    def next_id(self) -> int:
        return next(self._ids)

    async def send_request(
        self, method: str, params: Any, msg_id: int | None = None
    ) -> tuple[int, asyncio.Future[Any]]:
        if msg_id is None:
            msg_id = self.next_id()
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        future.add_done_callback(lambda _f, msg_id=msg_id: self._pending.pop(msg_id, None))
        try:
            await self._write(rpc.encode(rpc.request(msg_id, method, params)))
        except BaseException:
            future.cancel()
            raise
        return msg_id, future

    async def call(self, method: str, params: Any, timeout: float) -> Any:
        _msg_id, future = await self.send_request(method, params)
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            future.cancel()

    async def notify(self, method: str, params: Any = None, timeout: float | None = None) -> None:
        await self._write(rpc.encode(rpc.notification(method, params)), timeout)

    async def _write(self, data: bytes, timeout: float | None = None) -> None:
        """Write one line within `timeout` (default: the event budget).

        A child that does not read its stdin fills the pipe: the write would wait
        forever, so past the bound the child is killed (`stdin_blocked`) and the
        caller gets `ProcessEnded`. A line is at most `rpc.MAX_MESSAGE_BYTES`.
        """
        if not self.alive or self._proc is None or self._proc.stdin is None:
            raise ProcessEnded()
        limit = self._write_timeout if timeout is None else timeout
        try:
            await asyncio.wait_for(self._write_locked(data), max(0.0, limit))
        except TimeoutError:
            self.kill(f"stdin_blocked: the plugin did not read its input within {limit:g} s")
            raise ProcessEnded() from None

    async def _write_locked(self, data: bytes) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        async with self._write_lock:
            try:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise ProcessEnded() from exc

    # Readers.

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        decoder = rpc.LineDecoder()
        while True:
            data = await self._proc.stdout.read(65536)
            if not data:
                return
            try:
                lines = decoder.feed(data)
            except rpc.FramingError as exc:
                self.kill(f"rpc_framing: {exc}")
                await self._discard_stdout()
                return
            for line in lines:
                if line.strip():
                    self._handle_line(line)

    async def _discard_stdout(self) -> None:
        """Drop what a killed child still writes, then close the pipe ourselves.

        On Python 3.11 `Process.wait()` returns only once every pipe is closed
        (gh-119710, fixed in 3.12): a reader that stopped on a full buffer would
        leave `_wait_exit` waiting for ever, the instance shown running with no
        process. Bounded, since a process that kept the descriptor (a grandchild)
        can hold it open as long as it lives.
        """
        assert self._proc is not None and self._proc.stdout is not None
        try:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(STDOUT_DRAIN_SECONDS):
                    while await self._proc.stdout.read(65536):
                        pass
        finally:
            self._close_pipes(1)

    def _close_pipes(self, *fds: int) -> None:
        """Close the host's end of the child's pipes (asyncio has no public seam)."""
        assert self._proc is not None
        transport = self._proc._transport  # type: ignore[attr-defined]
        for fd in fds:
            pipe = transport.get_pipe_transport(fd)
            if pipe is not None:
                pipe.close()

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            data = await self._proc.stderr.read(65536)
            if not data:
                self._log.flush()
                return
            self._log.feed(data)

    async def _wait_exit(self) -> None:
        assert self._proc is not None
        waiter = asyncio.ensure_future(self._proc.wait())
        while not waiter.done():
            await asyncio.wait({waiter}, timeout=0.5)
            if waiter.done() or self._proc.returncode is None:
                continue
            # The child is gone but, on 3.11, `wait()` also waits for every pipe
            # to close (gh-119710): a grandchild that kept one would hold it for
            # ever. Bounded: kill the group, then close our ends.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter), PIPE_CLOSE_SECONDS)
            if not waiter.done():
                self._signal_group(signal.SIGKILL)
                self._close_pipes(0, 1, 2)
                waiter.cancel()
                break
        # Let the readers drain what the child wrote last (its runner_error line).
        await asyncio.wait(self._readers[:2], timeout=3.0)
        self._exited.set()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ProcessEnded())

    def _handle_line(self, line: bytes) -> None:
        try:
            msg = rpc.decode(line)
        except rpc.ProtocolError:
            logger.debug("Plugin child sent a line that is not JSON-RPC")
            return
        if "method" not in msg:
            msg_id = msg.get("id")
            future = self._pending.get(msg_id) if isinstance(msg_id, int) else None
            if future is not None and not future.done():
                if "error" in msg:
                    future.set_exception(RpcError(msg["error"]))
                else:
                    future.set_result(msg.get("result"))
            return
        if "id" not in msg:
            if self._on_notify is not None:
                try:
                    self._on_notify(msg["method"], msg.get("params"))
                except Exception:
                    logger.exception("Plugin notification %s failed", msg["method"])
            return
        if len(self._tasks) >= MAX_CHILD_REQUESTS:
            overloaded = rpc.error(msg["id"], rpc.OVERLOADED, "too many pending host requests")
            task = asyncio.create_task(self._write_quietly(rpc.encode(overloaded)))
        else:
            task = asyncio.create_task(self._answer(msg["id"], msg["method"], msg.get("params")))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _answer(self, msg_id: Any, method: str, params: Any) -> None:
        try:
            response = rpc.result(msg_id, await self._on_request(method, params))
        except HostMethodError as exc:
            response = rpc.error(msg_id, exc.code, str(exc), exc.data)
        except Exception:
            logger.exception("Plugin host method %s failed", method)
            response = rpc.error(msg_id, rpc.INTERNAL_ERROR, "host error")
        try:
            data = rpc.encode(response, rpc.result_limit(method))
        except ValueError:
            data = rpc.encode(rpc.error(msg_id, rpc.INTERNAL_ERROR, "result too large"))
        await self._write_quietly(data)

    async def _write_quietly(self, data: bytes) -> None:
        with contextlib.suppress(ProcessEnded):
            await self._write(data)

    async def shutdown(self, timeout: float) -> None:
        """End the child within `timeout`, whatever it does, and reap it.

        `plugin.shutdown` (a bounded write: a child that does not read its stdin
        cannot hold it), a grace for the exit, SIGTERM, then SIGKILL of the
        process group in `close()`, which always runs.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            if self.alive:
                with contextlib.suppress(ProcessEnded, TimeoutError):
                    await self.notify("plugin.shutdown", timeout=min(1.0, timeout / 4))
                with contextlib.suppress(TimeoutError):
                    grace = max(0.0, (deadline - loop.time()) * 0.6)
                    await asyncio.wait_for(self._exited.wait(), grace)
            if self.alive:
                self.kill("stopped: did not stop within the shutdown grace", signal.SIGTERM)
                with contextlib.suppress(TimeoutError):
                    rest = max(0.0, min(1.0, deadline - loop.time() - 1.0))
                    await asyncio.wait_for(self._exited.wait(), rest)
        finally:
            await self.close("stopped", reap_timeout=max(1.0, deadline - loop.time()))

    async def close(self, reason: str = "stopped", reap_timeout: float = 2.0) -> None:
        """Kill if still running, reap, stop the readers, remove the work directory."""
        if self._proc is not None:
            if self._proc.returncode is None:
                # SIGKILL even when an earlier SIGTERM set the reason.
                if self.kill_reason is None:
                    self.kill_reason = reason
                self._signal_group(signal.SIGKILL)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._proc.wait(), reap_timeout)
        for task in [*self._readers, *self._tasks]:
            task.cancel()
        await asyncio.gather(*self._readers, *self._tasks, return_exceptions=True)
        self._exited.set()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ProcessEnded())
        if self.work_dir is not None:
            (self._backend or isolation.current().backend).remove_work_dir(self.work_dir)


# --- one instance -----------------------------------------------------------------


@dataclass
class _Event:
    hook: str
    params: dict[str, Any]
    test: bool = False
    result: asyncio.Future[Any] | None = None
    sends: list[dict[str, Any]] = field(default_factory=list)
    sends_bytes: int = 0
    truncated: bool = False


class PluginInstance:
    """Supervises one instance: its current child, inbox, status and counters."""

    def __init__(
        self,
        manager: PluginManager,
        row: dict[str, Any],
        plugin_dir: Path,
        backend: isolation.IsolationBackend | None = None,
    ):
        self._manager = manager
        self.backend = backend
        self.budgets = manager.budgets
        self.instance_id: str = row["instance_id"]
        self.plugin_id: str = row["plugin_id"]
        self.label: str = row.get("label") or row["plugin_id"]
        manifest = row.get("manifest") or {}
        self.hooks = frozenset(h for h in manifest.get("hooks", []) if h in HOOKS)
        self.plugin_dir = plugin_dir
        # What the instance may do: declared by the manifest and granted by the
        # user, narrowed (`capabilities/grants.py`); the scope restricts reads.
        # (The capability package imports this module: imported here, not at the top.)
        from app.plugins.capabilities.grants import dropped_for_supervisor, effective_permissions

        self.permissions: dict[str, dict[str, Any]] = effective_permissions(
            manifest, row.get("granted_permissions")
        )
        if dropped := dropped_for_supervisor(manifest):
            # The whole permission is dropped (fail closed), public hosts included.
            logger.warning(
                "Plugin %s (%s): permission %s dropped, its manifest lists an address of "
                "the Home Assistant supervisor network",
                self.plugin_id,
                self.instance_id,
                ", ".join(dropped),
            )
        self.scope: dict[str, Any] = row.get("scope") or {}
        self.quotas: dict[str, Any] = row.get("quotas") or {}
        self.summary: str | None = None
        self._summary_at = -float("inf")
        self._timers: dict[int, asyncio.TimerHandle] = {}
        self._timer_ids = itertools.count(1)
        self.status: str = "installed"
        self.last_error: str | None = None
        self.dropped_events = 0
        self.restarts = 0
        self.consecutive_failures = 0
        self.log = LogRing(self.budgets.stderr_ring_bytes, self.budgets.stderr_line_bytes)
        self._inbox: deque[_Event] = deque()
        self._inbox_ready = asyncio.Event()
        self._in_flight: dict[int, _Event] = {}
        self._process: PluginProcess | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._stop_requested = asyncio.Event()
        self._handled_ok = False
        self._secrets: list[str] = []
        self._last_drop_log = -_DROP_LOG_INTERVAL
        self._drops_since_log = 0
        self._last_error_log = -_DROP_LOG_INTERVAL

    # Public surface.

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def queued(self) -> int:
        return len(self._inbox)

    def launch(self) -> None:
        self._task = asyncio.create_task(self._supervise(), name=f"plugin:{self.instance_id}")

    async def stop(self, timeout: float) -> None:
        """Stop within `timeout` in all cases: the child is ended and reaped, the task done."""
        self._stopping = True
        self._stop_requested.set()
        self._inbox_ready.set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            process = self._process
            if process is not None:
                await process.shutdown(max(0.5, timeout - 1.5))
        finally:
            if self._task is not None:
                with contextlib.suppress(Exception):
                    rest = max(0.0, deadline - loop.time() - 1.0)
                    await asyncio.wait_for(asyncio.shield(self._task), rest)
                if not self._task.done():
                    self.kill_now()
                    self._task.cancel()
                    await asyncio.wait({self._task}, timeout=1.0)
            self.kill_now()
            self._cancel_timers()
            self._drop_all("instance stopped")

    def kill_now(self) -> None:
        if self._process is not None:
            self._process.kill("stopped: server shutting down")

    def enqueue(self, event: _Event) -> bool:
        if self._stopping or self.status not in ("starting", "running"):
            if event.result is not None and not event.result.done():
                event.result.set_exception(
                    PluginHostError("instance_not_running", "the instance is not running")
                )
            return False
        if len(self._inbox) >= self.budgets.inbox_size:
            self._drop([self._inbox.popleft()], "queue full")
        self._inbox.append(event)
        self._inbox_ready.set()
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "plugin_id": self.plugin_id,
            "label": self.label,
            "status": self.status,
            "last_error": self.last_error,
            "dropped_events": self.dropped_events,
            "restarts": self.restarts,
            "summary": self.summary,
            "send_guard": send_guard.hits(self.instance_id),
            "attention": stored_attention(self.instance_id),
        }

    # Capability state (`app/plugins/capabilities/`).

    def add_timer(self, seconds: float, max_pending: int) -> int | None:
        """Arm one `event.timer` in `seconds`; None when `max_pending` are pending."""
        if len(self._timers) >= max_pending:
            return None
        timer_id = next(self._timer_ids)
        loop = asyncio.get_running_loop()
        self._timers[timer_id] = loop.call_later(seconds, self._fire_timer, timer_id, seconds)
        return timer_id

    def _fire_timer(self, timer_id: int, seconds: float) -> None:
        if self._timers.pop(timer_id, None) is not None:
            self.enqueue(_Event("timer", {"timer_id": timer_id, "seconds": seconds}))

    def _cancel_timers(self) -> None:
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()

    def summary_wait(self, interval: float) -> float:
        """Seconds before the summary may change again (0 when it may now)."""
        return max(0.0, self._summary_at + interval - time.monotonic())

    async def set_summary(self, summary: str | None) -> None:
        self._summary_at = time.monotonic()
        summary = self._scrub(summary)
        if summary != self.summary:
            self.summary = summary
            await self._manager._status_changed(self)

    def _on_child_notification(self, method: str, params: Any) -> None:
        if method == "log":
            from app.plugins.capabilities.log import log_line

            self.log.feed(log_line(params))

    # Supervision loop.

    async def _supervise(self) -> None:
        while not self._stopping:
            await self._set_status("starting", self.last_error)
            started = time.monotonic()
            self._handled_ok = False
            reason = await self._run_once()
            if self._stopping or reason is None:
                return
            if self._handled_ok or time.monotonic() - started >= self.budgets.stable_after:
                self.consecutive_failures = 0
            self.consecutive_failures += 1
            self._drop_all("process ended")
            logger.warning(
                "Plugin %s (%s) failed (%d in a row): %s",
                self.label,
                self.instance_id,
                self.consecutive_failures,
                reason,
            )
            if self.consecutive_failures >= self.budgets.crash_loop_after:
                await self._set_status("crash_loop", reason)
                logger.error(
                    "Plugin %s (%s) stopped after %d consecutive failures",
                    self.label,
                    self.instance_id,
                    self.consecutive_failures,
                )
                return
            await self._set_status("error", reason)
            with contextlib.suppress(TimeoutError):
                delay = self.budgets.backoff(self.consecutive_failures)
                await asyncio.wait_for(self._stop_requested.wait(), delay)
            self.restarts += 1

    async def _run_once(self) -> str | None:
        """One child lifetime; returns why it ended, None after a requested stop."""
        process = PluginProcess(
            self.plugin_dir,
            self.log,
            self._on_child_request,
            write_timeout=self.budgets.event_timeout,
            on_notify=self._on_child_notification,
            backend=self.backend,
        )
        self._process = process
        cpu_task: asyncio.Task[None] | None = None
        try:
            try:
                init_params = await self._init_params()
                await process.start(self._params_line)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Plugin %s (%s) cannot start: %s: %s",
                    self.label,
                    self.instance_id,
                    type(exc).__name__,
                    _detail(self._scrub(str(exc))),
                )
                return f"start_failed: cannot start the plugin process ({type(exc).__name__})"
            except ProcessEnded:
                return process.exit_reason()
            if self._stopping:
                process.kill("stopped")
            try:
                await process.call("plugin.init", init_params, self.budgets.init_timeout)
            except TimeoutError:
                process.kill(
                    f"init_timeout: plugin.init did not answer within {self.budgets.init_timeout:g} s"
                )
            except RpcError as exc:
                self._log_child_error("plugin.init", exc)
                process.kill(f"init_failed: plugin.init raised {_error_type(exc)}")
            except (ProcessEnded, ValueError):
                pass
            if process.alive and not self._stopping:
                await self._set_status("running", self.last_error)
                cpu_task = self._manager._watch_cpu(process)
                await self._serve(process)
            await process.wait()
            return None if self._stopping else process.exit_reason()
        finally:
            if cpu_task is not None:
                cpu_task.cancel()
                await asyncio.gather(cpu_task, return_exceptions=True)
            await process.close()
            self._process = None
            self._cancel_timers()

    def _params_line(self, work_dir: Path) -> bytes:
        return launch.runner_params(
            self.plugin_dir,
            work_dir,
            self.instance_id,
            isolation_level(),
            self.budgets.runner_limits or None,
        )

    async def _init_params(self) -> dict[str, Any]:
        from app.repository.plugins import PluginRepository

        row = await PluginRepository.get(self.instance_id)
        config = await PluginRepository.config_for_init(self.instance_id)
        if row is None or config is None:
            raise ValueError("the instance no longer exists")
        secret_names = set(row.get("secret_fields") or [])
        self._secrets = [str(v) for k, v in config.items() if k in secret_names and v]
        # `language` is a standard config field of every instance (`en`, `fr`).
        lang = config.get("language")
        if lang not in LANGUAGES:
            lang = DEFAULT_LANGUAGE
        config = {**config, "language": lang}
        return {
            "api": API_VERSION,
            "instance": {
                "id": self.instance_id,
                "plugin_id": self.plugin_id,
                "label": self.label,
            },
            "config": config,
            "lang": lang,
            "granted": sorted(self.permissions),
            "limits": {"message_bytes": message_bytes_limit()},
            "scope": self.scope,
            "quotas": self._effective_quotas(),
            "isolation": isolation_level(),
        }

    def _effective_quotas(self) -> dict[str, Any]:
        send = self.permissions.get("messages.send")
        if send is None:
            return {}
        limits = effective_send_limits(send, self.quotas)
        return {"messages.send": {"per_hour": limits.per_hour, "per_day": limits.per_day}}

    async def _serve(self, process: PluginProcess) -> None:
        in_flight: set[asyncio.Task[None]] = set()
        exited = asyncio.create_task(process.wait())
        ready: asyncio.Task[Any] | None = None
        try:
            while process.alive and not self._stopping:
                while self._inbox and len(in_flight) < self.budgets.max_in_flight:
                    task = asyncio.create_task(self._deliver(process, self._inbox.popleft()))
                    in_flight.add(task)
                    task.add_done_callback(in_flight.discard)
                if not self._inbox:
                    self._inbox_ready.clear()
                if ready is None or ready.done():
                    ready = asyncio.create_task(self._inbox_ready.wait())
                await asyncio.wait({exited, ready, *in_flight}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (exited, ready):
                if task is not None:
                    task.cancel()
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)

    async def _deliver(self, process: PluginProcess, event: _Event) -> None:
        method = f"event.{event.hook}"
        # `event_id` lets the child tie its own requests (a test event's
        # `messages.send`) to the event that caused them.
        msg_id = process.next_id()
        params = {**event.params, "event_id": msg_id}
        if event.test:
            params["test"] = True
        self._in_flight[msg_id] = event
        # The budget counts from the hand-off to the child: writing the event to a
        # child that does not read its stdin uses it up too.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.budgets.event_timeout
        try:
            _id, future = await process.send_request(method, params, msg_id)
        except ProcessEnded:
            self._in_flight.pop(msg_id, None)
            self._drop([event], "process ended")
            return
        except ValueError as exc:
            self._in_flight.pop(msg_id, None)
            self._finish(event, error=PluginHostError("event_too_large", str(exc)))
            return
        try:
            try:
                rest = max(0.0, deadline - loop.time())
                value = await asyncio.wait_for(asyncio.shield(future), rest)
            except TimeoutError:
                with contextlib.suppress(ProcessEnded):
                    await process.notify(
                        "event.cancel", {"id": msg_id}, timeout=self.budgets.cancel_grace
                    )
                try:
                    await asyncio.wait_for(future, self.budgets.cancel_grace)
                except TimeoutError:
                    process.kill(
                        f"event_cancel_ignored: {method} ran over "
                        f"{self.budgets.event_timeout:g} s and ignored event.cancel"
                    )
                    self._drop([event], "process killed")
                    return
                except RpcError:
                    pass
                await self._set_error(
                    f"event_timeout: {method} timed out after {self.budgets.event_timeout:g} s"
                )
                self._finish(event, error=PluginHostError("event_timeout", "the event timed out"))
                return
        except ProcessEnded:
            self._drop([event], "process ended")
            return
        except RpcError as exc:
            self._log_child_error(method, exc)
            await self._set_error(f"handler_error: {method} raised {_error_type(exc)}")
            self._finish(event, error=PluginHostError("handler_error", _error_type(exc)))
            return
        finally:
            self._in_flight.pop(msg_id, None)
            future.cancel()
        self._handled_ok = True
        self._finish(event, value=value)

    def _finish(self, event: _Event, *, value: Any = None, error: Exception | None = None) -> None:
        if event.result is None or event.result.done():
            return
        if error is not None:
            event.result.set_exception(error)
        else:
            event.result.set_result(
                {"result": value, "sends": event.sends, "truncated": event.truncated}
            )

    async def _on_child_request(self, method: str, params: Any) -> Any:
        event_id = params.get("event_id") if isinstance(params, dict) else None
        if isinstance(event_id, int):
            event = self._in_flight.get(event_id)
        elif len(self._in_flight) == 1:
            # No `event_id`: the only event in flight is the one that caused it.
            event = next(iter(self._in_flight.values()))
        else:
            event = None
        override = self._manager.host_methods.get(method)
        if override is not None:
            return await override(self, params)
        from app.plugins import capabilities

        return await capabilities.dispatch(self, method, params, event)

    # Counters and statuses.

    def _log_child_error(self, method: str, exc: RpcError) -> None:
        """The child's own error text goes to the server log (capped, scrubbed), at
        most once per `_DROP_LOG_INTERVAL`; `last_error` only names its type."""
        now = time.monotonic()
        if now - self._last_error_log < _DROP_LOG_INTERVAL:
            return
        self._last_error_log = now
        logger.warning(
            "Plugin %s (%s): %s raised %s: %s",
            self.label,
            self.instance_id,
            method,
            _error_type(exc),
            _detail(self._scrub(str(exc))),
        )

    def _drop(self, events: list[_Event], why: str) -> None:
        if not events:
            return
        for event in events:
            self._finish(event, error=PluginHostError("event_dropped", f"event dropped ({why})"))
        self.dropped_events += len(events)
        self._drops_since_log += len(events)
        now = time.monotonic()
        if now - self._last_drop_log >= _DROP_LOG_INTERVAL:
            logger.warning(
                "Plugin %s (%s): dropped %d event(s) (%s), %d dropped since start",
                self.label,
                self.instance_id,
                self._drops_since_log,
                why,
                self.dropped_events,
            )
            self._last_drop_log = now
            self._drops_since_log = 0

    def _drop_all(self, why: str) -> None:
        pending = list(self._inbox)
        self._inbox.clear()
        self._drop(pending, why)

    def _scrub(self, text: str | None) -> str | None:
        return scrub_error(text, self._secrets)

    async def _set_error(self, error: str) -> None:
        await self._set_status(self.status, error)

    async def _set_status(self, status: str, error: str | None = None) -> None:
        """Set the status and `last_error` (scrubbed of the instance's secrets), then fan out."""
        error = sanitize_error(self._scrub(error))
        if status == self.status and error == self.last_error:
            return
        self.status = status
        self.last_error = error
        await self._manager._status_changed(self)


# --- manager ------------------------------------------------------------------------

HostMethod = Callable[[PluginInstance, Any], Awaitable[Any]]


class PluginManager:
    """Every running instance; the seam the fanout bus, the API and the lifespan use."""

    def __init__(
        self,
        budgets: Budgets | None = None,
        *,
        plugins_root: Path | None = None,
        cpu_reader: Callable[[int], float | None] | None | Literal["default"] = "default",
        clock: Callable[[], float] = time.monotonic,
        mem_total: Callable[[], int | None] = mem_total_bytes,
    ):
        self.budgets = budgets or Budgets()
        self._plugins_root = plugins_root
        # Set when the selected isolation could not be prepared: fail closed.
        self._isolation_failed = False
        self._cpu_reader = default_cpu_reader() if cpu_reader == "default" else cpu_reader
        self._clock = clock
        self._mem_total = mem_total
        self._instances: dict[str, PluginInstance] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._load_task: asyncio.Task[None] | None = None
        # The global pause (MSHL-216, `app/plugins/pause.py` owns the persisted
        # state): nothing starts and no event is dispatched while it is set; the
        # per-instance `enabled` flags are never touched.
        self.paused = False
        # Overrides of the capability layer (`app/plugins/capabilities/`), name to
        # coroutine: tests register extra methods here. Checked first.
        self.host_methods: dict[str, HostMethod] = {}
        # Bundled instances refused at their last start because the code on disk is
        # not the version their row records (`code_matches`): status `error`,
        # `last_error = plugin_code_mismatch` until a start finds them matching.
        self._code_mismatch: set[str] = set()
        # Under `setuid`: the directories that made the last purge of each uid fail
        # (`setuid.PurgeBlockedError`), shown in health as `plugins_purge_blocked`.
        self._purge_blocked: dict[int, tuple[str, ...]] = {}

    # Gates.

    def disabled_source(self) -> DisabledSource | None:
        """Why the plugin system is off: the host lock, the platform, then an isolation
        setup that failed (`fail_closed()`); None when on."""
        from app.config import settings

        if settings.disable_plugins:
            return "env"
        if not platform_supported():
            return "platform"
        if self._isolation_failed:
            return "isolation"
        return None

    def fail_closed(self) -> None:
        """The isolation setup failed: no child starts until the server restarts."""
        self._isolation_failed = True

    def instance_cap(self) -> int:
        return instance_cap(self._mem_total())

    def plugins_root(self) -> Path:
        if self._plugins_root is not None:
            return self._plugins_root
        from app.config import settings

        return Path(settings.database_path).resolve().parent / "plugins"

    def code_matches(self, row: dict[str, Any]) -> bool:
        """A bundled instance's shared code is the version and plugin its row
        records. False (fail closed) when its `manifest.json` is missing, unreadable
        or says otherwise, e.g. after a bundled update that installed the new tree,
        could not write the rows and could not put the old tree back
        (`bundled_update.py`). Other kinds are not checked."""
        if row.get("kind") != "bundled":
            return True
        path = self.plugins_root() / row["plugin_id"] / "manifest.json"
        try:
            if path.is_symlink():
                return False
            manifest = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return False
        return (
            isinstance(manifest, dict)
            and manifest.get("id") == row["plugin_id"]
            and manifest.get("version") == row["version"]
        )

    def code_dir(self, row: dict[str, Any]) -> Path:
        """The plugin's code: shared per `plugin_id`; a local plugin's source is written out
        (a converted bot's as `bot.py` beside the `compat.py` wrapper, `bot_conversion.py`)."""
        if row.get("kind") != "local":
            return self.plugins_root() / row["plugin_id"]
        directory = self.plugins_root() / "local" / row["instance_id"]
        manifest = row.get("manifest") or {}
        entry = str(manifest.get("entry", "plugin.py")).partition(":")[0]
        target = (directory / entry).resolve()
        if target.suffix != ".py":
            target = target / "__init__.py"
        if not target.is_relative_to(directory.resolve()):
            raise PluginHostError("entry_invalid", "the entry is outside the plugin directory")
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if bot_conversion.is_compat_manifest(manifest):
            # A converted 4.18.x bot: its code is bot.py, run by a fixed wrapper.
            (directory / bot_conversion.COMPAT_SOURCE_FILE).write_text(
                row.get("source") or "", encoding="utf-8"
            )
            target.write_text(bot_conversion.COMPAT_WRAPPER, encoding="utf-8")
        else:
            target.write_text(row.get("source") or "", encoding="utf-8")
        return directory.resolve()

    # Lifecycle.

    async def load_from_db(self) -> None:
        """Start every enabled instance not held by a sticky status, 500 ms apart.

        Returns at once: the staggered starts run in a task kept here and
        cancelled by `stop_all()`.
        """
        from app.repository.plugins import PluginRepository

        if self.disabled_source() is not None or self.paused:
            return
        rows = await PluginRepository.get_enabled()
        ids = [r["instance_id"] for r in rows if r.get("status") not in STICKY_STATUSES]
        if ids:
            self._load_task = asyncio.create_task(self._start_staggered(ids))

    async def _start_staggered(self, instance_ids: list[str]) -> None:
        for index, instance_id in enumerate(instance_ids):
            if index:
                await asyncio.sleep(self.budgets.stagger)
            try:
                await self.start(instance_id)
            except PluginHostError as exc:
                logger.warning("Plugin instance %s not started: %s", instance_id, exc)
            except Exception:
                logger.exception("Plugin instance %s failed to start", instance_id)

    async def start(self, instance_id: str) -> None:
        """Start one instance (no-op when it runs, or while the global pause holds).

        Raises `PluginHostError`."""
        from app.repository.plugins import PluginRepository

        source = self.disabled_source()
        if source is not None:
            raise PluginHostError("plugins_disabled", f"plugins are disabled ({source})")
        async with self._lock(instance_id):
            if self.paused:
                return
            current = self._instances.get(instance_id)
            if current is not None and current.active:
                return
            row = await PluginRepository.get(instance_id)
            if row is None:
                raise PluginHostError("unknown_instance", "no such plugin instance")
            if row.get("status") == "needs_attention" and (row.get("attention") or {}).get(
                "converted_bot"
            ):
                # A converted 4.18.x bot never runs before its grant is accepted (the
                # router clears the attention first); defence in depth for other callers.
                raise PluginHostError("attention_required", "the instance needs a review")
            if not await asyncio.to_thread(self.code_matches, row):
                # Fail closed: never run code under another version's manifest and grants.
                if instance_id not in self._code_mismatch:
                    self._code_mismatch.add(instance_id)
                    _broadcast_status(self.statuses().get(instance_id) or {})
                raise PluginHostError(
                    CODE_MISMATCH, "the installed code is not the version of the instance"
                )
            self._code_mismatch.discard(instance_id)
            running = sum(1 for i in self._instances.values() if i.active)
            cap = self.instance_cap()
            if running >= cap:
                raise PluginCapacityError(cap)
            backend = await self._instance_backend(instance_id)
            instance = PluginInstance(self, row, self.code_dir(row), backend)
            # The pause may have been set during the awaits above (MSHL-341):
            # `pause_all()` only stops what is in `_instances`, so check again with
            # no await between this check and the registration below.
            if self.paused:
                return
            self._instances[instance_id] = instance
            instance.launch()

    async def stop(self, instance_id: str, *, status: str = "disabled") -> None:
        async with self._lock(instance_id):
            instance = self._instances.pop(instance_id, None)
            if instance is None:
                return
            await instance.stop(self.budgets.stop_timeout)
            await instance._set_status(status, None)

    async def restart(self, instance_id: str) -> None:
        """Manual restart: a new supervisor, so the failure count starts again.

        While paused it only stops: the instance starts again on resume."""
        await self.stop(instance_id, status="installed" if self.paused else "starting")
        await self.start(instance_id)

    async def reload(self, instance_id: str) -> None:
        """Apply a changed row: stopped when gone or disabled, restarted when enabled."""
        from app.repository.plugins import PluginRepository

        row = await PluginRepository.get(instance_id)
        if row is None:
            await self.remove(instance_id)
        elif row.get("enabled"):
            await self.restart(instance_id)
        else:
            await self.stop(instance_id)

    async def remove(self, instance_id: str, *, purge: bool = False) -> None:
        """Stop and forget an instance; `purge` (an uninstall) also cleans and frees
        its uid under `setuid`."""
        async with self._lock(instance_id):
            instance = self._instances.pop(instance_id, None)
            if instance is not None:
                await instance.stop(self.budgets.stop_timeout)
            if purge:
                await self._purge_uid(instance_id)
        self._locks.pop(instance_id, None)

    # Per-instance uids (`setuid`, MSHL-339).

    async def _instance_backend(self, instance_id: str) -> isolation.IsolationBackend:
        """The process-wide backend, bound to the instance's own uid under `setuid`
        (allocated on the first start, then kept)."""
        backend = isolation.current().backend
        if not backend.per_instance_uid:
            return backend
        from app.repository.plugins import PluginUidError, PluginUidRepository

        try:
            # `uid_available` also takes the uid's host-wide lease (MSHL-357), and
            # `claim_uid` holds it again for a stored uid: another Meshloom on this
            # kernel never gets it while this install keeps it.
            uid = await PluginUidRepository.allocate(
                instance_id,
                backend.uid_candidates(),
                backend.uid_available,
                release=backend.release_uid,
            )
            await asyncio.to_thread(backend.claim_uid, uid)
        except PluginUidError:
            raise PluginHostError(
                "uid_exhausted", "no free uid left for a plugin instance"
            ) from None
        except (ValueError, OSError) as exc:  # `UidLeaseError`: refused, or not checkable
            from app.plugins.isolation.uid_lease import UidLeaseIOError

            logger.warning("Plugin uid of %s refused: %s", instance_id, exc)
            if isinstance(exc, (UidLeaseIOError, OSError)):
                # ENOSPC, EACCES... on a lease: nobody else is known to hold the uid
                # (MSHL-360).
                raise PluginHostError(
                    "uid_lease_io_error", "the plugin uid lease cannot be written"
                ) from None
            raise PluginHostError(
                "uid_leased", "the plugin uid is held by another Meshloom on this machine"
            ) from None
        return backend.for_uid(uid)

    async def _purge_uid(self, instance_id: str, uid: int | None = None) -> bool:
        """Remove what the instance's uid left, then free the uid. A failed purge
        keeps the uid taken (startup tries again); True when freed."""
        backend = isolation.current().backend
        if not backend.per_instance_uid:
            return True
        from app.repository.plugins import PluginUidRepository

        if uid is None:
            uid = await PluginUidRepository.get(instance_id)
        if uid is None:
            return True
        error = await self._purge(backend, uid)
        if error is not None:
            logger.warning("Plugin uid %s of %s kept: purge failed: %s", uid, instance_id, error)
            return False
        await PluginUidRepository.release(instance_id)
        await asyncio.to_thread(backend.release_uid, uid)
        return True

    async def _purge(self, backend: isolation.IsolationBackend, uid: int) -> Exception | None:
        """`purge()` as `uid`, recording the directories that blocked it (or clearing
        them on success); the error, or None when the uid is clean."""
        try:
            await asyncio.to_thread(backend.for_uid(uid).purge)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # A helper that timed out also keeps the uid taken (fail closed).
            self._set_purge_blocked(uid, getattr(exc, "paths", ()))
            return exc
        self._set_purge_blocked(uid, ())
        return None

    def _set_purge_blocked(self, uid: int, paths: Iterable[str]) -> None:
        before = self.purge_blocked()
        if paths := tuple(paths):
            self._purge_blocked[uid] = paths
        else:
            self._purge_blocked.pop(uid, None)
        if self.purge_blocked() != before:
            _broadcast_health()

    def purge_blocked(self) -> list[str]:
        """The directories (in the shared temp dirs) a uid purge could not list: the
        uids stay taken until an administrator removes or opens them, then restarts."""
        from app.plugins.isolation.setuid import PURGE_BLOCKED_MAX

        return sorted({p for paths in self._purge_blocked.values() for p in paths})[
            :PURGE_BLOCKED_MAX
        ]

    async def reap_uids(self) -> None:
        """Before the first start under `setuid`: purge and free the uids of deleted
        instances, purge the uids of the others that left files (a crash) and the
        shared uid of the releases before per-instance uids."""
        backend = isolation.current().backend
        if not backend.per_instance_uid:
            return
        from app.plugins.isolation import setuid
        from app.repository.plugins import PluginUidRepository

        # The lease directories exist before any child runs (a plugin cannot plant them).
        # One that is not this server's fails closed: every claim and purge below
        # then refuses, and health names it.
        try:
            await asyncio.to_thread(backend.prepare_uid_leases)
        except (ValueError, OSError) as exc:
            logger.warning("Plugin uid leases not usable: %s", exc)
        running = {i for i, instance in self._instances.items() if instance.active}
        orphans = await PluginUidRepository.orphans()
        for instance_id, uid in orphans.items():
            if instance_id not in running:
                await self._purge_uid(instance_id, uid)
        kept = {
            uid
            for instance_id, uid in (await PluginUidRepository.all()).items()
            if instance_id not in orphans and instance_id not in running
        }
        # The account's own uid: every instance ran under it before MSHL-339.
        legacy = getattr(backend, "uid", None)
        refused: set[int] = set()
        for uid in sorted(kept):
            # Hold the lease of every uid this install keeps (written again after a
            # crash or a cleared temp dir); another install's lease keeps it blocked.
            try:
                await asyncio.to_thread(backend.claim_uid, uid)
            except (ValueError, OSError) as exc:  # one uid refused: the others go on
                logger.warning("Plugin uid %s: lease refused: %s", uid, exc)
                self._set_purge_blocked(uid, getattr(exc, "paths", ()))
                refused.add(uid)
        for uid in sorted(kept | ({legacy} if legacy else set())):
            if uid in refused:
                continue
            if not await asyncio.to_thread(setuid.owns_something, uid):
                self._set_purge_blocked(uid, ())
            else:
                error = await self._purge(backend, uid)
                if error is not None:
                    logger.warning("Plugin uid %s: leftover files not removed: %s", uid, error)
                    continue
            if uid == legacy and uid not in kept:
                # No row holds the account's uid: its lease (taken by the purge) goes.
                await asyncio.to_thread(backend.release_uid, uid)

    async def start_all(self) -> None:
        """Start every enabled instance not held by a sticky status, staggered."""
        await self.load_from_db()

    async def pause_all(self) -> None:
        """The global pause: stop every running instance cleanly (`plugin.shutdown`,
        a bounded wait, then SIGKILL), concurrently, and start none until
        `resume_all()`. The `enabled` flags stay as they are; an instance held by
        a sticky status (`crash_loop`...) is left alone. Idempotent."""
        self.paused = True
        if self._load_task is not None:
            self._load_task.cancel()
            await asyncio.gather(self._load_task, return_exceptions=True)
            self._load_task = None
        running = [i for i, instance in self._instances.items() if instance.active]
        results = await asyncio.gather(
            *(self.stop(i, status="installed") for i in running), return_exceptions=True
        )
        for instance_id, result in zip(running, results, strict=True):
            if isinstance(result, BaseException):
                logger.error("Pausing plugin instance %s failed", instance_id, exc_info=result)
        self._announce_all()

    async def resume_all(self) -> None:
        """Lift the global pause: start exactly the enabled instances, 500 ms apart."""
        if not self.paused:
            return
        self.paused = False
        self._announce_all()
        await self.load_from_db()

    def _announce_all(self) -> None:
        """`plugin_status` for every stopped instance (paused or back to its stored status)."""
        for instance_id, entry in self.statuses().items():
            if instance_id not in self._instances:
                _broadcast_status(entry)

    async def stop_all(self, timeout: float = 10.0) -> None:
        """Stop every instance: `plugin.shutdown`, a bounded wait, then SIGKILL; never over `timeout`."""
        if self._load_task is not None:
            self._load_task.cancel()
            await asyncio.gather(self._load_task, return_exceptions=True)
            self._load_task = None
        instances = list(self._instances.values())
        self._instances.clear()
        if not instances:
            return
        budget = max(0.5, min(self.budgets.stop_timeout, timeout) - 1.0)
        stops = [asyncio.create_task(i.stop(budget)) for i in instances]
        done, pending = await asyncio.wait(stops, timeout=budget + 0.5)
        failed = [t for t in done if not t.cancelled() and t.exception() is not None]
        for task in failed:
            logger.error("Stopping a plugin instance failed", exc_info=task.exception())
        if pending or failed:
            # Whatever did not stop cleanly is killed, its task cancelled and reaped.
            for instance in instances:
                instance.kill_now()
                if instance._task is not None and not instance._task.done():
                    instance._task.cancel()
            for task in pending:
                task.cancel()
            leftovers = [*pending, *(i._task for i in instances if i._task is not None)]
            await asyncio.wait([t for t in leftovers if not t.done()] or stops, timeout=0.5)

    def _lock(self, instance_id: str) -> asyncio.Lock:
        return self._locks.setdefault(instance_id, asyncio.Lock())

    # Reading.

    def get(self, instance_id: str) -> PluginInstance | None:
        return self._instances.get(instance_id)

    def statuses(self) -> dict[str, dict[str, Any]]:
        """Every known instance: live state for running ones, the stored row for others."""
        from app.repository.plugins import _instances_cache

        result: dict[str, dict[str, Any]] = {}
        for instance_id, row in list(_instances_cache.items()):
            status = (
                row.get("status")
                if row.get("enabled") or row.get("status") in ATTENTION_STATUSES
                else "disabled"
            )
            last_error = row.get("last_error")
            if self.paused and row.get("enabled") and status not in STICKY_STATUSES:
                status = "paused"
            elif instance_id in self._code_mismatch and row.get("enabled"):
                status, last_error = "error", CODE_MISMATCH
            result[instance_id] = {
                "instance_id": instance_id,
                "plugin_id": row.get("plugin_id"),
                "label": row.get("label"),
                "status": status,
                "last_error": last_error,
                "dropped_events": 0,
                "restarts": 0,
                "summary": None,
                "send_guard": send_guard.hits(instance_id),
                "attention": row.get("attention"),
            }
        for instance_id, instance in self._instances.items():
            result[instance_id] = instance.snapshot()
        return result

    def logs(self, instance_id: str) -> list[str]:
        instance = self._instances.get(instance_id)
        return instance.log.lines() if instance is not None else []

    # Dispatch.

    def dispatch(self, hook: str, data: dict[str, Any]) -> None:
        """Queue a fanout event for every running instance that subscribes `hook`
        and holds its permission (`capabilities/grants.py`).

        Never blocks. `event.message` keeps the bots' 2 s settle (echo dedup),
        carries the bots' fields (`app/plugins/events.py`) and is filtered per
        instance (`messages.read`, then the user's scope); a message sent by a
        plugin instance is marked so that instance sees `own_send`. A raw packet
        keeps its decryption only for an instance that may read that message.
        Dropped, never queued, while the global pause holds.
        """
        if self.paused:
            return
        targets = [
            i
            for i in self._instances.values()
            if hook in i.hooks and i.status == "running" and self.accepts(i, hook, data)
        ]
        if not targets:
            return
        if hook == "message":
            from app.background_tasks import spawn
            from app.plugins.capabilities.messages import SENDING_INSTANCE

            spawn(self._enqueue_message(targets, data, SENDING_INSTANCE.get()))
            return
        from app.plugins.capabilities.grants import raw_packet_for

        for instance in targets:
            if hook == "raw_packet":
                instance.enqueue(
                    _Event(hook, raw_packet_for(instance.permissions, instance.scope, data))
                )
            else:
                instance.enqueue(_Event(hook, data))

    async def _enqueue_message(
        self, targets: list[PluginInstance], data: dict[str, Any], origin: str | None = None
    ) -> None:
        from app.plugins.capabilities.grants import message_visible
        from app.plugins.events import message_event_params

        if self.budgets.message_settle > 0:
            await asyncio.sleep(self.budgets.message_settle)
        if self.paused:
            return
        params = await message_event_params(data)
        for instance in targets:
            if not message_visible(instance.permissions, instance.scope, params):
                continue
            own = params["is_outgoing"] and origin == instance.instance_id
            send_guard.note_inbound(instance.instance_id, params)
            instance.enqueue(_Event("message", {**params, "own_send": True} if own else params))

    def accepts(self, instance: PluginInstance, hook: str, data: dict[str, Any]) -> bool:
        """The permission gate of a hook (`message` is filtered again per message)."""
        from app.plugins.capabilities.grants import hook_allowed

        return hook_allowed(instance.permissions, instance.scope, hook)

    async def dispatch_test(
        self, instance_id: str, hook: str, data: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        """Send one `test: true` event; returns `{"result", "sends"}` (sends captured, never sent)."""
        if self.paused:
            raise PluginHostError("plugins_paused", "all plugins are paused")
        instance = self._instances.get(instance_id)
        if instance is None or instance.status != "running":
            raise PluginHostError("instance_not_running", "the instance is not running")
        if hook not in HOOKS:
            raise PluginHostError("unknown_hook", f"unknown hook {hook}")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        instance.enqueue(_Event(hook, data, test=True, result=future))
        budget = self.budgets.event_timeout + self.budgets.cancel_grace + 1.0
        return await asyncio.wait_for(future, timeout or budget)

    # CPU budget.

    def _watch_cpu(self, process: PluginProcess) -> asyncio.Task[None] | None:
        if self._cpu_reader is None or process.pid is None:
            return None
        return asyncio.create_task(self._cpu_loop(process, self._cpu_reader))

    async def _cpu_loop(
        self, process: PluginProcess, reader: Callable[[int], float | None]
    ) -> None:
        budgets = self.budgets
        samples: deque[tuple[float, float]] = deque()
        pid = process.pid
        assert pid is not None
        while process.alive:
            cpu = reader(pid)
            if cpu is None:
                return
            now = self._clock()
            samples.append((now, cpu))
            while len(samples) > 1 and samples[1][0] <= now - budgets.cpu_window:
                samples.popleft()
            used = cpu - samples[0][1]
            if used > budgets.cpu_budget:
                process.kill(
                    f"cpu_budget: CPU budget exceeded: {used:.1f} s of CPU in {budgets.cpu_window:g} s "
                    f"(budget {budgets.cpu_budget:g} s)"
                )
                return
            await asyncio.sleep(budgets.cpu_sample_interval)

    # Status fan-out.

    def announce(self, instance_id: str) -> None:
        """Send `plugin_status` for an instance whose row the API just changed
        (the supervisor announces its own transitions)."""
        entry = self.statuses().get(instance_id)
        if entry is not None:
            _broadcast_status(entry)

    async def _status_changed(self, instance: PluginInstance) -> None:
        from app.repository.plugins import PluginRepository

        try:
            await PluginRepository.update(
                instance.instance_id, status=instance.status, last_error=instance.last_error
            )
        except Exception:
            logger.debug("Could not store the status of plugin %s", instance.instance_id)
        _broadcast_status(instance.snapshot())


def _broadcast_status(payload: dict[str, Any]) -> None:
    """`plugin_status` to the WebSocket clients, plus a health refresh (the fanout path)."""
    try:
        from app.websocket import broadcast_event

        broadcast_event(WsEvent.PLUGIN_STATUS, payload, realtime=False)
    except Exception:
        logger.debug("plugin_status broadcast failed", exc_info=True)
    _broadcast_health()


def _broadcast_health() -> None:
    try:
        from app.services.radio_runtime import radio_runtime
        from app.websocket import broadcast_health

        broadcast_health(radio_runtime.is_connected, radio_runtime.connection_info)
    except Exception:
        logger.debug("health broadcast failed", exc_info=True)


plugin_manager = PluginManager()
