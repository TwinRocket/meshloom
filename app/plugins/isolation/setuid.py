"""Level `setuid`: the server runs as root (Docker, the Home Assistant add-on), or
as another uid holding `CAP_SETUID` and `CAP_SETGID` (Docker with
`MESHLOOM_RUN_AS_USER`: docker-entrypoint.sh keeps them ambient), and starts each
plugin instance under its own uid.

Docker's default seccomp profile refuses unprivileged user namespaces, so
bubblewrap is not an option there; dropping to an unprivileged uid is. The
account `meshloom-plugin` is the opt-in: the level is selected only when it
exists. Each instance then gets a uid of its own from `INSTANCE_UIDS` (gid = uid,
no supplementary group, umask 077), allocated once and kept in the database
(`plugin_uids`, `PluginUidRepository`): the lowest uid of the range that no row
holds, that has no passwd or group entry and that is not the server's. The
runner then sets `RLIMIT_NPROC` (it counts every task of the uid) and
`PR_SET_NO_NEW_PRIVS`. The uid cannot read root's 0600 files (the database,
`/proc/<server>/environ`) nor signal or trace the server, nor another instance's
work directory or processes. The file model that makes this true is
`permissions.py`, applied at startup and before each spawn.

Without root the server has no `CAP_CHOWN`, `CAP_DAC_OVERRIDE` nor `CAP_KILL`:
a short helper running as the instance uid creates the work directory (`0711`:
the server must still `chdir` into it, which `subprocess` does before dropping
the uid), removes it with whatever the child left, and signals the child's
process group (a uid may signal its own processes). The helper and the runner
drop the capabilities they inherit (`meshloom_plugin.seccomp`).

A uid goes back to the pool only after `purge()` ran as that uid: it removes
what the uid owns anywhere in the shared temp directories (`/tmp`, `/var/tmp`,
`/dev/shm`, `/dev/mqueue`; also inside a directory someone else owns, such as
`/tmp/.X11-unix`, without crossing into another file system) and its System V
IPC objects (shared memory, message queues, semaphores, owner or creator), so
the next instance given the uid finds nothing of the previous one. Startup
purges the uids that left something behind (a crash, the shared uid of the
releases before per-instance uids) and releases the rows of deleted instances.
A walk that meets more than `PURGE_MAX_ENTRIES` entries fails: the uid is then
kept rather than handed over half-cleaned. So does a directory the uid may enter
but not list (such as `0733` owned by someone else: the uid could have left
entries there that it cannot see; another instance's `0711` work directory is
the one exception), and so does anything `owns_something` still finds when the
server checks again after the helper (as root it lists every directory). The
walk never crosses into another file system: a temp dir mounted below another
is purged only when it is itself one of the walked roots
(docs/dev/plugins-security.md, "OS isolation").
"""

import contextlib
import grp
import logging
import os
import pwd
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.plugins import launch
from app.plugins.isolation import permissions
from app.plugins.isolation.base import IsolationBackend

ACCOUNT = "meshloom-plugin"
# The uid and gid the Docker image gives the opt-in account (Dockerfile,
# `PLUGIN_UID`). No child runs under it since per-instance uids (MSHL-339); the
# releases before ran every instance under it, so startup still purges it.
ACCOUNT_UID = 10002
# The per-instance uids: below 65536 (a user namespace commonly maps that much),
# above the regular accounts useradd hands out first. A uid with a passwd or group
# entry is skipped at every allocation.
INSTANCE_UIDS = range(40000, 50000)
# A work directory made by the helper: the plugin uid owns it, the server traverses it.
HELPER_WORK_DIR_MODE = 0o711
HELPER_TIMEOUT = 30.0
# Entries one purge (or `owns_something`) walks per directory before giving up.
PURGE_MAX_ENTRIES = 100_000
# System V IPC tables (`/proc/sysvipc/<kind>`) and the id column of each.
SYSV_IPC_KINDS = (("shm", "shmid"), ("msg", "msqid"), ("sem", "semid"))

logger = logging.getLogger(__name__)

# Runs as the plugin uid, capabilities dropped first:
#   mkdir <path> | rmtree <path> | kill <pgid> <signal>
#   | purge <max entries> <kind:id column,...> <dir>...  (IPC_RMID is 0 on Linux)
_HELPER_SOURCE = """
import os, shutil, stat, sys
sys.path.insert(0, sys.argv[1])
from meshloom_plugin import seccomp
seccomp.drop_capabilities(seccomp.libc_prctl())
if os.getuid() == 0 or os.geteuid() == 0:
    sys.exit(3)
op, args = sys.argv[2], sys.argv[3:]
WORK_DIR_PREFIX = "meshloom-plugin-"
if op == "mkdir":
    os.mkdir(args[0], 0o700)
    os.chmod(args[0], 0o711)
elif op == "rmtree":
    try:
        shutil.rmtree(args[0])
    except FileNotFoundError:
        pass
elif op == "kill":
    try:
        os.killpg(int(args[0]), int(args[1]))
    except ProcessLookupError:
        pass
elif op == "purge":
    import ctypes, errno
    uid = os.getuid()

    def other_work_dir(path, base):
        try:
            st = os.lstat(path)
        except OSError:
            return False
        return (
            os.path.dirname(path) == base
            and os.path.basename(path).startswith(WORK_DIR_PREFIX)
            and stat.S_ISDIR(st.st_mode)
            and stat.S_IMODE(st.st_mode) == 0o711
            and st.st_uid != uid
        )
    limit, kinds = int(args[0]), args[1].split(",")
    for base in args[2:]:
        try:
            dev = os.lstat(base).st_dev
        except OSError:
            continue
        stack, seen = [base], 0
        while stack:
            path = stack.pop()
            try:
                entries = list(os.scandir(path))
            except (FileNotFoundError, NotADirectoryError):
                continue
            except PermissionError:
                # Not listable, yet enterable: the uid may have left entries in it
                # (or below) that this walk cannot see. Fail closed, except for
                # another instance's work directory (0711 without root).
                if os.access(path, os.X_OK) and not other_work_dir(path, base):
                    print(f"{path}: not listable", file=sys.stderr)
                continue
            except OSError as exc:
                print(f"{path}: {exc}", file=sys.stderr)
                continue
            for entry in entries:
                seen += 1
                if seen > limit:
                    print(f"{base}: more than {limit} entries", file=sys.stderr)
                    stack = []
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                    if st.st_uid == uid:
                        if stat.S_ISDIR(st.st_mode):
                            shutil.rmtree(entry.path)
                        else:
                            os.unlink(entry.path)
                    elif stat.S_ISDIR(st.st_mode) and st.st_dev == dev:
                        stack.append(entry.path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    print(f"{entry.path}: {exc}", file=sys.stderr)
    libc = ctypes.CDLL(None, use_errno=True)
    remove = {
        "shm": lambda i: libc.shmctl(i, 0, None),
        "msg": lambda i: libc.msgctl(i, 0, None),
        "sem": lambda i: libc.semctl(i, 0, 0),
    }
    for kind in kinds:
        kind, _, id_column = kind.partition(":")
        try:
            with open("/proc/sysvipc/" + kind) as table:
                lines = table.read().splitlines()
        except OSError:
            continue
        head = lines[0].split() if lines else []
        for line in lines[1:]:
            row = dict(zip(head, line.split(), strict=False))
            try:
                ipc_id, owners = int(row[id_column]), {int(row["uid"]), int(row["cuid"])}
            except (KeyError, ValueError):
                continue
            if uid in owners and remove[kind](ipc_id) != 0:
                err = ctypes.get_errno()
                if err not in (errno.EINVAL, errno.EIDRM):
                    print(f"sysvipc {kind} {ipc_id}: {os.strerror(err)}", file=sys.stderr)
else:
    sys.exit(2)
"""


def shared_temp_dirs() -> list[Path]:
    """Where a plugin uid may leave files outside its work directory."""
    seen: list[Path] = []
    for candidate in (tempfile.gettempdir(), "/tmp", "/var/tmp", "/dev/shm", "/dev/mqueue"):
        path = Path(candidate)
        with contextlib.suppress(OSError):
            path = path.resolve()
            if path.is_dir() and path not in seen:
                seen.append(path)
    return seen


def owns_something(uid: int, dirs: Iterable[Path] | None = None) -> bool:
    """True when `uid` owns an entry anywhere in the shared temp dirs (same file
    system as each dir, as `purge` walks them) or a System V IPC object. A walk
    past `PURGE_MAX_ENTRIES` answers True: the purge then decides."""
    for base in shared_temp_dirs() if dirs is None else dirs:
        try:
            dev = os.lstat(base).st_dev
        except OSError:
            continue
        stack, seen = [str(base)], 0
        while stack:
            try:
                entries = list(os.scandir(stack.pop()))
            except OSError:
                continue
            for entry in entries:
                seen += 1
                if seen > PURGE_MAX_ENTRIES:
                    return True
                with contextlib.suppress(OSError):
                    st = entry.stat(follow_symlinks=False)
                    if st.st_uid == uid:
                        return True
                    if stat.S_ISDIR(st.st_mode) and st.st_dev == dev:
                        stack.append(entry.path)
    return dirs is None and owns_sysv_ipc(uid)


def owns_sysv_ipc(uid: int, root: Path = Path("/proc/sysvipc")) -> bool:
    """True when `uid` owns or created a System V shared memory segment, message
    queue or semaphore set (the tables are world-readable)."""
    for kind, _ in SYSV_IPC_KINDS:
        try:
            lines = (root / kind).read_text().splitlines()
        except OSError:
            continue
        head = lines[0].split() if lines else []
        for line in lines[1:]:
            row = dict(zip(head, line.split(), strict=False))
            with contextlib.suppress(KeyError, ValueError):
                if uid in (int(row["uid"]), int(row["cuid"])):
                    return True
    return False


def uid_is_free(uid: int) -> bool:
    """A uid no account, no group and not this server uses: safe for one instance."""
    if uid <= 0 or uid in (os.getuid(), os.geteuid(), ACCOUNT_UID):
        return False
    try:
        pwd.getpwuid(uid)
        return False
    except KeyError:
        pass
    try:
        grp.getgrgid(uid)
        return False
    except KeyError:
        return True


class SetuidBackend(IsolationBackend):
    level = "setuid"
    per_instance_uid = True

    def __init__(self, uid: int, gid: int) -> None:
        if uid == 0 or gid == 0:
            raise ValueError(f"{ACCOUNT} must not be root")
        self.uid = uid
        self.gid = gid
        self.data_dir: Path | None = None

    def for_uid(self, uid: int) -> "SetuidBackend":
        backend = SetuidBackend(uid, uid)
        backend.data_dir = self.data_dir
        return backend

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
        self._helper("mkdir", str(work_dir))
        st = os.lstat(work_dir)
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != self.uid
            or stat.S_IMODE(st.st_mode) != HELPER_WORK_DIR_MODE
        ):
            raise ValueError(f"{work_dir} is not a private directory of uid {self.uid}")
        return work_dir

    def remove_work_dir(self, work_dir: Path) -> None:
        if os.geteuid() == 0:
            super().remove_work_dir(work_dir)
            return
        try:
            self._helper("rmtree", str(work_dir))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.warning("Could not remove the plugin work directory %s: %s", work_dir, exc)

    def signal_group(self, pgid: int, sig: int, *, wait: bool = False) -> None:
        if os.geteuid() == 0:
            super().signal_group(pgid, sig)
            return
        # No CAP_KILL: the instance uid signals its own process group. Off the event
        # loop unless asked; `close()` reaps with a timeout either way.
        if wait:
            self._signal_via_helper(pgid, sig)
        else:
            threading.Thread(target=self._signal_via_helper, args=(pgid, sig), daemon=True).start()

    def _signal_via_helper(self, pgid: int, sig: int) -> None:
        try:
            self._helper("kill", str(int(pgid)), str(int(sig)))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.warning("Could not signal the plugin process group %s: %s", pgid, exc)

    def purge(self, dirs: Iterable[Path] | None = None) -> None:
        """Remove what this uid owns in the shared temp dirs (at any depth) and its
        System V IPC objects. Raises `ValueError` (or `OSError`) when the helper
        fails, meets a directory it may enter but not list, or leaves something the
        server still sees (`owns_something`, run as the server afterwards: as root it
        lists every directory): the uid is then kept."""
        paths = list(shared_temp_dirs() if dirs is None else dirs)
        kinds = ",".join(f"{kind}:{column}" for kind, column in SYSV_IPC_KINDS)
        self._helper("purge", str(PURGE_MAX_ENTRIES), kinds, *(str(p) for p in paths))
        if owns_something(self.uid, None if dirs is None else paths):
            raise ValueError(f"uid {self.uid} still owns something after the purge")

    def _helper(self, op: str, *args: str) -> None:
        if self.uid in (0, os.getuid(), os.geteuid()):
            raise ValueError("the plugin helper must run under the plugin's own uid")
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
                *args,
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
        if proc.returncode != 0 or (op == "purge" and proc.stderr.strip()):
            tail = proc.stderr.strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
            raise ValueError(f"plugin helper ({op}) failed: {tail[0]}")

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
