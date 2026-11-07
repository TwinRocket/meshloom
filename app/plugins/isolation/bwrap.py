"""Level `bwrap`: the child runs in a bubblewrap sandbox (deb/rpm, RPi image, source).

Every namespace is new (`--unshare-all`): no network but a loopback that is
down, its own pid namespace and `/proc` (the server's `/proc/<pid>` does not
exist there), its own mount namespace built from nothing. What it sees:

- read-only: `/usr` (and the `/bin`, `/lib*` and `/sbin` links or directories),
  `/etc/ld.so.cache`, `/etc/localtime`, the interpreter's prefix when it lives
  outside `/usr`, `<app_root>/meshloom_plugin` (the runner) and the plugin's
  directory;
- writable: a fresh tmpfs on `/tmp` and the private work dir bound on top of
  it; the rest of the tree is read-only (`--remount-ro /`).

Not the whole app root, unlike the scoping draft: a source checkout keeps its
database in `<app_root>/data` and the Docker image in `/app/data`, so binding
the root would hand the database back. `--die-with-parent` kills the sandbox
with the server, and the sandbox's pid 1 takes every process of the namespace
down with it when the supervisor kills the process group.

Two hardening flags on top (docs/dev/plugins-security.md, "`bwrap`"):

- `--seccomp FD` (MSHL-413): a seccomp program of its own
  (`meshloom_plugin.seccomp.BWRAP_DENIED`), built from the same tables as the
  runner's filter and written to a pipe whose read end the child inherits. bwrap
  installs it right before it executes the interpreter; the runner's filter is
  stacked on top later, so a bypass of one still meets the other. No program on
  an architecture without a table (logged once).
- `--unshare-user --disable-userns` (MSHL-414, bubblewrap 0.8 and later): the
  kernel refuses any nested user namespace, on top of the filters' `unshare` and
  `setns` refusal. Detected at startup (`bwrap --version`, then the self-test);
  an older bwrap, or one that fails with it, runs without it and a warning is
  logged.

bwrap propagates the child's exit status, and a child killed by a signal as
`128 + signal` (the supervisor maps that back).
"""

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.plugins import launch
from app.plugins.isolation.base import IsolationBackend
from meshloom_plugin import seccomp

SELF_TEST_TIMEOUT_SECONDS = 15.0
# The top-level system paths an interpreter may need: bound read-only when they
# are directories, recreated as the same symlink when they are links (merged /usr).
SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
SYSTEM_FILES = ("/etc/ld.so.cache", "/etc/localtime")
# The first bubblewrap with `--disable-userns` (Ubuntu 22.04 ships 0.6).
DISABLE_USERNS_SINCE = (0, 8)
VERSION_TIMEOUT_SECONDS = 5.0
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


class BwrapBackend(IsolationBackend):
    level = "bwrap"

    def __init__(
        self,
        bwrap: str,
        *,
        app_root: Path = launch.APP_ROOT,
        python: str | None = None,
        prefix: str | None = None,
        disable_userns: bool = False,
        seccomp_abi: str | None = None,
        seccomp_filter: bool = True,
    ) -> None:
        self.bwrap = bwrap
        self.app_root = Path(app_root)
        # The real interpreter, not a venv link: `-I -S` reads nothing from the venv,
        # and the sandbox then needs no view of it.
        self.python = os.path.realpath(python or sys.executable)
        self.prefix = os.path.realpath(prefix or sys.base_prefix)
        self.disable_userns = disable_userns
        # The interpreter's ABI: the sandbox runs this very interpreter.
        abi = seccomp.native_abi() if seccomp_abi is None else seccomp_abi
        self.seccomp_abi = abi if abi in seccomp.TABLES else None
        # `seccomp_filter=False` only for tests that need a box without it.
        self.seccomp_program = (
            seccomp.program_bytes(seccomp.build_filter(self.seccomp_abi, seccomp.BWRAP_DENIED))
            if self.seccomp_abi is not None and seccomp_filter
            else None
        )

    def system_args(self) -> list[str]:
        args: list[str] = []
        for path in SYSTEM_DIRS:
            if os.path.islink(path):
                args += ["--symlink", os.readlink(path), path]
            elif os.path.isdir(path):
                args += ["--ro-bind", path, path]
        for path in SYSTEM_FILES:
            args += ["--ro-bind-try", path, path]
        bound = [p for p in SYSTEM_DIRS if os.path.isdir(p) and not os.path.islink(p)]
        for path in (self.prefix, os.path.dirname(self.python)):
            if not any(_inside(path, root) for root in bound):
                args += ["--ro-bind", path, path]
                bound.append(path)
        return args

    def command(
        self,
        argv: list[str],
        plugin_dir: Path,
        work_dir: Path,
        *,
        seccomp_fd: int | None = None,
    ) -> list[str]:
        runner_pkg = str(self.app_root / "meshloom_plugin")
        if argv and os.path.realpath(argv[0]) == self.python:
            argv = [self.python, *argv[1:]]
        hardening: list[str] = []
        if self.disable_userns:
            # `--disable-userns` needs the user namespace `--unshare-all` only tries.
            hardening += ["--unshare-user", "--disable-userns"]
        if seccomp_fd is not None:
            hardening += ["--seccomp", str(seccomp_fd)]
        return [
            self.bwrap,
            "--unshare-all",
            "--unshare-net",
            *hardening,
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            *self.system_args(),
            "--ro-bind",
            runner_pkg,
            runner_pkg,
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--ro-bind",
            str(plugin_dir),
            str(plugin_dir),
            "--bind",
            str(work_dir),
            str(work_dir),
            # The tmpfs root bwrap builds the tree on: read-only once the mount
            # points exist (/tmp and the work dir are mounts of their own).
            "--remount-ro",
            "/",
            "--chdir",
            str(work_dir),
            "--",
            *argv,
        ]

    def seccomp_fd(self) -> int | None:
        """A pipe's read end holding the bwrap filter (None without a table). The
        caller closes it once the child started (`launch.close_passed_fds`)."""
        if self.seccomp_program is None:
            return None
        read_fd, write_fd = os.pipe()
        try:
            # A few hundred bytes: far below the pipe's buffer, the write never blocks.
            os.write(write_fd, self.seccomp_program)
        except OSError:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        return read_fd

    def launch(self, plugin_dir: Path, work_dir: Path) -> tuple[list[str], dict[str, Any]]:
        fd = self.seccomp_fd()
        argv = self.command(
            launch.runner_command(), Path(plugin_dir), Path(work_dir), seccomp_fd=fd
        )
        kwargs = self.popen_kwargs(Path(work_dir))
        if fd is not None:
            kwargs["pass_fds"] = (fd,)
        return argv, kwargs

    def spawn(
        self, cmd: list[str], plugin_dir: Path, tmp_dir: Path, **extra: Any
    ) -> "subprocess.Popen[Any]":
        fd = self.seccomp_fd()
        kwargs = {**self.popen_kwargs(Path(tmp_dir)), **extra}
        if fd is not None:
            kwargs["pass_fds"] = (*kwargs.get("pass_fds", ()), fd)
        try:
            argv = self.command(cmd, Path(plugin_dir), Path(tmp_dir), seccomp_fd=fd)
            return subprocess.Popen(argv, **kwargs)
        finally:
            if fd is not None:
                os.close(fd)


def bwrap_version(bwrap: str) -> tuple[int, ...] | None:
    """`bwrap --version` ("bubblewrap 0.11.0") as integers; None when unreadable."""
    try:
        result = subprocess.run(
            [bwrap, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            check=False,
            env=launch.runner_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search(result.stdout.decode("utf-8", "replace"))
    if result.returncode != 0 or match is None:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


# Run in the sandbox by the self-test: the runner package imports, and the bwrap
# filter is in place when one was passed (`Seccomp: 2`, before any runner filter).
_SELF_TEST_PROBE = """
import sys
sys.path.insert(0, {app_root!r})
import meshloom_plugin
if {filtered!r}:
    with open("/proc/self/status") as fh:
        modes = [line.split()[1] for line in fh if line.startswith("Seccomp:")]
    if modes != ["2"]:
        sys.exit("the bwrap seccomp filter is not installed")
"""


def run_self_test(backend: BwrapBackend) -> str | None:
    """Start the interpreter in the sandbox the runner would get, with every flag
    `backend` passes. None when it works, else why not (user namespaces refused, a
    path missing, a flag this bwrap does not know...)."""
    with tempfile.TemporaryDirectory(prefix="meshloom-bwrap-test-") as tmp:
        plugin_dir = Path(tmp) / "plugin"
        work_dir = Path(tmp) / "work"
        plugin_dir.mkdir()
        work_dir.mkdir(mode=0o700)
        probe = _SELF_TEST_PROBE.format(
            app_root=str(backend.app_root), filtered=backend.seccomp_program is not None
        )
        argv = [backend.python, "-I", "-S", "-B", "-c", probe]
        fd = backend.seccomp_fd()
        try:
            result = subprocess.run(
                backend.command(argv, plugin_dir, work_dir, seccomp_fd=fd),
                cwd=work_dir,
                env=launch.runner_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=SELF_TEST_TIMEOUT_SECONDS,
                pass_fds=() if fd is None else (fd,),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"bwrap self-test failed: {type(exc).__name__}"
        finally:
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        return f"bwrap self-test failed: {detail[-1] if detail else f'exit {result.returncode}'}"
    return None


def probe(bwrap: str | None = None) -> tuple[BwrapBackend | None, tuple[str, ...]]:
    """The backend with every hardening this bwrap supports and a list of warnings
    (hardening left out, and why); or None and why the sandbox does not work."""
    path = bwrap or shutil.which("bwrap")
    if path is None:
        return None, ("bwrap is not installed",)
    warnings: list[str] = []
    version = bwrap_version(path)
    shown = ".".join(map(str, version)) if version else "of unknown version"
    disable_userns = version is not None and version[:2] >= DISABLE_USERNS_SINCE
    if not disable_userns:
        warnings.append(
            f"bwrap {shown} has no --disable-userns (0.8 or later): nested user "
            "namespaces are refused by the seccomp filters only"
        )
    backend = BwrapBackend(path, disable_userns=disable_userns)
    if backend.seccomp_program is None:
        warnings.append(
            f"no seccomp table for {os.uname().machine}: bwrap runs without its own filter"
        )
    error = run_self_test(backend)
    if error is not None and disable_userns:
        fallback = BwrapBackend(path, disable_userns=False)
        if run_self_test(fallback) is None:
            warnings.append(
                f"bwrap {shown} fails with --disable-userns ({error}): running without it, "
                "nested user namespaces are refused by the seccomp filters only"
            )
            return fallback, tuple(warnings)
    if error is not None:
        return None, (error,)
    return backend, tuple(warnings)


def self_test(bwrap: str | None = None) -> str | None:
    """None when the bwrap sandbox works here (with whatever hardening `probe`
    keeps), else why not."""
    backend, reasons = probe(bwrap)
    return None if backend is not None else reasons[-1]
