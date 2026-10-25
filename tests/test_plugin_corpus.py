"""The hostile plugin corpus (`tests/plugin_corpus/`) under every isolation level.

Each case directory is a minimal plugin plus `case.json`: what it does, the
events to send and the expected outcome (format in `tests/plugin_corpus/README.md`).
Three stages:

- `runner` (the default): a real runner child, with this test as the server
  (`tests/test_plugin_runner.py`'s `Runner`), under `none`, `bwrap` and `setuid`
  where this machine offers them. The parent hands the child a polluted
  environment and an inherited descriptor on purpose; after every event the
  database file and the app tree are checked untouched and the child must still
  answer.
- `host`: the real supervisor (`app/plugins/host.py`) with the isolation
  selection forced to each level, for what only the host enforces (cancel then
  kill, the CPU budget, the framing cap on what the child writes, capabilities).
  After every event the instance must serve again.
- `install`: an archive built from the case and given to `extract_package`;
  level-independent (the server validates before any child exists).

The checks named by `check` and `os_check` are written here. Docs:
docs/dev/plugins-security.md, "Tests".
"""

import asyncio
import contextlib
import errno
import io
import json
import os
import shutil
import sys
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.plugins import launch
from app.plugins.isolation import selection
from app.plugins.isolation.base import IsolationBackend
from meshloom_plugin import policy
from tests.plugin_isolation_support import available_backends, run_probe, shared_tmp_dir
from tests.test_plugin_runner import Runner

CORPUS = Path(__file__).parent / "plugin_corpus"
LEVELS = ("none", "bwrap", "setuid")
STAGES = ("runner", "host", "install")
ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the runner refuses Windows")


def _load(case_dir: Path) -> dict[str, Any]:
    return json.loads((case_dir / "case.json").read_text(encoding="utf-8"))


def _cases(stage: str) -> list[Path]:
    return sorted(
        p
        for p in CORPUS.iterdir()
        if (p / "case.json").is_file() and _load(p).get("stage", "runner") == stage
    )


def _backend(level: str) -> IsolationBackend:
    backend = available_backends().get(level)
    if backend is None:
        pytest.skip(f"the {level} backend is not available here")
    return backend


def _skip_platform(case: dict[str, Any]) -> None:
    platforms = case.get("platforms")
    if platforms and not any(sys.platform.startswith(p) for p in platforms):
        pytest.skip(f"only on {', '.join(platforms)}")


@pytest.fixture
def work(tmp_path: Path) -> Path:
    # A short, resolved path: macOS temp dirs go through the /var -> /private/var link.
    return tmp_path.resolve()


def _error(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("error") or {}


# --- runner stage -----------------------------------------------------------------


class _Targets:
    """What a hostile runner case aims at: a database, a data dir, the app tree."""

    PROBES = (".corpus-write-probe", "app/.corpus-write-probe")

    def __init__(self) -> None:
        # The setuid file model (docs/dev/plugins-security.md): 0600 in 0711.
        self.data_dir = shared_tmp_dir("meshloom-corpus-data-")
        self.db = self.data_dir / "meshcore.db"
        self.db_bytes = b"SQLite format 3\x00corpus"
        self.db.write_bytes(self.db_bytes)
        os.chmod(self.db, 0o600)
        os.chmod(self.data_dir, 0o711)
        self.values = {
            "$DB": str(self.db),
            "$DATA_DIR": str(self.data_dir),
            "$APP_ROOT": str(launch.APP_ROOT),
            "$SERVER_PID": str(os.getpid()),
        }

    def substitute(self, value: Any) -> Any:
        if isinstance(value, str):
            for key, real in self.values.items():
                value = value.replace(key, real)
            return int(value) if value.isdigit() else value
        if isinstance(value, dict):
            return {k: self.substitute(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.substitute(v) for v in value]
        return value

    def assert_untouched(self) -> None:
        assert self.db.read_bytes() == self.db_bytes
        for probe in self.PROBES:
            assert not (launch.APP_ROOT / probe).exists(), probe

    def close(self) -> None:
        for probe in self.PROBES:
            (launch.APP_ROOT / probe).unlink(missing_ok=True)
        shutil.rmtree(self.data_dir, ignore_errors=True)


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("case_dir", _cases("runner"), ids=lambda p: p.name)
def test_runner_case(case_dir: Path, level: str, work: Path) -> None:
    backend = _backend(level)
    case = _load(case_dir)
    _skip_platform(case)
    targets = _Targets()
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
                response = runner.event(targets.substitute(event["params"]))
                try:
                    _check_expectation(event["expect"], response, marker_stat)
                except AssertionError:
                    open_path = event.get("open_path")
                    if not _open_path_applies(open_path, level):
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
                targets.assert_untouched()
                assert runner.request("event.health", {"ok": True})["result"] is None
        finally:
            runner.close()
    finally:
        os.close(marker_fd)
        targets.close()


def _check_expectation(
    expect: dict[str, Any], response: dict[str, Any], marker: os.stat_result
) -> None:
    if "error_type" in expect:
        assert _error(response).get("data", {}).get("type") == expect["error_type"], response
        if expect["error_type"] == "PermissionError":
            # Refused by the runner (hook, import guard or stub), not by the OS
            # by luck: a removed guard must fail the case on every level.
            assert "plugin sandbox: " in _error(response).get("message", ""), response
    elif "error_code" in expect:
        assert _error(response).get("code") == expect["error_code"], response
    elif "result" in expect:
        assert "error" not in response and response.get("result") == expect["result"], response
    else:
        _CHECKS[expect["check"]](response, marker)


def _open_path_applies(open_path: dict[str, Any] | None, level: str) -> bool:
    if open_path is None:
        return False
    if open_path.get("nproc_bypass") and not _nproc_bypassed(level):
        return False
    below = open_path.get("python_below")
    if below is None:
        return True
    major, minor = (int(part) for part in below.split("."))
    return sys.version_info[:2] < (major, minor)


def _nproc_bypassed(level: str) -> bool:
    """Where the runner's RLIMIT_NPROC 0 does not hold: not set (not Linux), or
    ignored by a child running as root (none or bwrap started by root)."""
    if not sys.platform.startswith("linux"):
        return True
    return os.geteuid() == 0 and level != "setuid"


def _refused_by_the_sandbox(response: dict[str, Any]) -> bool:
    error = _error(response)
    return error.get("data", {}).get(
        "type"
    ) == "PermissionError" and "plugin sandbox: " in error.get("message", "")


def _check_spawn_refused(response: dict[str, Any], _marker: os.stat_result) -> None:
    """The hook refused the rebuild, or the fork failed: RLIMIT_NPROC is 0."""
    if _refused_by_the_sandbox(response):
        return
    assert response.get("result") == {"refused": errno.EAGAIN}, response


def _check_thread_refused(response: dict[str, Any], _marker: os.stat_result) -> None:
    """The hook refused the start (3.12+), or the clone failed: RLIMIT_NPROC is 0."""
    if _refused_by_the_sandbox(response):
        return
    error = _error(response)
    assert error.get("data", {}).get("type") == "RuntimeError", response
    assert "can't start new thread" in error.get("message", ""), response


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
    "spawn_refused": _check_spawn_refused,
    "thread_refused": _check_thread_refused,
}


def _os_check_exec_escape(_runner: Runner, backend: IsolationBackend, work: Path) -> None:
    """What native code or an exec'd program gives a plugin: no hook, but in the box."""
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


_OS_CHECKS = {
    "exec_escape": _os_check_exec_escape,
}


# --- host stage -------------------------------------------------------------------

HOST_BUDGETS = {
    "init_timeout": 10.0,
    "event_timeout": 0.6,
    "cancel_grace": 0.6,
    "shutdown_grace": 1.0,
    "backoff_initial": 0.05,
    "backoff_max": 0.2,
    "stagger": 0.05,
    "message_settle": 0.0,
}


async def _wait_until(predicate: Any, what: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def _pid_gone(pid: int) -> bool:
    """True once `pid` is reaped: a zombie still answers signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:  # another uid's process (setuid): still there
        return False
    return False


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("case_dir", _cases("host"), ids=lambda p: p.name)
async def test_host_case(
    case_dir: Path, level: str, test_db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.plugins import host as host_module
    from app.plugins.host import Budgets, PluginManager
    from app.repository.plugins import PluginRepository

    backend = _backend(level)
    case = _load(case_dir)
    _skip_platform(case)
    monkeypatch.setattr(selection, "_current", selection.Selection(backend))
    monkeypatch.setattr(host_module, "_broadcast_status", lambda payload: None)
    # The dedicated uid cannot enter pytest's 0700 base temp dir.
    root = shared_tmp_dir("meshloom-corpus-plugins-") if level == "setuid" else tmp_path
    plugin_id = "corpus." + case_dir.name.replace("_", "-")
    shutil.copytree(case_dir, root / plugin_id, ignore=shutil.ignore_patterns("case.json"))
    budgets = replace(Budgets(**HOST_BUDGETS), **case.get("budgets", {}))
    cpu_reader = (
        host_module.default_cpu_reader() if "cpu_budget" in case.get("budgets", {}) else None
    )
    manager = PluginManager(
        budgets, plugins_root=root, cpu_reader=cpu_reader, mem_total=lambda: 8 * 1024**3
    )
    permissions = case.get("permissions", {})
    row = await PluginRepository.create(
        plugin_id=plugin_id,
        label=case_dir.name,
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": ["message"], "permissions": permissions},
        granted_permissions=permissions,
        enabled=True,
    )
    instance_id = row["instance_id"]
    try:
        await manager.start(instance_id)
        instance = manager.get(instance_id)
        assert instance is not None
        await _wait_until(lambda: instance.status == "running", "running")
        for event in case["events"]:
            pid, restarts = instance.pid, instance.restarts
            expect = event["expect"]
            if "killed" in expect:
                task = asyncio.create_task(
                    manager.dispatch_test(instance_id, event["hook"], event["params"], timeout=60)
                )
                await _wait_until(
                    lambda before=restarts: (
                        instance.restarts > before and instance.status == "running"
                    ),
                    f"a restart after {expect['killed']}",
                    timeout=60,
                )
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                assert (instance.last_error or "").startswith(expect["killed"]), instance.last_error
                assert pid is not None and _pid_gone(pid)
            else:
                outcome = await manager.dispatch_test(instance_id, event["hook"], event["params"])
                assert outcome["result"] == expect["result"], outcome
                if expect.get("same_process"):
                    assert (instance.pid, instance.restarts) == (pid, restarts)
            # The host keeps running, and so does the instance.
            ping = await manager.dispatch_test(instance_id, "message", {"ping": True})
            assert ping["result"] == "pong"
    finally:
        await manager.stop_all(5.0)


# --- install stage ----------------------------------------------------------------

_VECTORS = json.loads((ROOT / "docs/dev/plugins/vectors/manifest.json").read_text("utf-8"))
_SAMPLE = next(v["manifest"] for v in _VECTORS["vectors"] if v["name"] == "sample")


def _zip(entries: list[dict[str, Any]], *, base: bool) -> bytes:
    files: list[tuple[zipfile.ZipInfo, bytes]] = []
    if base:
        # A package the validator accepts, so only the case's entries can fail it.
        for name, data in {
            "manifest.json": json.dumps(_SAMPLE).encode(),
            "plugin.py": b"class Weather: ...\n",
            "README.md": b"# Weather\n",
            "README.fr.md": b"# Meteo\n",
            "LICENSE": b"MIT\n",
            "config.schema.json": b'{"type": "object", "properties": {}}',
            "i18n/en.json": b"{}",
            "i18n/fr.json": b"{}",
        }.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o644 << 16
            files.append((info, data))
    for entry in entries:
        info = zipfile.ZipInfo(entry["name"])
        info.external_attr = 0o644 << 16
        if "symlink" in entry:
            info.create_system = 3
            info.external_attr = (0o120000 | 0o777) << 16
            data = entry["symlink"].encode()
        elif "zip" in entry:
            data = _zip(entry["zip"], base=False)
        elif "hex" in entry:
            data = bytes.fromhex(entry["hex"])
        else:
            data = entry["text"].encode()
        files.append((info, data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for info, data in files:
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def test_the_install_base_archive_is_accepted(tmp_path: Path) -> None:
    from app.plugins.package import extract_package

    extract_package(_zip([], base=True), tmp_path / "dest")
    assert (tmp_path / "dest" / "plugin.py").is_file()


@pytest.mark.parametrize("case_dir", _cases("install"), ids=lambda p: p.name)
def test_install_case(case_dir: Path, tmp_path: Path) -> None:
    from app.plugins.errors import PluginValidationError
    from app.plugins.package import extract_package

    case = _load(case_dir)
    data = _zip(case["archive"], base=True)
    outside = tmp_path / "outside"
    dest = outside / "plugins" / "dest"
    with pytest.raises(PluginValidationError) as exc:
        extract_package(data, dest)
    assert case["expect"]["install_error"] in exc.value.codes, exc.value.codes
    # Nothing written, at the destination or around it.
    assert not outside.exists()
    for entry in case["archive"]:
        if entry["name"].startswith("/"):
            assert not Path(entry["name"]).exists()


# --- the corpus itself ------------------------------------------------------------

_KEYS = {
    "runner": {"description", "events", "platforms"},
    "host": {"description", "stage", "events", "platforms", "budgets", "permissions"},
    "install": {"description", "stage", "archive", "expect"},
}


@pytest.mark.parametrize("case_dir", sorted(CORPUS.iterdir()), ids=lambda p: p.name)
def test_every_case_is_well_formed_and_documented(case_dir: Path) -> None:
    if not case_dir.is_dir():
        return
    case = _load(case_dir)
    stage = case.get("stage", "runner")
    assert stage in STAGES
    assert set(case) <= _KEYS[stage], set(case) - _KEYS[stage]
    assert case["description"]
    if stage != "install":
        assert json.loads((case_dir / "manifest.json").read_text()) == {"entry": "plugin.py"}
        assert (case_dir / "plugin.py").is_file()
        assert case["events"]
        for event in case["events"]:
            assert len(event["expect"]) >= 1
    doc = (ROOT / "docs/dev/plugins-security.md").read_text(encoding="utf-8")
    assert f"`{case_dir.name}`" in doc, f"{case_dir.name} is not listed in plugins-security.md"
