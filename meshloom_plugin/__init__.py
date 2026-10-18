"""Child-side runtime of Meshloom plugins (docs/dev/plugins.md, docs/dev/plugins-security.md).

This package runs inside the plugin child process, launched as
`python -I -S -B -X utf8 -c <bootstrap>` (see `app/plugins/launch.py`). It must
stay importable with the standard library only: the child never sees the
server's site-packages. Keep this module free of imports; the runner reads it
before the import surface is frozen.
"""

from typing import Any

_host: Any = None


def __getattr__(name: str) -> Any:
    # `from meshloom_plugin import Plugin, Message`: loaded on first use, because the
    # runner imports this package before it freezes the import surface.
    if name in ("Plugin", "Message", "HostError"):
        from meshloom_plugin import sdk

        return getattr(sdk, name)
    raise AttributeError(f"module 'meshloom_plugin' has no attribute {name!r}")


def host() -> Any:
    """The RPC channel to the server, set by the runner before the entry module loads.

    `await host().call(method, params)` sends a request and returns its result;
    `host().notify(method, params)` sends a notification; `host().call_sync(...)`
    blocks the handler until the answer (legacy bots only). `meshloom_plugin.sdk`
    wraps it; outside a runner there is no host and this raises `RuntimeError`.
    """
    if _host is None:
        raise RuntimeError("not running inside a Meshloom plugin runner")
    return _host
