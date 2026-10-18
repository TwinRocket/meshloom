"""The plugin child runtime (`meshloom_plugin.runner`), run as a real subprocess.

Every test launches the child the way the supervisor will (`app/plugins/launch.py`:
`-I -S -B -X utf8 -c <bootstrap>`, scrubbed env, cwd an empty temp dir) and talks
JSON-RPC to it over its pipes. Hostile cases must be refused and leave the child
serving. Needs nothing beyond the standard library and pytest, so it also runs
under other interpreters: `uv run --no-project --python 3.13 --with pytest
pytest --noconftest tests/test_plugin_runner.py`.
"""

import json
import os
import select
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from app.plugins import launch
from app.plugins.isolation.base import IsolationBackend
from app.plugins.isolation.none import NoneBackend
from meshloom_plugin import policy, rpc
from tests.plugin_isolation_support import available_backends, run_probe, shared_tmp_dir

CORPUS = Path(__file__).parent / "plugin_corpus"
LINUX = sys.platform.startswith("linux")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the runner refuses Windows")


class Runner:
    """One child process and the server end of its RPC stream."""

    def __init__(
        self,
        plugin_dir: Path,
        work: Path,
        *,
        env: dict[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
        isolation: str = "none",
        limits: dict[str, int] | None = None,
        params_line: bytes | None = None,
        cwd: Path | None = None,
        backend: IsolationBackend | None = None,
    ):
        # The supervisor's way: the backend's prepare_work_dir() and launch() (the
        # runner command and launch.popen_kwargs(), wrapped by the isolation).
        # Tests override env, pass_fds or cwd only to play a parent that gets it wrong.
        backend = backend or NoneBackend()
        if backend.level != "none":
            isolation = backend.level
        # The dedicated uid cannot enter pytest's 0700 base temp dir.
        parent = shared_tmp_dir("meshloom-runner-") if backend.level == "setuid" else work
        self.cwd = backend.prepare_work_dir(Path(tempfile.mkdtemp(dir=parent, prefix="cwd-")))
        self.stderr_path = work / f"stderr-{self.cwd.name}.log"
        self._stderr = self.stderr_path.open("wb")
        argv, kwargs = backend.launch(plugin_dir, self.cwd)
        if env is not None:
            kwargs["env"] = env
        if pass_fds:
            kwargs["pass_fds"] = pass_fds
        if cwd is not None:
            kwargs["cwd"] = str(cwd)
        self.proc = subprocess.Popen(argv, stderr=self._stderr, **kwargs)
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self._stdin = self.proc.stdin
        self._stdout_fd = self.proc.stdout.fileno()
        self._buf = b""
        self._next_id = 0
        if params_line is None:
            params_line = launch.runner_params(
                plugin_dir, self.cwd, "test-instance", isolation, limits
            )
        self.write(params_line)

    def write(self, data: bytes) -> None:
        self._stdin.write(data)
        self._stdin.flush()

    def send(self, message: dict[str, Any]) -> None:
        self.write(rpc.encode(message))

    def read_message(self, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no message from the runner; stderr: {self.stderr()[-2000:]}")
            ready, _, _ = select.select([self._stdout_fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self._stdout_fd, 65536)
            if not chunk:
                raise EOFError(f"runner closed its stdout; stderr: {self.stderr()[-2000:]}")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return json.loads(line)

    def request(
        self,
        method: str,
        params: Any = None,
        *,
        on_request: Any = None,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        msg_id = self.start(method, params)
        return self.wait_for(msg_id, on_request=on_request, timeout=timeout)

    def start(self, method: str, params: Any = None) -> int:
        self._next_id += 1
        self.send(rpc.request(self._next_id, method, params))
        return self._next_id

    def wait_for(self, msg_id: int, *, on_request: Any = None, timeout: float = 15.0) -> dict:
        while True:
            message = self.read_message(timeout)
            if "method" in message:
                assert on_request is not None, f"unexpected request from the child: {message}"
                self.send(on_request(message))
                continue
            assert message.get("id") == msg_id, f"unexpected response {message}"
            return message

    def event(self, params: Any = None, **kwargs: Any) -> dict[str, Any]:
        return self.request("event.message", params, **kwargs)

    def stderr(self) -> str:
        self._stderr.flush()
        return self.stderr_path.read_text(errors="replace")

    def close(self) -> int:
        if self.proc.poll() is None:
            try:
                self.request("plugin.shutdown", timeout=5)
            except (TimeoutError, EOFError, BrokenPipeError, OSError):
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._stdin.close()
        if self.proc.stdout is not None:
            self.proc.stdout.close()
        self._stderr.close()
        return self.proc.returncode


def make_plugin(root: Path, source: str, entry: str = "plugin.py") -> Path:
    plugin_dir = root / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(json.dumps({"entry": entry}))
    module = entry.partition(":")[0]
    (plugin_dir / module).write_text(textwrap.dedent(source))
    return plugin_dir


@pytest.fixture
def work(tmp_path: Path) -> Path:
    # A short, resolved path: macOS temp dirs go through the /var -> /private/var link.
    return tmp_path.resolve()


def _error_type(response: dict[str, Any]) -> str | None:
    return response.get("error", {}).get("data", {}).get("type")


# --- Hostile behaviour --------------------------------------------------------

HOSTILE = """
    import asyncio
    import os
    import sys

    import meshloom_plugin

    _active = 0
    _peak = 0


    def _code_patch():
        def f():
            return 1

        f.__code__ = (lambda: 2).__code__


    def _class_patch():
        class A:
            pass

        class B:
            pass

        A().__class__ = B


    def _audit_hook():
        seen = []
        sys.addaudithook(lambda event, args: seen.append(event))
        compile("1", "<x>", "eval")
        return bool(seen)


    def _import(name):
        __import__(name)


    CASES = {
        "open_db": lambda p: open(p["path"], "rb").read(1),
        "etc_passwd": lambda p: open("/etc/passwd").read(1),
        "relative_path": lambda p: open("../../escape.txt").read(1),
        "write_outside": lambda p: open(p["path"], "w"),
        "list_outside": lambda p: os.listdir("/etc"),
        "symlink": lambda p: os.symlink("/etc/passwd", os.getcwd() + "/link"),
        "native_in_tmp": lambda p: open(os.getcwd() + "/evil.so", "wb"),
        "fork": lambda p: os.fork(),
        "spawn": lambda p: os.posix_spawn("/bin/true", ["/bin/true"], {}),
        "system": lambda p: os.system("true"),
        "kill": lambda p: os.kill(os.getppid(), 0),
        "putenv": lambda p: os.environ.__setitem__("X", "1"),
        "code_patch": lambda p: _code_patch(),
        "class_patch": lambda p: _class_patch(),
        "audit_hook": lambda p: _audit_hook(),
        "import": lambda p: _import(p["name"]),
        "sys_modules": lambda p: sorted(
            n for n in ("socket", "ssl", "subprocess", "_posixsubprocess") if n in sys.modules
        ),
        "write_fd1": lambda p: os.write(1, b"garbage on fd 1\\n"),
        "write_plugin_dir": lambda p: open(os.path.join(os.path.dirname(__file__), "x.py"), "w"),
        "unserializable": lambda p: object(),
        "oversized": lambda p: "x" * (200 * 1024),
    }


    def _write_tmp(p):
        path = os.path.join(os.getcwd(), "note.txt")
        with open(path, "w") as fh:
            fh.write("kept")
        with open(path) as fh:
            return fh.read()


    CASES["write_tmp"] = _write_tmp


    async def on_message(params):
        global _active, _peak
        case = params.get("case", "ping")
        if case == "ping":
            print("handler print goes to stderr")
            return "pong"
        if case == "open_connection":
            await asyncio.open_connection("127.0.0.1", 9)
            return "connected"
        if case == "host_call":
            return await meshloom_plugin.host().call("storage.get", {"key": "k"})
        if case == "host_call_caught":
            from meshloom_plugin.errors import HostError

            try:
                await meshloom_plugin.host().call("storage.get", {"key": "k"})
            except HostError as exc:
                return {"caught": exc.error}
            return "not raised"
        if case == "sleep":
            await asyncio.sleep(params.get("seconds", 30))
            return "slept"
        if case == "slow":
            _active += 1
            _peak = max(_peak, _active)
            try:
                await asyncio.sleep(0.3)
            finally:
                _active -= 1
            return _peak
        if case == "memory":
            return len(b"x" * (1024 * 1024 * 1024))
        return CASES[case](params)


    def on_health(params):
        return {"health": params}
"""


@pytest.fixture
def hostile(work: Path):
    runner = Runner(make_plugin(work, HOSTILE), work)
    yield runner
    runner.close()


def test_starts_under_isolated_mode_in_an_empty_cwd(hostile: Runner) -> None:
    assert hostile.event({"case": "ping"})["result"] == "pong"
    assert list(hostile.cwd.iterdir()) == []
    assert "handler print goes to stderr" in hostile.stderr()


@pytest.mark.parametrize(
    "case",
    [
        "etc_passwd",
        "write_plugin_dir",
        "relative_path",
        "list_outside",
        "symlink",
        "native_in_tmp",
        "fork",
        "spawn",
        "system",
        "kill",
        "putenv",
        "code_patch",
        "class_patch",
    ],
)
def test_hostile_call_is_refused_and_the_child_keeps_serving(hostile: Runner, case: str) -> None:
    response = hostile.event({"case": case})
    assert _error_type(response) == "PermissionError", response
    assert hostile.event({"case": "ping"})["result"] == "pong"


def test_extra_audit_hook_is_never_added(hostile: Runner) -> None:
    # sys.addaudithook() swallows an Exception raised by an existing hook and
    # returns None without adding the new one: the refusal is silent by design.
    assert hostile.event({"case": "audit_hook"})["result"] is False
    assert hostile.event({"case": "ping"})["result"] == "pong"


def test_database_path_is_refused(hostile: Runner, work: Path) -> None:
    db = work / "data" / "meshcore.db"
    db.parent.mkdir()
    db.write_bytes(b"SQLite format 3\x00")
    response = hostile.event({"case": "open_db", "path": str(db)})
    assert _error_type(response) == "PermissionError", response
    response = hostile.event({"case": "write_outside", "path": str(db)})
    assert _error_type(response) == "PermissionError", response
    assert db.read_bytes() == b"SQLite format 3\x00"


def test_private_tmp_is_writable(hostile: Runner) -> None:
    assert hostile.event({"case": "write_tmp"})["result"] == "kept"
    assert (hostile.cwd / "note.txt").read_text() == "kept"


@pytest.mark.parametrize(
    "name",
    [
        "socket",
        "_socket",
        "ssl",
        "subprocess",
        "_posixsubprocess",
        "ctypes",
        "sqlite3",
        "multiprocessing",
        "http.client",
        "urllib.request",
        "logging.handlers",
        "threading",
        "zipimport",
        "importlib",
        "gc",
    ],
)
def test_import_outside_the_allowlist_is_refused(hostile: Runner, name: str) -> None:
    response = hostile.event({"case": "import", "name": name})
    assert _error_type(response) == "PermissionError", response
    assert hostile.event({"case": "ping"})["result"] == "pong"


@pytest.mark.parametrize("name", ["json", "datetime", "urllib.parse", "hashlib", "logging"])
def test_allowlisted_import_works(hostile: Runner, name: str) -> None:
    response = hostile.event({"case": "import", "name": name})
    assert response.get("result", response) is None


def test_sys_modules_holds_no_network_or_process_module(hostile: Runner) -> None:
    assert hostile.event({"case": "sys_modules"})["result"] == []


def test_asyncio_open_connection_is_refused(hostile: Runner) -> None:
    response = hostile.event({"case": "open_connection"})
    assert _error_type(response) in ("PermissionError", "RuntimeError"), response
    assert hostile.event({"case": "ping"})["result"] == "pong"


def test_writes_to_fd_1_never_reach_the_rpc_stream(hostile: Runner) -> None:
    assert hostile.event({"case": "write_fd1"})["result"] > 0
    assert hostile.event({"case": "ping"})["result"] == "pong"
    assert "garbage on fd 1" in hostile.stderr()


@pytest.mark.skipif(not LINUX, reason="RLIMIT_AS is only enforced on Linux")
def test_memory_bomb_hits_the_address_space_limit(hostile: Runner) -> None:
    response = hostile.event({"case": "memory"})
    assert _error_type(response) == "MemoryError", response
    assert hostile.event({"case": "ping"})["result"] == "pong"


@pytest.mark.skipif(not LINUX, reason="reads /proc/<pid>/limits and status")
def test_limits_and_no_new_privs_are_set(hostile: Runner) -> None:
    hostile.event({"case": "ping"})
    pid = hostile.proc.pid
    limits = Path(f"/proc/{pid}/limits").read_text()

    def hard(label: str) -> str:
        line = next(line for line in limits.splitlines() if line.startswith(label))
        return line[len(label) :].split()[1]

    assert hard("Max address space") == str(policy.DEFAULT_LIMITS["address_space"])
    assert hard("Max open files") == str(policy.DEFAULT_LIMITS["open_files"])
    assert hard("Max file size") == str(policy.DEFAULT_LIMITS["file_size"])
    assert hard("Max core file size") == "0"
    assert hard("Max cpu time") == str(policy.DEFAULT_LIMITS["cpu_seconds"])
    status = Path(f"/proc/{pid}/status").read_text()
    assert "NoNewPrivs:\t1" in status


@pytest.mark.parametrize("level", ["none", "bwrap", "setuid"])
def test_runner_serves_under_each_isolation_level(level: str, work: Path) -> None:
    backend = available_backends().get(level)
    if backend is None:
        pytest.skip(f"the {level} backend is not available here")
    root = shared_tmp_dir("meshloom-plugin-") if level == "setuid" else work
    runner = Runner(make_plugin(root, HOSTILE), work, backend=backend)
    try:
        assert runner.event({"case": "ping"})["result"] == "pong"
        assert _error_type(runner.event({"case": "etc_passwd"})) == "PermissionError"
        if level == "setuid":
            status = Path(f"/proc/{runner.proc.pid}/status").read_text()
            uids = next(line for line in status.splitlines() if line.startswith("Uid:")).split()[1:]
            assert set(uids) == {str(backend.uid)}  # real, effective, saved, fs
            _os_check_nproc(runner, backend, work)
    finally:
        runner.close()


_SIBLING = """
import sys
try:
    with open("/proc/%s/mem" % sys.argv[1], "r+b"):
        print("allowed")
except OSError as exc:
    print("denied: " + type(exc).__name__)
"""


@pytest.mark.skipif(not LINUX, reason="/proc/<pid>/mem and prctl are Linux only")
@pytest.mark.parametrize("level", ["none", "bwrap", "setuid"])
def test_a_sibling_under_the_same_uid_cannot_open_the_child_memory(level: str, work: Path) -> None:
    # Another instance (same uid under setuid, the server's uid otherwise) escaping
    # its hooks must not reach this child's memory: the runner is not dumpable.
    backend = available_backends().get(level)
    if backend is None:
        pytest.skip(f"the {level} backend is not available here")
    if level == "none" and os.geteuid() == 0:
        pytest.skip("a root sibling bypasses dumpable checks; covered by the setuid case")
    root = shared_tmp_dir("meshloom-plugin-") if level == "setuid" else work
    runner = Runner(make_plugin(root, HOSTILE), work, backend=backend)
    try:
        assert runner.event({"case": "ping"})["result"] == "pong"
        sibling_work = backend.prepare_work_dir(Path(tempfile.mkdtemp(prefix="meshloom-sibling-")))
        sibling = backend.spawn(
            [sys.executable, "-I", "-S", "-c", _SIBLING, str(runner.proc.pid)],
            shared_tmp_dir("meshloom-sibling-plugin-"),
            sibling_work,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        out, err = sibling.communicate(timeout=30)
        assert sibling.returncode == 0, err
        assert out.decode().strip().startswith("denied"), out
    finally:
        runner.close()


def test_setuid_announced_to_a_root_child_is_refused(work: Path) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("needs root: the runner checks it really dropped to another uid")
    error = _start_with(
        work, make_plugin(work, "def on_message(params):\n    return 1\n"), isolation="setuid"
    )
    assert error["runner_error"] == "params_invalid"
    assert "root" in error["message"]


# --- Corpus -----------------------------------------------------------------------


def _corpus_cases() -> list[Path]:
    return sorted(p for p in CORPUS.iterdir() if (p / "case.json").is_file())


@pytest.mark.parametrize("level", ["none", "bwrap", "setuid"])
@pytest.mark.parametrize("case_dir", _corpus_cases(), ids=lambda p: p.name)
def test_corpus_case(case_dir: Path, level: str, work: Path) -> None:
    backend = available_backends().get(level)
    if backend is None:
        pytest.skip(f"the {level} backend is not available here")
    case = json.loads((case_dir / "case.json").read_text())
    secret_env = {**launch.runner_env(), "MESHLOOM_SECRET": "leak", "HOME": "/root"}
    marker = work / "parent-only.txt"
    marker.write_text("inherited?")
    marker_fd = os.open(marker, os.O_RDONLY)
    os.set_inheritable(marker_fd, True)
    marker_stat = os.fstat(marker_fd)
    try:
        # The child must hold its line even when the parent gets it wrong:
        # a polluted environment and a descriptor passed on purpose.
        runner = Runner(case_dir, work, env=secret_env, pass_fds=(marker_fd,), backend=backend)
        try:
            for event in case["events"]:
                response = runner.event(event["params"])
                try:
                    _check_expectation(event["expect"], response, marker_stat)
                except AssertionError:
                    open_path = event.get("open_path")
                    if not _open_path_applies(open_path):
                        raise
                    assert open_path is not None
                    if level in open_path.get("closed_by", []):
                        # The runner cannot close it in-process; the OS level does,
                        # and its check proves it (docs/dev/plugins-security.md).
                        _OS_CHECKS[open_path["os_check"]](runner, backend, work)
                    else:
                        # A documented open path at this level: pinned so a later
                        # ticket that closes it turns it into a pass.
                        pytest.xfail(f"open path: {open_path['note']}: {response}")
                assert runner.request("event.health", {"ok": True})["result"] is None
        finally:
            runner.close()
    finally:
        os.close(marker_fd)


def _check_expectation(
    expect: dict[str, Any], response: dict[str, Any], marker: os.stat_result
) -> None:
    if "error_type" in expect:
        assert _error_type(response) == expect["error_type"], response
    elif "result" in expect:
        assert response.get("result") == expect["result"], response
    else:
        _CHECKS[expect["check"]](response, marker)


def _open_path_applies(open_path: dict[str, Any] | None) -> bool:
    if open_path is None:
        return False
    below = open_path.get("python_below")
    if below is None:
        return True
    major, minor = (int(part) for part in below.split("."))
    return sys.version_info[:2] < (major, minor)


def _check_hook_frame(response: dict[str, Any], _marker: os.stat_result) -> None:
    result = response["result"]
    assert isinstance(result, dict), response
    assert result["globals"] == []
    assert result["builtins"] == []
    assert set(result["local_types"]) <= {"str", "bytes", "int", "bool", "tuple", "type"}


def _check_environ(response: dict[str, Any], _marker: os.stat_result) -> None:
    assert set(response["result"]) <= set(policy.ENV_KEYS), response


def _check_inherited_fds(response: dict[str, Any], marker: os.stat_result) -> None:
    seen = response["result"]
    assert [fd for fd, _dev, _ino in seen][:3] == [0, 1, 2]
    assert not any(dev == marker.st_dev and ino == marker.st_ino for _fd, dev, ino in seen)


_CHECKS = {
    "hook_frame": _check_hook_frame,
    "environ": _check_environ,
    "inherited_fds": _check_inherited_fds,
}


def _os_check_exec_escape(_runner: Runner, backend: IsolationBackend, work: Path) -> None:
    """What a rebuilt `fork_exec` gives a plugin: a program with no hook, in the box."""
    data = shared_tmp_dir("meshloom-data-")
    secret = data / "meshcore.db"
    secret.write_bytes(b"SQLite format 3\x00")
    os.chmod(secret, 0o600)
    os.chmod(data, 0o711)
    result = run_probe(backend, secret)
    assert result["read_secret"].startswith("denied"), result
    assert result["read_environ"].startswith("denied"), result
    assert result["app_root_written"] is False, result
    if backend.level == "bwrap":
        assert result["read_app_code"].startswith("denied"), result
        assert result["connect"].startswith("denied"), result


def _os_check_nproc(runner: Runner, _backend: IsolationBackend, _work: Path) -> None:
    """Threads stay possible on 3.11, but bounded by RLIMIT_NPROC for the uid."""
    limits = Path(f"/proc/{runner.proc.pid}/limits").read_text()
    line = next(line for line in limits.splitlines() if line.startswith("Max processes"))
    assert line.split()[3] == str(policy.DEFAULT_LIMITS["processes"]), line


_OS_CHECKS = {
    "exec_escape": _os_check_exec_escape,
    "nproc": _os_check_nproc,
}


def test_launch_env_keeps_only_the_whitelisted_keys() -> None:
    assert tuple(launch.runner_env()) == policy.ENV_KEYS


def test_print_flood_keeps_the_stream_valid(work: Path) -> None:
    runner = Runner(CORPUS / "print_flood", work)
    try:
        assert runner.event({})["result"] == "done"
        assert runner.event({})["result"] == "done"
        log = runner.stderr()
        assert "flood line 19999" in log
        assert '"result": "forged"' in log
    finally:
        runner.close()


# --- Protocol -----------------------------------------------------------------------


def test_host_call_round_trip(hostile: Runner) -> None:
    seen: list[dict[str, Any]] = []

    def answer(message: dict[str, Any]) -> dict[str, Any]:
        seen.append(message)
        return rpc.result(message["id"], {"value": 42})

    response = hostile.event({"case": "host_call"}, on_request=answer)
    assert response["result"] == {"value": 42}
    assert seen[0]["method"] == "storage.get"
    assert seen[0]["params"] == {"key": "k"}


def test_host_error_reaches_the_handler(hostile: Runner) -> None:
    def refuse(message: dict[str, Any]) -> dict[str, Any]:
        return rpc.error(message["id"], -32010, "permission_denied")

    response = hostile.event({"case": "host_call"}, on_request=refuse)
    assert _error_type(response) == "HostError"
    assert "permission_denied" in response["error"]["message"]


def test_plugin_catches_host_error_from_its_module(hostile: Runner) -> None:
    # HostError lives in meshloom_plugin.errors, not in the runner (which runs as
    # __main__, so a plugin importing meshloom_plugin.runner would get a copy).
    def refuse(message: dict[str, Any]) -> dict[str, Any]:
        return rpc.error(message["id"], -32010, "permission_denied")

    response = hostile.event({"case": "host_call_caught"}, on_request=refuse)
    assert response["result"] == {"caught": {"code": -32010, "message": "permission_denied"}}


def test_event_cancel(hostile: Runner) -> None:
    slow = hostile.start("event.message", {"case": "sleep"})
    cancel = hostile.request("event.cancel", {"id": slow})
    assert cancel["result"] is True
    response = hostile.wait_for(slow)
    assert response["error"]["code"] == rpc.CANCELLED
    assert hostile.event({"case": "ping"})["result"] == "pong"


def test_at_most_eight_handlers_run_at_once(hostile: Runner) -> None:
    ids = [hostile.start("event.message", {"case": "slow"}) for _ in range(20)]
    peaks = [hostile.wait_for(msg_id, timeout=30)["result"] for msg_id in ids]
    assert max(peaks) == policy.MAX_CONCURRENT_HANDLERS


@pytest.mark.parametrize("case", ["unserializable", "oversized"])
def test_result_that_cannot_be_sent_is_an_internal_error(hostile: Runner, case: str) -> None:
    assert hostile.event({"case": case})["error"]["code"] == rpc.INTERNAL_ERROR
    assert hostile.event({"case": "ping"})["result"] == "pong"


def test_unknown_method_and_missing_handler(hostile: Runner) -> None:
    assert hostile.request("plugin.frobnicate")["error"]["code"] == rpc.METHOD_NOT_FOUND
    assert hostile.request("event.contact", {})["result"] is None
    assert hostile.request("event.health", {"x": 1})["result"] == {"health": {"x": 1}}


def test_malformed_line_gets_a_parse_error_and_the_child_keeps_serving(hostile: Runner) -> None:
    hostile.write(b"{not json\n")
    assert hostile.read_message()["error"]["code"] == rpc.PARSE_ERROR
    hostile.write(b'{"jsonrpc": "2.0", "id": 5, "result": NaN}\n')
    assert hostile.read_message()["error"]["code"] == rpc.PARSE_ERROR
    assert hostile.event({"case": "ping"})["result"] == "pong"


def test_oversized_line_ends_the_child(hostile: Runner) -> None:
    hostile.write(b"x" * (rpc.MAX_MESSAGE_BYTES + 10))
    assert hostile.proc.wait(timeout=10) == 2
    assert "message over" in hostile.stderr()


def test_stdin_eof_ends_the_child(hostile: Runner) -> None:
    hostile.event({"case": "ping"})
    hostile.proc.stdin.close()  # type: ignore[union-attr]
    assert hostile.proc.wait(timeout=10) == 0


def test_shutdown_answers_then_exits(hostile: Runner) -> None:
    assert hostile.request("plugin.shutdown")["result"] is None
    assert hostile.proc.wait(timeout=10) == 0


def test_params_line_after_the_first_request_is_kept(work: Path) -> None:
    plugin_dir = make_plugin(work, "def on_init(params):\n    return params\n")
    first = rpc.encode(rpc.request(1, "plugin.init", {"api": 1}))
    runner = Runner(plugin_dir, work, params_line=b"", cwd=None)
    runner.write(launch.runner_params(plugin_dir, runner.cwd, "i", "none") + first)
    try:
        assert runner.read_message() == rpc.result(1, {"api": 1})
    finally:
        runner.close()


def test_entry_class_is_instantiated(work: Path) -> None:
    source = """
        class Plugin:
            def __init__(self):
                self.calls = 0

            async def on_message(self, params):
                self.calls += 1
                return self.calls
    """
    runner = Runner(make_plugin(work, source, entry="plugin.py:Plugin"), work)
    try:
        assert runner.event({})["result"] == 1
        assert runner.event({})["result"] == 2
    finally:
        runner.close()


# --- Refusal to start --------------------------------------------------------------


def _start_error(runner: Runner) -> dict[str, Any]:
    assert runner.proc.wait(timeout=10) == 3
    log = runner.stderr()
    runner.close()
    return json.loads(log.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "params",
    [
        b"not json\n",
        b"[]\n",
        b'{"plugin_dir": "relative", "instance_id": "i", "isolation": "none"}\n',
        b'{"plugin_dir": "/nonexistent-dir", "instance_id": "i", "isolation": "none"}\n',
    ],
)
def test_bad_parameters_are_a_typed_start_error(work: Path, params: bytes) -> None:
    runner = Runner(work, work, params_line=params)
    assert _start_error(runner)["runner_error"] == "params_invalid"


def _start_with(work: Path, plugin_dir: Path, cwd: Path | None = None, **changes: Any) -> dict:
    runner = Runner(plugin_dir, work, params_line=b"", cwd=cwd)
    params = {
        "plugin_dir": str(plugin_dir),
        "work_dir": str(runner.cwd),
        "instance_id": "i",
        "isolation": "none",
        **changes,
    }
    runner.write(json.dumps(params).encode() + b"\n")
    return _start_error(runner)


def test_unknown_limit_is_refused(work: Path) -> None:
    plugin_dir = make_plugin(work, "")
    assert _start_with(work, plugin_dir, limits={"x": 1})["runner_error"] == "params_invalid"


def test_unknown_isolation_level_is_refused(work: Path) -> None:
    plugin_dir = make_plugin(work, "")
    error = _start_with(work, plugin_dir, isolation="chroot")
    assert error["runner_error"] == "params_invalid"
    assert "isolation" in error["message"]


def test_cwd_other_than_work_dir_is_refused(work: Path) -> None:
    # A supervisor that forgot cwd= would make its own cwd (the data dir) writable.
    data_dir = work / "data"
    data_dir.mkdir()
    (data_dir / "meshcore.db").write_bytes(b"db")
    plugin_dir = make_plugin(work, "")
    error = _start_with(work, plugin_dir, cwd=data_dir)
    assert error["runner_error"] == "params_invalid"
    assert "work_dir" in error["message"]


@pytest.mark.parametrize("where", ["root", "plugin_dir", "inside_plugin_dir", "around_plugin"])
def test_work_dir_must_not_be_root_or_nest_with_the_plugin(work: Path, where: str) -> None:
    plugin_dir = make_plugin(work, "")
    target = {
        "root": Path("/"),
        "plugin_dir": plugin_dir,
        "inside_plugin_dir": plugin_dir / "tmp",
        "around_plugin": work,
    }[where]
    target.mkdir(exist_ok=True)
    error = _start_with(work, plugin_dir, cwd=target, work_dir=str(target))
    assert error["runner_error"] == "params_invalid"


def test_missing_work_dir_is_refused(work: Path) -> None:
    plugin_dir = make_plugin(work, "")
    assert _start_with(work, plugin_dir, work_dir=None)["runner_error"] == "params_invalid"


def test_popen_kwargs_fix_cwd_fds_session_and_env(work: Path) -> None:
    work_dir = launch.prepare_work_dir(work / "instance" / "tmp")
    kwargs = launch.popen_kwargs(work_dir)
    assert kwargs["cwd"] == str(work_dir)
    assert kwargs["close_fds"] is True
    assert kwargs["pass_fds"] == ()
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == kwargs["stdout"] == subprocess.PIPE
    assert kwargs["env"] == launch.runner_env()
    assert (work_dir.stat().st_mode & 0o777) == 0o700


def test_prepare_work_dir_refuses_a_symlink(work: Path) -> None:
    (work / "real").mkdir()
    (work / "link").symlink_to(work / "real")
    with pytest.raises(ValueError):
        launch.prepare_work_dir(work / "link")


def test_runner_params_refuse_an_unknown_isolation_level(work: Path) -> None:
    with pytest.raises(ValueError):
        launch.runner_params(work, work / "tmp", "i", "chroot")


def test_entry_failure_is_a_typed_start_error(work: Path) -> None:
    runner = Runner(make_plugin(work, "raise ValueError('boom')\n"), work)
    error = _start_error(runner)
    assert error["runner_error"] == "entry_load_failed"
    assert "boom" in error["message"]


def test_entry_import_of_a_denied_module_fails_the_start(work: Path) -> None:
    runner = Runner(make_plugin(work, "import socket\n"), work)
    assert _start_error(runner)["runner_error"] == "entry_load_failed"


def test_missing_resource_module_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from meshloom_plugin import runner

    monkeypatch.setitem(sys.modules, "resource", None)
    with pytest.raises(runner.RunnerStartError) as exc:
        runner._apply_limits({"isolation": "none"})
    assert exc.value.code == "platform_unsupported"


def test_audit_hook_failure_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from meshloom_plugin import runner

    def refuse(_hook: Any) -> None:
        raise RuntimeError("no hooks here")

    monkeypatch.setattr(sys, "addaudithook", refuse)
    with pytest.raises(runner.RunnerStartError) as exc:
        runner._install_audit_hook(("/nonexistent",), ("/nonexistent",))
    assert exc.value.code == "audit_hook_failed"


def test_hook_source_compiles_to_a_function_without_globals() -> None:
    from meshloom_plugin import runner

    hook = runner._build_hook(("/plugin",), ("/tmp-private",))
    assert hook.__globals__ == {}
    assert hook.__builtins__ == {}
    with pytest.raises(PermissionError):
        hook("open", ("/etc/passwd", "r", os.O_RDONLY))
    with pytest.raises(PermissionError):
        hook("open", ("/tmp-private/x.so", "w", os.O_WRONLY))
    with pytest.raises(PermissionError):
        hook("open", ("/plugin/../etc/passwd", "r", os.O_RDONLY))
    with pytest.raises(PermissionError):
        hook("os.rename", ("/tmp-private/a", "/plugin/a", -1, -1))
    hook("open", ("/plugin/plugin.py", "r", os.O_RDONLY))
    hook("open", (b"/plugin/plugin.py", "r", os.O_RDONLY))
    hook("open", ("/tmp-private/note.txt", "w", os.O_WRONLY | os.O_CREAT))
    hook("open", (3, "r", os.O_RDONLY))
    hook("compile", (b"", "<x>"))


@pytest.mark.parametrize(
    "line",
    [
        b"\xff\xfe",
        b"[" * 100000 + b"]" * 100000,
        b'{"jsonrpc": "2.0", "id": 1}',
        b'{"jsonrpc": "1.0", "id": 1, "method": "event.message"}',
        b'{"jsonrpc": "2.0", "id": true, "method": "event.message"}',
        b'{"jsonrpc": "2.0", "id": 1, "method": 5}',
        b'{"jsonrpc": "2.0", "id": 1, "method": "event.message", "params": 3}',
    ],
)
def test_decode_refuses_malformed_messages(line: bytes) -> None:
    with pytest.raises(rpc.ProtocolError):
        rpc.decode(line)


def test_decode_caps_the_nesting_depth() -> None:
    deep = rpc.MAX_NESTING_DEPTH - 1  # plus the envelope object
    ok = b'{"jsonrpc": "2.0", "id": 1, "result": ' + b"[" * deep + b"]" * deep + b"}"
    assert rpc.decode(ok)["id"] == 1
    too_deep = ok.replace(b"[", b"[[", 1).replace(b"]", b"]]", 1)
    with pytest.raises(rpc.ProtocolError, match="nested deeper"):
        rpc.decode(too_deep)
    # Brackets inside strings do not count.
    text = b'{"jsonrpc": "2.0", "id": 1, "result": "' + b"[" * 500 + b'\\"]"}'
    assert rpc.decode(text)["result"].startswith("[[")


def test_deeply_nested_line_gets_a_parse_error(hostile: Runner) -> None:
    hostile.write(b'{"jsonrpc": "2.0", "id": 9, "method": "event.message", "params": ')
    hostile.write(b"[" * 1000 + b"]" * 1000 + b"}\n")
    assert hostile.read_message()["error"]["code"] == rpc.PARSE_ERROR
    assert hostile.event({"case": "ping"})["result"] == "pong"


def test_line_decoder_caps_a_line_without_newline() -> None:
    decoder = rpc.LineDecoder(max_bytes=10)
    assert decoder.feed(b"abc\nde") == [b"abc"]
    assert decoder.feed(b"f\n") == [b"def"]
    with pytest.raises(rpc.FramingError):
        decoder.feed(b"x" * 11)


def test_encode_refuses_an_oversized_message() -> None:
    with pytest.raises(ValueError):
        rpc.encode(rpc.result(1, "x" * rpc.MAX_MESSAGE_BYTES))
