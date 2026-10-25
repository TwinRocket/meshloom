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
    assert not owns_sysv_ipc(40001, tmp_path / "missing")


def test_purge_asks_the_helper_for_every_depth_and_sysv_ipc(monkeypatch) -> None:
    from app.plugins.isolation import setuid as setuid_module

    calls: list[tuple[str, ...]] = []
    backend = SetuidBackend(40010, 40010)
    monkeypatch.setattr(backend, "_helper", lambda op, *args: calls.append((op, *args)))
    backend.purge([])
    assert calls == [
        ("purge", str(setuid_module.PURGE_MAX_ENTRIES), "shm:shmid,msg:msqid,sem:semid")
    ]


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

    found = [uid for uid in INSTANCE_UIDS[-200:] if uid_is_free(uid)][:count]
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
        with pytest.raises(ValueError, match="not listable"):
            mine.purge([shared])
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
    work = shared / "meshloom-plugin-abc123"
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
