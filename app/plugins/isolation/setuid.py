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
kept rather than handed over half-cleaned. A tree the uid owns gets owner rwx back
on each of its directories before removal (no symlink followed), so a plugin cannot
lock its own leftovers with `0300` or `0000`. A directory the uid may enter
but not list fails the walk too (such as `0733` owned by someone else: the uid could
have left entries there that it cannot see; the one exception is another instance's
`0711` work directory, named as `mkdtemp` makes it and owned by another plugin uid:
`INSTANCE_UIDS` or the account's uid, read at runtime), and so does anything
`owns_something` still finds when the server checks again after the helper (as root
it lists every directory). The directories that blocked a purge are raised as
`PurgeBlockedError` and shown in health (`plugins_purge_blocked`). The
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
# The uid and gid the Docker image gives the opt-in account by default (Dockerfile,
# `PLUGIN_UID`). No child runs under it since per-instance uids (MSHL-339); the
# releases before ran every instance under it, so startup still purges it. An image
# may be built with another `PLUGIN_UID`: what the account really has is read at
# runtime (`account_uid()`); this default is only kept out of `INSTANCE_UIDS`.
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
# What the helper prints after a directory the uid may enter but not list.
NOT_LISTABLE = ": not listable"
# Blocking directories one failed purge reports (health `plugins_purge_blocked`).
PURGE_BLOCKED_MAX = 20

logger = logging.getLogger(__name__)

# Runs as the plugin uid, capabilities dropped first:
#   mkdir <path> | rmtree <path> | kill <pgid> <signal>
#   | purge <max entries> <kind:id column,...> <plugin uids> <dir>...
# (IPC_RMID is 0 on Linux; <plugin uids> is `<first>-<last + 1>,<uid>,...`).
# Before removing a tree the uid owns, `unlock` gives the owner rwx back on each of
# its directories (the plugin may have left them 0300 or 0000: `rmtree` could not
# list them and the uid would never be freed). It opens each directory with
# O_PATH | O_NOFOLLOW relative to its parent and changes the mode through that
# descriptor, so no symlink is followed, and only a directory the uid owns (which
# it could chmod anyway) is touched.
_HELPER_SOURCE = """
import errno, os, re, shutil, stat, sys
sys.path.insert(0, sys.argv[1])
from meshloom_plugin import seccomp
seccomp.drop_capabilities(seccomp.libc_prctl())
if os.getuid() == 0 or os.geteuid() == 0:
    sys.exit(3)
op, args = sys.argv[2], sys.argv[3:]
# `tempfile.mkdtemp(prefix="meshloom-plugin-")`: 8 characters of [a-z0-9_].
WORK_DIR_NAME = re.compile(r"meshloom-plugin-[a-z0-9_]{8}")
uid = os.getuid()
O_DIR_PATH = os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
PROC = "/proc"


def shown(path):
    # One printable line per path: control characters, line breaks and undecodable
    # bytes escaped, any other character (accents, CJK) kept as is.
    return "".join(c if c.isprintable() else ascii(c)[1:-1] for c in path)


def unlock_one(parent_fd, name):
    # True when `name` (in `parent_fd`) is a directory of this uid, now owner rwx.
    try:
        fd = os.open(name, O_DIR_PATH, dir_fd=parent_fd)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        if st.st_uid != uid:
            return False
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o700 != 0o700:
            try:
                os.chmod(f"{PROC}/self/fd/{fd}", mode | 0o700)
            except FileNotFoundError:
                # No /proc: an O_PATH descriptor cannot be chmod-ed any other way,
                # and chmod(follow_symlinks=False) is not implemented on Linux.
                # The removal (and so the purge) fails, with this reason.
                raise OSError(
                    errno.ENOSYS, f"cannot give owner rwx back to {name}: /proc is not mounted"
                ) from None
        return True
    finally:
        os.close(fd)


def unlock(top):
    # Owner rwx on `top` and every directory of this uid below it, before rmtree.
    parent = os.open(os.path.dirname(top) or ".", O_DIR_PATH)
    try:
        if not unlock_one(parent, os.path.basename(top)):
            return
    finally:
        os.close(parent)
    stack = [top]
    while stack:
        path = stack.pop()
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            with os.scandir(fd) as entries:
                names = [e.name for e in entries if e.is_dir(follow_symlinks=False)]
            for name in names:
                if unlock_one(fd, name):
                    stack.append(os.path.join(path, name))
        except OSError:
            pass
        finally:
            os.close(fd)


def remove_tree(path):
    unlock(path)
    shutil.rmtree(path)


if op == "mkdir":
    os.mkdir(args[0], 0o700)
    os.chmod(args[0], 0o711)
elif op == "rmtree":
    try:
        remove_tree(args[0])
    except FileNotFoundError:
        pass
elif op == "kill":
    try:
        os.killpg(int(args[0]), int(args[1]))
    except ProcessLookupError:
        pass
elif op == "purge":
    import ctypes, errno

    def plugin_uid(owner):
        for item in args[2].split(","):
            first, _, end = item.partition("-")
            if int(first) <= owner < (int(end) if end else int(first) + 1):
                return True
        return False

    def other_work_dir(path, base):
        # Another instance's work directory: named by mkdtemp, 0711, owned by
        # another plugin uid (not any uid that could hide files for this one).
        try:
            st = os.lstat(path)
        except OSError:
            return False
        return (
            os.path.dirname(path) == base
            and WORK_DIR_NAME.fullmatch(os.path.basename(path)) is not None
            and stat.S_ISDIR(st.st_mode)
            and stat.S_IMODE(st.st_mode) == 0o711
            and st.st_uid != uid
            and plugin_uid(st.st_uid)
        )
    limit, kinds = int(args[0]), args[1].split(",")
    for base in args[3:]:
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
                    print(f"{shown(path)}: not listable", file=sys.stderr)
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
                            remove_tree(entry.path)
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
    # A missing table is skipped only on a kernel without System V IPC (/proc
    # mounted): without /proc an object this uid owns would survive the uid's
    # release and reach the next instance given it, so the purge fails.
    proc_mounted = os.path.isdir(PROC + "/self")
    for kind in kinds:
        kind, _, id_column = kind.partition(":")
        try:
            with open(PROC + "/sysvipc/" + kind) as table:
                lines = table.read().splitlines()
        except FileNotFoundError:
            if not proc_mounted:
                print(f"sysvipc {kind}: cannot be listed, /proc is not mounted", file=sys.stderr)
            continue
        except OSError as exc:
            print(f"sysvipc {kind}: {exc}", file=sys.stderr)
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


class PurgeBlockedError(ValueError):
    """The purge met directories the uid may enter but not list (`paths`, at most
    `PURGE_BLOCKED_MAX`): it could have hidden files there, so the uid is kept until
    an administrator removes or opens them."""

    def __init__(self, message: str, paths: Iterable[str]) -> None:
        super().__init__(message)
        self.paths = tuple(paths)[:PURGE_BLOCKED_MAX]


def account_uid(getpwnam: Any = pwd.getpwnam) -> int | None:
    """The uid the `meshloom-plugin` account has on this machine (the image may be
    built with another `PLUGIN_UID` than `ACCOUNT_UID`); None when the account is
    missing or is root."""
    try:
        uid = getpwnam(ACCOUNT).pw_uid
    except KeyError:
        return None
    return uid or None


def plugin_uid_spec(getpwnam: Any = pwd.getpwnam) -> str:
    """The plugin uids for the purge helper: whose `0711` work dirs it may skip.
    The account's uid is looked up at each purge. Without the account (removed after
    startup: the level is only selected while it exists) only `INSTANCE_UIDS` are
    listed, so a work dir the account left still blocks the purge (fail closed)."""
    spec = f"{INSTANCE_UIDS.start}-{INSTANCE_UIDS.stop}"
    uid = account_uid(getpwnam)
    return spec if uid is None else f"{spec},{uid}"


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
    queue or semaphore set (the tables are world-readable). Also True when a table
    cannot be read (no /proc, fail closed); a table missing while /proc is mounted
    is a kernel without System V IPC."""
    proc_mounted = (root.parent / "self").is_dir()
    for kind, _ in SYSV_IPC_KINDS:
        try:
            lines = (root / kind).read_text().splitlines()
        except FileNotFoundError:
            if proc_mounted:
                continue
            return True
        except OSError:
            return True
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
        fails (`subprocess.TimeoutExpired` when it hangs), meets a directory it may
        enter but not list, or leaves something the
        server still sees (`owns_something`, run as the server afterwards: as root it
        lists every directory): the uid is then kept. `PurgeBlockedError` (a
        `ValueError`) names the directories it could not list."""
        paths = list(shared_temp_dirs() if dirs is None else dirs)
        kinds = ",".join(f"{kind}:{column}" for kind, column in SYSV_IPC_KINDS)
        self._helper(
            "purge", str(PURGE_MAX_ENTRIES), kinds, plugin_uid_spec(), *(str(p) for p in paths)
        )
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
            encoding="utf-8",
            errors="backslashreplace",
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
            lines = proc.stderr.strip().splitlines()
            tail = lines[-1] if lines else f"exit {proc.returncode}"
            message = f"plugin helper ({op}) failed: {tail}"
            blocked = [
                line.removesuffix(NOT_LISTABLE) for line in lines if line.endswith(NOT_LISTABLE)
            ]
            if op == "purge" and blocked:
                raise PurgeBlockedError(message, blocked)
            raise ValueError(message)

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
