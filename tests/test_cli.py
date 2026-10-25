"""``meshloom-admin`` (app/cli.py) against a temporary database file."""

import asyncio
import io
import json
import os
import pty
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app import cli
from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import token_matches
from app.database import Database
from app.repository import auth as auth_repo

PASSWORD = "correct horse battery staple"
ROOT = Path(__file__).resolve().parent.parent
SHIM = Path(__file__).resolve().parent.parent / "pkg" / "nfpm" / "meshloom-admin"


def _init_db(path: Path) -> None:
    async def run() -> None:
        database = Database(str(path))
        await database.connect()
        await database.disconnect()

    asyncio.run(run())


def _query(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _add_user(path: Path, username: str, role: str = "admin", password: str | None = PASSWORD):
    async def run() -> None:
        conn = await cli._open_database(str(path))
        try:
            await auth_repo.UserRepository.create(
                username, role, password_hash=hash_password(password) if password else None
            )
        finally:
            cli.db._connection = None
            await conn.close()

    asyncio.run(run())


def _add_session(path: Path, session_id: str, user_id: int) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at)"
        " VALUES (?, ?, ?, 1, 1, 4000000000)",
        (session_id, user_id, "h" + session_id),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def dbfile(tmp_path):
    path = tmp_path / "meshcore.db"
    _init_db(path)
    return path


@pytest.fixture
def stdin(monkeypatch):
    def feed(text: str) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))

    return feed


def run(dbfile: Path, *argv: str) -> int:
    return cli.main(["--db", str(dbfile), *argv])


def test_reset_password_takes_effect_in_the_database_and_signs_out(dbfile, stdin, capsys):
    _add_user(dbfile, "alice")
    uid = _query(dbfile, "SELECT id FROM users")[0][0]
    _add_session(dbfile, "s1", uid)

    stdin("a brand new passphrase\n")
    assert run(dbfile, "reset-password", "ALICE", "--stdin") == 0
    (stored,) = _query(dbfile, "SELECT password_hash FROM users")[0]
    assert verify_password(stored, "a brand new passphrase")
    assert not verify_password(stored, PASSWORD)
    assert _query(dbfile, "SELECT revoked_at IS NOT NULL FROM sessions") == [(1,)]
    assert _query(dbfile, "SELECT action, outcome, via FROM auth_audit") == [
        ("password.changed", "success", "cli")
    ]
    out = capsys.readouterr()
    assert "a brand new passphrase" not in out.out + out.err


def test_reset_password_refuses_short_unknown_and_ingress(dbfile, stdin, capsys):
    _add_user(dbfile, "alice")
    stdin("short\n")
    assert run(dbfile, "reset-password", "alice", "--stdin") == 1
    assert "at least 12" in capsys.readouterr().err
    stdin("long enough password\n")
    assert run(dbfile, "reset-password", "nobody", "--stdin") == 1
    assert "no such user" in capsys.readouterr().err
    stdin("alicealicealice\n")
    assert run(dbfile, "reset-password", "alice", "--stdin") == 0  # not equal to the name
    stdin("ALICE\n")
    assert run(dbfile, "reset-password", "alice", "--stdin") == 1


def test_reset_password_refuses_a_kiosk(dbfile, stdin, capsys):
    _add_user(dbfile, "wall", role="kiosk", password=None)
    stdin("long enough password\n")
    assert run(dbfile, "reset-password", "wall", "--stdin", "--must-change") == 1
    assert "kiosk" in capsys.readouterr().err
    assert _query(dbfile, "SELECT password_hash, must_change_password FROM users") == [(None, 0)]


def test_create_admin(dbfile, stdin, capsys):
    stdin(PASSWORD + "\n")
    assert run(dbfile, "create-admin", "Bob", "--stdin", "--display-name", "Bob B") == 0
    rows = _query(dbfile, "SELECT username, role, provider, display_name, password_hash FROM users")
    assert rows[0][:4] == ("Bob", "admin", "local", "Bob B")
    assert verify_password(rows[0][4], PASSWORD)
    stdin(PASSWORD + "\n")
    assert run(dbfile, "create-admin", "bob", "--stdin") == 1
    assert "already exists" in capsys.readouterr().err


def test_setup_code_is_accepted_by_the_stored_hash_and_rotates(dbfile, capsys):
    assert run(dbfile, "setup-code") == 0
    first = capsys.readouterr().out.strip()
    # The 8-digit form POST /api/auth/setup accepts, like the startup log line.
    assert len(first) == 8 and first.isdigit()
    (stored,) = _query(dbfile, "SELECT setup_code_hash FROM auth_settings")[0]
    assert stored != first and token_matches(first, stored)
    assert run(dbfile, "setup-code") == 0
    second = capsys.readouterr().out.strip()
    (stored,) = _query(dbfile, "SELECT setup_code_hash FROM auth_settings")[0]
    assert second != first
    assert token_matches(second, stored) and not token_matches(first, stored)


def test_setup_code_refused_once_an_account_exists(dbfile, capsys):
    _add_user(dbfile, "alice")
    assert run(dbfile, "setup-code") == 1
    assert capsys.readouterr().out == ""


def test_list_users_table_and_json(dbfile, capsys):
    _add_user(dbfile, "alice")
    _add_user(dbfile, "kiosk", role="kiosk", password=None)
    assert run(dbfile, "list-users") == 0
    table = capsys.readouterr().out
    assert "alice" in table and "kiosk" in table and "hash" not in table.lower()
    assert run(dbfile, "list-users", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert [u["username"] for u in data] == ["alice", "kiosk"]
    assert all("password_hash" not in u for u in data)


def test_disable_and_enable_user_and_last_admin_guard(dbfile, capsys):
    _add_user(dbfile, "alice")
    _add_user(dbfile, "bob")
    assert run(dbfile, "disable-user", "bob") == 0
    assert _query(dbfile, "SELECT disabled FROM users WHERE username = 'bob'") == [(1,)]
    assert run(dbfile, "disable-user", "alice") == 1  # last active admin
    assert "last active administrator" in capsys.readouterr().err
    assert run(dbfile, "disable-user", "alice", "--force") == 0
    assert run(dbfile, "enable-user", "alice") == 0
    assert _query(dbfile, "SELECT disabled FROM users WHERE username = 'alice'") == [(0,)]


def test_revoke_sessions_one_and_all(dbfile):
    _add_user(dbfile, "alice")
    _add_user(dbfile, "bob")
    _add_session(dbfile, "a", 1)
    _add_session(dbfile, "b", 2)
    assert run(dbfile, "revoke-sessions", "alice") == 0
    assert _query(dbfile, "SELECT id FROM sessions WHERE revoked_at IS NULL") == [("b",)]
    assert run(dbfile, "revoke-sessions", "--all") == 0
    assert _query(dbfile, "SELECT id FROM sessions WHERE revoked_at IS NULL") == []


def test_unreadable_database_exits_non_zero_with_a_message(tmp_path, capsys):
    missing = tmp_path / "nope" / "meshcore.db"
    assert cli.main(["--db", str(missing), "list-users"]) == 1
    assert "database not found" in capsys.readouterr().err
    assert not missing.parent.exists()  # never created

    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not a database" * 100)
    assert cli.main(["--db", str(garbage), "list-users"]) == 1
    assert "meshloom-admin:" in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_unreadable_permissions(dbfile, capsys):
    dbfile.chmod(0)
    try:
        assert run(dbfile, "list-users") == 1
        assert "cannot read database" in capsys.readouterr().err
    finally:
        dbfile.chmod(0o600)


def test_database_without_account_tables_asks_for_a_server_start(tmp_path, capsys):
    path = tmp_path / "old.db"
    sqlite3.connect(path).close()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    assert cli.main(["--db", str(path), "list-users"]) == 1
    assert "start Meshloom once" in capsys.readouterr().err
    assert _query(path, "SELECT name FROM sqlite_master") == [("t",)]  # not migrated


def test_database_path_comes_from_the_environment(dbfile, monkeypatch, capsys):
    monkeypatch.setattr(cli.settings, "database_path", str(dbfile))
    assert cli.main(["list-users"]) == 0


def test_help_runs():
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    for name in (
        "reset-password",
        "create-admin",
        "setup-code",
        "list-users",
        "disable-user",
        "host-guard-observe",
    ):
        assert name in result.stdout


def _fake_install(tmp_path: Path) -> tuple[Path, Path]:
    app = tmp_path / "opt"
    (app / ".venv" / "bin").mkdir(parents=True)
    fake_python = app / ".venv" / "bin" / "python"
    fake_python.write_text('#!/bin/sh\necho "py|$MESHCORE_DATABASE_PATH|$PYTHONPATH|$*"\n')
    fake_python.chmod(0o755)
    fake_runuser = tmp_path / "runuser"
    fake_runuser.write_text(
        f'#!/bin/sh\necho "runuser $*" >&2\necho "runuser $*" >>"{tmp_path}/runuser.log"\n'
        # runuser -s /bin/sh -c 'exec "$0" "$@"' -- meshloom CMD...: run CMD.
        'while [ "$1" != -- ]; do shift; done\nshift 2\nexec "$@"\n'
    )
    fake_runuser.chmod(0o755)
    return app, fake_runuser


def _shim(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    base = {"PATH": os.environ["PATH"]}
    return subprocess.run(
        ["sh", str(SHIM), *args], capture_output=True, text=True, env={**base, **env}, check=False
    )


def test_shim_container_kind_needs_no_root_and_isolates_the_path(tmp_path):
    app, _ = _fake_install(tmp_path)
    env = {"MESHLOOM_APP_DIR": str(app), "MESHLOOM_PACKAGE_ROOT": str(tmp_path / "none")}
    result = _shim(["list-users"], env)
    assert result.returncode == 0
    assert result.stdout.strip() == f"py|{app}/data/meshcore.db|{app}|-P -m app.cli list-users"
    result = _shim(["--help"], {**env, "MESHCORE_DATABASE_PATH": "/explicit.db"})
    assert result.stdout.startswith("py|/explicit.db|")


def test_shim_package_kind_refuses_non_root(tmp_path):
    app, runuser = _fake_install(tmp_path)
    env = {"MESHLOOM_PACKAGE_ROOT": str(app), "MESHLOOM_EUID": "1000"}
    result = _shim(["list-users"], env)
    assert result.returncode == 1 and "sudo meshloom-admin" in result.stderr
    assert "py|" not in result.stdout


def test_shim_package_kind_reads_the_env_file_as_data_and_drops_privileges(tmp_path):
    app, runuser = _fake_install(tmp_path)
    marker = tmp_path / "executed"
    env_file = tmp_path / "meshloom.env"
    env_file.write_text(
        f"A=$(touch {marker})\n# MESHCORE_DATABASE_PATH=/commented.db\n"
        'MESHCORE_DATABASE_PATH="/srv/m/x y.db"\n'
    )
    env = {
        "MESHLOOM_PACKAGE_ROOT": str(app),
        "MESHLOOM_EUID": "0",
        "MESHLOOM_ENV_FILE": str(env_file),
        "MESHLOOM_RUNUSER": str(runuser),
        "MESHLOOM_ROOT_UID": str(os.getuid()),
    }
    result = _shim(["list-users"], env)
    assert result.returncode == 0, result.stderr
    assert 'runuser -s /bin/sh -c exec "$0" "$@" -- meshloom env ' in result.stderr
    assert result.stdout.strip() == f"py|/srv/m/x y.db|{app}|-P -m app.cli list-users"
    assert not marker.exists()  # the file is parsed, never sourced

    env_file.unlink()  # no env file: the packaged default
    result = _shim(["list-users"], env)
    assert result.stdout.split("|")[1] == "/var/lib/meshloom/meshcore.db"

    result = _shim(["list-users"], {**env, "MESHCORE_DATABASE_PATH": "/given.db"})
    assert result.stdout.split("|")[1] == "/given.db"


def _package_env(tmp_path: Path, env_file: Path) -> dict[str, str]:
    app, runuser = _fake_install(tmp_path)
    return {
        "MESHLOOM_PACKAGE_ROOT": str(app),
        "MESHLOOM_EUID": "0",
        "MESHLOOM_ENV_FILE": str(env_file),
        "MESHLOOM_RUNUSER": str(runuser),
        "MESHLOOM_ROOT_UID": str(os.getuid()),
    }


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_shim_package_kind_warns_when_the_env_file_is_unreadable(tmp_path):
    env_file = tmp_path / "meshloom.env"
    env_file.write_text("MESHCORE_DATABASE_PATH=/x.db\n")
    env_file.chmod(0)
    env = _package_env(tmp_path, env_file)
    try:
        result = _shim(["list-users"], env)
        # --db makes the file irrelevant: no exit, the CLI gets the path.
        with_db = _shim(["--db", "/given.db", "list-users"], env)
        with_db_eq = _shim(["--db=/given.db", "list-users"], env)
    finally:
        env_file.chmod(0o600)
    assert result.returncode == 1 and "cannot read" in result.stderr and "--db" in result.stderr
    assert "py|" not in result.stdout
    for run in (with_db, with_db_eq):
        assert run.returncode == 0, run.stderr
        assert "--db" in run.stdout and "list-users" in run.stdout


@pytest.mark.parametrize("problem", ["group-writable", "world-writable", "owner", "link", "dir"])
def test_shim_package_kind_refuses_an_unsafe_env_file(tmp_path, problem):
    etc = tmp_path / "etc"
    etc.mkdir(mode=0o750)
    env_file = etc / "meshloom.env"
    env_file.write_text("MESHCORE_DATABASE_PATH=/attacker.db\n")
    env_file.chmod(0o640)
    env = _package_env(tmp_path, env_file)
    if problem == "group-writable":
        env_file.chmod(0o660)
    elif problem == "world-writable":
        env_file.chmod(0o646)
    elif problem == "owner":
        env["MESHLOOM_ROOT_UID"] = str(os.getuid() + 1)
    elif problem == "link":
        target = tmp_path / "elsewhere.env"
        env_file.rename(target)
        env_file.symlink_to(target)
    elif problem == "dir":
        etc.chmod(0o770)
    result = _shim(["list-users"], env)
    assert result.returncode == 1, result.stdout
    assert "refusing" in result.stderr and "py|" not in result.stdout
    # With --db the file is not read, so it is not refused either.
    result = _shim(["--db", "/given.db", "list-users"], env)
    assert result.returncode == 0, result.stderr
    assert "/attacker.db" not in result.stdout


def test_shim_package_kind_accepts_the_packaged_modes(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir(mode=0o750)
    env_file = etc / "meshloom.env"
    env_file.write_text("MESHCORE_DATABASE_PATH=/srv/ok.db\n")
    env_file.chmod(0o640)
    result = _shim(["list-users"], _package_env(tmp_path, env_file))
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("|")[1] == "/srv/ok.db"


def test_shim_package_kind_uses_a_fixed_path(tmp_path):
    # A planted `id` or `sed` earlier in the caller's PATH is never run.
    planted = tmp_path / "planted"
    planted.mkdir()
    marker = tmp_path / "planted-ran"
    for tool in ("id", "sed", "find", "env", "tail", "tr", "dirname"):
        (planted / tool).write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
        (planted / tool).chmod(0o755)
    env_file = tmp_path / "meshloom.env"
    env_file.write_text("MESHCORE_DATABASE_PATH=/srv/ok.db\n")
    env = {**_package_env(tmp_path, env_file), "PATH": f"{planted}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["/bin/sh", str(SHIM), "list-users"], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("|")[1] == "/srv/ok.db"
    assert not marker.exists()


# The child is always started by `runuser -c` (a new session: no controlling
# terminal, so no TIOCSTI into root's), never `runuser -u` (same session).
# scripts/test/meshloom_admin_tty.sh checks the real runuser in CI.
NEW_SESSION = '-s /bin/sh -c exec "$0" "$@" -- meshloom env '


def _shim_on_terminal(env: dict[str, str], ttys: str) -> str:
    """Run the shim with fds 0-2 on a pty where ``ttys`` has a 1; return the runuser call."""
    primary, secondary = pty.openpty()
    pipe_r, pipe_w = os.pipe()
    fds = [
        secondary if flag == "1" else (pipe_r if n == 0 else pipe_w) for n, flag in enumerate(ttys)
    ]
    try:
        proc = subprocess.run(
            ["sh", str(SHIM), "list-users"],
            stdin=fds[0],
            stdout=fds[1],
            stderr=fds[2],
            env=env,
            check=False,
            timeout=30,
        )
    finally:
        for fd in (secondary, primary, pipe_r, pipe_w):
            os.close(fd)
    assert proc.returncode == 0
    return (Path(env["MESHLOOM_PACKAGE_ROOT"]).parent / "runuser.log").read_text().splitlines()[-1]


def test_shim_package_kind_never_shares_roots_terminal(tmp_path):
    env_file = tmp_path / "meshloom.env"
    env = {"PATH": os.environ["PATH"], **_package_env(tmp_path, env_file)}
    piped = _shim(["list-users"], env)
    assert piped.returncode == 0 and f"runuser {NEW_SESSION}" in piped.stderr

    # Fully interactive: a pty of its own too, for the password prompts.
    assert _shim_on_terminal(env, "111").startswith(f"runuser --pty {NEW_SESSION}")
    # Partly piped (`| less`, `echo y |`, `2>/dev/null`): no pty, which would
    # merge stderr into stdout, but still a new session.
    for ttys in ("101", "011", "110", "100", "001"):
        assert _shim_on_terminal(env, ttys).startswith(f"runuser {NEW_SESSION}"), ttys


def test_current_directory_cannot_shadow_the_installed_code(tmp_path):
    shadow = tmp_path / "shadow" / "app"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("")
    (shadow / "cli.py").write_text("print('SHADOWED')\n")
    result = subprocess.run(
        [sys.executable, "-P", "-m", "app.cli", "--help"],
        capture_output=True,
        text=True,
        cwd=tmp_path / "shadow",
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        check=False,
    )
    assert "SHADOWED" not in result.stdout and "list-users" in result.stdout


def test_refusals_and_list_users_are_audited(dbfile, stdin, capsys):
    _add_user(dbfile, "alice")
    assert run(dbfile, "list-users") == 0
    assert run(dbfile, "disable-user", "alice") == 1  # last admin
    assert run(dbfile, "disable-user", "ghost") == 1
    rows = _query(dbfile, "SELECT action, outcome, target FROM auth_audit ORDER BY id")
    assert rows == [
        ("users.listed", "success", None),
        ("user.disabled", "refused", "user:1"),
        ("user.disabled", "refused", "username:unknown"),
    ]


def test_change_is_visible_to_a_connection_the_server_keeps_open(dbfile, stdin):
    """A running server holds its connection (WAL); no restart is needed."""
    _add_user(dbfile, "alice")
    server = sqlite3.connect(dbfile)
    assert server.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    before = server.execute("SELECT password_hash FROM users").fetchone()[0]
    stdin("a brand new passphrase\n")
    assert run(dbfile, "reset-password", "alice", "--stdin") == 0
    after = server.execute("SELECT password_hash FROM users").fetchone()[0]
    server.close()
    assert after != before and verify_password(after, "a brand new passphrase")


def test_every_install_kind_ships_the_command():
    assert os.access(SHIM, os.X_OK)
    nfpm = (ROOT / "pkg" / "nfpm" / "nfpm.yaml.tmpl").read_text()
    assert "dst: /opt/meshloom/.venv/bin/meshloom-admin" in nfpm
    assert "src: __PKGDIR__/meshloom-admin" in nfpm
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "dst: /usr/bin/meshloom-admin" in nfpm
    assert "pkg/nfpm/meshloom-admin /usr/local/bin/meshloom-admin" in dockerfile


def test_setup_code_works_with_only_a_kiosk_account(dbfile, capsys):
    _add_user(dbfile, "kiosk", "kiosk", None)
    assert _query(dbfile, "SELECT count(*) FROM users") == [(1,)]
    assert run(dbfile, "setup-code") == 0


def test_host_guard_observe_resets_the_mode_and_is_audited(dbfile, capsys):
    from app.repository.auth import AuthSettingsRepository

    async def set_enforce() -> None:
        conn = await cli._open_database(str(dbfile))
        try:
            await AuthSettingsRepository.update(host_guard_mode="enforce")
        finally:
            from app.database import db

            db._connection = None
            await conn.close()

    asyncio.run(set_enforce())
    assert _query(dbfile, "SELECT host_guard_mode FROM auth_settings") == [("enforce",)]
    assert run(dbfile, "host-guard-observe") == 0
    assert _query(dbfile, "SELECT host_guard_mode FROM auth_settings") == [("observe",)]
    rows = _query(dbfile, "SELECT action, target, via FROM auth_audit")
    assert ("settings.changed", "auth_settings.host_guard_mode", "cli") in rows
    assert run(dbfile, "host-guard-observe") == 0
    assert "already" in capsys.readouterr().out


def test_disable_2fa_removes_the_factor_and_writes_an_audit_row(dbfile, capsys):
    _add_user(dbfile, "alice")
    uid = _query(dbfile, "SELECT id FROM users")[0][0]
    conn = sqlite3.connect(dbfile)
    conn.execute(
        "UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP', totp_enabled_at = 1,"
        " totp_last_counter = 9 WHERE id = ?",
        (uid,),
    )
    conn.executemany(
        "INSERT INTO recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, 1)",
        [(uid, f"hash-{i}") for i in range(3)],
    )
    conn.commit()
    conn.close()

    assert run(dbfile, "disable-2fa", "ALICE") == 0
    assert _query(dbfile, "SELECT totp_secret, totp_enabled_at, totp_last_counter FROM users") == [
        (None, None, None)
    ]
    assert _query(dbfile, "SELECT COUNT(*) FROM recovery_codes") == [(0,)]
    assert _query(dbfile, "SELECT action, outcome, via, target FROM auth_audit") == [
        ("totp.disabled", "success", "cli", f"user:{uid}")
    ]
    assert "removed" in capsys.readouterr().out

    # Nothing left to remove: no change, no row.
    assert run(dbfile, "disable-2fa", "alice") == 0
    assert "no second factor" in capsys.readouterr().out
    assert len(_query(dbfile, "SELECT 1 FROM auth_audit")) == 1


def test_disable_2fa_refuses_an_unknown_user_and_an_unmigrated_database(dbfile, capsys):
    assert run(dbfile, "disable-2fa", "nobody") == 1
    assert "no such user" in capsys.readouterr().err
    assert _query(dbfile, "SELECT action, outcome FROM auth_audit") == [
        ("totp.disabled", "refused")
    ]
    conn = sqlite3.connect(dbfile)
    conn.execute("DROP TABLE recovery_codes")
    conn.commit()
    conn.close()
    _add_user(dbfile, "alice")
    assert run(dbfile, "disable-2fa", "alice") == 1
    assert "recovery_codes" in capsys.readouterr().err


def test_remove_passkeys_removes_them_all_and_writes_an_audit_row(dbfile, capsys):
    _add_user(dbfile, "alice")
    _add_user(dbfile, "bob")
    uid, other = (row[0] for row in _query(dbfile, "SELECT id FROM users ORDER BY id"))
    conn = sqlite3.connect(dbfile)
    conn.executemany(
        "INSERT INTO webauthn_credentials (user_id, credential_id, public_key, name, created_at)"
        " VALUES (?, ?, x'01', 'key', 1)",
        [(uid, b"a"), (uid, b"b"), (other, b"c")],
    )
    conn.commit()
    conn.close()

    assert run(dbfile, "remove-passkeys", "ALICE") == 0
    assert "2 passkey(s)" in capsys.readouterr().out
    assert _query(dbfile, "SELECT user_id FROM webauthn_credentials") == [(other,)]
    assert _query(dbfile, "SELECT action, outcome, via, target FROM auth_audit") == [
        ("passkey.removed", "success", "cli", f"user:{uid}")
    ]
    assert run(dbfile, "remove-passkeys", "alice") == 0
    assert "no passkey" in capsys.readouterr().out
    assert len(_query(dbfile, "SELECT 1 FROM auth_audit")) == 1


def test_remove_passkeys_refuses_an_unmigrated_database(dbfile, capsys):
    _add_user(dbfile, "alice")
    conn = sqlite3.connect(dbfile)
    conn.execute("DROP TABLE webauthn_credentials")
    conn.commit()
    conn.close()
    assert run(dbfile, "remove-passkeys", "alice") == 1
    assert "webauthn_credentials" in capsys.readouterr().err


def test_rotate_device_key_replaces_the_key_and_is_audited(dbfile, capsys):
    conn = sqlite3.connect(dbfile)
    conn.execute("UPDATE auth_settings SET device_cookie_key = ?", ("ab" * 32,))
    conn.commit()
    conn.close()
    assert run(dbfile, "rotate-device-key") == 0
    [(key,)] = _query(dbfile, "SELECT device_cookie_key FROM auth_settings")
    assert len(key) == 64 and key != "ab" * 32
    bytes.fromhex(key)
    out = capsys.readouterr().out
    assert "rotated" in out and key not in out
    rows = _query(dbfile, "SELECT action, target, via, details FROM auth_audit")
    assert [r[:3] for r in rows] == [("settings.changed", "auth_settings.device_cookie_key", "cli")]
    assert key not in rows[0][3]
    # A never-used key (NULL) is rotated too.
    conn = sqlite3.connect(dbfile)
    conn.execute("UPDATE auth_settings SET device_cookie_key = NULL")
    conn.commit()
    conn.close()
    assert run(dbfile, "rotate-device-key") == 0
    assert _query(dbfile, "SELECT device_cookie_key FROM auth_settings")[0][0] is not None


def test_rotate_device_key_refuses_an_unmigrated_database(dbfile, capsys):
    conn = sqlite3.connect(dbfile)
    conn.execute("ALTER TABLE auth_settings DROP COLUMN device_cookie_key")
    conn.commit()
    conn.close()
    assert run(dbfile, "rotate-device-key") == 1
    assert "device_cookie_key" in capsys.readouterr().err


def test_sigterm_ends_the_cli_like_ctrl_c_and_getpass_works_with_stdout_piped(dbfile):
    """The wrapper's child runs in its own session (runuser -c): no controlling terminal, and
    Ctrl-C reaches it as SIGTERM. With stdout and stderr piped and only stdin on a terminal
    (no runuser --pty), getpass must still prompt (it falls back from /dev/tty to stdin and
    prompts on stderr), and SIGTERM must give a clean message, exit code 130, no traceback."""
    import select
    import signal
    import time

    _add_user(dbfile, "alice")
    primary, secondary = pty.openpty()
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.cli", "--db", str(dbfile), "reset-password", "alice"],
        cwd=ROOT,
        stdin=secondary,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # no controlling terminal, like runuser -c
    )
    os.close(secondary)
    try:
        err = b""
        deadline = time.monotonic() + 20
        assert proc.stderr is not None
        while b"New password for alice: " not in err and time.monotonic() < deadline:
            if select.select([proc.stderr], [], [], 0.2)[0]:
                chunk = os.read(proc.stderr.fileno(), 4096)
                if not chunk:
                    break
                err += chunk
        assert b"New password for alice: " in err, err
        os.write(primary, (PASSWORD + "\n").encode())
        while b"Repeat the password: " not in err and time.monotonic() < deadline:
            if select.select([proc.stderr], [], [], 0.2)[0]:
                chunk = os.read(proc.stderr.fileno(), 4096)
                if not chunk:
                    break
                err += chunk
        assert b"Repeat the password: " in err, err
        proc.send_signal(signal.SIGTERM)
        out, rest = proc.communicate(timeout=20)
    finally:
        os.close(primary)
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 130
    assert b"meshloom-admin: interrupted" in rest
    # The prompt line is ended before the message (no "Repeat the password: meshloom-admin").
    assert b"Repeat the password: \nmeshloom-admin: interrupted" in err + rest
    assert b"Traceback" not in rest
    assert out == b""
