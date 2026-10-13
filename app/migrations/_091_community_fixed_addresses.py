import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Fixed Community addresses; marker for the one-shot import of the retired env.

    The Community broker and API addresses are now fixed in code
    (``DEFAULT_BROKER_HOST`` / ``DEFAULT_API_BASE``). The old
    ``community_broker_host`` / ``community_api_base`` columns are emptied
    (a value set is logged first) and never read again; they are not dropped.
    A retained status left on an old custom broker is still cleared once at
    startup, from ``community_status_last``.

    ``community_env_imported`` marks the one-shot import of the retired
    ``MESHLOOM_COMMUNITY*`` variables (``import_legacy_community_env``).
    """
    tables_cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    if "app_settings" not in {row[0] for row in await tables_cursor.fetchall()}:
        await conn.commit()
        return

    col_cursor = await conn.execute("PRAGMA table_info(app_settings)")
    columns = {row[1] for row in await col_cursor.fetchall()}
    for name in ("community_broker_host", "community_api_base"):
        if name not in columns:
            continue
        cursor = await conn.execute(f"SELECT {name} FROM app_settings WHERE id = 1")
        row = await cursor.fetchone()
        value = (row[0] or "").strip() if row else ""
        if value:
            logger.warning(
                "Dropping app_settings.%s=%r: the Community addresses are fixed now", name, value
            )
        await conn.execute(f"UPDATE app_settings SET {name} = ''")
    if "community_env_imported" not in columns:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN community_env_imported INTEGER DEFAULT 0"
        )
        logger.info("Added community_env_imported setting")
    await conn.commit()
