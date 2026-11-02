"""E-mail sign-in: the SMTP profile it uses and each account's opt-in (migration 141, MSHL-426).

Rules in ``app/auth/email_factor.py`` and ``docs/dev/auth.md``, "E-mail code and
sign-in links".

- ``EmailSignInRepository``: the single ``email_sign_in`` row, the notification
  e-mail channel (``notification_channels.id``) whose SMTP settings send the sign-in
  mails. NULL: no SMTP profile, both features refused.
- ``EmailFactorRepository``: ``user_email_factor``, one row per account that verified
  an address, with its two opt-ins. Every write moves the account's ``updated_at``
  (the rule of ``app/repository/auth.py``), so a pending login, a sign-in link or a
  known-device cookie issued before the change stops working.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from app.database import db

ADDRESS_MAX_LENGTH = 254
CHANNEL_ID_MAX_LENGTH = 64

_BUMP_USER = "UPDATE users SET updated_at = MAX(?, updated_at + 1) WHERE id = ?"


class EmailFactor(BaseModel):
    user_id: int
    address: str
    otp_enabled: bool = False
    magic_link_enabled: bool = False
    verified_at: int
    updated_at: int


def _row(row) -> EmailFactor:
    return EmailFactor(
        user_id=row["user_id"],
        address=row["address"],
        otp_enabled=bool(row["otp_enabled"]),
        magic_link_enabled=bool(row["magic_link_enabled"]),
        verified_at=row["verified_at"],
        updated_at=row["updated_at"],
    )


class EmailSignInRepository:
    @staticmethod
    async def channel_id() -> str | None:
        async with db.readonly() as conn:
            async with conn.execute("SELECT channel_id FROM email_sign_in WHERE id = 1") as cur:
                row = await cur.fetchone()
        return (row["channel_id"] or None) if row else None

    @staticmethod
    async def set_channel_id(channel_id: str | None) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "INSERT INTO email_sign_in (id, channel_id, updated_at) VALUES (1, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET channel_id = excluded.channel_id,"
                " updated_at = excluded.updated_at",
                (channel_id, int(time.time())),
            )


class EmailFactorRepository:
    @staticmethod
    async def get(user_id: int) -> EmailFactor | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM user_email_factor WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row(row) if row else None

    @staticmethod
    async def any_magic_link() -> bool:
        """Whether an enabled account allows sign-in links (the login page's button)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT 1 FROM user_email_factor f JOIN users u ON u.id = f.user_id"
                " WHERE f.magic_link_enabled = 1 AND u.disabled = 0 LIMIT 1"
            ) as cur:
                return await cur.fetchone() is not None

    @staticmethod
    async def set_address(user_id: int, address: str) -> EmailFactor:
        """A verified address: a new row with both opt-ins off, or a changed address
        keeping the account's opt-ins."""
        now = int(time.time())
        async with db.tx() as conn:
            await conn.execute(
                "INSERT INTO user_email_factor (user_id, address, verified_at, updated_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET"
                " address = excluded.address, verified_at = excluded.verified_at,"
                " updated_at = excluded.updated_at",
                (user_id, address, now, now),
            )
            await conn.execute(_BUMP_USER, (now, user_id))
        factor = await EmailFactorRepository.get(user_id)
        assert factor is not None
        return factor

    @staticmethod
    async def set_flags(
        user_id: int, *, otp_enabled: bool | None = None, magic_link_enabled: bool | None = None
    ) -> EmailFactor | None:
        """Change the opt-ins of an account that has a verified address (None: none)."""
        now = int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE user_email_factor SET"
                " otp_enabled = COALESCE(?, otp_enabled),"
                " magic_link_enabled = COALESCE(?, magic_link_enabled),"
                " updated_at = ? WHERE user_id = ?",
                (
                    None if otp_enabled is None else int(otp_enabled),
                    None if magic_link_enabled is None else int(magic_link_enabled),
                    now,
                    user_id,
                ),
            ) as cur:
                changed = cur.rowcount > 0
            if changed:
                await conn.execute(_BUMP_USER, (now, user_id))
        return await EmailFactorRepository.get(user_id) if changed else None

    @staticmethod
    async def delete(user_id: int) -> bool:
        now = int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM user_email_factor WHERE user_id = ?", (user_id,)
            ) as cur:
                deleted = cur.rowcount > 0
            if deleted:
                await conn.execute(_BUMP_USER, (now, user_id))
        return deleted
