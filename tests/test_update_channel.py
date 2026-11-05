"""The release channel: read from the system, never set by the app (MSHL-466)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services import oss_updates, update_channel
from app.services.oss_updates import (
    fetch_latest_release,
    get_update_status,
    is_newer_release,
    reset_oss_update_cache,
)
from app.services.update_channel import (
    ChannelRelease,
    detect_update_channel,
    fetch_channel_release,
    parse_channel_file,
    reset_channel_state,
)
from app.version_info import AppBuildInfo

OFFICIAL = "https://twinrocket.github.io/meshloom"
_REAL_CLIENT = httpx.AsyncClient
GITHUB_STABLE = {
    "version": "4.18.2",
    "html_url": "https://github.com/TwinRocket/meshloom/releases/tag/4.18.2",
    "source": "github",
}


def channel_text(version="5.0.0-rc.2", stable="4.18.2", issued_at=1762300000) -> str:
    return f"schema=1\nchannel=testing\nversion={version}\nstable={stable}\nissued_at={issued_at}\n"


@pytest.fixture(autouse=True)
def _clean():
    reset_oss_update_cache()
    reset_channel_state()
    yield
    reset_oss_update_cache()
    reset_channel_state()


@pytest.fixture
def system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake /etc, whose files count as root-owned."""
    (tmp_path / "apt").mkdir()
    monkeypatch.setattr(update_channel, "APT_SOURCES_DIR", tmp_path / "apt")
    monkeypatch.setattr(update_channel, "DNF_REPO_FILE", tmp_path / "meshloom.repo")
    monkeypatch.setattr(update_channel, "_is_root_only", lambda path: path.is_file())
    return tmp_path


def _list(system: Path, suite: str) -> None:
    (system / "apt" / "meshloom.list").write_text(
        f"deb [signed-by=/usr/share/keyrings/meshloom-archive-keyring.gpg] {OFFICIAL}/apt {suite} main\n"
    )


# ── which channel ───────────────────────────────────────────────────────────


def test_a_package_install_reads_the_apt_source(system: Path) -> None:
    _list(system, "stable")
    assert detect_update_channel("package", "4.18.2") == "stable"
    _list(system, "testing")
    assert detect_update_channel("package", "4.18.2") == "testing"


def test_the_deb822_form_of_the_source_counts(system: Path) -> None:
    (system / "apt" / "meshloom.sources").write_text(
        f"Types: deb\nURIs: {OFFICIAL}/apt\nSuites: testing\nComponents: main\n"
        "Signed-By: /usr/share/keyrings/meshloom-archive-keyring.gpg\n"
    )
    assert detect_update_channel("package", "5.0.0") == "testing"
    (system / "apt" / "meshloom.sources").write_text(
        f"Types: deb\nURIs: {OFFICIAL}/apt\nSuites: stable\nComponents: main\n\n"
        "Types: deb\nURIs: https://deb.example.org\nSuites: testing\nComponents: main\n"
    )
    assert detect_update_channel("package", "5.0.0") == "stable"


def test_a_package_install_reads_the_dnf_source(system: Path) -> None:
    repo = system / "meshloom.repo"
    repo.write_text(f"[meshloom]\nbaseurl={OFFICIAL}/rpm/$basearch\n")
    assert detect_update_channel("package", "4.18.2") == "stable"
    repo.write_text(f"[meshloom]\nbaseurl={OFFICIAL}/rpm/testing/$basearch\n")
    assert detect_update_channel("package", "4.18.2") == "testing"


def test_other_sources_and_comments_are_not_a_channel(system: Path) -> None:
    (system / "apt" / "debian.list").write_text("deb https://deb.debian.org/debian testing main\n")
    (system / "apt" / "meshloom.list").write_text(f"# deb {OFFICIAL}/apt testing main\n")
    assert detect_update_channel("package", "4.18.2") == "stable"


def test_a_package_rc_on_a_stable_source_is_on_stable(system: Path) -> None:
    """The version says what runs, the source says what comes next."""
    _list(system, "stable")
    assert detect_update_channel("package", "5.0.0-rc.1") == "stable"


def test_a_source_the_app_could_have_written_does_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails the day a file owned by the service user could choose the channel."""
    if os.geteuid() == 0:
        pytest.skip("must run unprivileged")
    (tmp_path / "apt").mkdir()
    monkeypatch.setattr(update_channel, "APT_SOURCES_DIR", tmp_path / "apt")
    monkeypatch.setattr(update_channel, "DNF_REPO_FILE", tmp_path / "meshloom.repo")
    (tmp_path / "apt" / "meshloom.list").write_text(f"deb {OFFICIAL}/apt testing main\n")
    (tmp_path / "meshloom.repo").write_text(f"baseurl={OFFICIAL}/rpm/testing/$basearch\n")
    assert detect_update_channel("package", "4.18.2") == "stable"


def test_the_addon_has_no_prerelease_path(system: Path) -> None:
    assert detect_update_channel("addon", "5.0.0-rc.1") == "stable"


def test_compose_with_the_helper_reads_its_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = tmp_path / "update-status" / "status.json"
    status.parent.mkdir()
    monkeypatch.setenv("MESHLOOM_UPDATE_STATUS_PATH", str(status))
    assert detect_update_channel("compose", "4.18.2") == "stable"
    status.write_text('{"schema":1,"state":"succeeded","channel":"testing"}')
    assert detect_update_channel("compose", "4.18.2") == "testing"
    status.write_text('{"schema":1,"state":"succeeded"}')
    assert detect_update_channel("compose", "5.0.0-rc.1") == "stable"
    status.write_text("not json")
    assert detect_update_channel("compose", "4.18.2") == "stable"


@pytest.mark.parametrize("kind", ["compose", "container", "source"])
def test_without_a_helper_the_running_version_says_it(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MESHLOOM_UPDATE_STATUS_PATH", raising=False)
    assert detect_update_channel(kind, "5.0.0-rc.1") == "testing"
    assert detect_update_channel(kind, "5.0.0") == "stable"
    assert detect_update_channel(kind, "0.0.0") == "stable"


# ── the signed file ─────────────────────────────────────────────────────────


def test_parse_channel_file_is_strict() -> None:
    assert parse_channel_file(channel_text()) == ChannelRelease("5.0.0-rc.2", "4.18.2", 1762300000)
    assert parse_channel_file(channel_text("4.18.2")) == ChannelRelease(
        "4.18.2", "4.18.2", 1762300000
    )
    for bad in (
        "",
        channel_text() + "x=1\n",
        channel_text("5.0.0-rc.0"),
        channel_text("5.0.0-beta.1"),
        channel_text("5.0.0-rc.1\nversion=9.9.9"),
        channel_text(stable="5.0.0-rc.1"),
        channel_text(issued_at="soon"),
        channel_text().replace("channel=testing", "channel=stable"),
    ):
        assert parse_channel_file(bad) is None, bad


needs_gpg = pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("gpgv") is None, reason="needs gpg and gpgv"
)


@pytest.fixture(scope="module")
def signer(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("gpg")
    home = base / "home"
    home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-gen-key",
            "t <t@example.invalid>",
            "ed25519",
            "sign",
            "never",
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    keyring = base / "keyring.gpg"
    keyring.write_bytes(
        subprocess.run(
            ["gpg", "--batch", "--export"], env=env, check=True, capture_output=True
        ).stdout
    )

    def sign(data: bytes) -> bytes:
        return subprocess.run(
            ["gpg", "--batch", "--armor", "--detach-sign"],
            input=data,
            env=env,
            check=True,
            capture_output=True,
        ).stdout

    return keyring, sign


def _serve(monkeypatch: pytest.MonkeyPatch, files: dict[str, tuple[int, bytes]]) -> list[str]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        status, body = files.get(str(request.url), (404, b""))
        return httpx.Response(status, content=body)

    def client(**kwargs):
        assert kwargs.get("follow_redirects") is False
        return _REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(update_channel.httpx, "AsyncClient", client)
    return calls


def _published(sign, text: str, url: str = update_channel.CHANNEL_FILE_URL):
    data = text.encode()
    return {url: (200, data), f"{url}.asc": (200, sign(data))}


@needs_gpg
async def test_a_signed_file_is_accepted(signer, monkeypatch: pytest.MonkeyPatch) -> None:
    keyring, sign = signer
    monkeypatch.setattr(update_channel, "SYSTEM_KEYRING", keyring)
    _serve(monkeypatch, _published(sign, channel_text()))
    assert (await fetch_channel_release()) == ChannelRelease("5.0.0-rc.2", "4.18.2", 1762300000)


@needs_gpg
async def test_a_file_tampered_after_signing_is_refused(
    signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyring, sign = signer
    monkeypatch.setattr(update_channel, "SYSTEM_KEYRING", keyring)
    files = _published(sign, channel_text())
    files[update_channel.CHANNEL_FILE_URL] = (200, channel_text("9.9.9-rc.1").encode())
    _serve(monkeypatch, files)
    assert await fetch_channel_release() is None


@needs_gpg
async def test_a_file_signed_by_another_key_is_refused(
    signer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _keyring, sign = signer
    home = tmp_path / "other"
    home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-gen-key",
            "o <o@example.invalid>",
            "ed25519",
            "sign",
            "never",
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    other = tmp_path / "other.gpg"
    other.write_bytes(
        subprocess.run(
            ["gpg", "--batch", "--export"], env=env, check=True, capture_output=True
        ).stdout
    )
    monkeypatch.setattr(update_channel, "SYSTEM_KEYRING", other)
    _serve(monkeypatch, _published(sign, channel_text()))
    assert await fetch_channel_release() is None


@needs_gpg
async def test_an_old_signed_copy_is_refused_after_a_newer_one(
    signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A withdrawn rc read as a lower version with a later date is fine; a replay is not."""
    keyring, sign = signer
    monkeypatch.setattr(update_channel, "SYSTEM_KEYRING", keyring)
    _serve(monkeypatch, _published(sign, channel_text("5.0.0-rc.3", issued_at=2000)))
    assert (await fetch_channel_release()).version == "5.0.0-rc.3"
    _serve(monkeypatch, _published(sign, channel_text("5.0.0-rc.1", issued_at=1000)))
    assert await fetch_channel_release() is None
    _serve(monkeypatch, _published(sign, channel_text("5.0.0-rc.2", issued_at=3000)))
    assert (await fetch_channel_release()).version == "5.0.0-rc.2"


async def test_without_gpgv_nothing_is_believed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_channel.shutil, "which", lambda _name: None)
    _serve(
        monkeypatch,
        {
            update_channel.CHANNEL_FILE_URL: (200, channel_text().encode()),
            f"{update_channel.CHANNEL_FILE_URL}.asc": (200, b"sig"),
        },
    )
    assert await fetch_channel_release() is None


async def test_a_missing_file_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, {})
    assert await fetch_channel_release() is None


# ── what the updater offers ─────────────────────────────────────────────────


def _github(payload=GITHUB_STABLE):
    return patch.object(oss_updates, "fetch_github_latest", new=AsyncMock(return_value=payload))


def _channel(channel: str):
    return patch.object(oss_updates, "current_channel", return_value=channel)


def _signed(release: ChannelRelease | None):
    return patch.object(oss_updates, "fetch_channel_release", new=AsyncMock(return_value=release))


def _running(version: str):
    return patch.object(
        oss_updates,
        "get_app_build_info",
        return_value=AppBuildInfo(
            version=version,
            version_source="test",
            commit_hash=None,
            commit_source=None,
        ),
    )


async def test_stable_never_reads_the_channel_file() -> None:
    with (
        _github(),
        _channel("stable"),
        _signed(ChannelRelease("5.0.0-rc.2", "4.18.2", 1)) as signed,
    ):
        payload = await fetch_latest_release()
    assert payload == GITHUB_STABLE
    signed.assert_not_awaited()


async def test_stable_makes_no_request_to_the_repository_site(monkeypatch) -> None:
    """The same, at the network: stable users ask GitHub and nothing else."""
    calls = _serve(monkeypatch, {})
    with _github(), _channel("stable"):
        await fetch_latest_release()
    assert calls == []


async def test_testing_offers_the_rc() -> None:
    with _github(), _channel("testing"), _signed(ChannelRelease("5.0.0-rc.2", "4.18.2", 1)):
        payload = await fetch_latest_release()
    assert payload["version"] == "5.0.0-rc.2"
    assert payload["source"] == "channel"
    assert payload["html_url"].endswith("/releases/tag/5.0.0-rc.2")


async def test_testing_offers_the_final_once_it_is_out() -> None:
    final = {**GITHUB_STABLE, "version": "5.0.0"}
    with _github(final), _channel("testing"), _signed(ChannelRelease("5.0.0", "5.0.0", 2)):
        assert (await fetch_latest_release())["version"] == "5.0.0"
    # A copy from before the final, still cached somewhere, does not hide it.
    with _github(final), _channel("testing"), _signed(ChannelRelease("5.0.0-rc.2", "4.18.2", 1)):
        assert (await fetch_latest_release())["version"] == "5.0.0"


async def test_testing_offers_the_next_rc_after_the_final() -> None:
    final = {**GITHUB_STABLE, "version": "5.0.0"}
    with _github(final), _channel("testing"), _signed(ChannelRelease("5.1.0-rc.1", "5.0.0", 3)):
        assert (await fetch_latest_release())["version"] == "5.1.0-rc.1"


async def test_testing_falls_back_to_stable_when_the_file_cannot_be_trusted() -> None:
    with _github(), _channel("testing"), _signed(None):
        assert await fetch_latest_release() == GITHUB_STABLE


async def test_testing_still_works_when_github_does_not_answer() -> None:
    with (
        _github(None),
        patch.object(oss_updates, "fetch_meshloom_latest", new=AsyncMock(return_value=None)),
        _channel("testing"),
        _signed(ChannelRelease("5.0.0-rc.2", "4.18.2", 1)),
    ):
        assert (await fetch_latest_release())["version"] == "5.0.0-rc.2"


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("5.0.0-rc.1", "4.18.2", True),
        ("5.0.0-rc.2", "5.0.0-rc.1", True),
        ("5.0.0-rc.10", "5.0.0-rc.9", True),
        ("5.0.0", "5.0.0-rc.9", True),
        ("5.0.0-rc.1", "5.0.0-rc.1", False),
        ("5.0.0-rc.1", "5.0.0-rc.2", False),
        ("5.0.0-rc.9", "5.0.0", False),
        ("4.18.3", "5.0.0-rc.1", False),
    ],
)
def test_versions_order(latest: str, current: str, newer: bool) -> None:
    assert is_newer_release(latest, current) is newer


async def test_a_machine_back_on_stable_stops_seeing_the_rc() -> None:
    with _github(), _channel("testing"), _signed(ChannelRelease("5.0.0-rc.2", "4.18.2", 1)):
        await oss_updates.refresh_oss_update_cache()
    with _running("5.0.0-rc.1"), _channel("testing"):
        status = get_update_status()
        assert status["latest"] == "5.0.0-rc.2"
        assert status["update_available"] is True
        assert status["channel"] == "testing"
    with _running("4.18.2"), _channel("stable"):
        status = get_update_status()
        assert status["latest"] is None
        assert status["update_available"] is False
        assert status["channel"] == "stable"


# ── nothing in the app moves a machine ──────────────────────────────────────


def test_api_shows_the_channel_and_no_route_changes_it() -> None:
    from app.routers import updates

    routes = {(m, r.path) for r in updates.router.routes for m in r.methods}
    assert routes == {
        ("GET", "/updates"),
        ("POST", "/updates/refresh"),
        ("POST", "/updates/apply"),
        ("PATCH", "/updates/settings"),
    }
    # The one route that takes a body has no channel in it.
    assert "channel" not in updates.UpdateSettingsPatch.model_fields
    assert "channel" in updates.UpdateStatusResponse.model_fields


def test_no_app_code_writes_where_the_channel_lives() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("sources.list.d", "yum.repos.d", "compose-update.env"):
            if needle in text:
                assert path.name == "update_channel.py", f"{path} mentions {needle}"
    source = (root / "services" / "update_channel.py").read_text(encoding="utf-8")
    for forbidden in (".write_text(APT", "unlink", "chmod", "rename(", "replace("):
        assert forbidden not in source


@pytest.mark.parametrize(
    "line",
    [
        "URIs: https://example.invalid/?https://twinrocket.github.io/meshloom/apt",
        "URIs: https://twinrocket.github.io.example.invalid/meshloom/apt",
        "URIs: https://twinrocket.github.io/meshloom/aptx",
    ],
)
def test_a_deb822_source_elsewhere_is_not_the_official_one(line: str) -> None:
    assert update_channel._DEB822_URI.search(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "URIs: https://twinrocket.github.io/meshloom/apt",
        "URIs: https://twinrocket.github.io/meshloom/apt/",
        "uris: https://example.invalid/x https://twinrocket.github.io/meshloom/apt",
    ],
)
def test_the_official_deb822_source_is_recognised(line: str) -> None:
    assert update_channel._DEB822_URI.search(line) is not None


def test_a_list_source_with_the_address_inside_another_is_not_official() -> None:
    assert (
        update_channel._OFFICIAL_APT.search(
            "deb [x] https://example.invalid/?https://twinrocket.github.io/meshloom/apt testing"
        )
        is None
    )
