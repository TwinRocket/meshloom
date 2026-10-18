"""Launch contract of the plugin child (`meshloom_plugin.runner`), server side.

The project is not an installed package and `-I` drops the cwd, the script
directory and `PYTHONPATH` from `sys.path`, so the child cannot use
`python -m meshloom_plugin.runner`. It runs a fixed bootstrap instead:

    <python> -I -S -B -X utf8 -c "<bootstrap>"

`-S` also skips `site`: no site-packages on the path and no `.pth` file
executed before the runner freezes the process. The parameters arrive as one
JSON line on stdin; stdin and stdout then carry the JSON-RPC stream, stderr is
the plugin's log. The supervisor (MSHL-179) owns the process; this module only
builds what it needs (docs/dev/plugins.md, "Runner").
"""

import json
import sys
from pathlib import Path
from typing import Any

from meshloom_plugin import policy

APP_ROOT = Path(__file__).resolve().parents[2]
RUNNER_MODULE = "meshloom_plugin.runner"
ISOLATION_LEVELS = ("none", "setuid", "bwrap")


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


def runner_params(
    plugin_dir: Path,
    instance_id: str,
    isolation: str,
    limits: dict[str, int] | None = None,
) -> bytes:
    """The first stdin line. `limits` overrides entries of `policy.DEFAULT_LIMITS`."""
    if isolation not in ISOLATION_LEVELS:
        raise ValueError(f"unknown isolation level {isolation!r}")
    params: dict[str, Any] = {
        "plugin_dir": str(plugin_dir),
        "instance_id": instance_id,
        "isolation": isolation,
        "limits": {**policy.DEFAULT_LIMITS, **(limits or {})},
    }
    return json.dumps(params).encode("utf-8") + b"\n"
