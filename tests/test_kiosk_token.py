"""Kiosk device token (T60): provisioning, rotation, the bootstrap and the code exchange."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from app.auth import setup_code
from app.repository.auth import ApiTokenRepository, AuthAuditRepository, UserRepository
from app.services import kiosk
from tests.auth_helpers import ADMIN, PASSWORD, create_user, login, make_client

LOOP = "http://127.0.0.1:8000"
REPO = Path(__file__).resolve().parents[1]

real_auth = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]


@pytest.fixture
def kiosk_host(tmp_path, monkeypatch):
    marker = tmp_path / "kiosk"
    marker.touch()
    token_dir = tmp_path / "meshloom-kiosk"
    token_dir.mkdir(mode=0o750)
    token_dir.chmod(0o2750)
    # The directory's own group stands for meshloom-kiosk.
    gid = token_dir.stat().st_gid
    monkeypatch.setattr(kiosk, "kiosk_group_gid", lambda: gid)
    monkeypatch.setattr(kiosk, "KIOSK_MARKER", marker)
    monkeypatch.setattr(kiosk, "KIOSK_TOKEN_DIR", token_dir)
    kiosk.reset_bootstrap_codes()
    kiosk.reset_rate_limits()
    kiosk.remember_displayed_code(None)
    yield token_dir / kiosk.KIOSK_TOKEN_FILENAME
    kiosk.reset_bootstrap_codes()
    kiosk.reset_rate_limits()
    kiosk.remember_displayed_code(None)


async def _start() -> None:
    await setup_code.prepare_first_run(
        existing_database=False, basic_username="", basic_password=""
    )


def _secret(path: Path) -> str:
    return path.read_text(encoding="ascii").strip()


async def _bootstrap(client, token: str, **kwargs):
    return await client.post(
        "/api/auth/kiosk-bootstrap", headers={"Authorization": f"Bearer {token}"}, **kwargs
    )


async def _kiosk_user():
    user = await kiosk.kiosk_account()
    assert user is not None
    return user


# --- Provisioning ---------------------------------------------------------------


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_first_start_writes_a_read_token_of_the_kiosk_account(kiosk_host):
    await _start()
    secret = _secret(kiosk_host)
    assert re.fullmatch(r"mlt_[A-Za-z0-9_-]{43}", secret)
    assert stat.S_IMODE(os.stat(kiosk_host).st_mode) == 0o640
    user = await _kiosk_user()
    [row] = await ApiTokenRepository.list_live(user.id)
    assert (row.scopes, row.origin, row.expires_at) == (["read"], "pairing", None)
    # The token never carries ``read_private``: the role has none.
    assert "read_private" not in row.scopes
    # A second start keeps it.
    await _start()
    assert _secret(kiosk_host) == secret
    assert [t.id for t in await ApiTokenRepository.list_live(user.id)] == [row.id]


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_token_is_never_logged(kiosk_host, caplog):
    caplog.set_level("DEBUG")
    await _start()
    assert _secret(kiosk_host) not in caplog.text


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_lost_or_foreign_file_rotates_the_token(kiosk_host):
    await _start()
    user = await _kiosk_user()
    [first] = await ApiTokenRepository.list_live(user.id)
    kiosk_host.write_text("mlt_" + "x" * 43 + "\n")
    await _start()
    [second] = await ApiTokenRepository.list_live(user.id)
    assert second.id != first.id
    assert (await ApiTokenRepository.get(first.id)).revoked_at is not None
    kiosk_host.unlink()
    await _start()
    assert kiosk_host.exists()
    assert len(await ApiTokenRepository.list_live(user.id)) == 1


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_no_marker_no_token(kiosk_host, tmp_path, monkeypatch):
    monkeypatch.setattr(kiosk, "KIOSK_MARKER", tmp_path / "absent")
    await _start()
    assert not kiosk_host.exists()


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
@pytest.mark.parametrize(
    "problem", ["missing", "open", "symlink", "group", "no_setgid", "no_group"]
)
async def test_an_unsafe_directory_gets_no_token(kiosk_host, tmp_path, monkeypatch, problem):
    token_dir = kiosk_host.parent
    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o750)
    if problem == "missing":
        token_dir.rmdir()
    elif problem == "open":
        token_dir.chmod(0o2755)
    elif problem == "group":
        other = token_dir.stat().st_gid + 1
        monkeypatch.setattr(kiosk, "kiosk_group_gid", lambda: other)
    elif problem == "no_setgid":
        token_dir.chmod(0o750)
    elif problem == "no_group":
        monkeypatch.setattr(kiosk, "kiosk_group_gid", lambda: None)
    else:
        token_dir.rmdir()
        token_dir.symlink_to(real)
    await _start()
    assert not kiosk_host.exists()
    assert not (real / kiosk.KIOSK_TOKEN_FILENAME).exists()
    user = await _kiosk_user()
    assert await ApiTokenRepository.list_live(user.id) == []


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_failed_write_leaves_no_live_token(kiosk_host, monkeypatch):
    def broken(path, secret):
        raise OSError("disk full")

    monkeypatch.setattr(kiosk, "_write_token_file", broken)
    await _start()
    user = await _kiosk_user()
    assert await ApiTokenRepository.list_live(user.id) == []


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_admin_revocation_writes_a_new_token(kiosk_host):
    await _start()
    old = _secret(kiosk_host)
    user = await _kiosk_user()
    [row] = await ApiTokenRepository.list_live(user.id)
    await create_user()
    async with make_client(base_url=LOOP) as client:
        await login(client)
        listed = (await client.get("/api/auth/tokens")).json()
        assert row.id in [t["id"] for t in listed]
        response = await client.delete(f"/api/auth/tokens/{row.id}", headers={"Origin": LOOP})
        assert response.status_code == 204
    new = _secret(kiosk_host)
    assert new != old
    [fresh] = await ApiTokenRepository.list_live(user.id)
    assert fresh.id != row.id
    async with make_client(base_url=LOOP) as client:
        assert (await _bootstrap(client, old)).status_code == 401
        assert (await _bootstrap(client, new)).status_code == 200


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_revocation_signs_the_kiosk_out_and_voids_pending_codes(kiosk_host):
    await _start()
    await create_user()
    user = await _kiosk_user()
    [row] = await ApiTokenRepository.list_live(user.id)
    async with make_client(base_url=LOOP) as screen:
        path = (await _bootstrap(screen, _secret(kiosk_host))).json()["path"]
        assert "set-cookie" in (await screen.get(path)).headers
        assert (await screen.get("/api/auth/session")).json()["authenticated"] is True
        pending = (await _bootstrap(screen, _secret(kiosk_host))).json()["path"]
        async with make_client(base_url=LOOP) as admin:
            await login(admin)
            revoked = await admin.delete(f"/api/auth/tokens/{row.id}", headers={"Origin": LOOP})
            assert revoked.status_code == 204
        # The screen's session is gone, and the code issued before is void.
        assert (await screen.get("/api/auth/session")).json()["authenticated"] is False
    async with make_client(base_url=LOOP) as other:
        assert "set-cookie" not in (await other.get(pending)).headers


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_rotation_at_start_signs_the_kiosk_out(kiosk_host):
    await _start()
    await create_user()
    async with make_client(base_url=LOOP) as screen:
        path = (await _bootstrap(screen, _secret(kiosk_host))).json()["path"]
        await screen.get(path)
        kiosk_host.unlink()
        await _start()
        assert (await screen.get("/api/auth/session")).json()["authenticated"] is False


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_the_kiosk_token_works_only_on_the_bootstrap_route(kiosk_host):
    await _start()
    await create_user()
    token = {"Authorization": f"Bearer {_secret(kiosk_host)}"}
    async with make_client(base_url=LOOP) as loop:
        assert (await loop.get("/api/channels", headers=token)).status_code == 401
        assert (await loop.get("/api/auth/session", headers=token)).json()["authenticated"] is False
    async with make_client(base_url=LOOP, client=("192.168.1.20", 50000)) as lan:
        assert (await lan.get("/api/channels", headers=token)).status_code == 401
    async with make_client(base_url=LOOP) as loop:
        assert (await loop.post("/api/auth/kiosk-bootstrap", headers=token)).status_code == 200
    rows = await AuthAuditRepository.list_recent(limit=20)
    assert any(r.action == "token.used" and r.details.get("reason") == "kiosk_route" for r in rows)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_bootstrap_is_rate_limited(kiosk_host):
    await _start()
    await create_user()
    async with make_client(base_url=LOOP) as client:
        for _ in range(kiosk.BOOTSTRAP_RATE):
            assert (await _bootstrap(client, _secret(kiosk_host))).status_code == 200
        limited = await _bootstrap(client, _secret(kiosk_host))
        assert limited.status_code == 429
        assert limited.json()["detail"]["code"] == "auth_rate_limited"
        assert limited.headers["retry-after"] == "60"
    assert kiosk.BOOTSTRAP_RATE < kiosk.MAX_PENDING_CODES
    # The window slides.
    kiosk.reset_rate_limits()
    assert kiosk.bootstrap_allowed(now=0.0)
    for _ in range(kiosk.BOOTSTRAP_RATE - 1):
        kiosk.bootstrap_allowed(now=1.0)
    assert not kiosk.bootstrap_allowed(now=2.0)
    assert kiosk.bootstrap_allowed(now=61.0)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_failed_exchanges_are_rate_limited(kiosk_host):
    await _start()
    await create_user()
    async with make_client(base_url=LOOP) as client:
        for _ in range(kiosk.EXCHANGE_FAILURE_RATE):
            await client.get("/api/auth/kiosk-session?code=" + "z" * 43)
        path = (await _bootstrap(client, _secret(kiosk_host))).json()["path"]
        assert "set-cookie" not in (await client.get(path)).headers
    rows = await AuthAuditRepository.list_recent(limit=40)
    assert any(r.action == "login" and r.details.get("reason") == "rate_limited" for r in rows)


# --- Bootstrap and exchange -----------------------------------------------------


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_boots_into_a_kiosk_session_without_any_trusted_network_rule(kiosk_host):
    await _start()
    await create_user()
    from app.repository.auth import AuthSettingsRepository

    assert (await AuthSettingsRepository.get()).trusted_networks == []
    async with make_client(base_url=LOOP) as client:
        anonymous = (await client.get("/api/auth/session")).json()
        assert anonymous["authenticated"] is False

        answer = await _bootstrap(client, _secret(kiosk_host))
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["expires_in"] == 60
        assert re.fullmatch(r"/api/auth/kiosk-session\?code=[A-Za-z0-9_-]{43}", body["path"])
        assert _secret(kiosk_host) not in body["path"]
        # The bearer request sets no cookie: only the browser's exchange does.
        assert "set-cookie" not in answer.headers

        exchange = await client.get(body["path"])
        assert exchange.status_code == 303
        assert exchange.headers["location"] == "/"
        assert exchange.headers["cache-control"] == "no-store"
        assert "meshloom_session=" in exchange.headers["set-cookie"]

        session = (await client.get("/api/auth/session")).json()
        assert session["authenticated"] is True
        assert session["user"]["username"] == "kiosk"
        assert session["user"]["role"] == "kiosk"
        assert session["via"] == "lan"
        # The kiosk reads channels, never direct messages, never settings.
        assert (await client.get("/api/channels")).status_code == 200
        assert (await client.get("/api/settings")).status_code == 403
    rows = await AuthAuditRepository.list_recent(limit=20)
    assert any(
        r.action == "login" and r.outcome == "success" and r.details == {"method": "kiosk_token"}
        for r in rows
    )


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_used_or_unknown_code_is_refused(kiosk_host):
    await _start()
    await create_user()
    async with make_client(base_url=LOOP) as client:
        path = (await _bootstrap(client, _secret(kiosk_host))).json()["path"]
        assert "set-cookie" in (await client.get(path)).headers
    async with make_client(base_url=LOOP) as other:
        again = await other.get(path)
        assert again.status_code == 303
        assert "set-cookie" not in again.headers
        unknown = await other.get("/api/auth/kiosk-session?code=" + "a" * 43)
        assert "set-cookie" not in unknown.headers
        empty = await other.get("/api/auth/kiosk-session")
        assert "set-cookie" not in empty.headers
        assert (await other.get("/api/auth/session")).json()["authenticated"] is False
    failures = [
        r
        for r in await AuthAuditRepository.list_recent(limit=20)
        if r.action == "login" and r.outcome == "failure"
    ]
    assert len(failures) == 3
    assert {r.details["reason"] for r in failures} == {"invalid_code"}


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_an_expired_code_is_refused(kiosk_host):
    await _start()
    user = await _kiosk_user()
    code = kiosk.issue_bootstrap_code(user, now=1000.0)
    assert kiosk.redeem_bootstrap_code(code, now=1000.0 + kiosk.BOOTSTRAP_CODE_TTL_S) is None
    code = kiosk.issue_bootstrap_code(user, now=2000.0)
    assert kiosk.redeem_bootstrap_code(code, now=2001.0) == user.id
    assert kiosk.redeem_bootstrap_code(code, now=2001.0) is None


def test_pending_codes_are_bounded():
    from app.auth.models import User

    kiosk.reset_bootstrap_codes()
    user = User(id=7, username="kiosk", role="kiosk", provider="system", created_at=0, updated_at=0)
    codes = [kiosk.issue_bootstrap_code(user, now=10.0) for _ in range(kiosk.MAX_PENDING_CODES + 2)]
    assert kiosk.redeem_bootstrap_code(codes[0], now=11.0) is None
    assert kiosk.redeem_bootstrap_code(codes[-1], now=11.0) == 7
    kiosk.reset_bootstrap_codes()


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_bootstrap_refuses_non_loopback_and_proxied_peers(kiosk_host):
    await _start()
    await create_user()
    token = _secret(kiosk_host)
    async with make_client(base_url=LOOP, client=("192.168.1.20", 50000)) as lan:
        refused = await _bootstrap(lan, token)
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "auth_kiosk_bootstrap_refused"
    async with make_client(base_url=LOOP) as client:
        for headers in (
            {"X-Forwarded-For": "8.8.8.8"},
            {"Host": "evil.example.com"},
            {"Via": "1.1 tunnel"},
        ):
            answer = await client.post(
                "/api/auth/kiosk-bootstrap",
                headers={"Authorization": f"Bearer {token}", **headers},
            )
            assert answer.status_code in (403, 421), headers
    # The exchange refuses a code presented from the LAN.
    async with make_client(base_url=LOOP) as client:
        path = (await _bootstrap(client, token)).json()["path"]
    async with make_client(base_url=LOOP, client=("192.168.1.20", 50000)) as lan:
        assert "set-cookie" not in (await lan.get(path)).headers
    rows = await AuthAuditRepository.list_recent(limit=40)
    assert any(r.action == "token.used" and r.outcome == "refused" for r in rows)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_bootstrap_needs_the_kiosk_token(kiosk_host):
    await _start()
    await create_user()
    async with make_client(base_url=LOOP) as client:
        # No credential: the gate answers before the route.
        assert (await client.post("/api/auth/kiosk-bootstrap")).status_code == 401
        assert (await _bootstrap(client, "mlt_" + "y" * 43)).status_code == 401
        # An admin session is not the kiosk's token.
        await login(client)
        answer = await client.post("/api/auth/kiosk-bootstrap", headers={"Origin": LOOP})
        assert answer.status_code == 403
        # Nor is an admin's own API token.
        created = await client.post(
            "/api/auth/tokens",
            json={"name": "script", "scopes": ["read"]},
            headers={"Origin": LOOP},
        )
        admin_token = created.json()["token"]
    async with make_client(base_url=LOOP) as client:
        assert (await _bootstrap(client, admin_token)).status_code == 403


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_disabled_kiosk_account_cannot_bootstrap(kiosk_host):
    await _start()
    await create_user()
    user = await _kiosk_user()
    async with make_client(base_url=LOOP) as client:
        path = (await _bootstrap(client, _secret(kiosk_host))).json()["path"]
    await UserRepository.update(user.id, disabled=True)
    async with make_client(base_url=LOOP) as client:
        assert "set-cookie" not in (await client.get(path)).headers
        assert (await _bootstrap(client, _secret(kiosk_host))).status_code == 401


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_plain_host_refuses_both_routes(kiosk_host, tmp_path, monkeypatch):
    await _start()
    await create_user()
    token = _secret(kiosk_host)
    user = await _kiosk_user()
    code = kiosk.issue_bootstrap_code(user)
    monkeypatch.setattr(kiosk, "KIOSK_MARKER", tmp_path / "absent")
    async with make_client(base_url=LOOP) as client:
        assert (await _bootstrap(client, token)).status_code == 403
        exchange = await client.get(f"/api/auth/kiosk-session?code={code}")
        assert "set-cookie" not in exchange.headers


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_setup_page_still_shows_the_code_with_a_kiosk_session(kiosk_host):
    await _start()
    async with make_client(base_url=LOOP) as client:
        path = (await _bootstrap(client, _secret(kiosk_host))).json()["path"]
        assert "set-cookie" in (await client.get(path)).headers
        session = (await client.get("/api/auth/session")).json()
        # The screen keeps showing the setup page and its code until an admin exists.
        assert session["setup_required"] is True
        assert re.fullmatch(r"\d{8}", session["kiosk_setup_code"])
        response = await client.post(
            "/api/auth/setup",
            json={
                "username": ADMIN,
                "password": PASSWORD,
                "setup_code": session["kiosk_setup_code"],
            },
            headers={"Origin": LOOP},
        )
        assert response.status_code == 200, response.text


# --- Packaging and launcher -----------------------------------------------------


def test_packaging_provisions_the_token_directory():
    post = (REPO / "pkg/nfpm/postinstall.sh").read_text(encoding="utf-8")
    assert "meshloom-kiosk" in post
    assert "/var/lib/meshloom-kiosk" in post
    assert "2750" in post
    unit = (REPO / "pkg/nfpm/meshloom.service").read_text(encoding="utf-8")
    assert "-/var/lib/meshloom-kiosk" in unit
    setup = (REPO / "pkg/rpi/meshloom-kiosk-setup").read_text(encoding="utf-8")
    assert "meshloom-kiosk" in setup
    assert str(kiosk.KIOSK_TOKEN_DIR) == "/var/lib/meshloom-kiosk"


def test_launcher_never_puts_the_token_on_a_command_line():
    launcher = (REPO / "pkg/rpi/meshloom-kiosk").read_text(encoding="utf-8")
    assert "/var/lib/meshloom-kiosk/kiosk.token" in launcher
    assert "/api/auth/kiosk-bootstrap" in launcher
    # The header reaches curl through its config on stdin, never argv.
    assert "-K -" in launcher or "--config -" in launcher
    for line in launcher.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not re.search(r"(curl|wget|chromium)[^|]*\$token", stripped), line
    assert "TODO(T60)" not in launcher
    # A rotation restarts Chromium: the file's CRC is read on stdin, never argv.
    assert 'cksum <"$TOKEN_FILE"' in launcher


def _fake_tools(bin_dir: Path, answer: str, log: Path) -> None:
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >>"{log}.argv"\n'
        f'cat >>"{log}.stdin"\n'
        f"printf '%s' '{answer}'\n"
    )
    curl.chmod(0o755)
    browser = bin_dir / "chromium"
    browser.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >>"{log}.browser"\nexit 0\n')
    browser.chmod(0o755)


def _run_launcher_bootstrap(tmp_path: Path, answer: str, token: str | None) -> tuple[str, Path]:
    log = tmp_path / "log"
    _fake_tools(tmp_path / "bin", answer, log)
    token_file = tmp_path / "kiosk.token"
    if token is not None:
        token_file.write_text(token + "\n")
    script = (REPO / "pkg/rpi/meshloom-kiosk").read_text(encoding="utf-8")
    # Source the functions only: everything before the main part.
    functions = script[: script.index("# --- main")]
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "MESHLOOM_KIOSK_TOKEN_FILE": str(token_file),
    }
    result = subprocess.run(
        ["sh", "-c", functions + '\nbootstrap_url "http://127.0.0.1:8000"'],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip(), log


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_launcher_bootstrap_sends_the_token_on_stdin_only(tmp_path):
    token = "mlt_" + "A" * 43
    code = "B" * 43
    answer = f'{{"path":"/api/auth/kiosk-session?code={code}","expires_in":60}}'
    url, log = _run_launcher_bootstrap(tmp_path, answer, token)
    assert url == f"http://127.0.0.1:8000/api/auth/kiosk-session?code={code}"
    argv = (tmp_path / "log.argv").read_text()
    assert token not in argv
    assert "/api/auth/kiosk-bootstrap" in argv
    assert f"Authorization: Bearer {token}" in (tmp_path / "log.stdin").read_text()


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize(
    "answer,token",
    [
        ('{"path":"/api/auth/kiosk-session?code=B"}', None),  # no token file
        ('{"path":"/api/auth/kiosk-session?code=B"}', "not-a-token"),
        ('{"path":"https://evil.example/?code=B"}', "mlt_" + "A" * 43),
        ('{"path":"/api/auth/kiosk-session?code=B C"}', "mlt_" + "A" * 43),
        ("", "mlt_" + "A" * 43),
    ],
)
def test_launcher_bootstrap_fails_closed(tmp_path, answer, token):
    url, _ = _run_launcher_bootstrap(tmp_path, answer, token)
    assert url == ""


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")
def test_kiosk_scripts_pass_shellcheck():
    subprocess.run(
        [
            "shellcheck",
            "-s",
            "sh",
            str(REPO / "pkg/rpi/meshloom-kiosk"),
            str(REPO / "pkg/rpi/meshloom-kiosk-setup"),
        ],
        check=True,
    )
