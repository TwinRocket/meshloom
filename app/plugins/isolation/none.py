"""Level `none`, "hooks only": the runner's own guards and nothing around the child.

Selected when neither the dedicated uid nor bubblewrap is available (and on
macOS, a development platform). A hostile plugin can then read the database and
the code with the server's rights (docs/dev/plugins-security.md); the level is
logged with a warning and reported in `/api/health` and `plugin.init`.
"""

from app.plugins.isolation.base import IsolationBackend


class NoneBackend(IsolationBackend):
    level = "none"
