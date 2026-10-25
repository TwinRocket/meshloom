"""OS isolation of the plugin child (`app/plugins/isolation/`, MSHL-180).

The selection table runs everywhere with every probe monkeypatched. The
integration tests run a program with no runner and no hook (what a plugin gets
once it execs something) under each backend this machine offers: `bwrap` where
its self-test passes (the Linux CI job installs it), `setuid` only as root with
the `meshloom-plugin` account (the root container job of all-quality.yml). Like
`tests/test_plugin_runner.py`, needs nothing beyond the standard library and
pytest: `pytest --noconftest tests/test_plugin_isolation.py`.
"""

import contextlib
import logging
import os
import pwd
import shutil
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
from app.plugins.isolation.setuid import ACCOUNT, PurgeBlockedError, SetuidBackend
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
    (
        "environment",
        "platform",
        "euid",
        "account",
        "bwrap",
        "self_test",
        "level",
        "fallbacks",
        "caps",
    ),
    [
        (
            "docker or add-on, root",
            "linux",
            0,
            _pw(999, 999),
            "/usr/bin/bwrap",
            None,
            "setuid",
            0,
            0,
        ),
        ("root, account missing", "linux", 0, None, "/usr/bin/bwrap", None, "bwrap", 1, 0),
        ("root, account missing, no bwrap", "linux", 0, None, None, None, "none", 2, 0),
        ("root, account is uid 0", "linux", 0, _pw(0, 0), None, None, "none", 2, 0),
        (
            "deb/rpm or RPi, bwrap works",
            "linux",
            998,
            _pw(999, 999),
            "/usr/bin/bwrap",
            None,
            "bwrap",
            0,
            0,
        ),
        ("deb/rpm, no bubblewrap", "linux", 998, None, None, None, "none", 1, 0),
        (
            "Docker MESHLOOM_RUN_AS_USER (seccomp refuses user namespaces)",
            "linux",
            10001,
            _pw(999, 999),
            "/usr/bin/bwrap",
            "bwrap self-test failed: No permissions to create new namespace",
            "none",
            1,
            0,
        ),
        (
            "Docker MESHLOOM_RUN_AS_USER, CAP_SETUID and CAP_SETGID ambient (no CAP_KILL)",
            "linux",
            10001,
            _pw(999, 999),
            "/usr/bin/bwrap",
            "bwrap self-test failed: No permissions to create new namespace",
            "setuid",
            0,
            0xC0,
        ),
        (
            "capabilities but no account",
            "linux",
            10001,
            None,
            None,
            None,
            "none",
            2,
            0xC0,
        ),
        ("only CAP_SETUID", "linux", 10001, _pw(999, 999), None, None, "none", 1, 0x80),
        ("only CAP_SETGID", "linux", 10001, _pw(999, 999), None, None, "none", 1, 0x40),
        (
            "CAP_KILL is not needed nor enough",
            "linux",
            10001,
            _pw(999, 999),
            None,
            None,
            "none",
            1,
            0x60,
        ),
        ("macOS source", "darwin", 501, None, "/opt/homebrew/bin/bwrap", None, "none", 1, 0),
    ],
)
def test_backend_selection_table(
    environment, platform, euid, account, bwrap, self_test, level, fallbacks, caps
) -> None:
    tested: list[str] = []

    def fake_self_test(path: str) -> str | None:
        tested.append(path)
        return self_test

    chosen = selection.detect(
        platform=platform,
        geteuid=lambda: euid,
        capabilities=lambda: caps,
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
    # A non-root server cannot list the setuid work dir (0711, made by the plugin
    # uid): the markers then go in a sticky directory both can use.
    markers, prefix = work_dir, ""
    if level == "setuid" and os.geteuid() != 0:
        markers = shared_tmp_dir("meshloom-kill-markers-")
        os.chmod(markers, 0o1777)
        prefix = f"{markers}/"
    script = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.fork()\n"
        f"{setsid}"
        f"    open(f'{prefix}ready-{{os.getpid()}}', 'x').close()\n"
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
        while len(list(markers.glob("ready-*"))) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(list(markers.glob("ready-*"))) == 2, "the forks never got ready"
        # The child and the grandchild, plus the sandbox's own init under bwrap. Each
        # pid is kept with its start time, so a pid the system hands out again after
        # the kill is not taken for a survivor.
        tree = {pid: _identity(pid) for pid in _descendants(proc.pid)}
        assert len(tree) >= 2, tree
        # Through the backend: without CAP_KILL, setuid signals from a helper.
        backend.signal_group(proc.pid, signal.SIGKILL, wait=True)
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


# --- per-instance uids, the kill and purge helpers (MSHL-339) ----------------------


def test_instance_uids_skip_accounts_groups_root_and_the_server(monkeypatch) -> None:
    from app.plugins.isolation import setuid as setuid_module

    taken_users = {40000}
    taken_groups = {40001}

    def getpwuid(uid: int):
        if uid in taken_users:
            return _pw(uid, uid)
        raise KeyError(uid)

    def getgrgid(gid: int):
        if gid in taken_groups:
            return object()
        raise KeyError(gid)

    monkeypatch.setattr(setuid_module.pwd, "getpwuid", getpwuid)
    monkeypatch.setattr(setuid_module.grp, "getgrgid", getgrgid)
    monkeypatch.setattr(setuid_module.os, "getuid", lambda: 40003)
    monkeypatch.setattr(setuid_module.os, "geteuid", lambda: 40003)
    free = [uid for uid in range(40000, 40006) if setuid_module.uid_is_free(uid)]
    assert free == [40002, 40004, 40005]
    assert not setuid_module.uid_is_free(0)
    assert not setuid_module.uid_is_free(setuid_module.ACCOUNT_UID)
    uids = setuid_module.INSTANCE_UIDS
    assert uids.start > setuid_module.ACCOUNT_UID and uids.stop <= 65536


def test_for_uid_binds_uid_and_gid_and_keeps_the_data_dir(tmp_path: Path) -> None:
    base = SetuidBackend(10002, 10002)
    base.data_dir = tmp_path
    bound = base.for_uid(40007)
    assert (bound.uid, bound.gid, bound.data_dir) == (40007, 40007, tmp_path)
    assert bound.popen_kwargs(tmp_path)["user"] == 40007
    assert base.per_instance_uid and not NoneBackend().per_instance_uid
    assert NoneBackend().for_uid(40007) is not None


def test_non_root_setuid_signals_through_the_helper_never_killpg(monkeypatch) -> None:
    from app.plugins.isolation import setuid as setuid_module

    calls: list[tuple[str, ...]] = []
    backend = SetuidBackend(40010, 40010)
    monkeypatch.setattr(setuid_module.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(backend, "_helper", lambda op, *args: calls.append((op, *args)))

    def no_killpg(*_args):
        raise AssertionError("a server without CAP_KILL cannot killpg another uid")

    monkeypatch.setattr(setuid_module.os, "killpg", no_killpg)
    backend.signal_group(1234, signal.SIGTERM, wait=True)
    assert calls == [("kill", "1234", str(int(signal.SIGTERM)))]


def test_root_setuid_signals_directly(monkeypatch) -> None:
    import app.plugins.isolation.base as base_module
    from app.plugins.isolation import setuid as setuid_module

    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(setuid_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(base_module.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    SetuidBackend(40010, 40010).signal_group(1234, signal.SIGKILL)
    assert sent == [(1234, signal.SIGKILL)]


def test_the_helper_never_runs_as_the_server_or_root(monkeypatch) -> None:
    from app.plugins.isolation import setuid as setuid_module

    monkeypatch.setattr(setuid_module.os, "getuid", lambda: 10001)
    monkeypatch.setattr(setuid_module.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(
        setuid_module.subprocess, "run", lambda *a, **k: pytest.fail("helper started")
    )
    with pytest.raises(ValueError):
        SetuidBackend(10001, 10001).purge([Path("/tmp")])


def test_owns_something_looks_at_every_depth_on_the_same_file_system(tmp_path: Path) -> None:
    from app.plugins.isolation.setuid import owns_something

    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    mine = os.getuid()
    assert owns_something(mine, [tmp_path])
    assert owns_something(mine, [nested.parent])
    assert not owns_something(mine, [nested])  # empty: nothing below it
    (nested / "f").write_text("x")
    assert owns_something(mine, [nested])
    assert not owns_something(mine + 1, [tmp_path])
    assert not owns_something(mine, [tmp_path / "missing"])


def test_owns_something_gives_up_past_the_entry_cap(tmp_path: Path, monkeypatch) -> None:
    from app.plugins.isolation import setuid as setuid_module

    for n in range(5):
        (tmp_path / f"f{n}").write_text("x")
    monkeypatch.setattr(setuid_module, "PURGE_MAX_ENTRIES", 2)
    # Too many to look at: answer True, the purge then decides (fail closed).
    assert setuid_module.owns_something(os.getuid() + 1, [tmp_path])


def test_owns_sysv_ipc_reads_owner_and_creator(tmp_path: Path) -> None:
    from app.plugins.isolation.setuid import owns_sysv_ipc

    (tmp_path / "shm").write_text(
        "       key      shmid perms                  size  cpid  lpid nattch   uid   gid"
        "  cuid  cgid      atime      dtime      ctime                   rss                  swap\n"
        "         0          7   600                  4096   100   100      0 40001 40001"
        " 40001 40001          0          0 1700000000                     0                     0\n"
    )
    (tmp_path / "msg").write_text(
        "       key      msqid perms      cbytes       qnum lspid lrpid   uid   gid  cuid  cgid"
        "      stime      rtime      ctime\n"
        "         0          3   600           0          0     0     0 10001 10001 40002 40002"
        "          0          0 1700000000\n"
    )
    (tmp_path / "sem").write_text("garbage\n")
    assert owns_sysv_ipc(40001, tmp_path)  # owner of a segment
    assert owns_sysv_ipc(40002, tmp_path)  # creator of a queue it gave away
    assert not owns_sysv_ipc(40003, tmp_path)


def test_owns_sysv_ipc_fails_closed_when_the_tables_are_unreadable(tmp_path: Path) -> None:
    """MSHL-349: an unreadable table must not let the uid go (a segment it owns
    would reach the next instance), whatever /proc looks like (absent, or
    /proc/sysvipc masked by an empty tmpfs); only a kind the kernel lacks is
    skipped."""
    from app.plugins.isolation.setuid import kernel_has_sysv_ipc, owns_sysv_ipc

    masked = tmp_path / "proc" / "sysvipc"
    masked.mkdir(parents=True)  # /proc mounted, /proc/sysvipc an empty directory
    assert owns_sysv_ipc(40001, masked, lambda kind: True)
    assert owns_sysv_ipc(40001, tmp_path / "absent", lambda kind: True)  # no /proc
    (masked / "shm").mkdir()  # unreadable as a file
    assert owns_sysv_ipc(40001, masked, lambda kind: kind == "shm")
    assert not owns_sysv_ipc(40001, masked, lambda kind: False)  # no SysV IPC
    # The real kernel decides by default: the empty directory fails closed there.
    if all(kernel_has_sysv_ipc(kind) for kind in ("shm", "msg", "sem")):
        assert owns_sysv_ipc(40001, masked)


def test_purge_asks_the_helper_for_every_depth_and_sysv_ipc(monkeypatch) -> None:
    from app.plugins.isolation import setuid as setuid_module

    calls: list[tuple[str, ...]] = []
    backend = SetuidBackend(40010, 40010)
    monkeypatch.setattr(backend, "_helper", lambda op, *args: calls.append((op, *args)))
    # The account's uid as this machine has it, not the image's default (MSHL-349).
    monkeypatch.setattr(setuid_module, "account_uid", lambda *_a: 12345)
    backend.purge([])
    assert calls == [
        (
            "purge",
            str(setuid_module.PURGE_MAX_ENTRIES),
            "shm:shmid,msg:msqid,sem:semid",
            "40000-50000,12345",  # the plugin uids whose 0711 work dirs it may skip
        )
    ]


@pytest.mark.parametrize(
    ("entry", "uid", "spec"),
    [
        (_pw(10002, 10002), 10002, "40000-50000,10002"),
        (_pw(23456, 23456), 23456, "40000-50000,23456"),  # another PLUGIN_UID
        # No account (removed after startup): only the instance uids; a work dir the
        # account left then blocks the purge (fail closed).
        (None, None, "40000-50000"),
        (_pw(0, 0), None, "40000-50000"),  # never root
    ],
)
def test_the_account_uid_is_read_at_runtime(entry, uid, spec) -> None:
    from app.plugins.isolation import setuid as setuid_module

    assert setuid_module.account_uid(_getpwnam(entry)) == uid
    assert setuid_module.plugin_uid_spec(_getpwnam(entry)) == spec


def _completed(returncode: int, stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, "", stderr)


def test_a_purge_blocked_by_unlistable_dirs_names_them(monkeypatch) -> None:
    """MSHL-349: the directories that blocked the purge reach health."""
    from app.plugins.isolation import setuid as setuid_module

    stderr = "/tmp/a: not listable\n/tmp/b: [Errno 13] denied\n/tmp/c\\nx: not listable\n"
    monkeypatch.setattr(setuid_module.subprocess, "run", lambda *a, **k: _completed(0, stderr))
    with pytest.raises(setuid_module.PurgeBlockedError, match="not listable") as exc:
        SetuidBackend(40010, 40010).purge([Path("/tmp")])
    assert exc.value.paths == ("/tmp/a", "/tmp/c\\nx")

    # Any other failure stays a plain ValueError; other ops never report paths.
    monkeypatch.setattr(
        setuid_module.subprocess, "run", lambda *a, **k: _completed(0, "/tmp/b: boom\n")
    )
    with pytest.raises(ValueError, match="boom") as plain:
        SetuidBackend(40010, 40010).purge([Path("/tmp")])
    assert not isinstance(plain.value, setuid_module.PurgeBlockedError)
    monkeypatch.setattr(
        setuid_module.subprocess, "run", lambda *a, **k: _completed(1, "/x: not listable\n")
    )
    with pytest.raises(ValueError) as other_op:
        SetuidBackend(40010, 40010)._helper("rmtree", "/x")
    assert not isinstance(other_op.value, setuid_module.PurgeBlockedError)


def test_a_crashing_purge_helper_reports_the_exception_not_the_header(monkeypatch) -> None:
    """MSHL-352: a traceback is summed up by its last lines, escaped, still failing."""
    from app.plugins.isolation import setuid as setuid_module

    stderr = (
        "Traceback (most recent call last):\n"
        '  File "<string>", line 200, in <module>\n'
        '  File "<string>", line 241, in walk\n'
        "    raise OSError(path)\n"
        "OSError: /tmp/a\x1b[31mb\n"
    )
    monkeypatch.setattr(setuid_module.subprocess, "run", lambda *a, **k: _completed(1, stderr))
    with pytest.raises(ValueError) as exc:
        SetuidBackend(40010, 40010).purge([Path("/tmp")])
    message = str(exc.value)
    assert "Traceback" not in message
    assert message.endswith("OSError: /tmp/a\\x1b[31mb")
    assert "\x1b" not in message
    assert not isinstance(exc.value, setuid_module.PurgeBlockedError)


def test_a_crashing_purge_helper_message_falls_back_and_is_capped(monkeypatch) -> None:
    """MSHL-353: no exception line -> last stderr line or exit code; long lines capped."""
    from app.plugins.isolation import setuid as setuid_module

    def fail(stderr: str) -> str:
        monkeypatch.setattr(setuid_module.subprocess, "run", lambda *a, **k: _completed(1, stderr))
        with pytest.raises(ValueError) as exc:
            SetuidBackend(40010, 40010).purge([Path("/tmp")])
        return str(exc.value)

    group = 'Traceback (most recent call last):\n  File "<string>", line 1\n  | boom\n'
    assert fail(group).endswith("failed: boom")
    bare = fail("Traceback (most recent call last):\n")
    assert bare.endswith("failed: Traceback (most recent call last):")
    long = "OSError: " + "\x1b" * 5000
    message = fail("Traceback (most recent call last):\n" + long + "\n")
    assert len(message) < 400
    assert message.endswith("...")
    assert "\x1b" not in message
    body = message.removesuffix("...").rsplit("OSError: ", 1)[1]
    assert body and set(body.split("\\x1b")) == {""}  # whole escapes only, no stray cut


def test_a_real_exception_group_crash_is_escaped_and_summed_up(monkeypatch) -> None:
    """MSHL-353: real ExceptionGroup stderr (boxed, indented) is escaped and capped."""
    import subprocess
    import sys

    from app.plugins.isolation import setuid as setuid_module

    real_run = subprocess.run  # the module attribute is patched below
    code = (
        "raise ExceptionGroup('g', [ValueError('a\\x1b[31mb'), OSError('c' * 5000),"
        " ExceptionGroup('n', [KeyError('k'), TypeError('t')])])"
    )
    stderr = real_run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    ).stderr
    assert "Exception Group Traceback (" in stderr and "\x1b" in stderr
    monkeypatch.setattr(setuid_module.subprocess, "run", lambda *a, **k: _completed(1, stderr))
    with pytest.raises(ValueError) as exc:
        SetuidBackend(40010, 40010).purge([Path("/tmp")])
    message = str(exc.value)
    assert "\x1b" not in message
    assert "Traceback" not in message
    assert len(message) < 400
    assert "----" not in message and "+-" not in message
    assert message.count(";") == 1  # the last two exception lines, nothing else
    assert "TypeError: t" in message

    # Nested groups with 2+ sub-exceptions: no separator or counter leaks, whichever
    # exception comes last.
    for tail_exc in ("ValueError('v')", "OSError('o')"):
        code = (
            "raise ExceptionGroup('g', [ExceptionGroup('n', [KeyError('k'), TypeError('t')]),"
            f" {tail_exc}])"
        )
        err = real_run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        ).stderr
        monkeypatch.setattr(
            setuid_module.subprocess, "run", lambda *a, err=err, **k: _completed(1, err)
        )
        with pytest.raises(ValueError) as nested:
            SetuidBackend(40010, 40010).purge([Path("/tmp")])
        assert "----" not in str(nested.value) and "Traceback" not in str(nested.value)
        assert tail_exc.split("(")[0] in str(nested.value)

    # A plugin path that mentions "Traceback (" in a frame is not a crash header.
    monkeypatch.setattr(
        setuid_module.subprocess,
        "run",
        lambda *a, **k: _completed(0, '/tmp/x File "Traceback (a).py": boom\n'),
    )
    with pytest.raises(ValueError, match="boom"):
        SetuidBackend(40010, 40010).purge([Path("/tmp")])
    assert not isinstance(exc.value, setuid_module.PurgeBlockedError)

    # A non-crash failure line is escaped too, without re-mangling the helper's backslashes.
    monkeypatch.setattr(
        setuid_module.subprocess, "run", lambda *a, **k: _completed(0, "/tmp/a\\nb: x\x1b[31m\n")
    )
    with pytest.raises(ValueError) as plain:
        SetuidBackend(40010, 40010).purge([Path("/tmp")])
    assert str(plain.value).endswith("/tmp/a\\nb: x\\x1b[31m")


def test_purge_blocked_paths_are_capped(monkeypatch) -> None:
    from app.plugins.isolation import setuid as setuid_module

    stderr = "".join(f"/tmp/d{n}: not listable\n" for n in range(50))
    monkeypatch.setattr(setuid_module.subprocess, "run", lambda *a, **k: _completed(0, stderr))
    with pytest.raises(setuid_module.PurgeBlockedError) as exc:
        SetuidBackend(40010, 40010).purge([Path("/tmp")])
    assert len(exc.value.paths) == setuid_module.PURGE_BLOCKED_MAX


def test_purge_fails_when_the_server_still_sees_something(monkeypatch) -> None:
    """MSHL-347: the helper's word is not enough; the server checks again."""
    from app.plugins.isolation import setuid as setuid_module

    backend = SetuidBackend(40010, 40010)
    monkeypatch.setattr(backend, "_helper", lambda *_args: None)
    seen: list[tuple[int, object]] = []

    def owns(uid, dirs=None):
        seen.append((uid, dirs))
        return True

    monkeypatch.setattr(setuid_module, "owns_something", owns)
    with pytest.raises(ValueError, match="still owns"):
        backend.purge([Path("/nowhere")])
    with pytest.raises(ValueError, match="still owns"):
        backend.purge()  # every temp dir and System V IPC
    assert seen == [(40010, [Path("/nowhere")]), (40010, None)]


def _free_instance_uids(count: int) -> list[int]:
    from app.plugins.isolation.setuid import INSTANCE_UIDS, uid_is_free

    # MSHL-356: pytest-xdist runs these tests in parallel workers of one container, and
    # a purge removes every System V object and file the uid owns, so two workers
    # sharing a uid destroy each other's fixtures. Each worker gets its own block.
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    number = worker.removeprefix("gw")
    block = (int(number) if number.isdigit() else 0) % 20
    pool = INSTANCE_UIDS[-200 + 10 * block :][:10]
    found = [uid for uid in pool if uid_is_free(uid)][:count]
    assert len(found) == count
    return found


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_two_instance_uids_cannot_read_or_signal_each_other() -> None:
    base = _backend("setuid")
    first, second = (base.for_uid(uid) for uid in _free_instance_uids(2))
    plugin_dir = shared_tmp_dir("meshloom-uid-plugin-")
    work = first.prepare_work_dir(Path(tempfile.mkdtemp(prefix="meshloom-plugin-")))
    other = second.prepare_work_dir(Path(tempfile.mkdtemp(prefix="meshloom-plugin-")))
    try:
        assert os.lstat(work).st_uid == first.uid
        sleeper = first.spawn(
            [sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"],
            plugin_dir,
            work,
            stdin=subprocess.DEVNULL,
        )
        try:
            probe = (
                "import os, sys\n"
                "work, pid = sys.argv[1], int(sys.argv[2])\n"
                "out = []\n"
                "try:\n    os.listdir(work); out.append('listed')\n"
                "except PermissionError:\n    out.append('denied')\n"
                "try:\n    os.kill(pid, 0); out.append('signalled')\n"
                "except PermissionError:\n    out.append('denied')\n"
                "print(os.getuid(), *out)\n"
            )
            proc = second.spawn(
                [sys.executable, "-I", "-S", "-c", probe, str(work), str(sleeper.pid)],
                plugin_dir,
                other,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
            )
            out, _ = proc.communicate(timeout=30)
            assert out.decode().split() == [str(second.uid), "denied", "denied"]
            first.signal_group(sleeper.pid, signal.SIGKILL, wait=True)
            assert sleeper.wait(timeout=10) == -signal.SIGKILL
        finally:
            if sleeper.poll() is None:
                sleeper.kill()
                sleeper.wait()
    finally:
        first.remove_work_dir(work)
        second.remove_work_dir(other)
    assert not work.exists() and not other.exists()


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_purge_removes_only_what_the_instance_uid_left() -> None:
    base = _backend("setuid")
    mine, theirs = (base.for_uid(uid) for uid in _free_instance_uids(2))
    shared = shared_tmp_dir("meshloom-purge-")
    os.chmod(shared, 0o1777)
    plugin_dir = shared_tmp_dir("meshloom-purge-plugin-")
    leave = (
        "import os, sys\n"
        "d = os.path.join(sys.argv[1], 'dir-' + str(os.getuid()))\n"
        + "os.mkdir(d); open(os.path.join(d, 'f'), 'w').close()\n"
        "open(os.path.join(sys.argv[1], 'file-' + str(os.getuid())), 'w').close()\n"
    )
    try:
        for backend in (mine, theirs):
            proc = backend.spawn(
                [sys.executable, "-I", "-S", "-c", leave, str(shared)],
                plugin_dir,
                shared,
                stdin=subprocess.DEVNULL,
            )
            assert proc.wait(timeout=30) == 0
        mine.purge([shared])
        left = sorted(p.name for p in shared.iterdir())
        assert left == [f"dir-{theirs.uid}", f"file-{theirs.uid}"]
        theirs.purge([shared])
        assert list(shared.iterdir()) == []
    finally:
        import shutil

        shutil.rmtree(shared, ignore_errors=True)


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_purge_reaches_deep_leftovers_and_sysv_ipc() -> None:
    """MSHL-344: a file deep in a directory someone else owns (like /tmp/.X11-unix)
    and a System V shared memory segment are gone after the purge."""
    base = _backend("setuid")
    (mine,) = (base.for_uid(uid) for uid in _free_instance_uids(1))
    shared = shared_tmp_dir("meshloom-purge-deep-")
    os.chmod(shared, 0o755)
    socket_dir = shared / ".X11-unix" / "deeper"
    socket_dir.mkdir(parents=True)
    os.chmod(shared / ".X11-unix", 0o1777)
    os.chmod(socket_dir, 0o1777)
    plugin_dir = shared_tmp_dir("meshloom-purge-deep-plugin-")
    leave = (
        "import ctypes, os, sys\n"
        "open(os.path.join(sys.argv[1], 'X0'), 'w').close()\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "shmid = libc.shmget(0, 4096, 0o1000 | 0o600)\n"  # IPC_PRIVATE, IPC_CREAT
        "print(shmid)\n"
    )

    def segment_alive(shmid: int) -> bool:
        with open("/proc/sysvipc/shm") as table:
            return any(line.split()[1] == str(shmid) for line in table.read().splitlines()[1:])

    try:
        proc = mine.spawn(
            [sys.executable, "-I", "-S", "-c", leave, str(socket_dir)],
            plugin_dir,
            plugin_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
        )
        out, _ = proc.communicate(timeout=30)
        assert proc.returncode == 0
        shmid = int(out.decode().strip())
        assert shmid >= 0 and segment_alive(shmid)
        from app.plugins.isolation.setuid import owns_something, owns_sysv_ipc

        assert owns_something(mine.uid, [shared]) and owns_sysv_ipc(mine.uid)
        mine.purge([shared])
        assert not (socket_dir / "X0").exists()
        assert socket_dir.is_dir()  # not the uid's: kept
        assert not segment_alive(shmid)
        assert not owns_something(mine.uid, [shared]) and not owns_sysv_ipc(mine.uid)
    finally:
        import shutil

        shutil.rmtree(shared, ignore_errors=True)


def _run_as(backend, plugin_dir: Path, code: str, *args: str) -> None:
    proc = backend.spawn(
        [sys.executable, "-I", "-S", "-c", "import os, sys\n" + code, *args],
        plugin_dir,
        plugin_dir,
        stdin=subprocess.DEVNULL,
    )
    assert proc.wait(timeout=30) == 0


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_purge_fails_closed_on_a_directory_it_may_write_but_not_list() -> None:
    """MSHL-347: a 0733 directory of another uid hides what the instance uid put
    there from its own purge: the purge fails and the uid stays taken."""
    from app.plugins.isolation.setuid import owns_something

    base = _backend("setuid")
    mine, theirs = (base.for_uid(uid) for uid in _free_instance_uids(2))
    shared = shared_tmp_dir("meshloom-purge-unlistable-")
    os.chmod(shared, 0o1777)
    trap = shared / "trap"
    plugin_dir = shared_tmp_dir("meshloom-purge-unlistable-plugin-")
    try:
        _run_as(
            theirs, plugin_dir, "os.mkdir(sys.argv[1]); os.chmod(sys.argv[1], 0o733)", str(trap)
        )
        _run_as(
            mine, plugin_dir, "open(os.path.join(sys.argv[1], 'hidden'), 'w').close()", str(trap)
        )
        with pytest.raises(PurgeBlockedError, match="not listable") as blocked:
            mine.purge([shared])
        assert blocked.value.paths == (str(trap),)
        if os.geteuid() == 0:
            # Root lists it: even without the helper's word the server sees the file.
            assert (trap / "hidden").stat().st_uid == mine.uid
            assert owns_something(mine.uid, [shared])
        # Once listable, the purge removes it and succeeds.
        _run_as(theirs, plugin_dir, "os.chmod(sys.argv[1], 0o777)", str(trap))
        mine.purge([shared])
        assert list(trap.iterdir()) == []
    finally:
        theirs.purge([shared])
        mine.purge([shared])
        shutil.rmtree(shared, ignore_errors=True)


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_purge_skips_other_work_dirs_and_dirs_it_cannot_enter() -> None:
    """Another instance's work directory (0711 without root) and a 0700 directory of
    another uid do not fail the purge: the uid could not write in them."""
    base = _backend("setuid")
    mine, theirs = (base.for_uid(uid) for uid in _free_instance_uids(2))
    shared = shared_tmp_dir("meshloom-purge-skip-")
    os.chmod(shared, 0o1777)
    plugin_dir = shared_tmp_dir("meshloom-purge-skip-plugin-")
    work = shared / "meshloom-plugin-abc123_x"
    closed = shared / "closed"
    make = "for path, mode in zip(sys.argv[1::2], sys.argv[2::2]):\n    os.mkdir(path); os.chmod(path, int(mode, 8))"
    try:
        _run_as(theirs, plugin_dir, make, str(work), "711", str(closed), "700")
        mine.purge([shared])
        assert work.is_dir() and closed.is_dir()
        # The same 0711 directory under another name is not a work directory.
        _run_as(
            theirs, plugin_dir, "os.rename(sys.argv[1], sys.argv[2])", str(work), str(shared / "x")
        )
        with pytest.raises(ValueError, match="not listable"):
            mine.purge([shared])
    finally:
        theirs.purge([shared])
        shutil.rmtree(shared, ignore_errors=True)


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_purge_and_rmtree_remove_dirs_the_uid_made_unlistable() -> None:
    """MSHL-348: a plugin leaving its own directories 0300 or 0000 (its work dir or
    anything in the temp dirs) does not keep its uid forever: the helper gives the
    owner rwx back first, without following a symlink."""
    base = _backend("setuid")
    (mine,) = (base.for_uid(uid) for uid in _free_instance_uids(1))
    shared = shared_tmp_dir("meshloom-purge-locked-")
    os.chmod(shared, 0o1777)
    outside = shared_tmp_dir("meshloom-purge-locked-outside-")
    os.chmod(outside, 0o1777)
    target = outside / "target"
    plugin_dir = shared_tmp_dir("meshloom-purge-locked-plugin-")
    # No chown: the tests also run as a non-root server holding only CAP_SETUID.
    _run_as(mine, plugin_dir, "os.mkdir(sys.argv[1]); os.chmod(sys.argv[1], 0o500)", str(target))
    # top 0300 > sub 0000, sub2 0300 > deep 0000 holding a file; a symlink to a
    # directory of the uid outside the purged tree must keep its mode.
    lock = (
        "top = sys.argv[1]\n"
        "os.makedirs(os.path.join(top, 'sub2', 'deep'))\n"
        "open(os.path.join(top, 'sub2', 'deep', 'f'), 'w').close()\n"
        "os.mkdir(os.path.join(top, 'sub'))\n"
        "os.symlink(sys.argv[2], os.path.join(top, 'link'))\n"
        "os.chmod(os.path.join(top, 'sub'), 0o000)\n"
        "os.chmod(os.path.join(top, 'sub2', 'deep'), 0o000)\n"
        "os.chmod(os.path.join(top, 'sub2'), 0o300)\n"
        "os.chmod(top, int(sys.argv[3], 8))\n"
    )
    try:
        for mode in ("300", "000"):
            top = shared / f"locked-{mode}"
            _run_as(mine, plugin_dir, lock, str(top), str(target), mode)
            mine.purge([shared])
            assert not top.exists()
            assert stat.S_IMODE(os.lstat(target).st_mode) == 0o500
        # The work-dir removal (the helper's `rmtree`, used without root) too.
        work = shared / "work"
        _run_as(mine, plugin_dir, lock, str(work), str(target), "300")
        mine._helper("rmtree", str(work))
        assert not work.exists()
        assert stat.S_IMODE(os.lstat(target).st_mode) == 0o500
    finally:
        mine.purge([shared, outside])
        shutil.rmtree(shared, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_purge_skips_a_work_dir_only_when_a_plugin_uid_owns_it() -> None:
    """MSHL-348: a 0711 `meshloom-plugin-*` directory of a uid outside the plugin
    uids (one that could hide files for the instance) fails the purge, as does one
    whose name mkdtemp would not make."""
    from app.plugins.isolation.setuid import INSTANCE_UIDS, account_uid, uid_is_free

    base = _backend("setuid")
    (mine,) = (base.for_uid(uid) for uid in _free_instance_uids(1))
    account = account_uid()
    stranger = next(u for u in range(30000, 39999) if uid_is_free(u))
    assert stranger not in INSTANCE_UIDS and stranger != account
    other = next(u for u in reversed(INSTANCE_UIDS) if u != mine.uid and uid_is_free(u))
    shared = shared_tmp_dir("meshloom-purge-stranger-")
    os.chmod(shared, 0o1777)
    plugin_dir = shared_tmp_dir("meshloom-purge-stranger-plugin-")
    make = "os.mkdir(sys.argv[1]); os.chmod(sys.argv[1], 0o711)"
    try:
        # Made by each owner (no chown: also runs as a non-root server).
        for name, owner, fails in (
            ("meshloom-plugin-abcd1234", other, False),
            ("meshloom-plugin-abcd1235", account, False),
            ("meshloom-plugin-abcd1236", stranger, True),
            ("meshloom-plugin-abc", other, True),
        ):
            owner_backend = base.for_uid(owner)
            work = shared / name
            _run_as(owner_backend, plugin_dir, make, str(work))
            assert os.lstat(work).st_uid == owner
            if fails:
                with pytest.raises(ValueError, match="not listable"):
                    mine.purge([shared])
            else:
                mine.purge([shared])
            _run_as(owner_backend, plugin_dir, "os.rmdir(sys.argv[1])", str(work))
    finally:
        shutil.rmtree(shared, ignore_errors=True)


@pytest.mark.skipif(not LINUX, reason="the dedicated uid is Linux only")
def test_the_account_uid_comes_from_the_passwd_entry(monkeypatch) -> None:
    """MSHL-349: an image built with another PLUGIN_UID. The skipped work dirs are
    those of the account's real uid, not of the image's default 10002."""
    from app.plugins.isolation import setuid as setuid_module

    base = _backend("setuid")
    mine, rebuilt = (base.for_uid(uid) for uid in _free_instance_uids(2))
    shared = shared_tmp_dir("meshloom-purge-account-")
    os.chmod(shared, 0o1777)
    plugin_dir = shared_tmp_dir("meshloom-purge-account-plugin-")
    # The account as if it had `rebuilt`'s uid; the real account's uid is then a stranger.
    real = setuid_module.account_uid()
    monkeypatch.setattr(setuid_module, "account_uid", lambda *_a: rebuilt.uid)
    monkeypatch.setattr(setuid_module, "INSTANCE_UIDS", range(mine.uid, mine.uid + 1))
    work = shared / "meshloom-plugin-abcd1234"
    make = "os.mkdir(sys.argv[1]); os.chmod(sys.argv[1], 0o711)"
    try:
        _run_as(rebuilt, plugin_dir, make, str(work))
        mine.purge([shared])
        _run_as(rebuilt, plugin_dir, "os.rmdir(sys.argv[1])", str(work))
        if real is not None:
            legacy = base.for_uid(real)
            _run_as(legacy, plugin_dir, make, str(work))
            with pytest.raises(PurgeBlockedError):
                mine.purge([shared])
            _run_as(legacy, plugin_dir, "os.rmdir(sys.argv[1])", str(work))
    finally:
        shutil.rmtree(shared, ignore_errors=True)


def _helper_with_proc(proc: str) -> str:
    from app.plugins.isolation import setuid as setuid_module

    source = setuid_module._HELPER_SOURCE.replace('PROC = "/proc"', f"PROC = {proc!r}")
    assert source != setuid_module._HELPER_SOURCE
    return source


def _helper_directly(source: str, *args: str) -> subprocess.CompletedProcess:
    # The helper as this (unprivileged) uid, the way `_helper` starts it.
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", source, str(launch.APP_ROOT), *args],
        capture_output=True,
        encoding="utf-8",
        errors="backslashreplace",
        env=launch.runner_env(),
        timeout=30,
        check=False,
    )


@pytest.mark.skipif(not LINUX, reason="the helper is Linux only")
def test_without_proc_the_purge_fails_on_sysv_ipc(monkeypatch, tmp_path: Path) -> None:
    """MSHL-349: without /proc, or with /proc/sysvipc masked by an empty directory,
    the helper cannot list the System V IPC objects the uid owns; the purge fails
    (uid kept) instead of skipping them (the kernel has them: IPC_INFO)."""
    from app.plugins.isolation import setuid as setuid_module

    kinds = ",".join(f"{kind}:{column}" for kind, column in setuid_module.SYSV_IPC_KINDS)
    no_proc = _helper_with_proc("/nonexistent/proc")
    backends = available_backends()
    if "setuid" in backends:
        (runner,) = (backends["setuid"].for_uid(uid) for uid in _free_instance_uids(1))
        empty = shared_tmp_dir("meshloom-noproc-ipc-")
        fake = shared_tmp_dir("meshloom-masked-proc-")
        os.chmod(fake, 0o755)
        (fake / "self").mkdir(mode=0o755)
        (fake / "sysvipc").mkdir(mode=0o755)  # masked: an empty directory
        masked = _helper_with_proc(str(fake))
        try:
            monkeypatch.setattr(setuid_module, "_HELPER_SOURCE", no_proc)
            with pytest.raises(ValueError, match="sysvipc shm: cannot be listed"):
                runner.purge([empty])
            monkeypatch.setattr(setuid_module, "_HELPER_SOURCE", masked)
            with pytest.raises(ValueError, match="sysvipc shm: cannot be listed"):
                runner.purge([empty])
            monkeypatch.undo()
            runner.purge([empty])
        finally:
            shutil.rmtree(empty, ignore_errors=True)
            shutil.rmtree(fake, ignore_errors=True)
        return
    if os.geteuid() == 0:
        pytest.skip("root without the meshloom-plugin account")
    empty = tmp_path / "empty"
    empty.mkdir()
    args = ("purge", "100", kinds, "40000-50000", str(empty))
    proc = _helper_directly(no_proc, *args)
    assert proc.returncode == 0
    assert "sysvipc shm: cannot be listed (No such file or directory)" in proc.stderr
    masked = tmp_path / "proc"
    (masked / "self").mkdir(parents=True)
    (masked / "sysvipc").mkdir()
    proc = _helper_directly(_helper_with_proc(str(masked)), *args)
    assert proc.stderr.splitlines() == [
        f"sysvipc {kind}: cannot be listed (No such file or directory)"
        for kind in ("shm", "msg", "sem")
    ]
    assert _helper_directly(setuid_module._HELPER_SOURCE, *args).stderr == ""


@pytest.mark.skipif(
    not LINUX or os.geteuid() == 0, reason="the helper is Linux only; root lists all"
)
def test_other_helper_errors_cannot_forge_a_blocking_path(tmp_path: Path) -> None:
    """MSHL-349: every path the helper prints is escaped, so a name holding a line
    break cannot add a fake "not listable" line to the admin banner."""
    from app.plugins.isolation import setuid as setuid_module

    base = tmp_path / "b\nforged: not listable"
    base.mkdir()
    (base / "one").write_text("")
    (base / "two").write_text("")
    kinds = ",".join(f"{kind}:{column}" for kind, column in setuid_module.SYSV_IPC_KINDS)
    proc = _helper_directly(
        setuid_module._HELPER_SOURCE, "purge", "1", kinds, "40000-50000", str(base)
    )
    assert proc.stderr.splitlines() == [f"{tmp_path}/b\\nforged: not listable: more than 1 entries"]
    lines = proc.stderr.splitlines()
    assert not [line for line in lines if line.endswith(setuid_module.NOT_LISTABLE)]


@pytest.mark.skipif(
    not LINUX or os.geteuid() == 0, reason="the helper is Linux only; root lists all"
)
def test_blocking_paths_keep_printable_characters(tmp_path: Path) -> None:
    """MSHL-349: a blocking path keeps accents (shown as is in the UI) and escapes
    control characters, so it stays one line."""
    from app.plugins.isolation import setuid as setuid_module

    trap = tmp_path / "café\x1bnoir\nfin"
    trap.mkdir()
    trap.chmod(0o311)  # enterable, not listable (a scanned dir, not removed)
    kinds = ",".join(f"{kind}:{column}" for kind, column in setuid_module.SYSV_IPC_KINDS)
    try:
        proc = _helper_directly(
            setuid_module._HELPER_SOURCE, "purge", "100", kinds, "40000-50000", str(trap)
        )
    finally:
        trap.chmod(0o700)
    expected = f"{tmp_path}/café\\x1bnoir\\nfin{setuid_module.NOT_LISTABLE}"
    assert proc.stderr.splitlines() == [expected]


@pytest.mark.skipif(not LINUX, reason="the helper is Linux only")
def test_without_proc_a_locked_dir_fails_the_removal_clearly(monkeypatch) -> None:
    """MSHL-349: with no /proc the helper cannot chmod through an O_PATH descriptor;
    the removal fails (fail closed) and says why, instead of NotImplementedError."""
    from app.plugins.isolation import setuid as setuid_module

    no_proc = _helper_with_proc("/nonexistent/proc")
    assert no_proc != setuid_module._HELPER_SOURCE
    backends = available_backends()
    if "setuid" in backends:
        (runner,) = (backends["setuid"].for_uid(uid) for uid in _free_instance_uids(1))
    elif os.geteuid() != 0:
        runner = None  # the helper as this unprivileged uid
    else:
        pytest.skip("root without the meshloom-plugin account")
    shared = shared_tmp_dir("meshloom-noproc-")
    os.chmod(shared, 0o1777)
    plugin_dir = shared_tmp_dir("meshloom-noproc-plugin-")
    top = shared / "locked"
    lock = "os.makedirs(os.path.join(sys.argv[1], 'sub')); os.chmod(sys.argv[1], 0o300)"
    try:
        if runner is not None:
            _run_as(runner, plugin_dir, lock, str(top))
            monkeypatch.setattr(setuid_module, "_HELPER_SOURCE", no_proc)
            with pytest.raises(ValueError, match="/proc is not mounted"):
                runner._helper("rmtree", str(top))
            with pytest.raises(ValueError, match="/proc is not mounted"):
                runner.purge([shared])
            monkeypatch.undo()
            runner.purge([shared])
        else:
            os.makedirs(top / "sub")
            os.chmod(top, 0o300)
            proc = _helper_directly(no_proc, "rmtree", str(top))
            assert proc.returncode != 0
            assert "/proc is not mounted" in proc.stderr.strip().splitlines()[-1]
            assert top.exists()
    finally:
        with contextlib.suppress(OSError):
            os.chmod(top, 0o700)
        shutil.rmtree(shared, ignore_errors=True)
