"""Launch contract of the plugin child (`meshloom_plugin.runner`), server side.

The project is not an installed package and `-I` drops the cwd, the script
directory and `PYTHONPATH` from `sys.path`, so the child cannot use
`python -m meshloom_plugin.runner`. It runs a fixed bootstrap instead:

    <python> -I -S -B -X utf8 -c "<bootstrap>"

`-S` also skips `site`: no site-packages on the path and no `.pth` file
executed before the runner freezes the process. The parameters arrive as one
JSON line on stdin; stdin and stdout then carry the JSON-RPC stream, stderr is
the plugin's log. The supervisor (MSHL-179) owns the process; this module
builds everything it passes to `subprocess.Popen`, cwd included: the child's
cwd is its only writable place, and the runner refuses to start unless that cwd
is the `work_dir` named in its parameters (docs/dev/plugins.md, "Runner").
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from meshloom_plugin import policy

APP_ROOT = Path(__file__).resolve().parents[2]
RUNNER_MODULE = "meshloom_plugin.runner"
ISOLATION_LEVELS = policy.ISOLATION_LEVELS


def bootstrap_source(app_root: Path = APP_ROOT) -> str:
    """The `-c` program: `sys.path = [app_root] + stdlib`, then run the runner as `__main__`."""
    return (
        "import sys\n"
        f"sys.path[:] = [{str(app_root)!r}] + [p for p in sys.path if p and "
        "'site-packages' not in p and 'dist-packages' not in p]\n"
        "import runpy\n"
        f"runpy.run_module({RUNNER_MODULE!r}, run_name='__main__', alter_sys=True)\n"
    )


def runner_command(python: str | None = None, app_root: Path = APP_ROOT) -> list[str]:
    return [
        python or sys.executable,
        "-I",
        "-S",
        "-B",
        "-X",
        "utf8",
        "-c",
        bootstrap_source(app_root),
    ]


def runner_env() -> dict[str, str]:
    """The whole environment of a child: nothing of the server's leaks through."""
    return {"LANG": "C.UTF-8", "PYTHONHASHSEED": "random"}


def prepare_work_dir(work_dir: Path) -> Path:
    """Create the child's private work directory (0700) and return its resolved path.

    It must be empty of anything the server cares about: it is the only place the
    child may write. An existing directory is reused only when it is a directory
    the current user owns (not a symlink); its mode is reset to 0700.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(work_dir)
    if not os.path.isdir(work_dir) or os.path.islink(work_dir) or st.st_uid != os.getuid():
        raise ValueError(f"{work_dir} is not a private directory")
    os.chmod(work_dir, 0o700)
    return work_dir.resolve()


def popen_kwargs(work_dir: Path) -> dict[str, Any]:
    """Every `subprocess.Popen` argument but argv and stderr, fixed for the child.

    stdin/stdout are the RPC pipes, no other descriptor is passed
    (`close_fds=True`, no `pass_fds`), the child gets its own session (a process
    group the supervisor can kill), the minimal environment and `work_dir` as cwd.
    """
    return {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "cwd": str(work_dir),
        "env": runner_env(),
        "close_fds": True,
        "pass_fds": (),
        "start_new_session": True,
    }


def runner_params(
    plugin_dir: Path,
    work_dir: Path,
    instance_id: str,
    isolation: str,
    limits: dict[str, int] | None = None,
) -> bytes:
    """The first stdin line. `limits` overrides entries of `policy.DEFAULT_LIMITS`."""
    if isolation not in ISOLATION_LEVELS:
        raise ValueError(f"unknown isolation level {isolation!r}")
    params: dict[str, Any] = {
        "plugin_dir": str(plugin_dir),
        "work_dir": str(work_dir),
        "instance_id": instance_id,
        "isolation": isolation,
        "limits": {**policy.DEFAULT_LIMITS, **(limits or {})},
    }
    return json.dumps(params).encode("utf-8") + b"\n"
