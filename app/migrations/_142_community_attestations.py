"""Migration 142: cached Community attestations of the instance keys (MSHL-445).

``community_attestations``: one row per audience (``links``, ``remote``), the last
attestation Community issued for that service's instance key
(``app/services/community_attestation.py``, contract
``meshloom-community/docs/contracts/attestation.md``). Kept so a restart does not
ask Community again while the token is still valid. Never in a backup, emptied in a
database copy and on every restore (the restore rotates the instance keys anyway).

Idempotent: a second run changes nothing.
"""

import aiosqlite

_TABLE = """CREATE TABLE IF NOT EXISTS community_attestations (
    audience TEXT PRIMARY KEY CHECK (audience IN ('links', 'remote')),
    instance_key TEXT NOT NULL,
    token TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    fetched_at INTEGER NOT NULL
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    await conn.execute(_TABLE)
    await conn.commit()
