"""``community_attestations`` (migration 142): the cached Community attestations.

One row per audience (``links``, ``remote``). Rules in
``app/services/community_attestation.py``. Never in a backup; a database copy empties
the table (``SNAPSHOT_EMPTIED_TABLES``) and every restore clears it. The token is never
logged.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.database import db


class StoredAttestation(BaseModel):
    audience: str
    instance_key: str
    token: str
    issued_at: int
    expires_at: int
    fetched_at: int


_COLUMNS = "audience, instance_key, token, issued_at, expires_at, fetched_at"


class CommunityAttestationRepository:
    @staticmethod
    async def get(audience: str) -> StoredAttestation | None:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {_COLUMNS} FROM community_attestations WHERE audience = ?", (audience,)
            ) as cursor:
                row = await cursor.fetchone()
        return StoredAttestation(**dict(row)) if row is not None else None

    @staticmethod
    async def put(attestation: StoredAttestation) -> None:
        async with db.tx() as conn:
            await conn.execute(
                f"INSERT OR REPLACE INTO community_attestations ({_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attestation.audience,
                    attestation.instance_key,
                    attestation.token,
                    attestation.issued_at,
                    attestation.expires_at,
                    attestation.fetched_at,
                ),
            )

    @staticmethod
    async def delete(audience: str | None = None) -> None:
        """Forget one audience's attestation, or every one (``None``)."""
        async with db.tx() as conn:
            if audience is None:
                await conn.execute("DELETE FROM community_attestations")
            else:
                await conn.execute(
                    "DELETE FROM community_attestations WHERE audience = ?", (audience,)
                )
