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
  without an enabled administrator is refused (``accounts_no_admin``) and the
  current accounts stay: a restore never leaves the instance without one. Every
  login session is deleted (ids may now name other people), ``updated_at`` moves
  forward (known-device cookies are invalidated) and a trusted network rule is
  kept only when its account id names the same username before and after
  (``trusted_network_dropped:<cidr>``). The ``accounts`` restore hook reloads the
  trusted network rules; open WebSockets close at the session recheck.
- **Never log** a hash, a TOTP secret or a row: log lines carry counts only.
- **Extension point.** API tokens (Access T27) are not a table yet. They will be
  their own section ``api_tokens`` (hashes and scopes, ``sensitive``) registered
  with ``requires=["users"]`` and added to ``ACCOUNT_SECTIONS``, so the same box
  leaves them out and a file without accounts never restores tokens. Passkeys
  and recovery codes (T50, T52) go inside this section, as a new ``version``.
"""

import json
import logging
import time

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth.models import (
    USERNAME_MAX_LENGTH,
    AuthSettings,
    Provider,
    Role,
    TrustedNetworkRule,
)
from app.models import BackupSection
from app.repository.auth import normalize_username, username_key
from app.services.backup_sections import (
    RestoreSource,
    register_backup_section,
    register_on_restore,
)

logger = logging.getLogger(__name__)

USERS_SECTION = "users"
# Sections the "Include accounts" box covers (T27 adds "api_tokens").
ACCOUNT_SECTIONS: tuple[str, ...] = (USERS_SECTION,)

# A bound on a file, not a policy.
MAX_BACKUP_USERS = 10_000
_MAX_SECRET_LENGTH = 1024

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


async def restore_users(section: BackupSection, conn: aiosqlite.Connection) -> list[str]:
    data = BackupUsersData.model_validate(section.data)
    if not any(user.role == "admin" and not user.disabled for user in data.users):
        logger.warning(
            "Backup restore: the accounts section has no enabled administrator, kept the current accounts"
        )
        return ["accounts_no_admin"]
    before = await _username_keys(conn)
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
                max(now, user.updated_at + 1),
                username_key(name),
            ),
        )
    async with conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0"
    ) as cursor:
        row = await cursor.fetchone()
    if not row or int(row[0]) < 1:
        # Unreachable after the check above; raising rolls the section back.
        raise RuntimeError("accounts restore would leave no enabled administrator")
    warnings = await _keep_trusted_networks(conn, before, await _username_keys(conn))
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
