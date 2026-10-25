"""Process hardening applied once at startup."""

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

PR_SET_DUMPABLE = 4


def set_not_dumpable() -> bool:
    """Clear the dumpable flag so other same-uid processes cannot read our memory.

    With the flag at 0, ``/proc/<pid>/environ`` and ``/proc/<pid>/mem`` are
    unreadable for every non-root process, and no core dump is written. Linux
    only: returns False without doing anything elsewhere. A failure is logged at
    warning level and never raises.
    """
    if not sys.platform.startswith("linux"):
        logger.debug("Not Linux: prctl(PR_SET_DUMPABLE) skipped")
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            errno = ctypes.get_errno()
            logger.warning("prctl(PR_SET_DUMPABLE, 0) failed (errno %d)", errno)
            return False
    except Exception as exc:
        logger.warning("Could not clear the dumpable flag: %s", exc)
        return False
    logger.info("Process set not dumpable")
    return True
