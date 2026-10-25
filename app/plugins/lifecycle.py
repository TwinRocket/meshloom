"""The plugin host's three seams for the app: lifespan start, bounded stop, health fields.

`app/main.py` calls `start()` once the database is open and the fanout modules
are loaded, and `stop()` as a shutdown step before the background tasks are
drained; `/api/health` merges `health_fields()` into its payload. Nothing else
here: the supervisor is `app/plugins/host.py`.
"""

import asyncio
import logging
from typing import Any, Final

from app.plugins import isolation
from app.plugins.host import isolation_level, plugin_manager

logger = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT_SECONDS = 10.0
# Who may read a plugin's `last_error` and `send_guard` (health) and its
# `plugin_status` events: admins only (MSHL-64). The others get the statuses
# without them (`app/routers/health.py`, `without_admin_details`).
PLUGIN_ERROR_PERMISSION: Final = "admin"


async def start() -> None:
    """Start the enabled instances (staggered, in the background). Never raises."""
    source = plugin_manager.disabled_source()
    if source is not None:
        logger.info("Plugin system disabled (%s)", source)
        return
    try:
        # Detect the OS isolation (the bwrap self-test runs a process) and apply
        # the dedicated uid's file model before the first child, off the loop.
        await asyncio.to_thread(isolation.startup, plugin_manager.plugins_root().parent)
    except Exception:
        # Fail closed: a dedicated uid without its file model could read the
        # database. No child starts; health says why (`plugins_disabled_source`).
        logger.exception("Plugin isolation setup failed: no plugin will start")
        plugin_manager.fail_closed()
        return
    try:
        await plugin_manager.load_from_db()
    except Exception:
        logger.exception("Failed to start plugins")


async def stop(timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
    """Stop every child: `plugin.shutdown`, a bounded wait, then SIGKILL; at most `timeout`."""
    await plugin_manager.stop_all(timeout)


def without_plugin_errors(health: dict[str, Any]) -> dict[str, Any]:
    """`health` as a principal without `PLUGIN_ERROR_PERMISSION` gets it."""
    statuses = health.get("plugin_statuses")
    if not isinstance(statuses, dict) or not statuses:
        return health
    return {
        **health,
        "plugin_statuses": {
            key: {**value, "last_error": None, "send_guard": {}}
            if isinstance(value, dict)
            else value
            for key, value in statuses.items()
        },
    }


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
