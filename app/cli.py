"""``meshloom-admin``: recover access on the host, without the UI.

Run it on the machine (or in the container) that holds the database::

    meshloom-admin reset-password alice
    meshloom-admin create-admin alice
    meshloom-admin setup-code
    meshloom-admin list-users
    meshloom-admin disable-user alice
    meshloom-admin revoke-sessions --all

It opens the SQLite file directly, through the same repositories as the server
(``app/repository/auth.py``), so a change is visible to a running server on its
next request: no restart. The server leaves the file in WAL mode (the mode is
stored in the file, this module does not set it), and the file is never created or migrated here: a missing or unreadable file, or one the
server has not migrated yet, is a clear error and exit status 1. Per-install
commands and the wrapper script: ``docs/user/en/deep/access.md``.

The database path is ``MESHCORE_DATABASE_PATH`` (``--db`` overrides it). Run it
as the user that owns the file, so SQLite does not leave a ``-wal`` or ``-shm``
file the server then cannot write.

Exit status: 0 done, 1 refused or failed (message on stderr), 2 bad usage.
Passwords are read from the terminal (twice) or from stdin with ``--stdin``,
never from an argument, and no command prints one. The one secret ``setup-code``
prints is the installation code, on purpose.
"""

import argparse
import asyncio
import getpass
import json
import sqlite3
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import aiosqlite

from app.auth.models import User
from app.auth.passwords import MAX_PASSWORD_LENGTH, hash_password
from app.auth.tokens import hash_token, new_token
from app.config import settings
from app.database import db
from app.repository.auth import (
    AuthAuditRepository,
    AuthSessionRepository,
    AuthSettingsMissingError,
    AuthSettingsRepository,
    InvalidUsernameError,
    UsernameTakenError,
    UserRepository,
)

# The audit trail names who acted: the CLI has no account, only the host.
AUDIT_VIA = "cli"

# Same floor as the setup form (cadrage P1); the full policy (common-password
# list, differs from the username) lands with the login routes.
MIN_PASSWORD_LENGTH = 12

_REQUIRED_TABLES = ("users", "sessions", "auth_audit", "auth_settings")


class CliError(Exception):
    """A refusal the operator can act on; printed without a traceback."""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def _open_database(path: str) -> aiosqlite.Connection:
    """Connect ``app.database.db`` to an existing, already migrated file.

    Does not create the file, run the schema script or run migrations: the
    server owns those, and doing them from a second process while it runs is
    not safe.
    """
    file = Path(path)
    if not file.is_file():
        raise CliError(f"database not found: {path} (set MESHCORE_DATABASE_PATH or use --db)")
    try:
        with file.open("rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise CliError(f"cannot read database {path}: {exc.strerror or exc}") from None
    try:
        conn = await aiosqlite.connect(f"{file.resolve().as_uri()}?mode=rw", uri=True)
    except (sqlite3.Error, OSError) as exc:
        raise CliError(
            f"cannot open database {path} for writing: {exc} (run as the user that owns the file)"
        ) from None
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute("PRAGMA busy_timeout = 5000")
        await conn.execute("PRAGMA foreign_keys = ON")
        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
    except sqlite3.DatabaseError as exc:
        await conn.close()
        raise CliError(f"{path} is not a usable SQLite database: {exc}") from None
    missing = [name for name in _REQUIRED_TABLES if name not in tables]
    if missing:
        await conn.close()
        raise CliError(
            f"{path} has no account tables ({', '.join(missing)}): start Meshloom once "
            "on this version so it migrates the database, then retry"
        )
    # The repositories read the module-level ``db``; attach it to this connection.
    db._connection = conn
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_password(from_stdin: bool, username: str) -> str:
    if from_stdin:
        line = sys.stdin.readline()
        password = line.rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise CliError("no terminal to prompt on: pipe the password and pass --stdin")
        password = getpass.getpass(f"New password for {username}: ")
        if getpass.getpass("Repeat the password: ") != password:
            raise CliError("the two passwords differ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CliError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise CliError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")
    if password.casefold() == username.casefold():
        raise CliError("password must differ from the username")
    return password


async def _find_user(username: str) -> User:
    user = await UserRepository.get_by_username(username)
    if user is None:
        raise CliError(f"no such user: {username}")
    return user


async def _audit(action: str, target: str | None, **details: object) -> None:
    await AuthAuditRepository.add(action, "ok", target=target, via=AUDIT_VIA, details=details)


async def _audit_refusal(args: argparse.Namespace, reason: str) -> None:
    """Record a refused command; the audit trail must not hide the refusal itself."""
    target = getattr(args, "username", None)
    try:
        await AuthAuditRepository.add(
            "cli." + args.command.replace("-", "_"),
            "refused",
            target=target,
            via=AUDIT_VIA,
            details={"reason": reason},
        )
    except (sqlite3.Error, ValueError):
        pass


def _status(user: User) -> str:
    return "disabled" if user.disabled else "active"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def _reset_password(args: argparse.Namespace) -> int:
    user = await _find_user(args.username)
    if user.provider != "local":
        raise CliError(f"{user.username} signs in through {user.provider}: it has no password")
    password = _read_password(args.stdin, user.username)
    await UserRepository.set_password_hash(
        user.id, hash_password(password), must_change_password=args.must_change
    )
    revoked = await AuthSessionRepository.revoke_all_for_user(user.id)
    await _audit("cli.reset_password", user.username, sessions_revoked=revoked)
    print(f"Password of {user.username} reset; {revoked} session(s) signed out.")
    if user.disabled:
        print(f"Note: {user.username} is disabled. Enable it with: meshloom-admin enable-user")
    return 0


async def _create_admin(args: argparse.Namespace) -> int:
    try:
        existing = await UserRepository.get_by_username(args.username)
    except InvalidUsernameError as exc:
        raise CliError(str(exc)) from None
    if existing is not None:
        raise CliError(
            f"{existing.username} already exists: use reset-password to set its password"
        )
    password = _read_password(args.stdin, args.username.strip())
    try:
        user = await UserRepository.create(
            args.username,
            "admin",
            display_name=args.display_name or "",
            password_hash=hash_password(password),
            must_change_password=args.must_change,
        )
    except (InvalidUsernameError, UsernameTakenError) as exc:
        raise CliError(str(exc)) from None
    await _audit("cli.create_admin", user.username, user_id=user.id)
    print(f"Administrator {user.username} created (id {user.id}).")
    return 0


async def _setup_code(args: argparse.Namespace) -> int:
    # Only the SHA-256 of the code is stored, so a code cannot be read back:
    # each run issues a new one and the previous one stops working.
    if await UserRepository.list_all():
        raise CliError(
            "the installation code only matters while no account exists, and one does: "
            "use create-admin or reset-password instead"
        )
    code = new_token()
    await AuthSettingsRepository.set_setup_code_hash(hash_token(code))
    await _audit("cli.setup_code", None)
    print(code)
    print(
        "New installation code above; any earlier one no longer works.",
        file=sys.stderr,
    )
    return 0


async def _list_users(args: argparse.Namespace) -> int:
    users = await UserRepository.list_all()
    await _audit("cli.list_users", None, count=len(users))
    if args.json:
        print(json.dumps([user.model_dump() for user in users], indent=2))
        return 0
    if not users:
        print("No users.")
        return 0
    rows = [
        (
            str(user.id),
            user.username,
            user.role,
            user.provider,
            _status(user),
            "yes" if user.has_password else "no",
            "yes" if user.totp_enabled_at else "no",
        )
        for user in users
    ]
    header = ("ID", "USERNAME", "ROLE", "PROVIDER", "STATUS", "PASSWORD", "2FA")
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    for row in [header, *rows]:
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        )
    return 0


async def _disable_user(args: argparse.Namespace) -> int:
    user = await _find_user(args.username)
    if user.disabled:
        print(f"{user.username} is already disabled.")
        return 0
    if user.role == "admin" and not args.force and await UserRepository.count_enabled_admins() <= 1:
        raise CliError(
            f"{user.username} is the last active administrator: disabling it locks everyone "
            "out (use --force if you mean it, create-admin first otherwise)"
        )
    await UserRepository.update(user.id, disabled=True)
    revoked = await AuthSessionRepository.revoke_all_for_user(user.id)
    await _audit("cli.disable_user", user.username, sessions_revoked=revoked)
    print(f"{user.username} disabled; {revoked} session(s) signed out.")
    return 0


async def _enable_user(args: argparse.Namespace) -> int:
    user = await _find_user(args.username)
    if not user.disabled:
        print(f"{user.username} is already active.")
        return 0
    await UserRepository.update(user.id, disabled=False)
    await _audit("cli.enable_user", user.username)
    print(f"{user.username} enabled.")
    return 0


async def _revoke_sessions(args: argparse.Namespace) -> int:
    if args.all:
        targets = await UserRepository.list_all()
    else:
        targets = [await _find_user(args.username)]
    revoked = 0
    for user in targets:
        revoked += await AuthSessionRepository.revoke_all_for_user(user.id)
    scope = "every user" if args.all else targets[0].username
    await _audit("cli.revoke_sessions", None if args.all else scope, sessions_revoked=revoked)
    print(f"{revoked} session(s) signed out ({scope}).")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_COMMANDS: dict[str, Callable[[argparse.Namespace], Awaitable[int]]] = {
    "reset-password": _reset_password,
    "create-admin": _create_admin,
    "setup-code": _setup_code,
    "list-users": _list_users,
    "disable-user": _disable_user,
    "enable-user": _enable_user,
    "revoke-sessions": _revoke_sessions,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meshloom-admin",
        description="Recover access to a Meshloom instance from the host, without the web UI.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="SQLite file (default: MESHCORE_DATABASE_PATH, then data/meshcore.db)",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, description=help_text)

    def password_options(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument(
            "--stdin", action="store_true", help="read the password from one line of stdin"
        )
        cmd.add_argument(
            "--must-change",
            action="store_true",
            help="ask for a new password at the next sign-in",
        )

    cmd = add("reset-password", "set a new password for a local user and sign it out everywhere")
    cmd.add_argument("username")
    password_options(cmd)

    cmd = add("create-admin", "create an administrator account")
    cmd.add_argument("username")
    cmd.add_argument("--display-name", default="")
    password_options(cmd)

    add("setup-code", "issue a new installation code (only while no account exists)")

    cmd = add("list-users", "list the accounts")
    cmd.add_argument("--json", action="store_true", help="machine-readable output")

    cmd = add("disable-user", "block a user from signing in and sign it out")
    cmd.add_argument("username")
    cmd.add_argument(
        "--force", action="store_true", help="allow disabling the last active administrator"
    )

    cmd = add("enable-user", "allow a disabled user to sign in again")
    cmd.add_argument("username")

    cmd = add("revoke-sessions", "sign a user (or everyone) out")
    target = cmd.add_mutually_exclusive_group(required=True)
    target.add_argument("username", nargs="?")
    target.add_argument("--all", action="store_true", help="every user")
    return parser


async def _run(args: argparse.Namespace) -> int:
    conn = await _open_database(args.db or settings.database_path)
    try:
        try:
            return await _COMMANDS[args.command](args)
        except CliError as exc:
            await _audit_refusal(args, str(exc))
            raise
    except AuthSettingsMissingError:
        raise CliError("auth_settings row missing: start Meshloom once to repair it") from None
    except sqlite3.OperationalError as exc:
        raise CliError(f"database error: {exc} (is another process holding it?)") from None
    finally:
        db._connection = None
        await conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except CliError as exc:
        print(f"meshloom-admin: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("meshloom-admin: interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
