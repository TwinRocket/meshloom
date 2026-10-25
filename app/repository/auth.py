"""Repository for the local accounts tables: users, sessions, api_tokens, auth_audit, auth_settings,
recovery_codes, webauthn_credentials.

Schema and rules: ``docs/dev/auth.md``. Hashes go in and out of this module but
are never logged; the general models (``app/auth/models.py``) carry none.
"""

import asyncio
import hmac
import json
import re
import secrets
import sqlite3
import time
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from typing import Any

import aiosqlite
from pydantic import BaseModel, ConfigDict, SecretStr

from app.auth.models import (
    PROVIDERS,
    ROLES,
    USERNAME_MAX_LENGTH,
    ApiToken,
    AuthAuditEntry,
    AuthSession,
    AuthSettings,
    HostGuardMode,
    PairingCode,
    Provider,
    Role,
    TokenOrigin,
    TrustedNetworkRule,
    User,
    UserCredentials,
)
from app.database import db
from app.middleware import proxy_headers
from app.middleware.proxy_headers import valid_stored_proxies

# Live API tokens per user; creating one more is refused (``TokenLimitError``).
MAX_TOKENS_PER_USER = 100
# Active sessions kept per user; creating one more revokes the oldest.
MAX_SESSIONS_PER_USER = 50

# ``touch()`` writes ``last_seen_at`` at most this often per session.
LAST_SEEN_WRITE_INTERVAL_S = 60

# Audit ``details`` are free-form JSON. A key is checked in three steps
# (``_is_secret_key``), after NFKC and case folding:
# 1. anything outside ``[a-z0-9_-]`` is refused (a Cyrillic look-alike, a
#    space, a dot), so the next two steps see every character;
# 2. the key is split into words (snake_case, kebab-case, camelCase, digits:
#    ``radio_ble_pin``, ``blePin``, ``newPassword1``) and refused when a word
#    is in ``_SECRET_WORDS``, which keeps ``ping`` and ``pinned`` allowed;
# 3. the joined form (separators removed) is refused when it contains one of
#    ``_SECRET_FRAGMENTS``, for keys written without separators
#    (``oldpassword``, ``privatekey``).
# Over-refusing a harmless key is acceptable.
_SECRET_WORDS = frozenset(
    {
        "apikey",
        "auth",
        "authorization",
        "bearer",
        "code",
        "codes",
        "cookie",
        "credential",
        "credentials",
        "hash",
        "key",
        "keys",
        "otp",
        "pass",
        "passphrase",
        "passwd",
        "password",
        "pin",
        "pins",
        "pwd",
        "secret",
        "secrets",
        "seed",
        "session",
        "token",
        "tokens",
        "totp",
    }
)
_SECRET_FRAGMENTS = (
    "apikey",
    "authorization",
    "backupcode",
    "cookie",
    "credential",
    "pairingcode",
    "passphrase",
    "passwd",
    "password",
    "privatekey",
    "recoverycode",
    "secret",
    "setupcode",
    "token",
)
_ALLOWED_KEY = re.compile(r"[a-z0-9_-]+")
_KEY_WORDS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")

# Failed logins are written without authentication and kept for a year:
# every field is bounded. Text is truncated; ``details`` larger than this
# once serialised are replaced by a fixed marker.
MAX_AUDIT_DETAILS_BYTES = 2048
_AUDIT_TEXT_LIMITS = {
    "action": 64,
    "outcome": 32,
    "target": 256,
    "ip": 64,
    "via": 32,
    "actor_token_id": 64,
}
MAX_USER_AGENT_LENGTH = 512

# Audit rows deleted per transaction by ``purge_older_than()``.
AUDIT_PURGE_BATCH = 1000


class UsernameTakenError(ValueError):
    """A user with the same name (compared case-insensitively) already exists."""


class InvalidUsernameError(ValueError):
    """Empty, too long, or containing control characters."""


class LastAdminError(ValueError):
    """The change would leave no enabled administrator (``auth_last_admin``)."""


class MustChangeNotApplicableError(ValueError):
    """``must_change_password`` on an account that has no password to change:
    no password, a ``kiosk``, or a Home Assistant or system identity."""


class TokenLimitError(ValueError):
    """The user already holds ``MAX_TOKENS_PER_USER`` live tokens."""


class AuthSettingsMissingError(RuntimeError):
    """The ``auth_settings`` row is gone: refuse to act on defaults (fail closed).

    Migration 101 and ``SCHEMA_TABLES`` insert it; only a hand-edited database
    lacks it, and a restart puts it back."""


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else int(now)


def normalize_username(username: str) -> str:
    """The display form: NFC, surrounding whitespace removed. Raises on bad names."""
    name = unicodedata.normalize("NFC", username).strip()
    if not name or len(name) > USERNAME_MAX_LENGTH:
        raise InvalidUsernameError("username must be 1 to 64 characters")
    if any(unicodedata.category(ch).startswith("C") for ch in name):
        raise InvalidUsernameError("username contains control characters")
    return name


def username_key(username: str) -> str:
    """The uniqueness key: NFKC, Unicode case folding, NFKC again (case folding
    can leave a string that is not normalised): ``Élise`` = ``éLISE``."""
    folded = unicodedata.normalize("NFKC", normalize_username(username)).casefold()
    return unicodedata.normalize("NFKC", folded)


def _row_to_user(row: Any) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"] or "",
        role=row["role"],
        provider=row["provider"],
        external_id=row["external_id"],
        disabled=bool(row["disabled"]),
        must_change_password=bool(row["must_change_password"]),
        has_password=row["password_hash"] is not None,
        totp_enabled_at=row["totp_enabled_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_session(row: Any) -> AuthSession:
    return AuthSession(
        id=row["id"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        expires_at=row["expires_at"],
        ip=row["ip"],
        user_agent=row["user_agent"],
        via=row["via"],
        revoked_at=row["revoked_at"],
        remember=bool(row["remember"]),
    )


def _row_to_token(row: Any) -> ApiToken:
    try:
        scopes = json.loads(row["scopes"] or "[]")
    except ValueError:
        scopes = []
    return ApiToken(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        scopes=[s for s in scopes if isinstance(s, str)] if isinstance(scopes, list) else [],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        origin=row["origin"],
    )


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")


# Every write to an account moves ``updated_at`` forward by at least one
# second, so the known-device cookies keyed on it (``app/auth/device_cookie.py``)
# are invalidated even by a second write in the same second.
_BUMP_UPDATED_AT = "updated_at = MAX(?, updated_at + 1)"


# Removes the TOTP second factor (secret, enable time, replay counter).
_CLEAR_TOTP = ("totp_secret = NULL", "totp_enabled_at = NULL", "totp_last_counter = NULL")


# Only a local account outside ``kiosk`` with a password can be asked to
# change it; anything else would be stuck behind the gate's forced change.
_CAN_CHANGE_PASSWORD = "password_hash IS NOT NULL AND role != 'kiosk' AND provider = 'local'"


async def _enforce_must_change(conn: aiosqlite.Connection, user_id: int, requested: bool) -> None:
    """Refuse an explicit ``must_change_password`` the account cannot honour,
    and drop a stale one (a role or password change made it inapplicable)."""
    if requested:
        async with conn.execute(
            f"SELECT 1 FROM users WHERE id = ? AND {_CAN_CHANGE_PASSWORD}", (user_id,)
        ) as cursor:
            if await cursor.fetchone() is None:
                raise MustChangeNotApplicableError("this account has no password to change")
    await conn.execute(
        "UPDATE users SET must_change_password = 0"
        f" WHERE id = ? AND must_change_password = 1 AND NOT ({_CAN_CHANGE_PASSWORD})",
        (user_id,),
    )


async def _count_enabled_admins(conn: aiosqlite.Connection) -> int:
    async with conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0"
    ) as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _fetch_user(conn: aiosqlite.Connection, user_id: int) -> User | None:
    async with conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
    return _row_to_user(row) if row else None


class UserRepository:
    @staticmethod
    async def create(
        username: str,
        role: Role,
        *,
        display_name: str = "",
        password_hash: str | None = None,
        provider: Provider = "local",
        external_id: str | None = None,
        must_change_password: bool = False,
        grant_ingress_admin: bool = False,
        now: int | None = None,
    ) -> User:
        """Insert a user. ``password_hash`` is None for ingress and kiosk identities.

        ``grant_ingress_admin`` sets ``auth_settings.ingress_admin_granted`` in the
        same transaction as the insert (the first Home Assistant admin), so a crash
        cannot leave an ingress admin without the flag (a second one would follow).
        """
        _check_role(role)
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider: {provider}")
        name = normalize_username(username)
        ts = _now(now)
        try:
            async with db.tx() as conn:
                async with conn.execute(
                    """
                    INSERT INTO users (username, username_key, display_name, password_hash,
                        role, provider, external_id, must_change_password, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        username_key(name),
                        display_name.strip(),
                        password_hash,
                        role,
                        provider,
                        external_id,
                        int(must_change_password),
                        ts,
                        ts,
                    ),
                ) as cursor:
                    user_id = cursor.lastrowid
                await _enforce_must_change(conn, int(user_id or 0), must_change_password)
                if grant_ingress_admin:
                    async with conn.execute(
                        "UPDATE auth_settings SET ingress_admin_granted = 1 WHERE id = 1"
                    ) as cursor:
                        if cursor.rowcount != 1:
                            raise AuthSettingsMissingError("auth_settings row missing")
                user = await _fetch_user(conn, int(user_id or 0))
        except sqlite3.IntegrityError as exc:
            if "username_key" in str(exc):
                raise UsernameTakenError("username already taken") from None
            raise
        assert user is not None
        return user

    @staticmethod
    async def get(user_id: int) -> User | None:
        async with db.readonly() as conn:
            return await _fetch_user(conn, user_id)

    @staticmethod
    async def get_by_username(username: str) -> User | None:
        try:
            key = username_key(username)
        except InvalidUsernameError:
            return None
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM users WHERE username_key = ?", (key,)) as cur:
                row = await cur.fetchone()
        return _row_to_user(row) if row else None

    @staticmethod
    async def get_by_external_id(provider: Provider, external_id: str) -> User | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM users WHERE provider = ? AND external_id = ?",
                (provider, external_id),
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_user(row) if row else None

    @staticmethod
    async def get_credentials(username: str) -> UserCredentials | None:
        """The user and its stored hash, looked up case-insensitively."""
        try:
            key = username_key(username)
        except InvalidUsernameError:
            return None
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM users WHERE username_key = ?", (key,)) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        stored = row["password_hash"]
        return UserCredentials(
            user=_row_to_user(row),
            password_hash=SecretStr(stored) if stored is not None else None,
        )

    @staticmethod
    async def list_all() -> list[User]:
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM users ORDER BY id") as cursor:
                rows = await cursor.fetchall()
        return [_row_to_user(row) for row in rows]

    @staticmethod
    async def any_exist() -> bool:
        """Whether setup is done: at least one account other than a ``kiosk`` one.

        The kiosk image creates its ``kiosk`` account before any administrator
        exists (``app/services/kiosk.py``); it has no password and must not
        close the first-run setup.
        """
        async with db.readonly() as conn:
            async with conn.execute("SELECT 1 FROM users WHERE role != 'kiosk' LIMIT 1") as cursor:
                return await cursor.fetchone() is not None

    @staticmethod
    async def count_enabled_admins() -> int:
        async with db.readonly() as conn:
            return await _count_enabled_admins(conn)

    @staticmethod
    async def update(
        user_id: int,
        *,
        display_name: str | None = None,
        role: Role | None = None,
        disabled: bool | None = None,
        must_change_password: bool | None = None,
        clear_password: bool = False,
        keep_an_admin: bool = False,
        now: int | None = None,
    ) -> User | None:
        """Change profile fields. Revoking sessions on a role change or a
        disable is the caller's decision (``app.auth.sessions.sign_out_user``,
        which also closes their WebSockets).

        ``clear_password`` removes the password (a ``kiosk`` has none).
        ``keep_an_admin`` refuses, in the same transaction, a change that
        leaves no enabled administrator: ``LastAdminError``, nothing written."""
        updates: list[str] = []
        params: list[Any] = []
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name.strip())
        if role is not None:
            _check_role(role)
            updates.append("role = ?")
            params.append(role)
        if disabled is not None:
            updates.append("disabled = ?")
            params.append(int(disabled))
        if must_change_password is not None:
            updates.append("must_change_password = ?")
            params.append(int(must_change_password))
        if clear_password:
            # The second factor goes with the password (a ``kiosk`` has neither).
            updates.append("password_hash = NULL")
            updates.extend(_CLEAR_TOTP)
        async with db.tx() as conn:
            if clear_password:
                await conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
            if updates:
                updates.append(_BUMP_UPDATED_AT)
                params.extend([_now(now), user_id])
                await conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
                await _enforce_must_change(conn, user_id, bool(must_change_password))
                if keep_an_admin and await _count_enabled_admins(conn) == 0:
                    raise LastAdminError("the last enabled administrator must stay")
            return await _fetch_user(conn, user_id)

    @staticmethod
    async def set_password_hash(
        user_id: int,
        password_hash: str | None,
        *,
        must_change_password: bool = False,
        now: int | None = None,
    ) -> bool:
        """Store a new hash (or None to remove the password). True if the user exists.
        ``must_change_password`` on an account that cannot honour it raises
        ``MustChangeNotApplicableError`` (nothing written)."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = ?, "
                f"{_BUMP_UPDATED_AT} WHERE id = ?",
                (password_hash, int(must_change_password), _now(now), user_id),
            ) as cursor:
                found = cursor.rowcount > 0
            if found:
                await _enforce_must_change(conn, user_id, must_change_password)
            return found

    @staticmethod
    async def delete(user_id: int, *, keep_an_admin: bool = False) -> bool:
        """Delete a user; its sessions and push devices go with it (foreign key cascade).
        ``keep_an_admin``: as for ``update()``."""
        async with db.tx() as conn:
            # Explicit, so the sessions and push devices go even on a connection
            # without PRAGMA foreign_keys (migration tests, scripts).
            await conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM user_notification_rules WHERE user_id = ?", (user_id,))
            await conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
            async with conn.execute("DELETE FROM users WHERE id = ?", (user_id,)) as cursor:
                deleted = cursor.rowcount > 0
            if deleted and keep_an_admin and await _count_enabled_admins(conn) == 0:
                raise LastAdminError("the last enabled administrator must stay")
        from app.repository import user_notification_rules

        user_notification_rules.invalidate_cache()
        return deleted


class TotpState(BaseModel):
    """The TOTP material of one account, for ``app/auth/totp.py`` only. The
    secret never reaches a general model, a log or an error message."""

    model_config = ConfigDict(hide_input_in_errors=True)

    secret: SecretStr | None = None
    enabled_at: int | None = None
    last_counter: int | None = None


class TotpRepository:
    """``users.totp_*`` and ``recovery_codes`` (migration 109). Rules:
    ``docs/dev/auth.md``, "Second factor". Every write that turns the factor on
    or off moves ``updated_at`` (known-device cookies, pending logins)."""

    @staticmethod
    async def get_state(user_id: int) -> TotpState | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT totp_secret, totp_enabled_at, totp_last_counter FROM users WHERE id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return TotpState(
            secret=SecretStr(row[0]) if row[0] is not None else None,
            enabled_at=row[1],
            last_counter=row[2],
        )

    @staticmethod
    async def set_pending_secret(user_id: int, secret: str) -> bool:
        """Store a secret awaiting its first code. Never replaces an enabled
        factor: False when the account has one (or does not exist). Sign-in
        does not change until ``enable()``, so ``updated_at`` stays."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE users SET totp_secret = ?, totp_last_counter = NULL"
                " WHERE id = ? AND totp_enabled_at IS NULL",
                (secret, user_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def enable(
        user_id: int, secret: str, counter: int, code_hashes: list[str], *, now: int | None = None
    ) -> bool:
        """Turn the pending factor on with its first accepted time step, and store
        the recovery codes. False (nothing written) when the pending secret is no
        longer ``secret`` (enrolled again meanwhile) or a factor is already on."""
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE users SET totp_enabled_at = ?, totp_last_counter = ?, "
                f"{_BUMP_UPDATED_AT} WHERE id = ? AND totp_enabled_at IS NULL AND totp_secret = ?",
                (ts, counter, ts, user_id, secret),
            ) as cursor:
                if cursor.rowcount == 0:
                    return False
            await _replace_codes(conn, user_id, code_hashes, ts)
            return True

    @staticmethod
    async def claim_counter(user_id: int, counter: int) -> bool:
        """Record an accepted time step. False when this step or a later one was
        already used (a replay), or the factor is off: one statement, so two
        concurrent requests with the same code cannot both pass."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE users SET totp_last_counter = ? WHERE id = ?"
                " AND totp_enabled_at IS NOT NULL"
                " AND (totp_last_counter IS NULL OR totp_last_counter < ?)",
                (counter, user_id, counter),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def unused_codes(user_id: int) -> list[tuple[int, str]]:
        """``(id, code_hash)`` of the account's unused recovery codes."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT id, code_hash FROM recovery_codes"
                " WHERE user_id = ? AND used_at IS NULL ORDER BY id",
                (user_id,),
            ) as cursor:
                return [(int(row[0]), str(row[1])) for row in await cursor.fetchall()]

    @staticmethod
    async def use_code(user_id: int, code_id: int, *, now: int | None = None) -> bool:
        """Spend one recovery code. False when it was spent meanwhile."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE recovery_codes SET used_at = ?"
                " WHERE id = ? AND user_id = ? AND used_at IS NULL",
                (_now(now), code_id, user_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def codes_left(user_id: int) -> int:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    async def replace_codes(
        user_id: int, code_hashes: list[str], *, now: int | None = None
    ) -> bool:
        """New recovery codes for an account whose factor is on; the old ones stop working."""
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT 1 FROM users WHERE id = ? AND totp_enabled_at IS NOT NULL", (user_id,)
            ) as cursor:
                if await cursor.fetchone() is None:
                    return False
            await _replace_codes(conn, user_id, code_hashes, ts)
            return True

    @staticmethod
    async def disable(user_id: int, *, now: int | None = None) -> bool:
        """Remove the factor (enabled or pending) and its recovery codes. True
        when there was something to remove."""
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM recovery_codes WHERE user_id = ?", (user_id,)
            ) as cursor:
                removed_codes = cursor.rowcount > 0
            async with conn.execute(
                f"UPDATE users SET {', '.join(_CLEAR_TOTP)}, {_BUMP_UPDATED_AT}"
                " WHERE id = ? AND (totp_secret IS NOT NULL OR totp_enabled_at IS NOT NULL)",
                (_now(now), user_id),
            ) as cursor:
                return cursor.rowcount > 0 or removed_codes


async def _replace_codes(
    conn: aiosqlite.Connection, user_id: int, code_hashes: list[str], ts: int
) -> None:
    await conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    await conn.executemany(
        "INSERT INTO recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, ?)",
        [(user_id, code_hash, ts) for code_hash in code_hashes],
    )


MAX_PASSKEYS_PER_USER = 20
_PASSKEY_COLUMNS = (
    "id, user_id, credential_id, public_key, sign_count, transports, aaguid, name,"
    " backup_eligible, backed_up, created_at, last_used_at"
)


class StoredPasskey(BaseModel):
    """One WebAuthn credential (migration 110). A public key and a counter:
    nothing here signs anyone in on its own."""

    id: int
    user_id: int
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list[str]
    aaguid: str
    name: str
    backup_eligible: bool
    backed_up: bool
    created_at: int
    last_used_at: int | None = None


def _row_to_passkey(row: Any) -> StoredPasskey:
    return StoredPasskey(
        id=int(row[0]),
        user_id=int(row[1]),
        credential_id=bytes(row[2]),
        public_key=bytes(row[3]),
        sign_count=int(row[4]),
        transports=[str(item) for item in _json_list(row[5])],
        aaguid=str(row[6]),
        name=str(row[7]),
        backup_eligible=bool(row[8]),
        backed_up=bool(row[9]),
        created_at=int(row[10]),
        last_used_at=row[11],
    )


class PasskeyLimitError(ValueError):
    """The account already has ``MAX_PASSKEYS_PER_USER`` passkeys."""


class PasskeyRepository:
    """``webauthn_credentials`` (migration 110). Rules: ``app/auth/webauthn.py``,
    ``docs/dev/auth.md``, "Passkeys". A passkey never changes how the password
    signs in, so no write here moves ``users.updated_at``."""

    @staticmethod
    async def list_for_user(user_id: int) -> list[StoredPasskey]:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {_PASSKEY_COLUMNS} FROM webauthn_credentials"
                " WHERE user_id = ? ORDER BY id",
                (user_id,),
            ) as cursor:
                return [_row_to_passkey(row) for row in await cursor.fetchall()]

    @staticmethod
    async def get_by_credential_id(credential_id: bytes) -> StoredPasskey | None:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {_PASSKEY_COLUMNS} FROM webauthn_credentials WHERE credential_id = ?",
                (credential_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_passkey(row) if row else None

    @staticmethod
    async def count_for_user(user_id: int) -> int:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM webauthn_credentials WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    async def add(
        user_id: int,
        *,
        credential_id: bytes,
        public_key: bytes,
        sign_count: int,
        transports: list[str],
        aaguid: str,
        name: str,
        backup_eligible: bool,
        backed_up: bool,
        now: int | None = None,
    ) -> StoredPasskey | None:
        """Store a verified credential. None when its id is already stored (for
        any account); ``PasskeyLimitError`` past ``MAX_PASSKEYS_PER_USER``."""
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM webauthn_credentials WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row and int(row[0]) >= MAX_PASSKEYS_PER_USER:
                raise PasskeyLimitError(user_id)
            try:
                async with conn.execute(
                    "INSERT INTO webauthn_credentials (user_id, credential_id, public_key,"
                    " sign_count, transports, aaguid, name, backup_eligible, backed_up,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        credential_id,
                        public_key,
                        sign_count,
                        json.dumps(transports),
                        aaguid,
                        name,
                        int(backup_eligible),
                        int(backed_up),
                        ts,
                    ),
                ) as cursor:
                    passkey_id = cursor.lastrowid
            except sqlite3.IntegrityError:
                return None
            async with conn.execute(
                f"SELECT {_PASSKEY_COLUMNS} FROM webauthn_credentials WHERE id = ?",
                (passkey_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_passkey(row) if row else None

    @staticmethod
    async def record_use(
        passkey_id: int,
        previous_sign_count: int,
        sign_count: int,
        backed_up: bool,
        *,
        now: int | None = None,
    ) -> bool:
        """Store the counter of a verified assertion. False when the stored
        counter is no longer ``previous_sign_count`` (a concurrent use of the
        same credential) or the row is gone: the caller refuses the sign-in."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE webauthn_credentials SET sign_count = ?, backed_up = ?, last_used_at = ?"
                " WHERE id = ? AND sign_count = ?",
                (sign_count, int(backed_up), _now(now), passkey_id, previous_sign_count),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def delete(user_id: int, passkey_id: int) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM webauthn_credentials WHERE id = ? AND user_id = ?",
                (passkey_id, user_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def delete_all_for_user(user_id: int) -> int:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM webauthn_credentials WHERE user_id = ?", (user_id,)
            ) as cursor:
                return cursor.rowcount


class AuthSessionRepository:
    @staticmethod
    async def create(
        user_id: int,
        token_hash: str,
        *,
        lifetime_s: int,
        ip: str | None = None,
        user_agent: str | None = None,
        via: str | None = None,
        remember: bool = True,
        now: int | None = None,
    ) -> AuthSession:
        """Store a session under the hash of its secret (``app/auth/tokens.py``).

        Beyond ``MAX_SESSIONS_PER_USER`` active sessions, the oldest are revoked.
        """
        session, _ = await AuthSessionRepository.create_capped(
            user_id,
            token_hash,
            lifetime_s=lifetime_s,
            ip=ip,
            user_agent=user_agent,
            via=via,
            remember=remember,
            now=now,
        )
        return session

    @staticmethod
    async def create_capped(
        user_id: int,
        token_hash: str,
        *,
        lifetime_s: int,
        ip: str | None = None,
        user_agent: str | None = None,
        via: str | None = None,
        remember: bool = True,
        now: int | None = None,
    ) -> tuple[AuthSession, list[str]]:
        """``create()``, plus the ids of the sessions the cap revoked (oldest
        first), so the caller can close their sockets."""
        ts = _now(now)
        session_id = uuid.uuid4().hex
        if user_agent is not None:
            user_agent = user_agent[:MAX_USER_AGENT_LENGTH]
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at,
                    expires_at, ip, user_agent, via, remember)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    token_hash,
                    ts,
                    ts,
                    ts + lifetime_s,
                    ip,
                    user_agent,
                    via,
                    int(remember),
                ),
            )
            async with conn.execute(
                """
                SELECT id FROM sessions
                WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ? AND id != ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT -1 OFFSET ?
                """,
                # The new session never evicts itself (a clock that went back
                # would otherwise sort it last); it counts as one of the cap.
                (user_id, ts, session_id, MAX_SESSIONS_PER_USER - 1),
            ) as cursor:
                evicted = [row["id"] for row in await cursor.fetchall()]
            evicted.reverse()
            if evicted:
                await conn.executemany(
                    "UPDATE sessions SET revoked_at = ? WHERE id = ?",
                    [(ts, evicted_id) for evicted_id in evicted],
                )
            async with conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
                row = await cur.fetchone()
        return _row_to_session(row), evicted

    @staticmethod
    async def get_active(token_hash: str, *, now: int | None = None) -> AuthSession | None:
        """The live session (not revoked, not expired) stored under ``token_hash``."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ? AND revoked_at IS NULL"
                " AND expires_at > ?",
                (token_hash, _now(now)),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None or not hmac.compare_digest(row["token_hash"], token_hash):
            return None
        return _row_to_session(row)

    @staticmethod
    async def remember_of(session_id: str) -> bool:
        """The "remember me" flag of a session (True when the row is gone)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT remember FROM sessions WHERE id = ?", (session_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return True if row is None else bool(row["remember"])

    @staticmethod
    async def touch(
        session_id: str, *, now: int | None = None, expires_at: int | None = None
    ) -> bool:
        """Record activity, at most once per ``LAST_SEEN_WRITE_INTERVAL_S``.

        ``expires_at`` slides the expiry in the same write. A revoked or already
        expired session is never written, so it can never come back. True when
        written.
        """
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = COALESCE(?, expires_at)"
                " WHERE id = ? AND revoked_at IS NULL AND expires_at > ? AND last_seen_at <= ?",
                (ts, expires_at, session_id, ts, ts - LAST_SEEN_WRITE_INTERVAL_S),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def list_for_user(user_id: int, *, now: int | None = None) -> list[AuthSession]:
        """Active sessions of a user, newest first."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM sessions WHERE user_id = ? AND revoked_at IS NULL"
                " AND expires_at > ? ORDER BY created_at DESC, rowid DESC",
                (user_id, _now(now)),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_session(row) for row in rows]

    @staticmethod
    async def revoke(session_id: str, *, now: int | None = None) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_now(now), session_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def revoke_all_for_user(
        user_id: int, *, except_session_id: str | None = None, now: int | None = None
    ) -> list[str]:
        """Revoke every live session of a user but ``except_session_id``.
        Returns the ids it revoked."""
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT id FROM sessions WHERE user_id = ? AND revoked_at IS NULL AND id IS NOT ?",
                (user_id, except_session_id),
            ) as cursor:
                revoked = [row["id"] for row in await cursor.fetchall()]
            if revoked:
                await conn.executemany(
                    "UPDATE sessions SET revoked_at = ? WHERE id = ?",
                    [(_now(now), session_id) for session_id in revoked],
                )
        return revoked

    @staticmethod
    async def filter_live(session_ids: list[str], *, now: int | None = None) -> set[str]:
        """Those of ``session_ids`` still usable: not revoked, not expired, and
        their user exists and is enabled (the rule of ``resolve_session``)."""
        if not session_ids:
            return set()
        placeholders = ", ".join("?" for _ in session_ids)
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT s.id FROM sessions s JOIN users u ON u.id = s.user_id"
                f" WHERE s.id IN ({placeholders}) AND s.revoked_at IS NULL"
                f" AND s.expires_at > ? AND u.disabled = 0",
                (*session_ids, _now(now)),
            ) as cursor:
                return {row["id"] for row in await cursor.fetchall()}

    @staticmethod
    async def purge(*, now: int | None = None) -> int:
        """Delete expired and revoked sessions. Returns how many."""
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM sessions WHERE revoked_at IS NOT NULL OR expires_at <= ?",
                (_now(now),),
            ) as cursor:
                return cursor.rowcount


_LIVE_TOKEN = "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"


async def insert_token(
    conn: aiosqlite.Connection,
    user_id: int,
    name: str,
    token_hash: str,
    scopes: list[str],
    *,
    expires_at: int | None,
    origin: TokenOrigin,
    now: int,
) -> ApiToken:
    """Insert a token inside the caller's transaction (``ApiTokenRepository.create``,
    the pairing redemption). Raises ``TokenLimitError`` past ``MAX_TOKENS_PER_USER``."""
    token_id = uuid.uuid4().hex
    async with conn.execute(
        f"SELECT COUNT(*) FROM api_tokens WHERE user_id = ? AND {_LIVE_TOKEN}",
        (user_id, now),
    ) as cursor:
        row = await cursor.fetchone()
    if row is not None and int(row[0]) >= MAX_TOKENS_PER_USER:
        raise TokenLimitError(str(MAX_TOKENS_PER_USER))
    await conn.execute(
        """
        INSERT INTO api_tokens (id, user_id, name, token_hash, scopes, created_at,
            expires_at, origin)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (token_id, user_id, name, token_hash, json.dumps(scopes), now, expires_at, origin),
    )
    async with conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)) as cur:
        created = await cur.fetchone()
    return _row_to_token(created)


class ApiTokenRepository:
    """``api_tokens``: rows keyed by the SHA-256 of their secret (``app/auth/tokens.py``)."""

    @staticmethod
    async def create(
        user_id: int,
        name: str,
        token_hash: str,
        scopes: list[str],
        *,
        expires_at: int | None = None,
        origin: TokenOrigin = "ui",
        now: int | None = None,
    ) -> ApiToken:
        """Store a token. Raises ``TokenLimitError`` past ``MAX_TOKENS_PER_USER``."""
        async with db.tx() as conn:
            return await insert_token(
                conn,
                user_id,
                name,
                token_hash,
                scopes,
                expires_at=expires_at,
                origin=origin,
                now=_now(now),
            )

    @staticmethod
    async def get_by_hash(token_hash: str) -> ApiToken | None:
        """The row stored under ``token_hash``, live or not: the caller decides
        (a revoked or expired token is audited, never accepted)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None or not hmac.compare_digest(row["token_hash"], token_hash):
            return None
        return _row_to_token(row)

    @staticmethod
    async def get(token_id: str) -> ApiToken | None:
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)) as cur:
                row = await cur.fetchone()
        return _row_to_token(row) if row is not None else None

    @staticmethod
    async def list_live(user_id: int | None = None, *, now: int | None = None) -> list[ApiToken]:
        """Live tokens (not revoked, not expired), newest first: one user's, or
        everyone's when ``user_id`` is None."""
        query = f"SELECT * FROM api_tokens WHERE {_LIVE_TOKEN}"
        params: list[Any] = [_now(now)]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        async with db.readonly() as conn:
            async with conn.execute(
                query + " ORDER BY created_at DESC, rowid DESC", params
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_token(row) for row in rows]

    @staticmethod
    async def touch(token_id: str, *, now: int | None = None) -> bool:
        """Record a use, at most once per ``LAST_SEEN_WRITE_INTERVAL_S``. A
        revoked token is never written. True when written."""
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE api_tokens SET last_used_at = ? WHERE id = ? AND revoked_at IS NULL"
                " AND (last_used_at IS NULL OR last_used_at <= ?)",
                (ts, token_id, ts - LAST_SEEN_WRITE_INTERVAL_S),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def revoke(token_id: str, *, now: int | None = None) -> bool:
        """Revoke a token. True when it was not revoked yet."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_now(now), token_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def filter_live(token_ids: list[str], *, now: int | None = None) -> set[str]:
        """Those of ``token_ids`` still usable: live, and their owner exists and
        is enabled (the rule of the bearer provider)."""
        if not token_ids:
            return set()
        placeholders = ", ".join("?" for _ in token_ids)
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT t.id FROM api_tokens t JOIN users u ON u.id = t.user_id"
                f" WHERE t.id IN ({placeholders}) AND t.revoked_at IS NULL"
                f" AND (t.expires_at IS NULL OR t.expires_at > ?) AND u.disabled = 0",
                (*token_ids, _now(now)),
            ) as cursor:
                return {row["id"] for row in await cursor.fetchall()}


class PairingLimitError(ValueError):
    """The user already holds ``MAX_PENDING_PAIRINGS_PER_USER`` pending codes."""


MAX_PENDING_PAIRINGS_PER_USER = 10
_PENDING_PAIRING = "used_at IS NULL AND revoked_at IS NULL AND expires_at > ?"


def _row_to_pairing(row: Any) -> PairingCode:
    extensions = None
    if row["extensions"]:
        try:
            parsed = json.loads(row["extensions"])
        except ValueError:
            parsed = None
        extensions = parsed if isinstance(parsed, dict) else None
    return PairingCode(
        id=row["id"],
        created_by=row["created_by"],
        label=row["label"],
        scopes=[s for s in _json_list(row["scopes"]) if isinstance(s, str)],
        urls=[u for u in _json_list(row["urls"]) if isinstance(u, str)],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        used_at=row["used_at"],
        revoked_at=row["revoked_at"],
        token_id=row["token_id"],
        device_name=row["device_name"],
        extensions=extensions,
    )


class PairingRedemption(BaseModel):
    """What ``PairingCodeRepository.redeem`` did. ``reason`` is None on success
    (``token`` and ``owner`` set), else why the code gave nothing: ``unknown``,
    ``used``, ``revoked``, ``expired``, or a refusal of ``authorize`` /
    ``token_limit`` (the code is spent then)."""

    reason: str | None = None
    code: PairingCode | None = None
    token: ApiToken | None = None
    owner: User | None = None


class PairingCodeRepository:
    """``pairing_codes``: rows keyed by the SHA-256 of their code (``app/auth/pairing.py``)."""

    @staticmethod
    async def create(
        created_by: int,
        label: str,
        code_hash: str,
        scopes: list[str],
        urls: list[str],
        *,
        expires_at: int,
        now: int | None = None,
    ) -> PairingCode:
        """Store a pending code. Raises ``PairingLimitError`` past
        ``MAX_PENDING_PAIRINGS_PER_USER``."""
        ts = _now(now)
        pairing_id = uuid.uuid4().hex
        async with db.tx() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) FROM pairing_codes WHERE created_by = ? AND {_PENDING_PAIRING}",
                (created_by, ts),
            ) as cursor:
                row = await cursor.fetchone()
            if row is not None and int(row[0]) >= MAX_PENDING_PAIRINGS_PER_USER:
                raise PairingLimitError(str(MAX_PENDING_PAIRINGS_PER_USER))
            await conn.execute(
                """
                INSERT INTO pairing_codes (id, code_hash, created_by, label, scopes, urls,
                    created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pairing_id,
                    code_hash,
                    created_by,
                    label,
                    json.dumps(scopes),
                    json.dumps(urls),
                    ts,
                    expires_at,
                ),
            )
            async with conn.execute(
                "SELECT * FROM pairing_codes WHERE id = ?", (pairing_id,)
            ) as cursor:
                created = await cursor.fetchone()
        return _row_to_pairing(created)

    @staticmethod
    async def get(pairing_id: str) -> PairingCode | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM pairing_codes WHERE id = ?", (pairing_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_pairing(row) if row is not None else None

    @staticmethod
    async def list_pending(
        created_by: int | None = None, *, now: int | None = None
    ) -> list[PairingCode]:
        """Codes still redeemable, newest first: one user's, or everyone's."""
        query = f"SELECT * FROM pairing_codes WHERE {_PENDING_PAIRING}"
        params: list[Any] = [_now(now)]
        if created_by is not None:
            query += " AND created_by = ?"
            params.append(created_by)
        async with db.readonly() as conn:
            async with conn.execute(
                query + " ORDER BY created_at DESC, rowid DESC", params
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_pairing(row) for row in rows]

    @staticmethod
    async def revoke(pairing_id: str, *, now: int | None = None) -> bool:
        """Revoke a pending code. True when it was still pending."""
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                f"UPDATE pairing_codes SET revoked_at = ? WHERE id = ? AND {_PENDING_PAIRING}",
                (ts, pairing_id, ts),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def redeem(
        code_hash: str,
        *,
        device_name: str,
        extensions: dict[str, Any] | None,
        token_hash: str,
        authorize: Callable[[User | None, list[str]], str | None],
        now: int | None = None,
    ) -> PairingRedemption:
        """Spend the code stored under ``code_hash`` and issue its device token,
        in one transaction (two concurrent redemptions cannot both win).

        Once the code is found pending it is spent, whatever follows: the
        owner check (``authorize(owner, scopes)`` returns a refusal reason or
        None) and the token cap can still refuse, and the code is gone then.
        """
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT * FROM pairing_codes WHERE code_hash = ?", (code_hash,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or not hmac.compare_digest(row["code_hash"], code_hash):
                return PairingRedemption(reason="unknown")
            code = _row_to_pairing(row)
            if code.used_at is not None:
                return PairingRedemption(reason="used", code=code)
            if code.revoked_at is not None:
                return PairingRedemption(reason="revoked", code=code)
            if code.expires_at <= ts:
                return PairingRedemption(reason="expired", code=code)
            async with conn.execute(
                "UPDATE pairing_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
                (ts, code.id),
            ) as cursor:
                if cursor.rowcount != 1:
                    return PairingRedemption(reason="used", code=code)
            code.used_at = ts
            owner = await _fetch_user(conn, code.created_by)
            refusal = authorize(owner, code.scopes)
            if refusal is not None or owner is None:
                return PairingRedemption(reason=refusal or "owner_unavailable", code=code)
            try:
                token = await insert_token(
                    conn,
                    owner.id,
                    device_name,
                    token_hash,
                    sorted(set(code.scopes)),
                    expires_at=None,
                    origin="pairing",
                    now=ts,
                )
            except TokenLimitError:
                return PairingRedemption(reason="token_limit", code=code, owner=owner)
            await conn.execute(
                "UPDATE pairing_codes SET token_id = ?, device_name = ?, extensions = ?"
                " WHERE id = ?",
                (
                    token.id,
                    device_name,
                    json.dumps(extensions, separators=(",", ":"))
                    if extensions is not None
                    else None,
                    code.id,
                ),
            )
            code.token_id, code.device_name, code.extensions = token.id, device_name, extensions
        return PairingRedemption(code=code, token=token, owner=owner)

    @staticmethod
    async def purge(*, now: int | None = None) -> int:
        """Delete codes that hold no device token and can no longer give one
        (expired, revoked, or spent by a refused redemption; a deleted token
        clears ``token_id``). A redeemed row stays with its token (its
        ``extensions``)."""
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM pairing_codes WHERE token_id IS NULL AND (used_at IS NOT NULL"
                " OR revoked_at IS NOT NULL OR expires_at <= ?)",
                (_now(now),),
            ) as cursor:
                return cursor.rowcount


def _is_secret_key(key: str) -> bool:
    composed = unicodedata.normalize("NFKC", key)
    folded = composed.casefold()
    if not _ALLOWED_KEY.fullmatch(folded):
        return True
    # Split on the composed form, before folding, so camelCase still shows.
    if {word.casefold() for word in _KEY_WORDS.findall(composed)} & _SECRET_WORDS:
        return True
    joined = folded.replace("_", "").replace("-", "")
    return any(fragment in joined for fragment in _SECRET_FRAGMENTS)


def _bounded(field: str, value: str | None) -> str | None:
    return None if value is None else value[: _AUDIT_TEXT_LIMITS[field]]


def _check_details(value: Any, path: str = "details") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: keys must be strings")
            if _is_secret_key(key):
                raise ValueError(f"{path}.{key}: looks like a secret, not stored")
            _check_details(item, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _check_details(item, f"{path}[{index}]")


class AuthAuditRepository:
    @staticmethod
    async def add(
        action: str,
        outcome: str,
        *,
        actor_user_id: int | None = None,
        actor_token_id: str | None = None,
        target: str | None = None,
        ip: str | None = None,
        via: str | None = None,
        details: Mapping[str, Any] | None = None,
        ts: int | None = None,
    ) -> int:
        """Append an audit row. ``details`` must hold no secret: a key such as
        ``password``, ``apiKey``, ``blePin`` or ``setup_code``, or one with
        characters outside ``[a-z0-9_-]`` once folded, raises ``ValueError``.

        Text fields are truncated to ``_AUDIT_TEXT_LIMITS``; ``details`` over
        ``MAX_AUDIT_DETAILS_BYTES`` serialised are stored as
        ``{"truncated": true, "bytes": <size>}``."""
        payload = dict(details or {})
        _check_details(payload)
        serialised = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        size = len(serialised.encode("utf-8"))
        if size > MAX_AUDIT_DETAILS_BYTES:
            serialised = json.dumps({"bytes": size, "truncated": True}, separators=(",", ":"))
        async with db.tx() as conn:
            async with conn.execute(
                """
                INSERT INTO auth_audit (ts, actor_user_id, actor_token_id, action, target,
                    ip, via, outcome, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(ts),
                    actor_user_id,
                    _bounded("actor_token_id", actor_token_id),
                    _bounded("action", action),
                    _bounded("target", target),
                    _bounded("ip", ip),
                    _bounded("via", via),
                    _bounded("outcome", outcome),
                    serialised,
                ),
            ) as cursor:
                return int(cursor.lastrowid or 0)

    @staticmethod
    async def list_recent(
        *, limit: int = 100, before_id: int | None = None
    ) -> list[AuthAuditEntry]:
        """Newest first; ``before_id`` pages backwards."""
        limit = max(1, min(int(limit), 1000))
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM auth_audit WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                (before_id, before_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        entries = []
        for row in rows:
            try:
                details = json.loads(row["details"] or "{}")
            except ValueError:
                details = {}
            entries.append(
                AuthAuditEntry(
                    id=row["id"],
                    ts=row["ts"],
                    actor_user_id=row["actor_user_id"],
                    actor_token_id=row["actor_token_id"],
                    action=row["action"],
                    target=row["target"],
                    ip=row["ip"],
                    via=row["via"],
                    outcome=row["outcome"],
                    details=details if isinstance(details, dict) else {},
                )
            )
        return entries

    @staticmethod
    async def purge_older_than(cutoff_ts: int, *, batch_size: int = AUDIT_PURGE_BATCH) -> int:
        """Delete rows with ``ts`` before ``cutoff_ts``, ``batch_size`` rows per
        transaction, so a large retention drop never holds the write lock for
        long (a Raspberry Pi SD card). Returns how many."""
        total = 0
        while True:
            async with db.tx() as conn:
                async with conn.execute(
                    """
                    DELETE FROM auth_audit WHERE id IN (
                        SELECT id FROM auth_audit WHERE ts < ? LIMIT ?
                    )
                    """,
                    (cutoff_ts, batch_size),
                ) as cur:
                    deleted = cur.rowcount
            total += deleted
            if deleted < batch_size:
                return total
            await asyncio.sleep(0)


def _json_list(raw: str | None) -> list[Any]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


async def _fetch_settings(conn: aiosqlite.Connection) -> AuthSettings:
    async with conn.execute("SELECT * FROM auth_settings WHERE id = 1") as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise AuthSettingsMissingError("auth_settings row missing")
    rules = []
    for item in _json_list(row["trusted_networks"]):
        try:
            rules.append(TrustedNetworkRule.model_validate(item))
        except ValueError:
            continue
    return AuthSettings(
        trusted_networks=rules,
        trusted_proxies=list(
            valid_stored_proxies(
                p for p in _json_list(row["trusted_proxies"]) if isinstance(p, str)
            )
        ),
        public_urls=[u for u in _json_list(row["public_urls"]) if isinstance(u, str)],
        host_guard_mode=row["host_guard_mode"],
        ingress_default_role=row["ingress_default_role"],
        session_lifetime_days=row["session_lifetime_days"],
        audit_retention_days=row["audit_retention_days"],
        basic_imported=bool(row["basic_imported"]),
        setup_code_set=row["setup_code_hash"] is not None,
        require_2fa_for_admins=bool(row["require_2fa_for_admins"]),
    )


_DEVICE_KEY_RE = re.compile(r"[0-9a-f]{64}")


def _valid_device_key(value: Any) -> bool:
    return isinstance(value, str) and _DEVICE_KEY_RE.fullmatch(value) is not None


class AuthSettingsRepository:
    """The single ``auth_settings`` row. Kept out of ``app_settings`` so no
    auth secret reaches ``AppSettings`` or the v1 backup settings."""

    @staticmethod
    async def get() -> AuthSettings:
        async with db.readonly() as conn:
            return await _fetch_settings(conn)

    @staticmethod
    async def update(
        *,
        trusted_networks: list[TrustedNetworkRule] | None = None,
        trusted_proxies: list[str] | None = None,
        public_urls: list[str] | None = None,
        host_guard_mode: HostGuardMode | None = None,
        ingress_default_role: Role | None = None,
        session_lifetime_days: int | None = None,
        audit_retention_days: int | None = None,
        basic_imported: bool | None = None,
        require_2fa_for_admins: bool | None = None,
    ) -> AuthSettings:
        """Validate the merged result as ``AuthSettings``, then write the given fields."""
        async with db.tx() as conn:
            current = await _fetch_settings(conn)
            changes: dict[str, Any] = {
                key: value
                for key, value in {
                    "trusted_networks": trusted_networks,
                    "trusted_proxies": trusted_proxies,
                    "public_urls": public_urls,
                    "host_guard_mode": host_guard_mode,
                    "ingress_default_role": ingress_default_role,
                    "session_lifetime_days": session_lifetime_days,
                    "audit_retention_days": audit_retention_days,
                    "basic_imported": basic_imported,
                    "require_2fa_for_admins": require_2fa_for_admins,
                }.items()
                if value is not None
            }
            merged = AuthSettings.model_validate({**current.model_dump(), **changes})
            columns: dict[str, Any] = {}
            for key in changes:
                value = getattr(merged, key)
                if key == "trusted_networks":
                    columns[key] = json.dumps([rule.model_dump() for rule in value])
                elif isinstance(value, list):
                    columns[key] = json.dumps(value)
                elif isinstance(value, bool):
                    columns[key] = int(value)
                else:
                    columns[key] = value
            if columns:
                assignments = ", ".join(f"{column} = ?" for column in columns)
                async with conn.execute(
                    f"UPDATE auth_settings SET {assignments} WHERE id = 1", list(columns.values())
                ) as cursor:
                    if cursor.rowcount != 1:
                        raise AuthSettingsMissingError("auth_settings row missing")
            updated = await _fetch_settings(conn)
        # The proxy list is read on every request (app/middleware/proxy_headers.py),
        # and so are the trusted network rules.
        proxy_headers.apply(updated)
        from app.auth.providers import trusted_network
        from app.middleware import host_guard

        trusted_network.apply(updated)
        host_guard.apply(updated)
        return updated

    @staticmethod
    async def require_2fa_for_admins() -> bool:
        """``require_2fa_for_admins`` alone, for the gate (one column, no parsing)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT require_2fa_for_admins FROM auth_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise AuthSettingsMissingError("auth_settings row missing")
        return bool(row[0])

    @staticmethod
    async def get_setup_code_hash() -> str | None:
        async with db.readonly() as conn:
            async with conn.execute("SELECT setup_code_hash FROM auth_settings WHERE id = 1") as c:
                row = await c.fetchone()
        if row is None:
            raise AuthSettingsMissingError("auth_settings row missing")
        return row[0]

    @staticmethod
    async def ingress_admin_granted() -> bool:
        """Whether a Home Assistant identity was already made ``admin`` as the
        first one (migration 107). Never cleared: deleting that identity does
        not make the next one ``admin`` again."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT ingress_admin_granted FROM auth_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise AuthSettingsMissingError("auth_settings row missing")
        return bool(row[0])

    @staticmethod
    async def device_cookie_key() -> bytes:
        """The HMAC key of the known-device cookies (``app/auth/device_cookie.py``,
        migration 119), created on first use. Read per use, never cached, so a
        rotation by ``meshloom-admin`` or a restored database applies at once."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT device_cookie_key FROM auth_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            raise AuthSettingsMissingError("auth_settings row missing")
        if _valid_device_key(row[0]):
            return bytes.fromhex(row[0])
        async with db.tx() as conn:
            # Replaces only the value just read (empty, or hand-edited garbage):
            # a concurrent first use keeps the winner's key.
            async with conn.execute(
                "UPDATE auth_settings SET device_cookie_key = ?"
                " WHERE id = 1 AND device_cookie_key IS ?",
                (secrets.token_hex(32), row[0]),
            ):
                pass
            async with conn.execute(
                "SELECT device_cookie_key FROM auth_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if row is None or not _valid_device_key(row[0]):
            raise AuthSettingsMissingError("auth_settings row missing")
        return bytes.fromhex(row[0])

    @staticmethod
    async def rotate_device_cookie_key() -> None:
        """A new known-device key: every issued device cookie becomes unknown."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE auth_settings SET device_cookie_key = ? WHERE id = 1",
                (secrets.token_hex(32),),
            ) as cursor:
                if cursor.rowcount != 1:
                    raise AuthSettingsMissingError("auth_settings row missing")

    @staticmethod
    async def set_setup_code_hash(code_hash: str | None) -> None:
        """Store the hash of the installation code (``app/auth/tokens.py``), or clear it."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE auth_settings SET setup_code_hash = ? WHERE id = 1", (code_hash,)
            ) as cursor:
                if cursor.rowcount != 1:
                    raise AuthSettingsMissingError("auth_settings row missing")
