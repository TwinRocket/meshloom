"""The plugin host's three seams for the app: lifespan start, bounded stop, health fields.

`app/main.py` calls `start()` once the database is open and the fanout modules
are loaded, and `stop()` as a shutdown step before the background tasks are
drained; `/api/health` merges `health_fields()` into its payload. Nothing else
here: the supervisor is `app/plugins/host.py`.
"""

import logging
from typing import Any

from app.plugins.host import isolation_level, plugin_manager

logger = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT_SECONDS = 10.0


async def start() -> None:
    """Start the enabled instances (staggered, in the background). Never raises."""
    source = plugin_manager.disabled_source()
    if source is not None:
        logger.info("Plugin system disabled (%s)", source)
        return
    try:
        await plugin_manager.load_from_db()
    except Exception:
        logger.exception("Failed to start plugins")


async def stop(timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
    """Stop every child: `plugin.shutdown`, a bounded wait, then SIGKILL; at most `timeout`."""
    await plugin_manager.stop_all(timeout)


def health_fields() -> dict[str, Any]:
    """`plugins_isolation`, `plugins_disabled_source` and `plugin_statuses` for `/api/health`."""
    source = plugin_manager.disabled_source()
    try:
        statuses = plugin_manager.statuses()
    except Exception:
        statuses = {}
    return {
        "plugins_isolation": None if source is not None else isolation_level(),
        "plugins_disabled_source": source,
        "plugin_statuses": statuses,
    }
