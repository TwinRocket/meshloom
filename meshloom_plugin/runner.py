"""Child runtime of one plugin instance: freeze the process, then serve JSON-RPC.

Launched by `app/plugins/launch.py` as `python -I -S -B -X utf8 -c <bootstrap>`;
the bootstrap sets `sys.path = [app_root] + stdlib` and runs this module as
`__main__`. Stages, in order (docs/dev/plugins.md, "Runner"):

1. Read the parameters line from stdin, close inherited descriptors >= 3.
2. Keep private duplicates of the RPC descriptors; fd 0 becomes /dev/null and
   fd 1 (and `sys.stdout`) becomes stderr, so a `print()` never reaches the RPC
   stream.
3. Scrub `os.environ`, set hard rlimits, a small thread stack size and
   `PR_SET_NO_NEW_PRIVS` (Linux), then import `asyncio`: it loads `socket`,
   `ssl` and `subprocess` at module level, which is why it comes before the
   purge rather than after it.
4. Install the audit hook (`_HOOK_SOURCE`): no reachable mutable state.
5. Purge `sys.modules` and scrub the references other modules hold to the
   denied ones, then install the import guard (a `sys.meta_path` finder and a
   `builtins.__import__` wrapper).
6. `sys.path = [plugin_dir, plugin_dir/vendor, stdlib]`, load `entry`, serve on
   an event loop whose self-pipe is an `os.pipe`, not a socket pair.

None of this is the security boundary: the OS isolation is
(docs/dev/plugins-security.md).
"""

import sys

# Everything loaded before this module ran (interpreter start-up, runpy, this
# package): the purge keeps these, it removes what the runner pulls in below.
_BOOT_MODULES = frozenset(sys.modules)

import importlib  # noqa: E402
import importlib.machinery  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import traceback  # noqa: E402
from typing import Any  # noqa: E402

import meshloom_plugin  # noqa: E402
from meshloom_plugin import policy, rpc  # noqa: E402

# Loaded at interpreter start-up (or by runpy) but not for plugins: purged too.
# `importlib._bootstrap*` are aliases of the frozen import machinery and stay.
_BOOT_MODULES_PURGED = ("importlib", "pkgutil", "runpy", "zipimport")
_BOOT_MODULES_KEPT = ("importlib._bootstrap", "importlib._bootstrap_external")

PARAMS_MAX_BYTES = 64 * 1024
HOOK_NAMES = ("message", "raw_packet", "contact", "telemetry", "health", "timer")
MAX_PENDING = 128
THREAD_STACK_SIZE = 256 * 1024
PR_SET_NO_NEW_PRIVS = 38

EXIT_START_FAILED = 3
EXIT_PROTOCOL = 2


class RunnerStartError(Exception):
    """The runner refuses to start. `code` is reported on stderr as a typed line."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _report_start_error(exc: RunnerStartError) -> None:
    line = json.dumps({"runner_error": exc.code, "message": str(exc)})
    os.write(2, line.encode("utf-8") + b"\n")


# --- stage 1: parameters and descriptors -------------------------------------


def _close_inherited_fds() -> None:
    try:
        limit = os.sysconf("SC_OPEN_MAX")
    except (ValueError, OSError):
        limit = 1024
    os.closerange(3, min(max(limit, 256), 1 << 20))


def _read_params() -> tuple[dict[str, Any], bytes]:
    """Read the first stdin line with raw reads; what follows it belongs to the RPC stream."""
    buf = bytearray()
    while b"\n" not in buf:
        if len(buf) > PARAMS_MAX_BYTES:
            raise RunnerStartError("params_invalid", "parameters line too long")
        chunk = os.read(0, 4096)
        if not chunk:
            raise RunnerStartError("params_invalid", "stdin closed before the parameters")
        buf += chunk
    line, _, rest = bytes(buf).partition(b"\n")
    try:
        params = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RunnerStartError("params_invalid", f"parameters are not JSON: {exc}") from None
    if not isinstance(params, dict):
        raise RunnerStartError("params_invalid", "parameters must be a JSON object")
    plugin_dir = params.get("plugin_dir")
    if not isinstance(plugin_dir, str) or not os.path.isabs(plugin_dir):
        raise RunnerStartError("params_invalid", "plugin_dir must be an absolute path")
    if not os.path.isdir(plugin_dir):
        raise RunnerStartError("params_invalid", "plugin_dir does not exist")
    for key in ("instance_id", "isolation"):
        if not isinstance(params.get(key), str):
            raise RunnerStartError("params_invalid", f"{key} must be a string")
    limits = params.get("limits", {})
    if not isinstance(limits, dict) or not all(
        k in policy.DEFAULT_LIMITS and type(v) is int and v > 0 for k, v in limits.items()
    ):
        raise RunnerStartError("params_invalid", "limits must map known names to positive ints")
    return params, rest


def _take_rpc_fds() -> tuple[int, int]:
    rpc_in = os.dup(0)
    rpc_out = os.dup(1)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(2, 1)
    os.close(devnull)
    sys.stdout = sys.stderr
    return rpc_in, rpc_out


# --- stage 3: environment, limits, runtime imports ----------------------------


def _scrub_environ() -> None:
    for key in list(os.environ):
        if key not in policy.ENV_KEYS:
            del os.environ[key]


def _apply_limits(params: dict[str, Any]) -> None:
    try:
        import resource
    except ImportError:
        raise RunnerStartError(
            "platform_unsupported", "the resource module is missing (Windows is not supported)"
        ) from None
    limits = {**policy.DEFAULT_LIMITS, **params.get("limits", {})}
    wanted = [
        (resource.RLIMIT_NOFILE, limits["open_files"]),
        (resource.RLIMIT_FSIZE, limits["file_size"]),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_CPU, limits["cpu_seconds"]),
    ]
    # macOS reserves gigabytes of address space at start-up and does not enforce
    # RLIMIT_AS; the limit only means something on Linux.
    if sys.platform.startswith("linux"):
        wanted.append((resource.RLIMIT_AS, limits["address_space"]))
    if params["isolation"] == "setuid":
        wanted.append((resource.RLIMIT_NPROC, limits["processes"]))
    for which, value in wanted:
        _soft, hard = resource.getrlimit(which)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        try:
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError) as exc:
            raise RunnerStartError("limits_failed", f"setrlimit({which}): {exc}") from None

    import _thread

    _thread.stack_size(THREAD_STACK_SIZE)

    if sys.platform.startswith("linux"):
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise RunnerStartError(
                "limits_failed", f"PR_SET_NO_NEW_PRIVS: errno {ctypes.get_errno()}"
            )


# --- stage 4: the audit hook ----------------------------------------------------

# Compiled once per child with its roots baked in as constants. The hook reads no
# global and no builtin at call time (everything it needs is a constant or a
# default argument), and the function's globals dict is empty: the frame a
# traceback exposes carries no mutable state (critique-1 §1.2, third bypass).
_HOOK_SOURCE = """
def hook(event, args, _str=str, _bytes=bytes, _int=int, _type=type, _error=PermissionError):
    if event in {denied_events!r} or event.startswith({denied_prefixes!r}):
        raise _error("plugin sandbox: " + event + " is not allowed")
    if event == "import":
        name = args[0]
        if _type(name) is not _str:
            return
        for denied in {denied_modules!r}:
            if name == denied or name.startswith(denied + "."):
                raise _error("plugin sandbox: importing " + name + " is not allowed")
        return
    if event == "object.__setattr__":
        if args[1] in ("__code__", "__class__"):
            raise _error("plugin sandbox: setting " + _str(args[1]) + " is not allowed")
        return
    if event == "open":
        mode = args[1]
        flags = args[2]
        write = (_type(flags) is _int and flags & {write_flags!r} != 0) or (
            _type(mode) is _str and ("w" in mode or "a" in mode or "x" in mode or "+" in mode)
        )
        paths = (args[0],)
    elif event in ("os.listdir", "os.scandir"):
        write = False
        paths = (args[0],)
    elif event in {mutation_events!r}:
        write = True
        paths = (args[0],)
    elif event == "os.rename":
        write = True
        paths = (args[0], args[1])
    else:
        return
    for path in paths:
        if _type(path) is _int:
            continue
        if _type(path) is _bytes:
            path = path.decode("utf-8", "surrogateescape")
        if _type(path) is not _str or not path.startswith("/") or "\\x00" in path:
            raise _error("plugin sandbox: only absolute paths are allowed")
        if "/../" in path + "/" or "/./" in path + "/":
            raise _error("plugin sandbox: path segments . and .. are not allowed")
        if write:
            roots = {write_roots!r}
            if path.endswith({native_suffixes!r}):
                raise _error("plugin sandbox: writing native code is not allowed")
        else:
            roots = {read_roots!r}
            if "/site-packages/" in path or "/dist-packages/" in path:
                raise _error("plugin sandbox: " + path + " is outside the plugin's reach")
        allowed = False
        for root in roots:
            if path == root or path.startswith(root + "/"):
                allowed = True
                break
        if not allowed:
            raise _error("plugin sandbox: " + path + " is outside the plugin's reach")
"""

_DENIED_EVENTS = frozenset(
    {
        "_thread.start_joinable_thread",
        "_thread.start_new_thread",
        "gc.get_objects",
        "gc.get_referents",
        "gc.get_referrers",
        "os.chdir",
        "os.chflags",
        "os.chown",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.killpg",
        "os.lchflags",
        "os.link",
        "os.posix_spawn",
        "os.putenv",
        "os.removexattr",
        "os.setxattr",
        "os.spawn",
        "os.symlink",
        "os.system",
        "os.unsetenv",
        "pty.spawn",
        "resource.prlimit",
        "resource.setrlimit",
        "signal.pthread_kill",
        "sqlite3.connect",
        "sqlite3.connect/handle",
        "sqlite3.enable_load_extension",
        "sqlite3.load_extension",
        "subprocess.Popen",
        "sys.addaudithook",
        "urllib.Request",
        "webbrowser.open",
    }
)
_DENIED_EVENT_PREFIXES = (
    "socket.",
    "ctypes.",
    "http.client.",
    "ftplib.",
    "smtplib.",
    "imaplib.",
    "poplib.",
    "nntplib.",
    "telnetlib.",
)
_MUTATION_EVENTS = frozenset(
    {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime"}
)


def _roots(*paths: str) -> tuple[str, ...]:
    out: list[str] = []
    for path in paths:
        for candidate in (os.path.normpath(path), os.path.realpath(path)):
            if candidate not in out and candidate != "/":
                out.append(candidate)
    return tuple(out)


def _build_hook(read_roots: tuple[str, ...], write_roots: tuple[str, ...]) -> Any:
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    source = _HOOK_SOURCE.format(
        # Set displays compile to frozenset constants; a frozenset repr would be a call.
        denied_events=set(_DENIED_EVENTS),
        denied_prefixes=_DENIED_EVENT_PREFIXES,
        denied_modules=tuple(sorted(policy.DENIED_MODULES)),
        mutation_events=set(_MUTATION_EVENTS),
        write_flags=write_flags,
        read_roots=read_roots,
        write_roots=write_roots,
        native_suffixes=tuple(importlib.machinery.EXTENSION_SUFFIXES) + (".dylib", ".dll"),
    )
    builtins = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    namespace: dict[str, Any] = {
        "__builtins__": {
            "str": builtins["str"],
            "bytes": builtins["bytes"],
            "int": builtins["int"],
            "type": builtins["type"],
            "PermissionError": builtins["PermissionError"],
        }
    }
    exec(compile(source, "<meshloom-audit-hook>", "exec"), namespace)
    hook = namespace["hook"]
    # The defaults are bound; the hook needs neither globals nor builtins any more.
    namespace.clear()
    hook.__builtins__.clear()
    return hook


def _install_audit_hook(read_roots: tuple[str, ...], write_roots: tuple[str, ...]) -> None:
    try:
        sys.addaudithook(_build_hook(read_roots, write_roots))
    except Exception as exc:  # noqa: BLE001 - any failure means no hook: refuse to start
        raise RunnerStartError(
            "audit_hook_failed", f"cannot install the audit hook: {exc}"
        ) from None


# --- stage 5: purge and import guard -------------------------------------------


def _purge_modules() -> None:
    import types

    class _Refused(types.ModuleType):
        def __getattr__(self, name: str) -> Any:
            raise PermissionError(f"plugin sandbox: {self.__name__} is not available")

    stdlib_names = frozenset(sys.stdlib_module_names)
    removed: dict[int, types.ModuleType] = {}
    dropped: list[types.ModuleType] = []
    for name in list(sys.modules):
        if name == "__main__" or name in _BOOT_MODULES_KEPT:
            continue
        top = name.split(".")[0]
        if name in _BOOT_MODULES and top not in _BOOT_MODULES_PURGED:
            continue
        denied = policy.is_denied(name)
        if denied or not policy.plugin_may_import(name, stdlib_names):
            module = sys.modules.pop(name)
            if module is None:
                continue
            if denied:
                removed[id(module)] = module
            else:
                dropped.append(module)
    # Purged modules stay alive through the references other modules hold
    # (`asyncio.base_events.socket`): replace those with a stub that refuses.
    stubs = {key: _Refused(module.__name__) for key, module in removed.items()}
    for module in [*sys.modules.values(), *dropped]:
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        for attr, value in list(namespace.items()):
            stub = stubs.get(id(value))
            if stub is not None and value is removed[id(value)]:
                namespace[attr] = stub
    for module in removed.values():
        module.__dict__.clear()


class _ImportGuard:
    """Import checks: denied names for everyone, the allowlist for plugin code.

    Two seams. The `sys.meta_path` finder sees modules that are not in
    `sys.modules` yet (`from urllib import request` included); the
    `builtins.__import__` wrapper sees every `import` statement, so a module the
    runtime loaded again after the purge (3.11 re-imports `threading`) is still
    refused to plugin code. Plugin code is the first frame outside the import
    machinery whose file is under the plugin directory, or compiled at run time.
    """

    def __init__(self, plugin_roots: tuple[str, ...], stdlib_names: frozenset[str]):
        self._plugin_roots = tuple(root + "/" for root in plugin_roots)
        self._stdlib_names = stdlib_names
        self._skip_codes: frozenset[Any] = frozenset()

    def install(self) -> None:
        import builtins

        original = builtins.__import__
        guard = self

        def guarded_import(
            name: str,
            globals: Any = None,
            locals: Any = None,
            fromlist: Any = (),
            level: int = 0,
        ) -> Any:
            # Only the frame holding the `import` statement decides: the import
            # machinery imports `_io` and friends through this hook as well.
            if level == 0:
                guard.check(name, sys._getframe(1), walk=False)
            return original(name, globals, locals, fromlist, level)

        self._skip_codes = frozenset({guarded_import.__code__})
        sys.meta_path.insert(0, self)
        builtins.__import__ = guarded_import

    def _is_plugin_frame(self, frame: Any, walk: bool) -> bool:
        while frame is not None:
            code = frame.f_code
            filename = code.co_filename
            if not walk or not (
                code in self._skip_codes
                or filename.startswith("<frozen importlib")
                or filename.endswith(("/importlib/__init__.py", "/importlib/_bootstrap.py"))
            ):
                if filename.startswith("<frozen "):
                    return False  # a frozen stdlib module (os, io, codecs...)
                return filename.startswith(self._plugin_roots) or filename.startswith("<")
            frame = frame.f_back
        return False

    def check(self, name: str, frame: Any, walk: bool = True) -> None:
        if policy.is_denied(name):
            raise PermissionError(f"plugin sandbox: importing {name} is not allowed")
        if not policy.plugin_may_import(name, self._stdlib_names) and self._is_plugin_frame(
            frame, walk
        ):
            raise PermissionError(f"plugin sandbox: {name} is not in the plugin import allowlist")

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> None:
        # Frame 1 is whoever called find_spec (the import machinery).
        self.check(fullname, sys._getframe(1))
        return None

    def invalidate_caches(self) -> None:
        return None


# --- stage 6: entry and serving ---------------------------------------------------


def _load_entry(plugin_dir: str) -> Any:
    try:
        with open(os.path.join(plugin_dir, "manifest.json"), encoding="utf-8") as fh:
            entry = json.load(fh)["entry"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RunnerStartError("entry_invalid", f"cannot read entry from manifest: {exc}") from None
    if not isinstance(entry, str):
        raise RunnerStartError("entry_invalid", "entry must be a string")
    path, _, class_name = entry.partition(":")
    parts = path.split("/")
    if not path or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise RunnerStartError("entry_invalid", "entry path is not a safe relative path")
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    module_name = ".".join(parts)
    if not all(p.isidentifier() for p in parts):
        raise RunnerStartError("entry_invalid", f"entry {path} is not an importable module")
    try:
        module = importlib.import_module(module_name)
        target = getattr(module, class_name)() if class_name else module
    except Exception as exc:  # noqa: BLE001 - plugin code: report whatever it raised
        raise RunnerStartError("entry_load_failed", f"{type(exc).__name__}: {exc}") from None
    return target


class Channel:
    """Child end of the RPC stream: answers server requests, sends its own."""

    def __init__(self, loop: Any, rpc_in: int, rpc_out: int):
        import asyncio

        self._asyncio = asyncio
        self._loop = loop
        self._in = rpc_in
        self._out = rpc_out
        self.target: Any = None
        self._decoder = rpc.LineDecoder()
        self._semaphore = asyncio.Semaphore(policy.MAX_CONCURRENT_HANDLERS)
        self._tasks: dict[Any, Any] = {}
        self._pending: dict[int, Any] = {}
        self._next_id = 0
        self.done = loop.create_future()

    # Public API, reached through meshloom_plugin.host().

    async def call(self, method: str, params: Any = None) -> Any:
        self._next_id += 1
        msg_id = self._next_id
        future = self._loop.create_future()
        self._pending[msg_id] = future
        try:
            self._send(rpc.request(msg_id, method, params))
            return await future
        finally:
            self._pending.pop(msg_id, None)

    def notify(self, method: str, params: Any = None) -> None:
        self._send(rpc.notification(method, params))

    # Transport.

    def _send(self, message: dict[str, Any]) -> None:
        data = memoryview(rpc.encode(message))
        while data:
            written = os.write(self._out, data)
            data = data[written:]

    def _reply(self, message: dict[str, Any]) -> None:
        try:
            self._send(message)
        except ValueError as exc:
            self._send(rpc.error(message.get("id"), rpc.INTERNAL_ERROR, f"result refused: {exc}"))

    def feed(self, data: bytes) -> None:
        try:
            lines = self._decoder.feed(data)
        except rpc.FramingError as exc:
            self._finish(EXIT_PROTOCOL, f"protocol: {exc}")
            return
        for line in lines:
            if line.strip():
                self._handle_line(line)

    def on_readable(self) -> None:
        try:
            data = os.read(self._in, 65536)
        except BlockingIOError:
            return
        except OSError as exc:
            self._finish(EXIT_PROTOCOL, f"rpc read failed: {exc}")
            return
        if not data:
            self._finish(0, None)
            return
        self.feed(data)

    def _finish(self, code: int, reason: str | None) -> None:
        if reason:
            print(f"meshloom-plugin-runner: {reason}", file=sys.stderr)
        if not self.done.done():
            self.done.set_result(code)

    # Dispatch.

    def _handle_line(self, line: bytes) -> None:
        try:
            msg = rpc.decode(line)
        except rpc.ProtocolError as exc:
            self._reply(rpc.error(None, exc.code, str(exc)))
            return
        if "method" not in msg:
            future = self._pending.get(msg.get("id"))  # type: ignore[arg-type]
            if future is not None and not future.done():
                if "error" in msg:
                    future.set_exception(HostError(msg["error"]))
                else:
                    future.set_result(msg.get("result"))
            return
        method = msg["method"]
        msg_id = msg.get("id")
        is_request = "id" in msg
        params = msg.get("params")
        if method == "event.cancel":
            target_id = params.get("id") if isinstance(params, dict) else None
            task = self._tasks.get(target_id)
            if task is not None:
                task.cancel()
            if is_request:
                self._reply(rpc.result(msg_id, task is not None))
            return
        handler_name = _handler_name(method)
        if handler_name is None:
            if is_request:
                self._reply(rpc.error(msg_id, rpc.METHOD_NOT_FOUND, f"unknown method {method}"))
            return
        if is_request and msg_id in self._tasks:
            self._reply(rpc.error(msg_id, rpc.INVALID_REQUEST, "duplicate request id"))
            return
        if len(self._tasks) >= MAX_PENDING:
            if is_request:
                self._reply(rpc.error(msg_id, rpc.OVERLOADED, "too many pending events"))
            return
        key = msg_id if is_request else object()
        reply_id = msg_id if is_request else _NO_REPLY
        task = self._loop.create_task(self._run(method, handler_name, params, reply_id))
        self._tasks[key] = task
        task.add_done_callback(lambda t, key=key: self._task_done(key, reply_id, t))

    def _task_done(self, key: Any, reply_id: Any, task: Any) -> None:
        self._tasks.pop(key, None)
        # A task cancelled before its first step never ran `_run`: answer here.
        if task.cancelled() and reply_id is not _NO_REPLY:
            self._reply(rpc.error(reply_id, rpc.CANCELLED, "cancelled"))

    async def _run(self, method: str, handler_name: str, params: Any, msg_id: Any) -> None:
        asyncio = self._asyncio
        response: dict[str, Any] | None
        try:
            async with self._semaphore:
                value = None
                handler = getattr(self.target, handler_name, None)
                if handler is not None:
                    value = handler(params)
                    if asyncio.iscoroutine(value):
                        value = await value
            response = rpc.result(msg_id, value)
        except asyncio.CancelledError:
            response = rpc.error(msg_id, rpc.CANCELLED, "cancelled")
        except Exception as exc:  # noqa: BLE001 - plugin code: report, keep serving
            traceback.print_exc(file=sys.stderr)
            response = rpc.error(
                msg_id,
                rpc.HANDLER_ERROR,
                f"{type(exc).__name__}: {exc}",
                {"code": "handler_error", "type": type(exc).__name__},
            )
        if msg_id is not _NO_REPLY:
            self._reply(response)
        if method == "plugin.shutdown":
            self._finish(0, None)


_NO_REPLY = object()


class HostError(Exception):
    """The server answered a child request with a JSON-RPC error."""

    def __init__(self, error: Any):
        message = error.get("message") if isinstance(error, dict) else None
        super().__init__(message or "host error")
        self.error = error


def _handler_name(method: str) -> str | None:
    if method == "plugin.init":
        return "on_init"
    if method == "plugin.shutdown":
        return "on_shutdown"
    if method == "config.changed":
        return "on_config_changed"
    if method.startswith("event.") and method[6:] in HOOK_NAMES:
        return "on_" + method[6:]
    return None


def _new_loop() -> Any:
    import asyncio

    class _PipeEnd:
        """The part of the socket API the selector loop's self-pipe uses."""

        def __init__(self, fd: int):
            self._fd = fd

        def fileno(self) -> int:
            return self._fd

        def setblocking(self, flag: bool) -> None:
            os.set_blocking(self._fd, flag)

        def recv(self, size: int) -> bytes:
            return os.read(self._fd, size)

        def send(self, data: bytes) -> int:
            return os.write(self._fd, data)

        def close(self) -> None:
            os.close(self._fd)

    class _PipeSelectorLoop(asyncio.SelectorEventLoop):
        """A selector loop that wakes itself through `os.pipe()`, never a socket pair."""

        def _make_self_pipe(self) -> None:
            read_fd, write_fd = os.pipe()
            self._ssock = _PipeEnd(read_fd)
            self._csock = _PipeEnd(write_fd)
            self._ssock.setblocking(False)
            self._csock.setblocking(False)
            self._internal_fds += 1
            self._add_reader(read_fd, self._read_from_self)  # pyright: ignore[reportAttributeAccessIssue]

    return _PipeSelectorLoop()


def main() -> int:
    try:
        _close_inherited_fds()
        params, leftover = _read_params()
        rpc_in, rpc_out = _take_rpc_fds()
        _scrub_environ()
        _apply_limits(params)
        import asyncio  # noqa: F401 - loaded before the hook, purged of its sockets after

        plugin_dir = os.path.realpath(params["plugin_dir"])
        tmp_dir = os.getcwd()
        stdlib = [p for p in sys.path[1:] if p]
        package_dir = os.path.dirname(os.path.abspath(meshloom_plugin.__file__ or ""))
        _install_audit_hook(
            read_roots=_roots(plugin_dir, tmp_dir, package_dir, *stdlib),
            write_roots=_roots(tmp_dir),
        )
        _purge_modules()
        _ImportGuard(_roots(plugin_dir), frozenset(sys.stdlib_module_names)).install()
        sys.path[:] = [plugin_dir, os.path.join(plugin_dir, "vendor"), *stdlib]
        sys.path_importer_cache.clear()
        loop = _new_loop()
        asyncio.set_event_loop(loop)
        channel = Channel(loop, rpc_in, rpc_out)
        meshloom_plugin._host = channel
        channel.target = _load_entry(plugin_dir)
    except RunnerStartError as exc:
        _report_start_error(exc)
        return EXIT_START_FAILED

    os.set_blocking(rpc_in, False)
    loop.add_reader(rpc_in, channel.on_readable)
    if leftover:
        loop.call_soon(channel.feed, leftover)
    code = loop.run_until_complete(channel.done)
    sys.stderr.flush()
    return code


if __name__ == "__main__":
    code = main()
    sys.stderr.flush()
    os._exit(code)
