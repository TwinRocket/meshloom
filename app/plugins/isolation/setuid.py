"""Level `setuid`: the server runs as root (Docker, the Home Assistant add-on) and
starts each child as the dedicated account `meshloom-plugin`.

Docker's default seccomp profile refuses unprivileged user namespaces, so
bubblewrap is not an option there; dropping to an unprivileged uid is. The
child gets that uid and its primary group, no supplementary group, umask 077;
the runner then sets `RLIMIT_NPROC` (it counts every task of the uid) and
`PR_SET_NO_NEW_PRIVS`. The uid cannot read root's 0600 files (the database,
`/proc/<server>/environ`) nor signal or trace the server. The file model that
makes this true is `permissions.py`, applied at startup and before each spawn.

The instances share the one uid: they can read each other's work directories.
A uid per instance is a later step (docs/dev/plugins-security.md).
"""

import os
from pathlib import Path
from typing import Any

from app.plugins import launch
from app.plugins.isolation import permissions
from app.plugins.isolation.base import IsolationBackend

ACCOUNT = "meshloom-plugin"


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
        path = launch.prepare_work_dir(work_dir)
        os.chown(path, self.uid, self.gid)
        return path

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
