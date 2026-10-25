"""OS isolation of the plugin child (`app/plugins/isolation/`, MSHL-180).

The selection table runs everywhere with every probe monkeypatched. The
integration tests run a program with no runner and no hook (what a plugin gets
once it execs something) under each backend this machine offers: `bwrap` where
its self-test passes (the Linux CI job installs it), `setuid` only as root with
the `meshloom-plugin` account (the root container job of all-quality.yml). Like
`tests/test_plugin_runner.py`, needs nothing beyond the standard library and
pytest: `pytest --noconftest tests/test_plugin_isolation.py`.
"""

import logging
import os
import pwd
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from app.plugins import launch
from app.plugins.isolation import permissions, selection
from app.plugins.isolation.base import LEVEL_LABELS
from app.plugins.isolation.bwrap import BwrapBackend
from app.plugins.isolation.none import NoneBackend
from app.plugins.isolation.setuid import ACCOUNT, SetuidBackend
from tests.plugin_isolation_support import LINUX, available_backends, run_probe, shared_tmp_dir

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="plugins need POSIX")


def _pw(uid: int, gid: int) -> pwd.struct_passwd:
    return pwd.struct_passwd((ACCOUNT, "x", uid, gid, "", "/nonexistent", "/usr/sbin/nologin"))


def _getpwnam(entry: pwd.struct_passwd | None):
    def lookup(name: str) -> pwd.struct_passwd:
        assert name == ACCOUNT
        if entry is None:
            raise KeyError(name)
        return entry

    return lookup


# --- selection (cadrage §4.3) ----------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "platform", "euid", "account", "bwrap", "self_test", "level", "fallbacks"),
    [
        ("docker or add-on, root", "linux", 0, _pw(999, 999), "/usr/bin/bwrap", None, "setuid", 0),
        ("root, account missing", "linux", 0, None, "/usr/bin/bwrap", None, "bwrap", 1),
        ("root, account missing, no bwrap", "linux", 0, None, None, None, "none", 2),
        ("root, account is uid 0", "linux", 0, _pw(0, 0), None, None, "none", 2),
        (
            "deb/rpm or RPi, bwrap works",
            "linux",
            998,
            _pw(999, 999),
            "/usr/bin/bwrap",
            None,
            "bwrap",
            0,
        ),
        ("deb/rpm, no bubblewrap", "linux", 998, None, None, None, "none", 1),
        (
            "Docker MESHLOOM_RUN_AS_USER (seccomp refuses user namespaces)",
            "linux",
            10001,
            _pw(999, 999),
            "/usr/bin/bwrap",
            "bwrap self-test failed: No permissions to create new namespace",
            "none",
            1,
        ),
        ("macOS source", "darwin", 501, None, "/opt/homebrew/bin/bwrap", None, "none", 1),
    ],
)
def test_backend_selection_table(
    environment, platform, euid, account, bwrap, self_test, level, fallbacks
) -> None:
    tested: list[str] = []

    def fake_self_test(path: str) -> str | None:
        tested.append(path)
        return self_test

    chosen = selection.detect(
        platform=platform,
        geteuid=lambda: euid,
        getpwnam=_getpwnam(account),
        which=lambda name: bwrap if name == "bwrap" else None,
        bwrap_self_test=fake_self_test,
    )
    assert chosen.level == level, environment
    assert len(chosen.fallbacks) == fallbacks, chosen.fallbacks
    if level == "setuid":
        assert tested == []  # the dedicated uid wins without probing bwrap
        assert isinstance(chosen.backend, SetuidBackend)
        assert (chosen.backend.uid, chosen.backend.gid) == (999, 999)


def test_none_is_reported_as_hooks_only_and_warned_once(monkeypatch, caplog) -> None:
    calls: list[int] = []

    def fake_detect() -> selection.Selection:
        calls.append(1)
        return selection.Selection(NoneBackend(), ("bwrap is not installed",))

    monkeypatch.setattr(selection, "detect", fake_detect)
    selection.reset_for_tests()
    try:
        with caplog.at_level(logging.INFO, logger="app.plugins.isolation.selection"):
            first = selection.current()
            second = selection.current()
    finally:
        selection.reset_for_tests()
    assert first is second and calls == [1]
    assert first.level == "none"
    assert first.backend.label == LEVEL_LABELS["none"] == "hooks only"
    hooks_only = [r for r in caplog.records if "hooks only" in r.getMessage()]
    assert len(hooks_only) == 1 and hooks_only[0].levelno == logging.WARNING
    assert any("bwrap is not installed" in r.getMessage() for r in caplog.records)


def test_isolated_level_is_logged_at_info(caplog) -> None:
    chosen = selection.Selection(BwrapBackend("/usr/bin/bwrap"))
    with caplog.at_level(logging.INFO, logger="app.plugins.isolation.selection"):
        selection.log_selection(chosen)
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "isolated by bubblewrap" in caplog.records[0].getMessage()


# --- what each backend launches ----------------------------------------------------


def test_none_launches_the_plain_runner(tmp_path: Path) -> None:
    argv, kwargs = NoneBackend().launch(tmp_path / "plugin", tmp_path)
    assert argv == launch.runner_command()
    assert kwargs == launch.popen_kwargs(tmp_path)


def test_bwrap_command_binds_only_the_runner_package_and_the_plugin() -> None:
    backend = BwrapBackend(
        "/usr/bin/bwrap",
        app_root=Path("/opt/meshloom"),
        python="/opt/meshloom/python/bin/python3.13",
        prefix="/opt/meshloom/python",
    )
    plugin_dir = Path("/var/lib/meshloom/plugins/acme.echo")
    work_dir = Path("/tmp/meshloom-plugin-abc")
    argv = backend.command(
        ["/opt/meshloom/python/bin/python3.13", "-I", "-c", "pass"], plugin_dir, work_dir
    )
    head, child = argv[: argv.index("--")], argv[argv.index("--") + 1 :]
    assert head[0] == "/usr/bin/bwrap"
    for flag in ("--unshare-all", "--unshare-net", "--die-with-parent", "--new-session"):
        assert flag in head
    pairs = {(head[i], head[i + 1], head[i + 2]) for i in range(len(head) - 2)}
    assert ("--ro-bind", "/opt/meshloom/meshloom_plugin", "/opt/meshloom/meshloom_plugin") in pairs
    assert ("--ro-bind", "/opt/meshloom/python", "/opt/meshloom/python") in pairs
    assert ("--ro-bind", str(plugin_dir), str(plugin_dir)) in pairs
    assert ("--bind", str(work_dir), str(work_dir)) in pairs
    # Never the app root (the source and Docker layouts keep the database below it),
    # never the data directory, never writable code.
    assert "/opt/meshloom" not in head
    assert not any(a.startswith("/var/lib/meshloom") and a != str(plugin_dir) for a in head)
    assert head.index("--tmpfs") < head.index("--bind")  # the work dir sits on top of /tmp
    assert head[head.index("--chdir") + 1] == str(work_dir)
    assert child == ["/opt/meshloom/python/bin/python3.13", "-I", "-c", "pass"]


def test_setuid_drops_to_the_account_with_no_groups(tmp_path: Path) -> None:
    kwargs = SetuidBackend(999, 998).popen_kwargs(tmp_path)
    assert (kwargs["user"], kwargs["group"], kwargs["extra_groups"]) == (999, 998, [])
    assert kwargs["umask"] == 0o077
    assert kwargs["close_fds"] and kwargs["start_new_session"]
    with pytest.raises(ValueError):
        SetuidBackend(0, 998)


# --- file permissions model (dedicated uid) ----------------------------------------


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_file_permissions_model(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "plugins" / "acme.echo" / "vendor").mkdir(parents=True)
    (data / "backups").mkdir()
    for name in ("meshcore.db", "meshcore.db-wal", "update-attempt.json"):
        (data / name).write_bytes(b"secret")
        os.chmod(data / name, 0o644)
    code = data / "plugins" / "acme.echo" / "plugin.py"
    code.write_text("x = 1")
    os.chmod(code, 0o600)
    os.chmod(data / "plugins" / "acme.echo" / "vendor", 0o700)
    outside = tmp_path / "outside.txt"
    outside.write_text("not ours")
    os.chmod(outside, 0o644)
    (data / "link.db").symlink_to(outside)
    options = tmp_path / "options.json"
    options.write_text("{}")
    os.chmod(options, 0o644)

    old = os.umask(0o022)
    try:
        permissions.apply(data, extra_private=(options,))
        assert os.umask(0o022) == 0o077
    finally:
        os.umask(old)

    assert _mode(data) == 0o711
    for name in ("meshcore.db", "meshcore.db-wal", "update-attempt.json"):
        assert _mode(data / name) == 0o600
    assert _mode(data / "backups") == 0o700
    assert _mode(data / "plugins") == 0o755
    assert _mode(data / "plugins" / "acme.echo" / "vendor") == 0o755
    assert _mode(code) == 0o644
    assert _mode(outside) == 0o644  # a link is never followed
    assert _mode(options) == 0o600


# --- integration: arbitrary code under each backend --------------------------------


def _backend(level: str):
    backend = available_backends().get(level)
    if backend is None:
        pytest.skip(f"the {level} backend is not available here")
    return backend


@pytest.mark.skipif(not LINUX, reason="bubblewrap is Linux only")
def test_bwrap_child_reaches_no_database_environ_code_or_network(tmp_path: Path) -> None:
    backend = _backend("bwrap")
    secret = tmp_path / "data" / "meshcore.db"
    secret.parent.mkdir()
    secret.write_bytes(b"SQLite format 3\x00")
    result = run_probe(backend, secret)
    assert result["read_secret"].startswith("denied"), result
    assert result["read_environ"].startswith("denied"), result
    assert result["read_app_code"].startswith("denied"), result
    assert result["write_app_root"].startswith("denied"), result
    assert result["app_root_written"] is False
    # No network at all, hooks or not: even the host's loopback is out of reach.
    assert result["connect"].startswith("denied"), result


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_setuid_child_runs_as_the_account_and_reaches_no_database_or_environ() -> None:
    backend = _backend("setuid")
    data = shared_tmp_dir("meshloom-data-")
    secret = data / "meshcore.db"
    secret.write_bytes(b"SQLite format 3\x00")
    os.chmod(secret, 0o644)  # as a server with umask 022 would have left it
    old = os.umask(0o022)
    try:
        permissions.apply(data, extra_private=())
    finally:
        os.umask(old)
    result = run_probe(backend, secret)
    assert result["uid"] == backend.uid != 0
    assert result["read_secret"].startswith("denied"), result
    assert result["read_environ"].startswith("denied"), result
    assert result["write_app_root"].startswith("denied"), result
    assert result["app_root_written"] is False


def _descendants(pid: int) -> set[int]:
    found: set[int] = set()
    todo = [pid]
    while todo:
        current = todo.pop()
        try:
            tasks = os.listdir(f"/proc/{current}/task")
        except OSError:
            continue
        for tid in tasks:
            try:
                children = Path(f"/proc/{current}/task/{tid}/children").read_text().split()
            except OSError:
                continue
            for child in map(int, children):
                if child not in found:
                    found.add(child)
                    todo.append(child)
    return found


def _stat(pid: int) -> list[str] | None:
    """The `/proc/<pid>/stat` fields after the command name (state first), or None."""
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    return stat_line[stat_line.rfind(b")") + 2 :].decode().split()


def _identity(pid: int) -> str | None:
    """The process start time (stat field 22): tells a pid from a later reuse of it."""
    fields = _stat(pid)
    return fields[19] if fields else None


def _alive(pid: int, identity: str | None = None) -> bool:
    """Running or sleeping (not a zombie) and, given `identity`, still the same process."""
    fields = _stat(pid)
    if fields is None or fields[0] == "Z":
        return False
    return identity is None or fields[19] == identity


@pytest.mark.skipif(not LINUX, reason="walks /proc")
@pytest.mark.parametrize("level", ["none", "bwrap", "setuid"])
def test_sigkill_of_the_group_leaves_no_process(level: str) -> None:
    backend = _backend(level)
    plugin_dir = shared_tmp_dir("meshloom-kill-plugin-")
    work_dir = backend.prepare_work_dir(Path(tempfile.mkdtemp(prefix="meshloom-kill-work-")))
    # The child forks twice. Under bwrap the forks even leave the session, as a
    # plugin escaping through fork_exec could: the pid namespace still takes them
    # down. Without a pid namespace a new session would outlive the group kill
    # (docs/dev/plugins-security.md, "OS isolation"), so the other levels stay in it.
    # Each fork drops a marker once it is set up: the tree is read only after both,
    # never in the middle of a fork (MSHL-301).
    setsid = "    os.setsid()\n" if level == "bwrap" else ""
    script = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.fork()\n"
        f"{setsid}"
        "    open(f'ready-{os.getpid()}', 'x').close()\n"
        "time.sleep(60)\n"
    )
    proc = backend.spawn(
        [sys.executable, "-I", "-S", "-c", script],
        plugin_dir,
        work_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while len(list(work_dir.glob("ready-*"))) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(list(work_dir.glob("ready-*"))) == 2, "the forks never got ready"
        # The child and the grandchild, plus the sandbox's own init under bwrap. Each
        # pid is kept with its start time, so a pid the system hands out again after
        # the kill is not taken for a survivor.
        tree = {pid: _identity(pid) for pid in _descendants(proc.pid)}
        assert len(tree) >= 2, tree
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
        deadline = time.monotonic() + 10
        while any(_alive(p, i) for p, i in tree.items()) and time.monotonic() < deadline:
            time.sleep(0.05)
        survivors = {p: _stat(p) for p, i in tree.items() if _alive(p, i)}
        assert not survivors, survivors
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_isolation_package_uses_only_the_standard_library() -> None:
    # The root container job runs these tests with no project dependency installed.
    import re

    allowed = set(sys.stdlib_module_names) | {"app", "meshloom_plugin"}
    package = launch.APP_ROOT / "app" / "plugins" / "isolation"
    for path in [*package.glob("*.py"), launch.APP_ROOT / "app" / "plugins" / "launch.py"]:
        for match in re.finditer(
            r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", path.read_text(), re.M
        ):
            name = match.group(1) or match.group(2)
            top = name.split(".")[0]
            assert top in allowed, f"{path.name} imports {top}"
            if top == "app":
                assert name.startswith(("app.plugins.isolation", "app.plugins.launch")) or (
                    name == "app.plugins"
                ), f"{path.name} imports {name}"


def test_setuid_opens_plugin_dirs_created_after_startup(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    backend = SetuidBackend(999, 998)
    old = os.umask(0o022)
    try:
        backend.prepare_data_dir(data)
        # Created later under the server's umask 077, as an install would.
        code = data / "plugins" / "local" / "inst-1"
        code.mkdir(parents=True)
        (code / "plugin.py").write_text("x = 1")
    finally:
        os.umask(old)
    assert _mode(data / "plugins") == 0o700
    backend.prepare_plugin_dir(code)
    assert _mode(data) == 0o711
    assert _mode(data / "plugins") == _mode(data / "plugins" / "local") == 0o755
    assert _mode(code) == 0o755 and _mode(code / "plugin.py") == 0o644
