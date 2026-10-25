"""Shared by the isolation and runner tests: which backends run here, and a probe.

Standard library and the isolation package only, so both test files keep
running with `--noconftest` (the root container job of all-quality.yml).
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

from app.plugins import launch
from app.plugins.isolation import selection
from app.plugins.isolation.base import IsolationBackend
from app.plugins.isolation.bwrap import BwrapBackend, self_test
from app.plugins.isolation.none import NoneBackend
from app.plugins.isolation.setuid import SetuidBackend

LINUX = sys.platform.startswith("linux")


@cache
def available_backends() -> dict[str, IsolationBackend]:
    """`none` always, `bwrap` when its self-test passes, `setuid` with the account as
    root or with `CAP_SETUID`, `CAP_SETGID` and `CAP_KILL` (the image's `MESHLOOM_RUN_AS_USER`)."""
    backends: dict[str, IsolationBackend] = {"none": NoneBackend()}
    if not LINUX:
        return backends
    import shutil

    path = shutil.which("bwrap")
    if path is not None and self_test(path) is None:
        backends["bwrap"] = BwrapBackend(path)
    if os.geteuid() == 0 or selection.can_switch_uid(selection.effective_capabilities()):
        import pwd

        account = selection._account(pwd.getpwnam)
        if account is not None:
            backends["setuid"] = SetuidBackend(*account)
    return backends


def shared_tmp_dir(prefix: str) -> Path:
    """A directory under the system temp dir, traversable by the plugin uid (pytest's
    own base temp dir is 0700, which the dedicated uid cannot enter)."""
    path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    os.chmod(path, 0o755)
    return path


# Runs with no runner and no hook: what a plugin gets once it execs a program
# (the `_posixsubprocess` open path). Every attempt is reported, never raised.
PROBE = r"""
import json, os, socket, sys
targets = json.loads(sys.argv[1])
out = {"uid": os.getuid()}
def attempt(name, fn):
    try:
        fn()
        out[name] = "allowed"
    except OSError as exc:
        out[name] = "denied: " + type(exc).__name__
def write_app_root():
    with open(os.path.join(targets["app_root"], targets["write_name"]), "wb") as fh:
        fh.write(b"x")
attempt("read_secret", lambda: open(targets["secret"], "rb").read())
attempt("read_environ", lambda: open("/proc/%d/environ" % targets["server_pid"], "rb").read())
attempt("read_app_code", lambda: open(targets["app_code"], "rb").read())
attempt("write_app_root", write_app_root)
attempt("connect", lambda: socket.create_connection(("127.0.0.1", targets["port"]), timeout=3).close())
print(json.dumps(out))
"""


def run_probe(backend: IsolationBackend, secret: Path) -> dict[str, Any]:
    """Run PROBE under `backend` against a listening loopback port and `secret`."""
    plugin_dir = shared_tmp_dir("meshloom-probe-plugin-")
    work_dir = backend.prepare_work_dir(Path(tempfile.mkdtemp(prefix="meshloom-probe-work-")))
    write_name = f".isolation-probe-{os.getpid()}"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    targets = {
        "secret": str(secret),
        "server_pid": os.getpid(),
        "app_code": str(launch.APP_ROOT / "app" / "main.py"),
        "app_root": str(launch.APP_ROOT),
        "write_name": write_name,
        "port": listener.getsockname()[1],
    }
    try:
        proc = backend.spawn(
            [sys.executable, "-I", "-S", "-c", PROBE, json.dumps(targets)],
            plugin_dir,
            work_dir,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0, err.decode(errors="replace")
        result = json.loads(out)
        # A write that landed in a throwaway mount is no write: check the real tree.
        written = launch.APP_ROOT / write_name
        result["app_root_written"] = written.exists()
        written.unlink(missing_ok=True)
        return result
    finally:
        listener.close()
