"""Kiosk host marker (app/services/kiosk.py): the account, the banner code, the packaging."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.auth import setup_code
from app.repository.auth import AuthSettingsRepository, UserRepository
from app.services import kiosk
from tests.auth_helpers import ADMIN, PASSWORD, make_client

LOOP = "http://127.0.0.1:8000"
REPO = Path(__file__).resolve().parents[1]

real_auth = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]


@pytest.fixture
def marker(tmp_path, monkeypatch):
    path = tmp_path / "kiosk"
    monkeypatch.setattr(kiosk, "KIOSK_MARKER", path)
    return path


@pytest.fixture(autouse=True)
def _clean_code():
    kiosk.remember_displayed_code(None)
    yield
    kiosk.remember_displayed_code(None)


async def _prepare(existing: bool = False) -> None:
    await setup_code.prepare_first_run(existing_database=existing)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_without_the_marker_nothing_is_created(marker):
    await _prepare()
    assert await UserRepository.list_all() == []
    assert not (await AuthSettingsRepository.get()).setup_code_set


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_marker_creates_the_kiosk_user_once_and_no_trusted_network(marker):
    marker.touch()
    await _prepare()
    await _prepare()
    [user] = await UserRepository.list_all()
    assert (user.username, user.role, user.has_password) == ("kiosk", "kiosk", False)
    assert (await AuthSettingsRepository.get()).trusted_networks == []


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_kiosk_account_does_not_close_setup_and_the_code_is_shown_to_loopback(marker):
    marker.touch()
    await _prepare()
    async with make_client(base_url=LOOP) as client:
        session = (await client.get("/api/auth/session")).json()
        assert session["setup_required"] is True
        assert session["setup_code_required"] is True
        code = session["kiosk_setup_code"]
        assert re.fullmatch(r"\d{8}", code)

        # Not through a proxy, not from another machine.
        proxied = await client.get("/api/auth/session", headers={"X-Forwarded-For": "8.8.8.8"})
        assert "kiosk_setup_code" not in proxied.json()
    async with make_client(base_url=LOOP, client=("192.168.1.20", 50000)) as lan:
        assert "kiosk_setup_code" not in (await lan.get("/api/auth/session")).json()

    async with make_client(base_url=LOOP) as client:
        response = await client.post(
            "/api/auth/setup", json={"username": ADMIN, "password": PASSWORD, "setup_code": code}
        )
        assert response.status_code == 200, response.text
        assert "kiosk_setup_code" not in (await client.get("/api/auth/session")).json()
    assert {u.username for u in await UserRepository.list_all()} == {"kiosk", ADMIN}


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_code_replaced_by_the_cli_is_no_longer_shown(marker):
    marker.touch()
    await _prepare()
    await setup_code.issue_setup_code()
    async with make_client(base_url=LOOP) as client:
        assert "kiosk_setup_code" not in (await client.get("/api/auth/session")).json()


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_plain_host_never_shows_a_code(marker):
    await _prepare(existing=True)
    async with make_client(base_url=LOOP) as client:
        session = (await client.get("/api/auth/session")).json()
    assert session["setup_code_required"] is True
    assert "kiosk_setup_code" not in session


def test_deb_ships_the_launcher_and_postinstall_marks_existing_kiosks():
    nfpm = (REPO / "pkg/nfpm/nfpm.yaml.tmpl").read_text(encoding="utf-8")
    assert "__RPIDIR__/meshloom-kiosk" in nfpm
    assert "dst: /usr/lib/meshloom/meshloom-kiosk" in nfpm
    post = (REPO / "pkg/nfpm/postinstall.sh").read_text(encoding="utf-8")
    assert "/etc/xdg/autostart/meshloom-kiosk.desktop" in post
    assert "/var/lib/meshloom/kiosk" in post
    build = (REPO / "scripts/build/build_rpi_kiosk_image.sh").read_text(encoding="utf-8")
    assert "/var/lib/meshloom/kiosk" in build


def _run_postinstall(tmp_path: Path, with_autostart: bool, with_setup: bool = False):
    root = tmp_path / "root"
    (root / "etc/xdg/autostart").mkdir(parents=True)
    (root / "usr/lib/meshloom").mkdir(parents=True)
    (root / "var/lib/meshloom").mkdir(parents=True)
    if with_autostart:
        (root / "etc/xdg/autostart/meshloom-kiosk.desktop").touch()
    if with_setup:
        (root / "usr/lib/meshloom/meshloom-kiosk-setup").touch()
    script = (REPO / "pkg/nfpm/postinstall.sh").read_text(encoding="utf-8")
    start = script.index("mark_kiosk_host()")
    end = script.index("\nmark_kiosk_host || true", start)
    func = script[start:end]
    func = func.replace("/etc/xdg", f"{root}/etc/xdg").replace("/usr/lib", f"{root}/usr/lib")
    func = func.replace("/var/lib", f"{root}/var/lib")
    subprocess.run(["sh", "-c", f"{func}\nmark_kiosk_host\nmark_kiosk_host"], check=True)
    return root / "var/lib/meshloom/kiosk"


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_postinstall_marker_follows_the_kiosk_files(tmp_path):
    assert not _run_postinstall(tmp_path / "plain", with_autostart=False).exists()
    assert _run_postinstall(tmp_path / "a", with_autostart=True).exists()
    assert _run_postinstall(tmp_path / "b", with_autostart=False, with_setup=True).exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_launcher_and_postinstall_parse():
    for rel in ("pkg/rpi/meshloom-kiosk", "pkg/nfpm/postinstall.sh"):
        subprocess.run(["sh", "-n", str(REPO / rel)], check=True)


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_code_is_refused_with_a_foreign_host_or_relay_marker(marker):
    marker.touch()
    await _prepare()
    async with make_client(base_url=LOOP) as client:
        assert "kiosk_setup_code" in (await client.get("/api/auth/session")).json()
        rebinding = await client.get("/api/auth/session", headers={"Host": "evil.example.com"})
        assert "kiosk_setup_code" not in rebinding.json()
        via = await client.get("/api/auth/session", headers={"Via": "1.1 tunnel"})
        assert "kiosk_setup_code" not in via.json()
