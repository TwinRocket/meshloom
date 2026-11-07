"""Host-wide leases of the per-instance uids at the `setuid` level (MSHL-357).

`plugin_uids` keeps each uid unique in one database, and `uid_is_free` only asks
passwd and group. Two Meshloom installs on one kernel (two containers, or two
servers on one system) could therefore pick the same uid. Where they share
nothing but the kernel (Docker's default: own mount, IPC and pid namespaces)
that is harmless for files and IPC; where they share a temp directory or an IPC
namespace (`--ipc=host`, a `/tmp` bind mount, two servers on one system), one
install's purge would delete what the other's plugin owns there, and the two
plugins could read each other's leftovers.

A lease is a file `<domain>/.meshloom-plugin-uids/<uid>` holding the id of the
install that owns the uid (`install_id`, a random id kept in the data dir). The
domains are the shared temp dirs the purge walks (`/dev/mqueue` aside: it holds
no directory; it follows the IPC namespace, like the System V objects, and so
does `/dev/shm` under Docker's `--ipc`). So a lease is visible exactly where the
purge could reach another install's files, and lives as long as they do: a
tmpfs domain loses its leases at reboot together with what they protected;
nothing else expires them.

- A uid is taken for an instance only when every domain's lease can be created
  or already holds this install's id; it is never purged otherwise (the purge
  raises `PurgeBlockedError` naming the lease, shown in health).
- The lease stays while `plugin_uids` keeps the row, across restarts; it is
  removed only after a successful purge freed the row.
- While the server runs it holds `flock(LOCK_EX)` on each of its leases: a copy
  of the same data dir (same install id) running at the same time is refused
  rather than taken for itself. A crash drops the locks, the files stay, the
  next start takes them back.
- The lease directory must be a real directory owned by the server's euid with
  no group or other bits (a plugin cannot write there, nor plant it first:
  startup creates it before any child runs). Anything else fails closed: no uid
  is allocated and no purge runs until an administrator removes it. A read-only
  domain is skipped: nobody with the server's rights could have written there.
- The server also holds `flock(LOCK_SH)` on each lease directory, which
  systemd-tmpfiles' age cleanup honours (tmpfiles.d(5)); a lease removed anyway
  is noticed (inode check) and written again before the next allocation or purge.

A lease of an install that is gone for good keeps its uid out of reach of the
others (fail closed) until the domain is cleared (reboot for a tmpfs) or an
administrator removes the file.
"""

import contextlib
import errno
import fcntl
import os
import re
import secrets
import stat
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

LEASE_DIR_NAME = ".meshloom-plugin-uids"
INSTALL_ID_FILE = "plugin-install-id"
_INSTALL_ID = re.compile(r"[0-9a-f]{32}")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_ATTEMPTS = 3


class UidLeaseError(ValueError):
    """A lease cannot be checked (the lease directory is not this server's):
    fail closed. `paths` names what an administrator should look at."""

    def __init__(self, message: str, paths: Iterable[str]) -> None:
        super().__init__(message)
        self.paths = tuple(paths)


class UidLeaseTaken(UidLeaseError):
    """Another install (or a running copy of this one) holds the uid."""


class UidLeaseIOError(UidLeaseError):
    """A lease or lease directory cannot be written or read: an `OSError` such as
    `ENOSPC` (a plugin filled the shared tmpfs), `EACCES` or `EROFS` (MSHL-360).
    Fail closed like the others, but nobody else is known to hold the uid."""


def install_id(data_dir: Path) -> str:
    """This install's id, created on first use in `<data_dir>/plugin-install-id`
    (`0600`). An unreadable or malformed file raises (fail closed)."""
    path = Path(data_dir) / INSTALL_ID_FILE
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, encoding="ascii", errors="replace") as fh:
            value = fh.read(64).strip()
        if not _INSTALL_ID.fullmatch(value):
            raise ValueError(f"{path} does not hold an install id") from None
        return value
    value = secrets.token_hex(16)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(value + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return value


def _same_file(dir_fd: int, name: str, fd: int) -> bool:
    """True when `name` in `dir_fd` is still the file `fd` has open."""
    try:
        named = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    held = os.fstat(fd)
    return (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino)


class UidLeases:
    """The leases this server holds. Thread-safe; one object per process (the
    `setuid` backend and its per-uid copies share it)."""

    def __init__(self, install: str, domains: Callable[[], Iterable[Path]]) -> None:
        if not _INSTALL_ID.fullmatch(install):
            raise ValueError("malformed install id")
        self.install_id = install
        self._domains = domains
        self._lock = threading.Lock()
        self._dirs: dict[Path, int] = {}  # lease directory -> its fd (LOCK_SH held)
        self._held: dict[int, dict[Path, int]] = {}  # uid -> lease directory -> lease fd

    # Lease directories.

    def _open_dir(self, path: Path) -> int | None:
        """The checked fd of `path`, created if missing; None for a read-only domain."""
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            if exc.errno == errno.EROFS:
                return None
            raise UidLeaseIOError(
                f"{path}: cannot be created ({exc.strerror})", [str(path)]
            ) from None
        try:
            fd = os.open(path, _DIR_FLAGS)
        except OSError as exc:
            raise UidLeaseError(
                f"{path}: not a directory of this server ({exc.strerror})", [str(path)]
            ) from None
        st = os.fstat(fd)
        mode = stat.S_IMODE(st.st_mode)
        if st.st_uid != os.geteuid() or mode & 0o077:
            os.close(fd)
            raise UidLeaseError(
                f"{path}: not a private directory of this server"
                f" (owner {st.st_uid}, mode {mode:o})",
                [str(path)],
            )
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return fd

    def _lease_dirs(self) -> dict[Path, int]:
        """Every domain's lease directory, opened and checked once, opened again
        when it was removed or replaced since."""
        current: dict[Path, int] = {}
        for domain in self._domains():
            path = Path(domain) / LEASE_DIR_NAME
            fd = self._dirs.get(path)
            if fd is not None:
                try:
                    named = os.stat(path, follow_symlinks=False)
                    held = os.fstat(fd)
                    if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
                        raise FileNotFoundError(path)
                except OSError:
                    self._forget_dir(path)
                    fd = None
            if fd is None:
                fd = self._open_dir(path)
                if fd is None:
                    continue
                self._dirs[path] = fd
            current[path] = fd
        return current

    def _forget_dir(self, path: Path) -> None:
        fd = self._dirs.pop(path)
        for leases in self._held.values():
            lease = leases.pop(path, None)
            if lease is not None:
                os.close(lease)
        os.close(fd)

    def prepare(self) -> None:
        """Create the lease directories now (at startup, before any child runs)."""
        with self._lock:
            self._lease_dirs()

    # Leases.

    def _create(self, dir_fd: int, name: str) -> int | None:
        """Write the lease atomically (a temp file linked to its name), so it is
        never seen empty. The temp file is locked before the link, so the lease is
        never seen unlocked either. Returns its locked fd when this call linked
        it; None when another process won the name (the caller opens and reads
        it). On failure, nothing this call wrote stays (MSHL-360)."""
        tmp = f".{name}.{secrets.token_hex(4)}"
        fd = os.open(
            tmp,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=dir_fd,
        )
        linked = False
        try:
            data = (self.install_id + "\n").encode("ascii")
            if os.write(fd, data) != len(data):
                raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                os.link(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
                linked = True
            except FileExistsError:
                pass
            os.unlink(tmp, dir_fd=dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                if linked and _same_file(dir_fd, name, fd):
                    os.unlink(name, dir_fd=dir_fd)
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dir_fd)
            os.close(fd)
            raise
        if not linked:
            os.close(fd)
            return None
        return fd

    def _take(self, dir_fd: int, name: str, path: Path) -> tuple[int, bool]:
        """The locked fd of this install's lease `name`, created if missing, and
        whether this call created it."""
        for _ in range(_ATTEMPTS):
            try:
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
            except FileNotFoundError:
                created = self._create(dir_fd, name)
                if created is not None:
                    return created, True
                continue
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise UidLeaseError(f"{path}: not a lease file", [str(path)])
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise UidLeaseTaken(
                        f"{path}: held by another running Meshloom", [str(path)]
                    ) from None
                if not _same_file(dir_fd, name, fd):
                    os.close(fd)  # removed or replaced in between: open it again
                    continue
                owner = os.pread(fd, 64, 0).decode("ascii", "replace").strip()
                if owner != self.install_id:
                    raise UidLeaseTaken(f"{path}: leased by another Meshloom install", [str(path)])
                return fd, False
            except BaseException:
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise
        raise UidLeaseError(f"{path}: keeps changing", [str(path)])

    def acquire(self, uid: int) -> None:
        """Hold the uid's lease in every domain (created, or already this install's).
        Raises `UidLeaseTaken` when another install holds it, `UidLeaseIOError` when
        a lease cannot be written or read (`ENOSPC`, `EACCES`...), `UidLeaseError`
        when a lease directory or file is not this server's. Idempotent; a lease
        removed meanwhile is written again. On failure, the leases this call
        created are removed."""
        uid = int(uid)
        name = str(uid)
        with self._lock:
            dirs = self._lease_dirs()
            held = self._held.setdefault(uid, {})
            new: list[tuple[int, Path]] = []
            try:
                for path, dir_fd in dirs.items():
                    fd = held.get(path)
                    if fd is not None and _same_file(dir_fd, name, fd):
                        continue
                    if fd is not None:
                        os.close(held.pop(path))
                    try:
                        held[path], created = self._take(dir_fd, name, path / name)
                    except OSError as exc:
                        # ENOSPC (a plugin filled the shared tmpfs), EACCES...: the
                        # lease cannot be written nor checked, so fail closed with
                        # the error every caller handles (MSHL-359), its own kind
                        # so the start reason does not blame another install
                        # (MSHL-360). `_create` left nothing behind.
                        raise UidLeaseIOError(
                            f"{path / name}: cannot be leased ({exc.strerror or exc})",
                            [str(path / name)],
                        ) from None
                    if created:
                        new.append((dir_fd, path))
            except BaseException:
                for dir_fd, path in new:
                    fd = held.pop(path)
                    with contextlib.suppress(OSError):
                        if _same_file(dir_fd, name, fd):
                            os.unlink(name, dir_fd=dir_fd)
                    os.close(fd)
                if not held:
                    self._held.pop(uid, None)
                raise

    def release(self, uid: int) -> None:
        """Remove this install's leases of `uid` (after a successful purge freed it)."""
        uid = int(uid)
        name = str(uid)
        with self._lock:
            for path, fd in self._held.pop(uid, {}).items():
                dir_fd = self._dirs.get(path)
                with contextlib.suppress(OSError):
                    if dir_fd is not None and _same_file(dir_fd, name, fd):
                        os.unlink(name, dir_fd=dir_fd)
                os.close(fd)

    def close(self) -> None:
        """Drop every lock; the lease files stay (what a crash does)."""
        with self._lock:
            for leases in self._held.values():
                for fd in leases.values():
                    os.close(fd)
            self._held.clear()
            for fd in self._dirs.values():
                os.close(fd)
            self._dirs.clear()
