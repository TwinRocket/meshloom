"""OS isolation of the plugin children (MSHL-180): `bwrap`, `setuid` or `none`.

`current()` detects the backend once per process (`selection.py`, with the table);
`startup(data_dir)` applies what the selected level needs before the first child
(the file model of `permissions.py` under `setuid`). The supervisor
(`app/plugins/host.py`) launches each child through `current().backend.launch()`.
What each level closes: docs/dev/plugins-security.md, "OS isolation".
"""

from pathlib import Path

from app.plugins.isolation.base import LEVEL_LABELS, IsolationBackend
from app.plugins.isolation.selection import Selection, current, detect

__all__ = [
    "LEVEL_LABELS",
    "IsolationBackend",
    "Selection",
    "current",
    "detect",
    "startup",
]


def startup(data_dir: Path) -> Selection:
    """Detect (once) and prepare the data directory for the selected level."""
    selection = current()
    selection.backend.prepare_data_dir(data_dir)
    return selection
