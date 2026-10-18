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
  on Linux; the runner's hard `RLIMIT_CPU` (3600 s) is the portable backstop.
- The number of running instances is capped from the machine's memory
  (`instance_cap()`), and the instances of `load_from_db()` start 500 ms apart.

Isolation: the OS backend (MSHL-180) is not there yet, so every child runs at
level `none` (the runner's hooks only); `isolation_level()` and `_spawn_argv()`
are the seam it replaces.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import itertools
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.plugins import launch
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
]
DisabledSource = Literal["env", "platform"]

HOOKS = ("message", "raw_packet", "contact", "telemetry", "health", "timer")
# Persisted statuses that keep an enabled instance stopped at start-up until
# someone acts (a manual restart, a review, an update).
STICKY_STATUSES = frozenset({"crash_loop", "needs_attention", "incompatible", "revoked"})
API_VERSION = 1

MEM_PER_INSTANCE = 128 * 1024 * 1024
INSTANCE_CAP_MIN = 3
INSTANCE_CAP_MAX = 20

_DROP_LOG_INTERVAL = 10.0


@dataclass(frozen=True)
class Budgets:
    """Every time and size bound of the supervisor (seconds, events, bytes)."""

    init_timeout: float = 20.0
    event_timeout: float = 30.0
    cancel_grace: float = 5.0
    shutdown_grace: float = 5.0
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


class HostMethodError(Exception):
    """Raised by a host method: answered to the child as a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


# --- platform -------------------------------------------------------------------


def platform_supported() -> bool:
    """The runner needs `resource` (rlimits): no `resource`, no plugins (Windows)."""
    return importlib.util.find_spec("resource") is not None


def isolation_level() -> str:
    """The OS isolation every child gets. `none` until the backend of MSHL-180 lands."""
    return "none"


def _spawn_argv() -> list[str]:
    return launch.runner_command()


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


def default_cpu_reader() -> Callable[[int], float | None] | None:
    return read_proc_cpu_seconds if sys.platform.startswith("linux") else None


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


class PluginProcess:
    """One child: spawn, JSON-RPC both ways, stderr capture, kill of its process group."""

    def __init__(self, plugin_dir: Path, log: LogRing, on_request: RequestHandler):
        self._plugin_dir = plugin_dir
        self._log = log
        self._on_request = on_request
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
        self.work_dir = launch.prepare_work_dir(Path(tempfile.mkdtemp(prefix="meshloom-plugin-")))
        self._proc = await asyncio.create_subprocess_exec(
            *_spawn_argv(),
            stderr=asyncio.subprocess.PIPE,
            **launch.popen_kwargs(self.work_dir),
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

    def kill(self, reason: str) -> None:
        """SIGKILL the child's process group (its own session). The first reason wins."""
        if self._proc is None or self._proc.returncode is not None:
            return
        if self.kill_reason is None:
            self.kill_reason = reason
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self._proc.pid, signal.SIGKILL)

    def exit_reason(self) -> str:
        if self.kill_reason is not None:
            return self.kill_reason
        code = self.returncode
        if code is None:
            return "process still running"
        if code < 0:
            sig = -code
            if sig == getattr(signal, "SIGXCPU", None):
                return "CPU time limit reached (RLIMIT_CPU)"
            with contextlib.suppress(ValueError):
                return f"killed by {signal.Signals(sig).name}"
            return f"killed by signal {sig}"
        if code == 3 and self._log.runner_error is not None:
            err = self._log.runner_error
            return f"start refused ({err['code']}): {err['message']}"
        if code == 2:
            return "protocol violation (exit 2)"
        return f"exited unexpectedly (code {code})"

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

    async def notify(self, method: str, params: Any = None) -> None:
        await self._write(rpc.encode(rpc.notification(method, params)))

    async def _write(self, data: bytes) -> None:
        if not self.alive or self._proc is None or self._proc.stdin is None:
            raise ProcessEnded()
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
                self.kill(f"protocol violation: {exc}")
                return
            for line in lines:
                if line.strip():
                    self._handle_line(line)

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
        await self._proc.wait()
        # Let the readers drain what the child wrote last (its runner_error line).
        await asyncio.wait(self._readers[:2], timeout=1.0)
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
            return  # child notifications carry nothing the host acts on yet
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
            data = rpc.encode(response)
        except ValueError:
            data = rpc.encode(rpc.error(msg_id, rpc.INTERNAL_ERROR, "result too large"))
        with contextlib.suppress(ProcessEnded):
            await self._write(data)

    async def shutdown(self, grace: float) -> None:
        """`plugin.shutdown`, then wait up to `grace` for the exit, then kill."""
        if self.alive:
            with contextlib.suppress(ProcessEnded):
                await asyncio.wait_for(self.notify("plugin.shutdown"), grace)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._exited.wait(), grace)
        await self.close("did not stop within the shutdown grace")

    async def close(self, reason: str = "stopped") -> None:
        """Kill if still running, reap, stop the readers, remove the work directory."""
        if self._proc is not None:
            if self._proc.returncode is None:
                self.kill(reason)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._proc.wait(), 5.0)
        for task in [*self._readers, *self._tasks]:
            task.cancel()
        await asyncio.gather(*self._readers, *self._tasks, return_exceptions=True)
        self._exited.set()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(ProcessEnded())
        if self.work_dir is not None:
            shutil.rmtree(self.work_dir, ignore_errors=True)


# --- one instance -----------------------------------------------------------------


@dataclass
class _Event:
    hook: str
    params: dict[str, Any]
    test: bool = False
    result: asyncio.Future[Any] | None = None
    sends: list[dict[str, Any]] = field(default_factory=list)


class PluginInstance:
    """Supervises one instance: its current child, inbox, status and counters."""

    def __init__(self, manager: PluginManager, row: dict[str, Any], plugin_dir: Path):
        self._manager = manager
        self.budgets = manager.budgets
        self.instance_id: str = row["instance_id"]
        self.plugin_id: str = row["plugin_id"]
        self.label: str = row.get("label") or row["plugin_id"]
        manifest = row.get("manifest") or {}
        self.hooks = frozenset(h for h in manifest.get("hooks", []) if h in HOOKS)
        self.plugin_dir = plugin_dir
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
        self._handled_ok = False
        self._secrets: list[str] = []
        self._last_drop_log = -_DROP_LOG_INTERVAL
        self._drops_since_log = 0

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

    async def stop(self, grace: float) -> None:
        self._stopping = True
        self._inbox_ready.set()
        process = self._process
        if process is not None:
            await process.shutdown(grace)
        if self._task is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._task), grace)
            if not self._task.done():
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
        self._drop_all("instance stopped")

    def kill_now(self) -> None:
        if self._process is not None:
            self._process.kill("server shutting down")

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
        }

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
            await asyncio.sleep(self.budgets.backoff(self.consecutive_failures))
            self.restarts += 1

    async def _run_once(self) -> str | None:
        """One child lifetime; returns why it ended, None after a requested stop."""
        process = PluginProcess(self.plugin_dir, self.log, self._on_child_request)
        self._process = process
        cpu_task: asyncio.Task[None] | None = None
        try:
            try:
                init_params = await self._init_params()
                await process.start(self._params_line)
            except (OSError, ValueError) as exc:
                return f"cannot start the plugin process: {type(exc).__name__}: {exc}"
            except ProcessEnded:
                return process.exit_reason()
            if self._stopping:
                process.kill("stopped")
            try:
                await process.call("plugin.init", init_params, self.budgets.init_timeout)
            except TimeoutError:
                process.kill(f"plugin.init did not answer within {self.budgets.init_timeout:g} s")
            except RpcError as exc:
                process.kill(f"plugin.init failed: {exc}")
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
        return {
            "api": API_VERSION,
            "instance_id": self.instance_id,
            "plugin_id": self.plugin_id,
            "label": self.label,
            "config": config,
            "scope": row.get("scope") or {},
            "permissions": row.get("granted_permissions") or {},
            "quotas": row.get("quotas") or {},
            "isolation": isolation_level(),
        }

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
                value = await asyncio.wait_for(asyncio.shield(future), self.budgets.event_timeout)
            except TimeoutError:
                with contextlib.suppress(ProcessEnded):
                    await process.notify("event.cancel", {"id": msg_id})
                try:
                    await asyncio.wait_for(future, self.budgets.cancel_grace)
                except TimeoutError:
                    process.kill(
                        f"{method} ran over {self.budgets.event_timeout:g} s and ignored "
                        "event.cancel"
                    )
                    self._drop([event], "process killed")
                    return
                except RpcError:
                    pass
                await self._set_error(f"{method} timed out after {self.budgets.event_timeout:g} s")
                self._finish(event, error=PluginHostError("event_timeout", "the event timed out"))
                return
        except ProcessEnded:
            self._drop([event], "process ended")
            return
        except RpcError as exc:
            await self._set_error(f"{method}: {exc}")
            self._finish(event, error=PluginHostError("handler_error", str(exc)))
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
            event.result.set_result({"result": value, "sends": event.sends})

    async def _on_child_request(self, method: str, params: Any) -> Any:
        event_id = params.get("event_id") if isinstance(params, dict) else None
        event = self._in_flight.get(event_id) if isinstance(event_id, int) else None
        if method == "messages.send" and event is not None and event.test:
            # A test event's sends are captured for the caller, never sent.
            assert isinstance(params, dict)
            event.sends.append({k: v for k, v in params.items() if k != "event_id"})
            return {"captured": True}
        handler = self._manager.host_methods.get(method)
        if handler is None:
            raise HostMethodError(rpc.METHOD_NOT_FOUND, f"{method} is not available")
        return await handler(self, params)

    # Counters and statuses.

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
        error = self._scrub(error)
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
        self._cpu_reader = default_cpu_reader() if cpu_reader == "default" else cpu_reader
        self._clock = clock
        self._mem_total = mem_total
        self._instances: dict[str, PluginInstance] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._load_task: asyncio.Task[None] | None = None
        # Host methods a child may call (`messages.send`, `storage.*`...), registered
        # by the capability layer (MSHL-181 and later). Unknown: -32601.
        self.host_methods: dict[str, HostMethod] = {}

    # Gates.

    def disabled_source(self) -> DisabledSource | None:
        """Why the plugin system is off: the host lock, then the platform; None when on."""
        from app.config import settings

        if settings.disable_plugins:
            return "env"
        if not platform_supported():
            return "platform"
        return None

    def instance_cap(self) -> int:
        return instance_cap(self._mem_total())

    def plugins_root(self) -> Path:
        if self._plugins_root is not None:
            return self._plugins_root
        from app.config import settings

        return Path(settings.database_path).resolve().parent / "plugins"

    def code_dir(self, row: dict[str, Any]) -> Path:
        """The plugin's code: shared per `plugin_id`; a local plugin's source is written out."""
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
        target.write_text(row.get("source") or "", encoding="utf-8")
        return directory.resolve()

    # Lifecycle.

    async def load_from_db(self) -> None:
        """Start every enabled instance not held by a sticky status, 500 ms apart.

        Returns at once: the staggered starts run in a task kept here and
        cancelled by `stop_all()`.
        """
        from app.repository.plugins import PluginRepository

        if self.disabled_source() is not None:
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
        """Start one instance (no-op when it runs). Raises `PluginHostError`."""
        from app.repository.plugins import PluginRepository

        source = self.disabled_source()
        if source is not None:
            raise PluginHostError("plugins_disabled", f"plugins are disabled ({source})")
        async with self._lock(instance_id):
            current = self._instances.get(instance_id)
            if current is not None and current.active:
                return
            row = await PluginRepository.get(instance_id)
            if row is None:
                raise PluginHostError("unknown_instance", "no such plugin instance")
            running = sum(1 for i in self._instances.values() if i.active)
            cap = self.instance_cap()
            if running >= cap:
                raise PluginCapacityError(cap)
            instance = PluginInstance(self, row, self.code_dir(row))
            self._instances[instance_id] = instance
            instance.launch()

    async def stop(self, instance_id: str, *, status: str = "disabled") -> None:
        async with self._lock(instance_id):
            instance = self._instances.pop(instance_id, None)
            if instance is None:
                return
            await instance.stop(self.budgets.shutdown_grace)
            await instance._set_status(status, None)

    async def restart(self, instance_id: str) -> None:
        """Manual restart: a new supervisor, so the failure count starts again."""
        await self.stop(instance_id, status="starting")
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

    async def remove(self, instance_id: str) -> None:
        async with self._lock(instance_id):
            instance = self._instances.pop(instance_id, None)
            if instance is not None:
                await instance.stop(self.budgets.shutdown_grace)
        self._locks.pop(instance_id, None)

    async def start_all(self) -> None:
        """Start every enabled instance (the seam of the global pause, MSHL T43)."""
        await self.load_from_db()

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
        grace = min(self.budgets.shutdown_grace, timeout / 2)
        stops = [asyncio.create_task(i.stop(grace)) for i in instances]
        _done, pending = await asyncio.wait(stops, timeout=timeout - 1.0)
        if pending:
            for instance in instances:
                instance.kill_now()
            await asyncio.wait(pending, timeout=1.0)
            for task in pending:
                task.cancel()

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
            result[instance_id] = {
                "instance_id": instance_id,
                "plugin_id": row.get("plugin_id"),
                "label": row.get("label"),
                "status": row.get("status") if row.get("enabled") else "disabled",
                "last_error": row.get("last_error"),
                "dropped_events": 0,
                "restarts": 0,
            }
        for instance_id, instance in self._instances.items():
            result[instance_id] = instance.snapshot()
        return result

    def logs(self, instance_id: str) -> list[str]:
        instance = self._instances.get(instance_id)
        return instance.log.lines() if instance is not None else []

    # Dispatch.

    def dispatch(self, hook: str, data: dict[str, Any]) -> None:
        """Queue a fanout event for every running instance that subscribes `hook`.

        Never blocks. `event.message` keeps the bots' 2 s settle (echo dedup).
        """
        targets = [
            i
            for i in self._instances.values()
            if hook in i.hooks and i.status == "running" and self.accepts(i, hook, data)
        ]
        if not targets:
            return
        if hook == "message" and self.budgets.message_settle > 0:
            from app.background_tasks import spawn

            spawn(self._settle_then_enqueue(targets, hook, data))
            return
        for instance in targets:
            instance.enqueue(_Event(hook, data))

    async def _settle_then_enqueue(
        self, targets: list[PluginInstance], hook: str, data: dict[str, Any]
    ) -> None:
        await asyncio.sleep(self.budgets.message_settle)
        for instance in targets:
            instance.enqueue(_Event(hook, data))

    def accepts(self, instance: PluginInstance, hook: str, data: dict[str, Any]) -> bool:
        """Permission and scope filter: the seam MSHL-181 (T6) fills. Everything passes."""
        return True

    async def dispatch_test(
        self, instance_id: str, hook: str, data: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        """Send one `test: true` event; returns `{"result", "sends"}` (sends captured, never sent)."""
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
                    f"CPU budget exceeded: {used:.1f} s of CPU in {budgets.cpu_window:g} s "
                    f"(budget {budgets.cpu_budget:g} s)"
                )
                return
            await asyncio.sleep(budgets.cpu_sample_interval)

    # Status fan-out.

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
        from app.services.radio_runtime import radio_runtime
        from app.websocket import broadcast_event, broadcast_health

        broadcast_event("plugin_status", payload, realtime=False)
        broadcast_health(radio_runtime.is_connected, radio_runtime.connection_info)
    except Exception:
        logger.debug("plugin_status broadcast failed", exc_info=True)


plugin_manager = PluginManager()
