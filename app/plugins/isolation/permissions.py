"""File permissions model for the dedicated-uid level (the server runs as root).

The plugin uid is "everyone else" for every file the server owns, so the model
is about the other bits:

- the data directory is `0711`: traversable (the plugin code sits below it),
  not listable;
- every other file directly in it (`meshcore.db`, its `-wal` and `-shm`,
  `update-attempt.json`, ...) is `0600`, every other subdirectory `0700`;
- `plugins/` and each plugin's tree are `0755` directories and `0644` files:
  the code is readable, never writable, by the child;
- the server's umask becomes `077`, so a file it creates later (a new `-wal`, a
  backup) is born `0600` and the model holds after startup; a plugin directory
  written later is opened up again before its child starts
  (`SetuidBackend.prepare_plugin_dir`).
- `/data/options.json` (the add-on's options, which may hold the retired Basic
  credentials) is `0600` when it exists.

Symbolic links are never followed or changed. Applied only when the server is
root and the `setuid` level is selected; elsewhere the server's own uid owns
the files and bubblewrap hides them.
"""

import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR_MODE = 0o711
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
CODE_DIR_MODE = 0o755
CODE_FILE_MODE = 0o644
SERVER_UMASK = 0o077
PLUGINS_DIRNAME = "plugins"
EXTRA_PRIVATE_FILES = (Path("/data/options.json"),)


def _chmod(path: Path, mode: int) -> None:
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or stat.S_IMODE(st.st_mode) == mode:
            return
        os.chmod(path, mode, follow_symlinks=False)
    except NotImplementedError:
        # No lchmod on Linux: the lstat above already ruled the link out.
        os.chmod(path, mode)
    except OSError as exc:
        logger.warning("Could not set mode %o on %s: %s", mode, path, exc.strerror)


def make_tree_readable(root: Path) -> None:
    """Directories `0755`, files `0644`, below and including `root`."""
    if not root.is_dir() or root.is_symlink():
        return
    _chmod(root, CODE_DIR_MODE)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            _chmod(Path(dirpath, name), CODE_DIR_MODE)
        for name in filenames:
            _chmod(Path(dirpath, name), CODE_FILE_MODE)


def apply(data_dir: Path, extra_private: tuple[Path, ...] = EXTRA_PRIVATE_FILES) -> None:
    """Apply the model to `data_dir` and set the server's umask (see the module doc)."""
    os.umask(SERVER_UMASK)
    if data_dir.is_dir() and not data_dir.is_symlink():
        _chmod(data_dir, DATA_DIR_MODE)
        for entry in os.scandir(data_dir):
            path = Path(entry.path)
            if entry.is_symlink():
                continue
            if entry.name == PLUGINS_DIRNAME and entry.is_dir():
                make_tree_readable(path)
            elif entry.is_dir():
                _chmod(path, PRIVATE_DIR_MODE)
            else:
                _chmod(path, PRIVATE_FILE_MODE)
    for path in extra_private:
        if path.is_file() and not path.is_symlink():
            _chmod(path, PRIVATE_FILE_MODE)
