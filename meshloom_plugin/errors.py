"""Exceptions plugin code may catch.

They live here, not in `meshloom_plugin.runner`: the runner runs as `__main__`,
so `import meshloom_plugin.runner` from a plugin would load a second copy whose
classes never match the ones raised.
"""

from typing import Any


class HostError(Exception):
    """The server answered a `host().call()` with a JSON-RPC error; `error` is that object."""

    def __init__(self, error: Any):
        message = error.get("message") if isinstance(error, dict) else None
        super().__init__(message or "host error")
        self.error = error
