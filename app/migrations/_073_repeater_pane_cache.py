import logging

import aiosqlite

logger = logging.getLogger(__name__)


async def migrate(conn: aiosqlite.Connection) -> None:
    """Store the last answer each repeater gave, per pane.

    The repeater dashboard held its values in browser memory only, so a reload
    emptied every pane and the sole way to see node info, radio settings or
    regions again was to ask the repeater over the air — for values that rarely
    change. One row per (public_key, pane) keeps the last successful payload so
    the dashboard can open on known values and query the mesh only on request.

    Only the newest answer per pane is kept: this is a cache, not a history.
    `repeater_telemetry_history` remains the place for time series.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repeater_pane_cache (
            public_key TEXT NOT NULL,
            pane TEXT NOT NULL,
            data TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            PRIMARY KEY (public_key, pane),
            FOREIGN KEY (public_key) REFERENCES contacts(public_key) ON DELETE CASCADE
        )
        """
    )
    await conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_repeater_pane_cache_pk
            ON repeater_pane_cache (public_key)
        """
    )
    await conn.commit()
