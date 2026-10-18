"""The local accounts in the full backup: the ``users`` section (Access T65).

Rules (``docs/dev/backup.md``, "Accounts"):

- **Export.** Every row of ``users``: username, display name, role, provider
  and external id, flags, the argon2id password hash and the TOTP material
  (secret, enable time, last counter). The section is ``sensitive``. The export
  dialog's "Include accounts" box (``BackupExportRequest.include_accounts``,
  checked by default) leaves every name of ``ACCOUNT_SECTIONS`` out when unchecked.
  Sessions, pairing codes, the audit trail, ``auth_settings`` (with the setup code
  hash) and the Remote identity are never in it (``NEVER_EXPORTED_SECTIONS``,
  ``BACKUP_COVERAGE``); keys a file adds to a user row are ignored.
- **Restore.** The file's accounts replace the instance's, ids kept, in one
  transaction. A file without the section keeps the current accounts. A section
  with no enabled administrator who can sign in here (a local account with a
  password, or a Home Assistant identity on the add-on) is refused
  (``SectionKept(["accounts_no_admin"])``) and the current accounts stay, with
  their tokens (the ``api_tokens`` section is skipped): a restore never locks
  everyone out. Rows follow the API's rules: a password hash, TOTP material or
  forced change only on a local account outside ``kiosk`` (stripped otherwise),
  a ``system`` account only as a kiosk, an argon2id hash only up to libsodium's
  MODERATE cost. Every login session is deleted and every socket of the old and
  new account ids is closed (ids may now name other people), ``updated_at``
  moves past both the file's and the replaced row's (known-device cookies are
  invalidated) and a trusted network rule is kept only when its account id
  names the same username before and after (``trusted_network_dropped:<cidr>``).
  The ``accounts`` restore hook reloads the trusted network rules.
- **Never log** a hash, a TOTP secret or a row: log lines carry counts only.
- **API tokens** (``api_tokens``, Access T27): every live token (not revoked,
  not expired) with its SHA-256 hash, scopes, expiry and origin, never a
  secret (the database has none). ``sensitive``, ``restore_with="users"`` (same
  transaction, right after the accounts, only when they were written) and in
  ``ACCOUNT_SECTIONS``, so the same box leaves it out and a file without
  accounts never restores tokens. Its restore replaces the tokens and keeps
  only the rows whose owner exists after the accounts restore and whose
  scopes that owner's role grants (``api_token_dropped:<id>`` otherwise). A
  restore of ``users`` without this section leaves no token (the accounts'
  rows cascade). Passkeys and recovery codes (T50, T52) go inside the
  ``users`` section, as a new ``version``.
"""

import json
import logging
import re
import time

import aiosqlite
import nacl.pwhash.argon2id as argon2id
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.auth.models import (
    USERNAME_MAX_LENGTH,
    AuthSettings,
    Provider,
    Role,
    TokenOrigin,
    TrustedNetworkRule,
)
from app.models import BackupSection
from app.repository.auth import normalize_username, username_key
from app.services.backup_sections import (
    RestoreSource,
    SectionKept,
    register_backup_section,
    register_on_restore,
)

logger = logging.getLogger(__name__)

USERS_SECTION = "users"
API_TOKENS_SECTION = "api_tokens"
# Sections the "Include accounts" box covers.
ACCOUNT_SECTIONS: tuple[str, ...] = (USERS_SECTION, API_TOKENS_SECTION)
MAX_BACKUP_TOKENS = 100_000

# A bound on a file, not a policy.
MAX_BACKUP_USERS = 10_000
_MAX_SECRET_LENGTH = 1024
# A restored hash must be argon2id with parameters a login can afford: up to
# libsodium's MODERATE limits (the app writes INTERACTIVE ones), so a file
# cannot make the next login for that name allocate gigabytes.
_HASH_PARAMS = re.compile(r"^\$argon2id\$v=19\$m=(\d+),t=(\d+),p=1\$[^$]+\$[^$]+$")
_MAX_HASH_MEM_KIB = argon2id.MEMLIMIT_MODERATE // 1024
_MAX_HASH_PASSES = argon2id.OPSLIMIT_MODERATE

_USER_COLUMNS = (
    "id",
    "username",
    "display_name",
    "password_hash",
    "role",
    "provider",
    "external_id",
    "disabled",
    "must_change_password",
    "totp_secret",
    "totp_enabled_at",
    "totp_last_counter",
    "created_at",
    "updated_at",
)


class BackupUser(BaseModel):
    """One account as the file holds it. Unknown keys (a ``sessions`` list a
    file might carry) are dropped; the secrets never reach an error message."""

    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    id: int = Field(ge=1)
    username: str = Field(min_length=1, max_length=USERNAME_MAX_LENGTH)
    display_name: str = Field(default="", max_length=256)
    password_hash: SecretStr | None = Field(default=None, max_length=_MAX_SECRET_LENGTH)
    role: Role
    provider: Provider = "local"
    external_id: str | None = Field(default=None, max_length=256)
    disabled: bool = False
    must_change_password: bool = False
    totp_secret: SecretStr | None = Field(default=None, max_length=_MAX_SECRET_LENGTH)
    totp_enabled_at: int | None = None
    totp_last_counter: int | None = None
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)

    @model_validator(mode="after")
    def _account_invariants(self) -> "BackupUser":
        """The rules the accounts API enforces: only a local account outside
        ``kiosk`` has a password, a TOTP secret or a forced change; a ``system``
        identity is a kiosk."""
        if self.provider == "system" and self.role != "kiosk":
            raise ValueError("a system account must be a kiosk")
        if self.provider != "local" or self.role == "kiosk":
            self.password_hash = None
            self.totp_secret = None
            self.totp_enabled_at = None
            self.totp_last_counter = None
        if self.password_hash is not None:
            match = _HASH_PARAMS.match(self.password_hash.get_secret_value())
            if match is None:
                raise ValueError("password_hash is not an argon2id hash")
            mem_kib, passes = (int(group) for group in match.groups())
            if mem_kib > _MAX_HASH_MEM_KIB or passes > _MAX_HASH_PASSES:
                raise ValueError("password_hash parameters are too costly")
        if self.password_hash is None:
            self.must_change_password = False
        return self

    def can_sign_in_here(self, *, addon: bool) -> bool:
        """An enabled admin someone can sign in as on this instance: a local
        account with a password, or a Home Assistant identity on the add-on."""
        if self.role != "admin" or self.disabled:
            return False
        if self.provider == "local":
            return self.password_hash is not None
        return self.provider == "ingress" and addon


class BackupUsersData(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    users: list[BackupUser] = Field(max_length=MAX_BACKUP_USERS)


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


async def export_users() -> BackupSection | None:
    from app.database import db

    async with db.readonly() as conn:
        async with conn.execute(
            f"SELECT {', '.join(_USER_COLUMNS)} FROM users ORDER BY id"
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return None
    users = []
    for row in rows:
        user = {column: row[column] for column in _USER_COLUMNS}
        user["disabled"] = bool(user["disabled"])
        user["must_change_password"] = bool(user["must_change_password"])
        users.append(user)
    return BackupSection(data={"users": users})


async def _username_keys(conn: aiosqlite.Connection) -> dict[int, str]:
    async with conn.execute("SELECT id, username_key FROM users") as cursor:
        return {int(row[0]): str(row[1]) for row in await cursor.fetchall()}


async def _keep_trusted_networks(
    conn: aiosqlite.Connection, before: dict[int, str], after: dict[int, str]
) -> list[str]:
    """Drop every trusted network rule whose account id now names someone else
    (or nobody). Returns the warnings, one per dropped rule."""
    from app.auth.providers import trusted_network

    async with conn.execute("SELECT trusted_networks FROM auth_settings WHERE id = 1") as cursor:
        row = await cursor.fetchone()
    if row is None:
        return []
    try:
        stored = json.loads(row[0] or "[]")
    except ValueError:
        stored = []
    kept: list[dict] = []
    warnings: list[str] = []
    for entry in stored if isinstance(stored, list) else []:
        user_id = entry.get("user_id") if isinstance(entry, dict) else None
        if isinstance(user_id, int) and user_id in before and before[user_id] == after.get(user_id):
            kept.append(entry)
        else:
            cidr = entry.get("cidr") if isinstance(entry, dict) else None
            warnings.append(
                f"trusted_network_dropped:{cidr}" if cidr else "trusted_network_dropped"
            )
    if warnings:
        await conn.execute(
            "UPDATE auth_settings SET trusted_networks = ? WHERE id = 1", (json.dumps(kept),)
        )
    # Fail closed until the hook reloads them: no rule names an account that changed.
    rules = []
    for entry in kept:
        try:
            rules.append(TrustedNetworkRule.model_validate(entry))
        except ValueError:
            continue
    trusted_network.apply(AuthSettings(trusted_networks=rules))
    return warnings


def _is_addon() -> bool:
    from app.services.install_kind import detect_install_kind

    return detect_install_kind()[0] == "addon"


# The same rule as ``BackupUser.can_sign_in_here``, on the written rows.
_USABLE_ADMIN_SQL = (
    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND ("
    "(provider = 'local' AND password_hash IS NOT NULL) OR (provider = 'ingress' AND ?))"
)


async def _updated_at_before(conn: aiosqlite.Connection) -> dict[int, int]:
    async with conn.execute("SELECT id, updated_at FROM users") as cursor:
        return {int(row[0]): int(row[1]) for row in await cursor.fetchall()}


def _close_account_sockets(user_ids: set[int]) -> None:
    """Close every socket of these accounts at once (sessions, Basic, ingress,
    trusted networks): an id kept from the file may now name another person."""
    from app.websocket import ws_manager

    closed = sum(ws_manager.close_user(user_id) for user_id in user_ids)
    if closed:
        logger.info("Backup restore: closed %d WebSocket(s) of replaced accounts", closed)


async def restore_users(section: BackupSection, conn: aiosqlite.Connection) -> list[str]:
    data = BackupUsersData.model_validate(section.data)
    addon = _is_addon()
    if not any(user.can_sign_in_here(addon=addon) for user in data.users):
        logger.warning(
            "Backup restore: no administrator in the accounts section can sign in here,"
            " kept the current accounts"
        )
        raise SectionKept(["accounts_no_admin"])
    before = await _username_keys(conn)
    updated_before = await _updated_at_before(conn)
    now = int(time.time())
    # Every session goes: an id kept from the file may now name another person.
    await conn.execute("DELETE FROM sessions")
    await conn.execute("DELETE FROM users")
    for user in data.users:
        name = normalize_username(user.username)
        await conn.execute(
            f"INSERT INTO users ({', '.join(_USER_COLUMNS)}, username_key)"
            f" VALUES ({', '.join('?' for _ in _USER_COLUMNS)}, ?)",
            (
                user.id,
                name,
                user.display_name.strip(),
                _secret(user.password_hash),
                user.role,
                user.provider,
                user.external_id,
                int(user.disabled),
                int(user.must_change_password),
                _secret(user.totp_secret),
                user.totp_enabled_at,
                user.totp_last_counter,
                user.created_at,
                # Moves every known-device cookie's generation forward.
                max(now, user.updated_at + 1, updated_before.get(user.id, -1) + 1),
                username_key(name),
            ),
        )
    async with conn.execute(_USABLE_ADMIN_SQL, (int(addon),)) as cursor:
        row = await cursor.fetchone()
    if not row or int(row[0]) < 1:
        # Unreachable after the check above; raising rolls the section back.
        raise RuntimeError("accounts restore would leave no administrator who can sign in")
    after = await _username_keys(conn)
    warnings = await _keep_trusted_networks(conn, before, after)
    # In memory only, and fail closed: a rolled-back commit just closed sockets.
    _close_account_sockets(set(before) | set(after))
    logger.info("Backup restore: %d account(s) restored", len(data.users))
    return ["accounts_restored", *warnings]


async def _reload_access_state(source: RestoreSource) -> None:
    """Rules are read again from the database on their next use."""
    from app.auth.providers import trusted_network

    trusted_network.reset()


register_backup_section(
    USERS_SECTION,
    export_users,
    restore_users,
    description={
        "en": "Accounts: usernames, roles, password hashes and two-factor secrets",
        "fr": "Comptes : noms, rôles, hachés de mots de passe et secrets de double authentification",
    },
    sensitive=True,
)
register_on_restore("accounts", _reload_access_state)


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_TOKEN_COLUMNS = (
    "id",
    "user_id",
    "name",
    "token_hash",
    "scopes",
    "created_at",
    "last_used_at",
    "expires_at",
    "origin",
)


class BackupApiToken(BaseModel):
    """One API or device token as the file holds it: a hash, never a secret."""

    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    user_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=64)
    token_hash: SecretStr
    scopes: list[str] = Field(min_length=1, max_length=5)
    created_at: int = Field(ge=0)
    last_used_at: int | None = None
    expires_at: int | None = None
    origin: TokenOrigin = "ui"

    @field_validator("token_hash")
    @classmethod
    def _sha256_hex(cls, value: SecretStr) -> SecretStr:
        if not _SHA256_HEX.fullmatch(value.get_secret_value()):
            raise ValueError("token_hash is not a SHA-256 hex digest")
        return value


class BackupApiTokensData(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="ignore")

    tokens: list[BackupApiToken] = Field(max_length=MAX_BACKUP_TOKENS)


async def export_api_tokens() -> BackupSection | None:
    from app.database import db

    now = int(time.time())
    async with db.readonly() as conn:
        async with conn.execute(
            f"SELECT {', '.join(_TOKEN_COLUMNS)} FROM api_tokens WHERE revoked_at IS NULL"
            " AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at, id",
            (now,),
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        return None
    tokens = []
    for row in rows:
        token = {column: row[column] for column in _TOKEN_COLUMNS}
        token["scopes"] = json.loads(token["scopes"] or "[]")
        tokens.append(token)
    return BackupSection(data={"tokens": tokens})


async def restore_api_tokens(section: BackupSection, conn: aiosqlite.Connection) -> list[str]:
    from app.auth.providers.token import close_token_sockets, scope_violation

    data = BackupApiTokensData.model_validate(section.data)
    async with conn.execute("SELECT id, role FROM users") as cursor:
        roles = {int(row[0]): str(row[1]) for row in await cursor.fetchall()}
    async with conn.execute("SELECT id FROM api_tokens") as cursor:
        replaced = [str(row[0]) for row in await cursor.fetchall()]
    await conn.execute("DELETE FROM api_tokens")
    warnings: list[str] = []
    restored = 0
    for token in data.tokens:
        role = roles.get(token.user_id)
        if role is None or scope_violation(role, token.scopes) is not None:  # type: ignore[arg-type]
            warnings.append(f"api_token_dropped:{token.id}")
            continue
        await conn.execute(
            f"INSERT OR IGNORE INTO api_tokens ({', '.join(_TOKEN_COLUMNS)})"
            f" VALUES ({', '.join('?' for _ in _TOKEN_COLUMNS)})",
            (
                token.id,
                token.user_id,
                token.name.strip() or "token",
                token.token_hash.get_secret_value(),
                json.dumps(sorted(set(token.scopes))),
                token.created_at,
                token.last_used_at,
                token.expires_at,
                token.origin,
            ),
        )
        restored += 1
    # In memory only, and fail closed: a rolled-back commit just closed sockets.
    close_token_sockets(replaced)
    logger.info("Backup restore: %d API token(s) restored", restored)
    return warnings


register_backup_section(
    API_TOKENS_SECTION,
    export_api_tokens,
    restore_api_tokens,
    description={
        "en": "API and device tokens: names, scopes and hashes (never the tokens themselves)",
        "fr": "Jetons d'API et d'appareil : noms, portées et hachés (jamais les jetons eux-mêmes)",
    },
    sensitive=True,
    restore_with=USERS_SECTION,
)
