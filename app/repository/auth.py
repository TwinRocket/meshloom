"""Repository for the local accounts tables: users, sessions, auth_audit, auth_settings.

Schema and rules: ``docs/dev/auth.md``. Hashes go in and out of this module but
are never logged; the general models (``app/auth/models.py``) carry none.
"""

import hmac
import json
import re
import sqlite3
import time
import unicodedata
import uuid
from collections.abc import Mapping
from typing import Any

import aiosqlite
from pydantic import SecretStr

from app.auth.models import (
    PROVIDERS,
    ROLES,
    USERNAME_MAX_LENGTH,
    AuthAuditEntry,
    AuthSession,
    AuthSettings,
    HostGuardMode,
    Provider,
    Role,
    TrustedNetworkRule,
    User,
    UserCredentials,
)
from app.database import db

# Active sessions kept per user; creating one more revokes the oldest.
MAX_SESSIONS_PER_USER = 50

# ``touch()`` writes ``last_seen_at`` at most this often per session.
LAST_SEEN_WRITE_INTERVAL_S = 60

# Audit ``details`` are free-form JSON: a key with one of these words
# (split on ``_``, ``-``, ``.`` and camelCase) is refused rather than stored.
_SECRET_WORDS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "hash",
        "otp",
        "passphrase",
        "passwd",
        "password",
        "pin",
        "pwd",
        "secret",
        "token",
        "totp",
    }
)
_KEY_WORDS = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


class UsernameTakenError(ValueError):
    """A user with the same name (compared case-insensitively) already exists."""


class InvalidUsernameError(ValueError):
    """Empty, too long, or containing control characters."""


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
    """The uniqueness key: NFKC then Unicode case folding (``Élise`` = ``éLISE``)."""
    return unicodedata.normalize("NFKC", normalize_username(username)).casefold()


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
    )


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")


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
        now: int | None = None,
    ) -> User:
        """Insert a user. ``password_hash`` is None for ingress and kiosk identities."""
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
    async def count_enabled_admins() -> int:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0"
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    async def update(
        user_id: int,
        *,
        display_name: str | None = None,
        role: Role | None = None,
        disabled: bool | None = None,
        must_change_password: bool | None = None,
        now: int | None = None,
    ) -> User | None:
        """Change profile fields. Revoking sessions on a role change or a
        disable is the caller's decision (``AuthSessionRepository.revoke_all_for_user``)."""
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
        async with db.tx() as conn:
            if updates:
                updates.append("updated_at = ?")
                params.extend([_now(now), user_id])
                await conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            return await _fetch_user(conn, user_id)

    @staticmethod
    async def set_password_hash(
        user_id: int,
        password_hash: str | None,
        *,
        must_change_password: bool = False,
        now: int | None = None,
    ) -> bool:
        """Store a new hash (or None to remove the password). True if the user exists."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ?"
                " WHERE id = ?",
                (password_hash, int(must_change_password), _now(now), user_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def delete(user_id: int) -> bool:
        """Delete a user; its sessions go with it (foreign key cascade)."""
        async with db.tx() as conn:
            # Explicit, so the sessions go even on a connection without
            # PRAGMA foreign_keys (migration tests, scripts).
            await conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            async with conn.execute("DELETE FROM users WHERE id = ?", (user_id,)) as cursor:
                return cursor.rowcount > 0


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
        now: int | None = None,
    ) -> AuthSession:
        """Store a session under the hash of its secret (``app/auth/tokens.py``).

        Beyond ``MAX_SESSIONS_PER_USER`` active sessions, the oldest are revoked.
        """
        ts = _now(now)
        session_id = uuid.uuid4().hex
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at,
                    expires_at, ip, user_agent, via)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_id, token_hash, ts, ts, ts + lifetime_s, ip, user_agent, via),
            )
            await conn.execute(
                """
                UPDATE sessions SET revoked_at = ?
                WHERE id IN (
                    SELECT id FROM sessions
                    WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (ts, user_id, ts, MAX_SESSIONS_PER_USER),
            )
            async with conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
                row = await cur.fetchone()
        return _row_to_session(row)

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
    async def touch(
        session_id: str, *, now: int | None = None, expires_at: int | None = None
    ) -> bool:
        """Record activity, at most once per ``LAST_SEEN_WRITE_INTERVAL_S``.

        ``expires_at`` slides the expiry in the same write. True when written.
        """
        ts = _now(now)
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE sessions SET last_seen_at = ?, expires_at = COALESCE(?, expires_at)"
                " WHERE id = ? AND revoked_at IS NULL AND last_seen_at <= ?",
                (ts, expires_at, session_id, ts - LAST_SEEN_WRITE_INTERVAL_S),
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
    ) -> int:
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL"
                " AND id IS NOT ?",
                (_now(now), user_id, except_session_id),
            ) as cursor:
                return cursor.rowcount

    @staticmethod
    async def purge(*, now: int | None = None) -> int:
        """Delete expired and revoked sessions. Returns how many."""
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM sessions WHERE revoked_at IS NOT NULL OR expires_at <= ?",
                (_now(now),),
            ) as cursor:
                return cursor.rowcount


def _check_details(value: Any, path: str = "details") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path}: keys must be strings")
            if {word.lower() for word in _KEY_WORDS.findall(key)} & _SECRET_WORDS:
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
        ``password`` or ``token`` raises ``ValueError``."""
        payload = dict(details or {})
        _check_details(payload)
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
                    actor_token_id,
                    action,
                    target,
                    ip,
                    via,
                    outcome,
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
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
    async def purge_older_than(cutoff_ts: int) -> int:
        """Delete rows with ``ts`` before ``cutoff_ts``. Returns how many."""
        async with db.tx() as conn:
            async with conn.execute("DELETE FROM auth_audit WHERE ts < ?", (cutoff_ts,)) as cur:
                return cur.rowcount


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
        return AuthSettings()
    rules = []
    for item in _json_list(row["trusted_networks"]):
        try:
            rules.append(TrustedNetworkRule.model_validate(item))
        except ValueError:
            continue
    return AuthSettings(
        trusted_networks=rules,
        trusted_proxies=[p for p in _json_list(row["trusted_proxies"]) if isinstance(p, str)],
        public_urls=[u for u in _json_list(row["public_urls"]) if isinstance(u, str)],
        host_guard_mode=row["host_guard_mode"],
        ingress_default_role=row["ingress_default_role"],
        session_lifetime_days=row["session_lifetime_days"],
        audit_retention_days=row["audit_retention_days"],
        basic_imported=bool(row["basic_imported"]),
        setup_code_set=row["setup_code_hash"] is not None,
    )


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
                await conn.execute(
                    f"UPDATE auth_settings SET {assignments} WHERE id = 1", list(columns.values())
                )
            return await _fetch_settings(conn)

    @staticmethod
    async def get_setup_code_hash() -> str | None:
        async with db.readonly() as conn:
            async with conn.execute("SELECT setup_code_hash FROM auth_settings WHERE id = 1") as c:
                row = await c.fetchone()
        return row[0] if row else None

    @staticmethod
    async def set_setup_code_hash(code_hash: str | None) -> None:
        """Store the hash of the installation code (``app/auth/tokens.py``), or clear it."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE auth_settings SET setup_code_hash = ? WHERE id = 1", (code_hash,)
            )
