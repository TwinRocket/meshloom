"""Database file modes on a package install (MSHL-461).

``meshloom.service`` keeps the state directory at ``0750`` and runs with ``UMask=0027``,
so whatever the server creates stays away from other local accounts. An install from
before that has its database at ``0644``: this removes the other-account bits at startup,
as the service user, before the first connection (SQLite gives ``-wal`` and ``-shm`` the
database's mode). It also covers a database moved out of the state directory with
``MESHCORE_DATABASE_PATH``. Files: the database, ``<db>-wal``, ``<db>-shm`` and copies
named ``<db>.*``.

Package installs only: in Docker, ``data/`` belongs to whoever runs the stack, and a copy
taken from the host must keep working. The setuid level has its own model
(``app/plugins/isolation/permissions.py``). Never raises: a file left as it was does not
stop the server.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from app.services.install_kind import detect_install_kind

logger = logging.getLogger(__name__)

_OTHER_BITS = stat.S_IRWXO


def _tighten(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return  # a link, or nothing we can open
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            return
        mode = stat.S_IMODE(info.st_mode)
        if mode & _OTHER_BITS:
            os.fchmod(fd, mode & ~_OTHER_BITS)
            logger.info("Removed other accounts' access to %s", path.name)
    except OSError as exc:
        logger.warning("Could not restrict access to %s: %s", path.name, exc.strerror)
    finally:
        os.close(fd)


def tighten_database_files(db_path: Path) -> None:
    """Remove other accounts' access to the database and its companions (module doc)."""
    if detect_install_kind()[0] != "package":
        return
    name = db_path.name
    try:
        entries = [
            Path(entry.path)
            for entry in os.scandir(db_path.parent)
            if entry.name == name or entry.name.startswith((f"{name}-", f"{name}."))
        ]
    except OSError:
        return
    for path in entries:
        _tighten(path)
