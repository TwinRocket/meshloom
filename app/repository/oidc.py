"""The OpenID Connect provider settings and account links (migration 140, MSHL-427).

Rules in ``app/auth/oidc.py`` and ``docs/dev/auth.md``, "OpenID Connect".

- ``OidcProviderRepository``: the single ``oidc_provider`` row. The client secret is
  sealed with ``app/secret_store.py`` on write and never leaves this module except
  through ``client_secret()`` (the token exchange). ``OidcProvider`` says only
  whether one is set.
- ``OidcLinkRepository``: ``oidc_links``, an external identity → an existing local
  account, bound to one issuer. ``bind_subject()`` turns an e-mail link into a
  subject link on its first sign-in (one conditional ``UPDATE``: two concurrent
  sign-ins cannot both bind it).
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Literal, get_args

from pydantic import BaseModel

from app import secret_store
from app.database import db

Preset = Literal["generic", "google", "discord", "keycloak"]
PRESETS: tuple[str, ...] = get_args(Preset)
TokenAuthMethod = Literal["client_secret_basic", "client_secret_post"]
TOKEN_AUTH_METHODS: tuple[str, ...] = get_args(TokenAuthMethod)

SECRET_AAD = secret_store.aad("oidc_provider", "1", "client_secret")

# Bounds of the stored fields (the API checks them again with a 422).
LABEL_MAX_LENGTH = 64
ISSUER_MAX_LENGTH = 512
CLIENT_ID_MAX_LENGTH = 256
CLIENT_SECRET_MAX_LENGTH = 1024
SCOPES_MAX_LENGTH = 256
SUBJECT_MAX_LENGTH = 255
EMAIL_MAX_LENGTH = 254
MAX_LINKS = 1000


class OidcProvider(BaseModel):
    """The provider row, without its secret."""

    enabled: bool = False
    preset: Preset = "generic"
    label: str = ""
    issuer: str = ""
    client_id: str = ""
    client_secret_set: bool = False
    scopes: str = "openid email profile"
    token_auth_method: TokenAuthMethod = "client_secret_basic"
    auto_create: bool = False
    allow_private_network: bool = False
    updated_at: int = 0

    @property
    def ready(self) -> bool:
        """Switched on with what a sign-in needs (the secret is checked at the exchange)."""
        return self.enabled and bool(self.issuer) and bool(self.client_id)


class OidcLink(BaseModel):
    id: int
    user_id: int
    username: str = ""
    issuer: str
    subject: str | None = None
    email: str | None = None
    created_at: int
    last_login_at: int | None = None


class LinkExistsError(ValueError):
    """The issuer already links this subject or e-mail."""


class LinkLimitError(ValueError):
    pass


_PROVIDER_FIELDS = (
    "enabled",
    "preset",
    "label",
    "issuer",
    "client_id",
    "scopes",
    "token_auth_method",
    "auto_create",
    "allow_private_network",
)


def _provider(row: Any) -> OidcProvider:
    return OidcProvider(
        enabled=bool(row["enabled"]),
        preset=row["preset"] if row["preset"] in PRESETS else "generic",
        label=row["label"] or "",
        issuer=row["issuer"] or "",
        client_id=row["client_id"] or "",
        client_secret_set=row["client_secret"] is not None,
        scopes=row["scopes"] or "openid",
        token_auth_method=(
            row["token_auth_method"]
            if row["token_auth_method"] in TOKEN_AUTH_METHODS
            else "client_secret_basic"
        ),
        auto_create=bool(row["auto_create"]),
        allow_private_network=bool(row["allow_private_network"]),
        updated_at=int(row["updated_at"] or 0),
    )


def _link(row: Any) -> OidcLink:
    keys = row.keys()
    return OidcLink(
        id=row["id"],
        user_id=row["user_id"],
        username=(row["username"] or "") if "username" in keys else "",
        issuer=row["issuer"],
        subject=row["subject"],
        email=row["email"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


class OidcProviderRepository:
    @staticmethod
    async def get() -> OidcProvider:
        """The provider row; the defaults (off) when the row is missing."""
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM oidc_provider WHERE id = 1") as cursor:
                row = await cursor.fetchone()
        return _provider(row) if row is not None else OidcProvider()

    @staticmethod
    async def update(
        fields: dict[str, Any],
        *,
        client_secret: str | None = None,
        clear_secret: bool = False,
        now: int | None = None,
    ) -> OidcProvider:
        """Write the given fields (names of ``OidcProvider``). ``client_secret`` is
        sealed before it is stored; ``clear_secret`` removes it. Raises
        ``secret_store.SecretStoreUnavailable`` when there is no key to seal with."""
        unknown = set(fields) - set(_PROVIDER_FIELDS)
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        assignments: list[str] = []
        params: list[Any] = []
        for name in _PROVIDER_FIELDS:
            if name in fields:
                value = fields[name]
                assignments.append(f"{name} = ?")
                params.append(int(value) if isinstance(value, bool) else value)
        if client_secret is not None:
            assignments.append("client_secret = ?")
            params.append(secret_store.encrypt(client_secret, SECRET_AAD))
        elif clear_secret:
            assignments.append("client_secret = NULL")
        assignments.append("updated_at = ?")
        params.append(int(time.time()) if now is None else now)
        async with db.tx() as conn:
            await conn.execute("INSERT OR IGNORE INTO oidc_provider (id) VALUES (1)")
            await conn.execute(
                f"UPDATE oidc_provider SET {', '.join(assignments)} WHERE id = 1", params
            )
            async with conn.execute("SELECT * FROM oidc_provider WHERE id = 1") as cursor:
                row = await cursor.fetchone()
        return _provider(row)

    @staticmethod
    async def client_secret() -> str | None:
        """The plain client secret, for the token request only. Raises
        ``secret_store.SecretUnreadable`` when it was sealed with another key."""
        async with db.readonly() as conn:
            async with conn.execute("SELECT client_secret FROM oidc_provider WHERE id = 1") as c:
                row = await c.fetchone()
        if row is None or row[0] is None:
            return None
        return secret_store.decrypt(row[0], SECRET_AAD)


_LINK_SELECT = (
    "SELECT l.*, u.username AS username FROM oidc_links l LEFT JOIN users u ON u.id = l.user_id"
)


class OidcLinkRepository:
    @staticmethod
    async def list_all() -> list[OidcLink]:
        async with db.readonly() as conn:
            async with conn.execute(f"{_LINK_SELECT} ORDER BY l.id") as cursor:
                return [_link(row) for row in await cursor.fetchall()]

    @staticmethod
    async def get(link_id: int) -> OidcLink | None:
        async with db.readonly() as conn:
            async with conn.execute(f"{_LINK_SELECT} WHERE l.id = ?", (link_id,)) as cursor:
                row = await cursor.fetchone()
        return _link(row) if row is not None else None

    @staticmethod
    async def by_subject(issuer: str, subject: str) -> OidcLink | None:
        async with db.readonly() as conn:
            async with conn.execute(
                f"{_LINK_SELECT} WHERE l.issuer = ? AND l.subject = ?", (issuer, subject)
            ) as cursor:
                row = await cursor.fetchone()
        return _link(row) if row is not None else None

    @staticmethod
    async def by_email(issuer: str, email: str) -> OidcLink | None:
        async with db.readonly() as conn:
            async with conn.execute(
                f"{_LINK_SELECT} WHERE l.issuer = ? AND l.email = ?", (issuer, email)
            ) as cursor:
                row = await cursor.fetchone()
        return _link(row) if row is not None else None

    @staticmethod
    async def add(
        user_id: int,
        issuer: str,
        *,
        subject: str | None = None,
        email: str | None = None,
        now: int | None = None,
    ) -> OidcLink:
        """Insert a link. Raises ``LinkExistsError`` when the issuer already links
        this subject or e-mail, ``LinkLimitError`` past ``MAX_LINKS``."""
        if subject is None and email is None:
            raise ValueError("a link needs a subject or an e-mail")
        ts = int(time.time()) if now is None else now
        try:
            async with db.tx() as conn:
                async with conn.execute("SELECT COUNT(*) FROM oidc_links") as cursor:
                    row = await cursor.fetchone()
                if row is not None and int(row[0]) >= MAX_LINKS:
                    raise LinkLimitError("too many links")
                async with conn.execute(
                    "INSERT INTO oidc_links (user_id, issuer, subject, email, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (user_id, issuer, subject, email, ts),
                ) as cursor:
                    link_id = int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError:
            raise LinkExistsError("already linked") from None
        link = await OidcLinkRepository.get(link_id)
        assert link is not None
        return link

    @staticmethod
    async def bind_subject(link_id: int, subject: str) -> bool:
        """Record the subject of an e-mail link on its first sign-in. False when the
        link is gone, already bound, or the subject is linked elsewhere."""
        try:
            async with db.tx() as conn:
                async with conn.execute(
                    "UPDATE oidc_links SET subject = ? WHERE id = ? AND subject IS NULL",
                    (subject, link_id),
                ) as cursor:
                    return cursor.rowcount == 1
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    async def touch(link_id: int, *, now: int | None = None) -> None:
        ts = int(time.time()) if now is None else now
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE oidc_links SET last_login_at = ? WHERE id = ?", (ts, link_id)
            )

    @staticmethod
    async def delete(link_id: int) -> bool:
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM oidc_links WHERE id = ?", (link_id,)) as cursor:
                return cursor.rowcount > 0
