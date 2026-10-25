"""What every OS isolation backend gives the supervisor: a level, a work dir, a launch.

A backend wraps the launch contract of `app/plugins/launch.py` (the runner
command and its `subprocess.Popen` arguments) without changing what the child
sees: the same parameters line, the same RPC pipes, the same scrubbed
environment. `launch()` returns argv and keyword arguments, so the supervisor
can hand them to `asyncio.create_subprocess_exec` and the tests to
`subprocess.Popen` (`spawn()`).
"""

import contextlib
import os
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

from app.plugins import launch

# What `/api/health` and `plugin.init` call each level, in the logs and the docs.
LEVEL_LABELS = {
    "bwrap": "isolated by bubblewrap",
    "setuid": "dedicated uid",
    "none": "hooks only",
}


class IsolationBackend:
    """The `none` behaviour; the other backends override what they change."""

    level: ClassVar[str] = "none"
    # True when each instance runs under a uid of its own (`for_uid`, `setuid`).
    per_instance_uid: ClassVar[bool] = False

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]

    def for_uid(self, uid: int) -> "IsolationBackend":
        """This backend bound to one instance's uid (`per_instance_uid` only)."""
        return self

    def purge(self, dirs: Iterable[Path] | None = None) -> None:
        """Remove what this backend's uid left in the shared temporary directories
        (`per_instance_uid` only); nothing to do elsewhere."""

    def signal_group(self, pgid: int, sig: int, *, wait: bool = False) -> None:
        """Signal a child's process group; a group already gone is not an error.

        `wait` only matters to a backend that signals through a helper process."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, sig)

    def prepare_work_dir(self, work_dir: Path) -> Path:
        """Create the child's private work directory, owned by whoever the child runs as."""
        return launch.prepare_work_dir(work_dir)

    def remove_work_dir(self, work_dir: Path) -> None:
        """Remove a work directory `prepare_work_dir` made, whatever the child left in it."""
        shutil.rmtree(work_dir, ignore_errors=True)

    def prepare_data_dir(self, data_dir: Path) -> None:
        """Startup work on the data directory for this level. No-op by default."""

    def prepare_plugin_dir(self, plugin_dir: Path) -> None:
        """Make the plugin's code readable by the child (and nothing more). No-op by default."""

    def command(self, argv: list[str], plugin_dir: Path, work_dir: Path) -> list[str]:
        return argv

    def popen_kwargs(self, work_dir: Path) -> dict[str, Any]:
        return launch.popen_kwargs(work_dir)

    def launch(self, plugin_dir: Path, work_dir: Path) -> tuple[list[str], dict[str, Any]]:
        """argv and every `Popen` argument but stderr, for one child."""
        argv = self.command(launch.runner_command(), Path(plugin_dir), Path(work_dir))
        return argv, self.popen_kwargs(Path(work_dir))

    def spawn(
        self, cmd: list[str], plugin_dir: Path, tmp_dir: Path, **extra: Any
    ) -> "subprocess.Popen[Any]":
        """Run `cmd` (any argv) inside this backend's isolation; `tmp_dir` is its cwd.

        Used by the tests to run arbitrary code (no runner, no hook) in the same box
        the runner gets: whatever a plugin could exec from inside lands there too.
        """
        kwargs = {**self.popen_kwargs(Path(tmp_dir)), **extra}
        argv = self.command(cmd, Path(plugin_dir), Path(tmp_dir))
        return subprocess.Popen(argv, **kwargs)
