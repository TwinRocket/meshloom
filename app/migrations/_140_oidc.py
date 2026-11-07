"""Migration 140: bring-your-own OpenID Connect sign-in (MSHL-427, ``app/auth/oidc.py``).

- ``oidc_provider``: one row (``id = 1``), the provider an admin configures in
  Settings → Connection → OpenID Connect. Off by default, nothing filled in.
  ``client_secret`` is a ``v1:`` token of ``app/secret_store.py`` (AAD
  ``oidc_provider:1:client_secret``), NULL until an admin sets one.
- ``oidc_links``: which external identity signs in which existing local account.
  An admin writes a link with a ``subject`` (the provider's ``sub``) or an
  ``email`` (matched against a verified e-mail claim; the first sign-in then
  records the subject, so later ones match on it). Bound to the ``issuer`` it was
  written for: another provider matches none. Deleted with the account.

Idempotent: a second run changes nothing.
"""

import aiosqlite

_PROVIDER = """CREATE TABLE IF NOT EXISTS oidc_provider (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    preset TEXT NOT NULL DEFAULT 'generic',
    label TEXT NOT NULL DEFAULT '',
    issuer TEXT NOT NULL DEFAULT '',
    client_id TEXT NOT NULL DEFAULT '',
    client_secret TEXT,
    scopes TEXT NOT NULL DEFAULT 'openid email profile',
    token_auth_method TEXT NOT NULL DEFAULT 'client_secret_basic',
    auto_create INTEGER NOT NULL DEFAULT 0,
    allow_private_network INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL DEFAULT 0
)"""
_PROVIDER_ROW = "INSERT OR IGNORE INTO oidc_provider (id) VALUES (1)"
_LINKS = """CREATE TABLE IF NOT EXISTS oidc_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    issuer TEXT NOT NULL,
    subject TEXT,
    email TEXT,
    created_at INTEGER NOT NULL,
    last_login_at INTEGER,
    CHECK (subject IS NOT NULL OR email IS NOT NULL),
    UNIQUE (issuer, subject),
    UNIQUE (issuer, email)
)"""
_LINKS_INDEX = "CREATE INDEX IF NOT EXISTS idx_oidc_links_user ON oidc_links(user_id)"


async def migrate(conn: aiosqlite.Connection) -> None:
    for statement in (_PROVIDER, _PROVIDER_ROW, _LINKS, _LINKS_INDEX):
        await conn.execute(statement)
    await conn.commit()
