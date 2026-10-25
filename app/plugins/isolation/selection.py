"""Which isolation backend this server gives its plugin children (cadrage §4.3).

| Server | Backend |
|---|---|
| Linux, root, `meshloom-plugin` exists | `setuid` |
| Linux, not root but `CAP_SETUID` and `CAP_SETGID` effective (Docker with `MESHLOOM_RUN_AS_USER`), `meshloom-plugin` exists | `setuid` |
| Linux, root, no such account | `bwrap` if its self-test passes, else `none` |
| Linux, not root without those capabilities (deb/rpm, RPi image, source) | `bwrap` if its self-test passes, else `none` |
| not Linux (macOS: development only) | `none` |

Detected once per process (`current()`), never silently: the level is logged at
INFO, every fallback at WARNING with its reason, and `none` with one warning
that plugins run with hooks only.
"""

import logging
import os
import pwd
import shutil
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from app.plugins.isolation import bwrap as bwrap_backend
from app.plugins.isolation.base import IsolationBackend
from app.plugins.isolation.none import NoneBackend
from app.plugins.isolation.setuid import ACCOUNT, SetuidBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Selection:
    backend: IsolationBackend
    # Why each better backend was not taken, in the order they were tried.
    fallbacks: tuple[str, ...] = field(default_factory=tuple)

    @property
    def level(self) -> str:
        return self.backend.level


def _account(getpwnam: Callable[[str], pwd.struct_passwd]) -> tuple[int, int] | None:
    try:
        entry = getpwnam(ACCOUNT)
    except KeyError:
        return None
    if entry.pw_uid == 0 or entry.pw_gid == 0:
        return None
    return entry.pw_uid, entry.pw_gid


CAP_SETGID = 6
CAP_SETUID = 7


def effective_capabilities(status_path: str = "/proc/self/status") -> int:
    """This process's effective capability mask (0 when unreadable)."""
    try:
        with open(status_path, encoding="ascii") as fh:
            for line in fh:
                if line.startswith("CapEff:"):
                    return int(line.split()[1], 16)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def can_switch_uid(caps: int) -> bool:
    """Enough to run children under other uids. No `CAP_KILL`: a helper running as
    the instance uid signals its children (`SetuidBackend.signal_group`)."""
    return all(caps >> bit & 1 for bit in (CAP_SETUID, CAP_SETGID))


def detect(
    *,
    platform: str = sys.platform,
    geteuid: Callable[[], int] = os.geteuid,
    capabilities: Callable[[], int] = effective_capabilities,
    getpwnam: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
    which: Callable[[str], str | None] = shutil.which,
    bwrap_self_test: Callable[[str], str | None] = bwrap_backend.self_test,
) -> Selection:
    """Pick the backend for this machine. Every probe is injectable for the tests."""
    if not platform.startswith("linux"):
        return Selection(NoneBackend(), ("OS isolation needs Linux",))
    fallbacks: list[str] = []
    if geteuid() == 0:
        account = _account(getpwnam)
        if account is not None:
            return Selection(SetuidBackend(*account))
        fallbacks.append(f"running as root but the account {ACCOUNT} does not exist")
    elif can_switch_uid(capabilities()):
        account = _account(getpwnam)
        if account is not None and account[0] != geteuid():
            return Selection(SetuidBackend(*account))
        fallbacks.append(
            f"CAP_SETUID and CAP_SETGID held but the account {ACCOUNT} does not exist "
            "or is the server's own"
        )
    path = which("bwrap")
    if path is None:
        fallbacks.append("bwrap is not installed")
    else:
        error = bwrap_self_test(path)
        if error is None:
            return Selection(bwrap_backend.BwrapBackend(path), tuple(fallbacks))
        fallbacks.append(error)
    return Selection(NoneBackend(), tuple(fallbacks))


def log_selection(selection: Selection) -> None:
    for reason in selection.fallbacks:
        logger.warning("Plugin isolation fallback: %s", reason)
    if selection.level == "none":
        logger.warning(
            "Plugin isolation: none (hooks only, no OS isolation): a hostile plugin could "
            "read the database and the code. Install bubblewrap, or run the image as root "
            "with the %s account, to isolate them (docs/user/en/deep/plugins.md).",
            ACCOUNT,
        )
    else:
        logger.info("Plugin isolation: %s (%s)", selection.level, selection.backend.label)


_lock = threading.Lock()
_current: Selection | None = None


def current() -> Selection:
    """The process-wide selection: detected and logged on the first call, then cached."""
    global _current
    with _lock:
        if _current is None:
            _current = detect()
            log_selection(_current)
        return _current


def reset_for_tests() -> None:
    global _current
    with _lock:
        _current = None
