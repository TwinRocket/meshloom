"""Migration 145: deleted channels still waiting for their radio slot to be emptied (MSHL-459).

``channel_slot_clears_pending``: the key of a channel deleted whose slots are not
known to be empty yet. Written with the delete, dropped once the slots are emptied;
otherwise (radio away, silent or refusing, interruption) the next connect sync
empties the slot without adopting the channel, then drops the row. Holds channel
keys: never in a backup, emptied in a database copy.

Idempotent: a second run changes nothing.
"""

import aiosqlite

_TABLE = """CREATE TABLE IF NOT EXISTS channel_slot_clears_pending (
    key TEXT PRIMARY KEY
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.execute(_TABLE)
    await conn.commit()
