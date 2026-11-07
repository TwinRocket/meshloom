"""Migration 141: e-mail second factor and sign-in links (MSHL-426, ``app/auth/email_factor.py``).

- ``email_sign_in``: one row (``id = 1``), the notification e-mail channel whose SMTP
  settings send the sign-in mails (Settings → Connection → E-mail sign-in). NULL
  (the default) means no SMTP profile: both features are refused.
- ``user_email_factor``: one row per account that verified an address. ``otp_enabled``
  asks for an e-mail code after the password; ``magic_link_enabled`` lets the account
  ask for a sign-in link. Both off until the account opts in. Deleted with the account.

Idempotent: a second run changes nothing.
"""

import aiosqlite

_SETTINGS = """CREATE TABLE IF NOT EXISTS email_sign_in (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    channel_id TEXT,
    updated_at INTEGER NOT NULL DEFAULT 0
)"""
_SETTINGS_ROW = "INSERT OR IGNORE INTO email_sign_in (id) VALUES (1)"
_FACTOR = """CREATE TABLE IF NOT EXISTS user_email_factor (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    address TEXT NOT NULL,
    otp_enabled INTEGER NOT NULL DEFAULT 0,
    magic_link_enabled INTEGER NOT NULL DEFAULT 0,
    verified_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)"""


async def migrate(conn: aiosqlite.Connection) -> None:
    for statement in (_SETTINGS, _SETTINGS_ROW, _FACTOR):
        await conn.execute(statement)
    await conn.commit()
