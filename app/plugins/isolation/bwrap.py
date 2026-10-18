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

bwrap propagates the child's exit status, and a child killed by a signal as
`128 + signal` (the supervisor maps that back).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.plugins import launch
from app.plugins.isolation.base import IsolationBackend

SELF_TEST_TIMEOUT_SECONDS = 15.0
# The top-level system paths an interpreter may need: bound read-only when they
# are directories, recreated as the same symlink when they are links (merged /usr).
SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32")
SYSTEM_FILES = ("/etc/ld.so.cache", "/etc/localtime")


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
    ) -> None:
        self.bwrap = bwrap
        self.app_root = Path(app_root)
        # The real interpreter, not a venv link: `-I -S` reads nothing from the venv,
        # and the sandbox then needs no view of it.
        self.python = os.path.realpath(python or sys.executable)
        self.prefix = os.path.realpath(prefix or sys.base_prefix)

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

    def command(self, argv: list[str], plugin_dir: Path, work_dir: Path) -> list[str]:
        runner_pkg = str(self.app_root / "meshloom_plugin")
        if argv and os.path.realpath(argv[0]) == self.python:
            argv = [self.python, *argv[1:]]
        return [
            self.bwrap,
            "--unshare-all",
            "--unshare-net",
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


def self_test(bwrap: str | None = None) -> str | None:
    """Start the interpreter in the sandbox the runner would get. None when it works,
    else why not (bwrap missing, user namespaces refused, a path missing...)."""
    path = bwrap or shutil.which("bwrap")
    if path is None:
        return "bwrap is not installed"
    backend = BwrapBackend(path)
    with tempfile.TemporaryDirectory(prefix="meshloom-bwrap-test-") as tmp:
        plugin_dir = Path(tmp) / "plugin"
        work_dir = Path(tmp) / "work"
        plugin_dir.mkdir()
        work_dir.mkdir(mode=0o700)
        probe = f"import sys; sys.path.insert(0, {str(backend.app_root)!r}); import meshloom_plugin"
        argv = [backend.python, "-I", "-S", "-B", "-c", probe]
        try:
            result = subprocess.run(
                backend.command(argv, plugin_dir, work_dir),
                cwd=work_dir,
                env=launch.runner_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=SELF_TEST_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"bwrap self-test failed: {type(exc).__name__}"
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        return f"bwrap self-test failed: {detail[-1] if detail else f'exit {result.returncode}'}"
    return None
