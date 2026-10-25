"""Level `setuid`: the server runs as root (Docker, the Home Assistant add-on), or
as another uid holding `CAP_SETUID`, `CAP_SETGID` and `CAP_KILL` (Docker with
`MESHLOOM_RUN_AS_USER`: docker-entrypoint.sh keeps them ambient), and starts each
child as the dedicated account `meshloom-plugin`.

Docker's default seccomp profile refuses unprivileged user namespaces, so
bubblewrap is not an option there; dropping to an unprivileged uid is. The
child gets that uid and its primary group, no supplementary group, umask 077;
the runner then sets `RLIMIT_NPROC` (it counts every task of the uid) and
`PR_SET_NO_NEW_PRIVS`. The uid cannot read root's 0600 files (the database,
`/proc/<server>/environ`) nor signal or trace the server. The file model that
makes this true is `permissions.py`, applied at startup and before each spawn.

Without root the server has no `CAP_CHOWN` nor `CAP_DAC_OVERRIDE`: a short
helper running as the plugin uid creates the work directory (`0711`: the server
must still `chdir` into it, which `subprocess` does before dropping the uid) and
removes it with whatever the child left. The runner drops the capabilities the
child inherits (`meshloom_plugin.seccomp`).

The instances share the one uid: they can read each other's work directories.
A uid per instance is a later step (docs/dev/plugins-security.md).
"""

import logging
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.plugins import launch
from app.plugins.isolation import permissions
from app.plugins.isolation.base import IsolationBackend

ACCOUNT = "meshloom-plugin"
# The uid and gid the Docker image gives it (Dockerfile, `PLUGIN_UID`). Any
# non-root uid works; this one keeps clear of common host accounts.
ACCOUNT_UID = 10002
# A work directory made by the helper: the plugin uid owns it, the server traverses it.
HELPER_WORK_DIR_MODE = 0o711
HELPER_TIMEOUT = 30.0

logger = logging.getLogger(__name__)

# Runs as the plugin uid, capabilities dropped first: `mkdir <path>` or `rmtree <path>`.
_HELPER_SOURCE = """
import os, shutil, sys
sys.path.insert(0, sys.argv[1])
from meshloom_plugin import seccomp
seccomp.drop_capabilities(seccomp.libc_prctl())
op, path = sys.argv[2], sys.argv[3]
if op == "mkdir":
    os.mkdir(path, 0o700)
    os.chmod(path, 0o711)
elif op == "rmtree":
    shutil.rmtree(path)
else:
    sys.exit(2)
"""


class SetuidBackend(IsolationBackend):
    level = "setuid"

    def __init__(self, uid: int, gid: int) -> None:
        if uid == 0 or gid == 0:
            raise ValueError(f"{ACCOUNT} must not be root")
        self.uid = uid
        self.gid = gid
        self.data_dir: Path | None = None

    def prepare_data_dir(self, data_dir: Path) -> None:
        permissions.apply(data_dir)
        self.data_dir = Path(data_dir).resolve()

    def prepare_work_dir(self, work_dir: Path) -> Path:
        if os.geteuid() == 0:
            path = launch.prepare_work_dir(work_dir)
            os.chown(path, self.uid, self.gid)
            return path
        # Not root: the plugin uid makes it. An empty directory the server already
        # created there (`tempfile.mkdtemp`) goes first; a name taken in between
        # makes the helper's mkdir fail.
        work_dir = Path(work_dir).resolve()
        if work_dir.is_dir() and not work_dir.is_symlink():
            work_dir.rmdir()
        self._helper("mkdir", work_dir)
        st = os.lstat(work_dir)
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != self.uid
            or stat.S_IMODE(st.st_mode) != HELPER_WORK_DIR_MODE
        ):
            raise ValueError(f"{work_dir} is not a private directory of {ACCOUNT}")
        return work_dir

    def remove_work_dir(self, work_dir: Path) -> None:
        if os.geteuid() == 0:
            super().remove_work_dir(work_dir)
            return
        try:
            self._helper("rmtree", Path(work_dir))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.warning("Could not remove the plugin work directory %s: %s", work_dir, exc)

    def _helper(self, op: str, path: Path) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                _HELPER_SOURCE,
                str(launch.APP_ROOT),
                op,
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=HELPER_TIMEOUT,
            cwd="/",
            env=launch.runner_env(),
            close_fds=True,
            user=self.uid,
            group=self.gid,
            extra_groups=[],
            umask=0o077,
            check=False,
        )
        if proc.returncode != 0:
            tail = proc.stderr.strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
            raise ValueError(f"plugin work dir helper ({op}) failed: {tail[0]}")

    def prepare_plugin_dir(self, plugin_dir: Path) -> None:
        # `plugins/` and `plugins/local/` may be created after startup, under the
        # server's umask 077: open the directories between the data dir and the code.
        plugin_dir = Path(plugin_dir).resolve()
        if self.data_dir is not None and plugin_dir.is_relative_to(self.data_dir):
            for parent in reversed(plugin_dir.relative_to(self.data_dir).parents[:-1]):
                permissions.make_dir_traversable(self.data_dir / parent)
        permissions.make_tree_readable(plugin_dir)

    def popen_kwargs(self, work_dir: Path) -> dict[str, Any]:
        return {
            **launch.popen_kwargs(work_dir),
            "user": self.uid,
            "group": self.gid,
            "extra_groups": [],
            "umask": 0o077,
        }
