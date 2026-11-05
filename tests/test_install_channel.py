"""The installer and the pre-release channel (MSHL-466).

Runs the functions of scripts/setup/install.sh for real, with `as_root` replaced by a
plain call. The stable path is checked against its literal output.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "setup" / "install.sh"
STAGE = REPO / "scripts" / "build" / "stage_install_sh.sh"
BUILD_REPO = REPO / "scripts" / "build" / "build_linux_repo.sh"
PUBLISH = REPO / ".github" / "workflows" / "publish-linux-repo.yml"
RELEASE = REPO / ".github" / "workflows" / "release.yml"
PAGES = "https://twinrocket.github.io/meshloom"
KEYRING = "/usr/share/keyrings/meshloom-archive-keyring.gpg"
KEYRING_ASC = "/usr/share/keyrings/meshloom-archive-keyring.asc"


def _fn(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = re.search(rf"^{name}\(\) \{{", text, re.MULTILINE)
    assert start, name
    end = text.index("\n}\n", start.start())
    return text[start.start() : end + 3]


# What the functions need from the top of the script, and a harmless as_root.
_PREAMBLE = """
set -euo pipefail
REPO="TwinRocket/meshloom"
PAGES_BASE="https://twinrocket.github.io/meshloom"
GHCR_IMAGE="ghcr.io/twinrocket/meshloom"
KEYRING_PATH=/usr/share/keyrings/meshloom-archive-keyring.gpg
KEYRING_ASC_PATH=/usr/share/keyrings/meshloom-archive-keyring.asc
INSTALLER_DEFAULT_CHANNEL=stable
CHANNEL=stable
CHANNEL_REQUESTED=""
APT_SUITE=stable
RPM_PREFIX=""
PRERELEASE_VERSION=""
ML_LANG=en
UI_COLOR=0
INSTALL_LOG=/dev/null
NEXT_URL="https://get.meshloom.app/next"
as_root() { "$@"; }
ui_err() { printf '%s\\n' "$1" >&2; }
t() { printf '%s' "$*"; }
"""


def _bash(names: tuple[str, ...], script: str, *, env: dict[str, str] | None = None):
    source = _PREAMBLE + "\n".join(_fn(n) for n in names)
    return subprocess.run(
        ["bash", "-c", f"{source}\n{script}"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def _ok(names: tuple[str, ...], script: str, **kwargs) -> str:
    result = _bash(names, script, **kwargs)
    assert result.returncode == 0, result.stderr
    return result.stdout


# ── the stable path is what it was ──────────────────────────────────────────


def test_the_file_in_the_repository_is_on_stable() -> None:
    assert "\nINSTALLER_DEFAULT_CHANNEL=stable\n" in INSTALL_SH.read_text(encoding="utf-8")


def test_stable_writes_the_same_apt_source_as_before(tmp_path: Path) -> None:
    out = _ok(
        ("add_signed_repo",),
        """
        PKG_MGR=apt
        install_release_key() { :; }
        _embed_apt_pin() { echo PIN; }
        install() { echo "install $*"; }
        mkdir() { :; }
        chmod() { :; }
        exec 3>&1
        tee() { echo "tee $*" >&3; command cat >&3; }
        add_signed_repo
        """,
    )
    assert f"deb [signed-by={KEYRING}] {PAGES}/apt stable main" in out
    assert "/etc/apt/sources.list.d/meshloom.list" in out


def test_stable_writes_the_same_dnf_source_as_before() -> None:
    out = _ok(
        ("add_signed_repo",),
        """
        PKG_MGR=dnf
        install_release_key() { :; }
        mkdir() { :; }
        chmod() { :; }
        exec 3>&1
        tee() { command cat >&3; }
        add_signed_repo
        """,
    )
    assert out == (
        "[meshloom]\nname=Meshloom\n"
        f"baseurl={PAGES}/rpm/$basearch\n"
        f"enabled=1\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=file://{KEYRING_ASC}\n"
    )


def test_stable_docker_still_follows_latest(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OTHER=1\nMESHLOOM_IMAGE=old\n")
    _ok(("write_unmanaged_env",), f'write_unmanaged_env "{tmp_path}"')
    assert (tmp_path / ".env").read_text() == (
        "MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:latest\nOTHER=1\n"
    )


def test_stable_reads_the_stable_release_file() -> None:
    out = _ok(
        ("host_arch", "pages_apt_has_host_arch"),
        """
        curl() { printf '%s\\n' "$*" >&2; echo "Architectures: amd64 arm64 armhf"; }
        uname() { echo x86_64; }
        pages_apt_has_host_arch && echo yes
        """,
    )
    assert out.strip() == "yes"


def test_a_stable_installer_without_arguments_stays_on_stable() -> None:
    out = _ok(
        ("set_channel", "parse_args"),
        'parse_args; echo "$CHANNEL $APT_SUITE [$RPM_PREFIX] [$CHANNEL_REQUESTED]"',
    )
    assert out.strip() == "stable stable [] []"


def test_the_stable_path_of_the_compose_helper_env_has_no_channel_line() -> None:
    body = _fn("install_compose_update_helper")
    assert re.search(
        r"""if \[ "\$CHANNEL" = "testing" \]; then\s+printf 'MESHLOOM_UPDATE_CHANNEL=testing""",
        body,
    )


def test_every_hardcoded_stable_left_in_the_installer_is_known() -> None:
    """A `stable` suite written in a URL would ignore the channel."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "/apt stable main" not in text
    assert "dists/stable" not in re.sub(
        r'.*http_ok "\$\{PAGES_BASE\}/apt/dists/stable/Release".*', "", text
    )


# ── an rc's copy of the script ──────────────────────────────────────────────


def _stage(tmp_path: Path, version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(STAGE), "--version", version, "--output", str(tmp_path / "install.sh")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_stable_release_attaches_the_file_byte_for_byte(tmp_path: Path) -> None:
    assert _stage(tmp_path, "5.0.0").returncode == 0
    assert (tmp_path / "install.sh").read_bytes() == INSTALL_SH.read_bytes()


def test_an_rc_attaches_the_file_with_only_the_channel_line_changed(tmp_path: Path) -> None:
    assert _stage(tmp_path, "5.0.0-rc.1").returncode == 0
    staged = (tmp_path / "install.sh").read_text(encoding="utf-8").splitlines()
    source = INSTALL_SH.read_text(encoding="utf-8").splitlines()
    assert len(staged) == len(source)
    diff = [(a, b) for a, b in zip(source, staged, strict=True) if a != b]
    assert diff == [("INSTALLER_DEFAULT_CHANNEL=stable", "INSTALLER_DEFAULT_CHANNEL=testing")]
    subprocess.run(["bash", "-n", str(tmp_path / "install.sh")], check=True)


@pytest.mark.parametrize(
    "version", ["", "latest", "5.0.0-rc.0", "5.0.0-beta.1", "5.0", "5.0.0-rc.1;x"]
)
def test_stage_refuses_anything_else(tmp_path: Path, version: str) -> None:
    assert _stage(tmp_path, version).returncode != 0
    assert not (tmp_path / "install.sh").exists()


def test_release_stages_the_installer_through_the_script() -> None:
    text = RELEASE.read_text(encoding="utf-8")
    assert "scripts/build/stage_install_sh.sh --version" in text
    assert "cp scripts/setup/install.sh artifacts/install.sh" not in text


@pytest.mark.parametrize(
    ("args", "default", "expected"),
    [
        ("", "testing", "testing testing [testing/] []"),
        ("--channel testing", "stable", "testing testing [testing/] [testing]"),
        ("--channel=testing", "stable", "testing testing [testing/] [testing]"),
        ("--channel stable", "testing", "stable stable [] [stable]"),
    ],
)
def test_channel_choice(args: str, default: str, expected: str) -> None:
    out = _ok(
        ("set_channel", "parse_args"),
        f'INSTALLER_DEFAULT_CHANNEL={default}; parse_args {args}; echo "$CHANNEL $APT_SUITE [$RPM_PREFIX] [$CHANNEL_REQUESTED]"',
    )
    assert out.strip() == expected


@pytest.mark.parametrize(
    "args", ["--channel beta", "--channel", "--chanel stable", "--channel=../x"]
)
def test_a_bad_option_stops_before_anything_happens(args: str) -> None:
    assert _bash(("set_channel", "parse_args"), f"parse_args {args}").returncode == 2


def test_testing_writes_the_testing_sources() -> None:
    apt = _ok(
        ("add_signed_repo", "set_channel"),
        """
        set_channel testing
        PKG_MGR=apt
        install_release_key() { :; }
        _embed_apt_pin() { echo PIN; }
        install() { :; }
        mkdir() { :; }
        chmod() { :; }
        exec 3>&1
        tee() { command cat >&3; }
        add_signed_repo
        """,
    )
    assert apt.strip() == f"deb [signed-by={KEYRING}] {PAGES}/apt testing main"
    dnf = _ok(
        ("add_signed_repo", "set_channel"),
        """
        set_channel testing
        PKG_MGR=dnf
        install_release_key() { :; }
        mkdir() { :; }
        chmod() { :; }
        exec 3>&1
        tee() { command cat >&3; }
        add_signed_repo
        """,
    )
    assert f"baseurl={PAGES}/rpm/testing/$basearch\n" in dnf
    assert "repo_gpgcheck=1" in dnf


def test_testing_docker_without_the_helper_pins_the_rc_by_digest(tmp_path: Path) -> None:
    digest = "sha256:" + "ab" * 32
    (tmp_path / ".env").write_text("MESHLOOM_IMAGE=x:latest\nOTHER=1\n")
    _ok(
        ("write_unmanaged_env", "set_channel"),
        f"""
        set_channel testing
        PRERELEASE_VERSION=5.0.0-rc.1
        signed_image_digest() {{ echo "{digest}"; }}
        write_unmanaged_env "{tmp_path}"
        """,
    )
    assert (tmp_path / ".env").read_text() == (
        f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:5.0.0-rc.1@{digest}\nOTHER=1\n"
    )


def test_testing_docker_refuses_an_unverifiable_image(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MESHLOOM_IMAGE=keep\n")
    result = _bash(
        ("write_unmanaged_env", "set_channel"),
        f"""
        set_channel testing
        PRERELEASE_VERSION=5.0.0-rc.1
        signed_image_digest() {{ return 1; }}
        write_unmanaged_env "{tmp_path}"
        """,
    )
    assert result.returncode == 1
    assert (tmp_path / ".env").read_text() == "MESHLOOM_IMAGE=keep\n"


@pytest.mark.parametrize(
    ("arch", "mode", "manager", "message"),
    [
        ("armv7l", "service", "apt", "prerelease_armhf"),
        ("x86_64", "service", "", "prerelease_no_package"),
        ("riscv64", "service", "apt", "prerelease_no_package"),
    ],
)
def test_prerelease_refuses_what_has_no_package(arch, mode, manager, message) -> None:
    result = _bash(
        ("set_channel", "host_arch", "prerelease_docs_url", "prepare_prerelease"),
        f"""
        set_channel testing
        INSTALL_MODE={mode}
        PKG_MGR={manager!r}
        ensure_log() {{ :; }}
        uname() {{ echo {arch}; }}
        ensure_cmd() {{ echo SHOULD-NOT-GET-HERE; }}
        prepare_prerelease
        """,
    )
    assert result.returncode == 1
    assert message in result.stderr
    assert "SHOULD-NOT-GET-HERE" not in result.stdout


def test_prerelease_on_armhf_can_still_use_docker() -> None:
    out = _ok(
        ("set_channel", "host_arch", "prerelease_docs_url", "prepare_prerelease"),
        """
        set_channel testing
        INSTALL_MODE=docker
        PKG_MGR=apt
        ensure_log() { :; }
        uname() { echo armv7l; }
        ensure_cmd() { :; }
        ensure_gpgv() { :; }
        prepare_release_key() { :; }
        testing_channel_version() { echo 5.0.0-rc.1; }
        prepare_prerelease
        echo "$PRERELEASE_VERSION"
        """,
    )
    assert out.strip() == "5.0.0-rc.1"


def test_prerelease_stops_clearly_when_there_is_none() -> None:
    result = _bash(
        ("set_channel", "host_arch", "prerelease_docs_url", "prepare_prerelease"),
        """
        set_channel testing
        INSTALL_MODE=service
        PKG_MGR=apt
        ensure_log() { :; }
        uname() { echo x86_64; }
        ensure_cmd() { :; }
        ensure_gpgv() { :; }
        prepare_release_key() { :; }
        testing_channel_version() { return 1; }
        prepare_prerelease
        """,
    )
    assert result.returncode == 1
    assert "prerelease_none" in result.stderr


def test_prerelease_package_has_no_fallback() -> None:
    body = _fn("install_native_service")
    testing = body.index('if [ "$CHANNEL" = "testing" ]')
    assert testing < body.index("pages_apt_has_host_arch")
    assert testing < body.index("install_from_release_asset")
    assert testing < body.index("run_service_from_source")
    assert "install_from_release_asset" not in _fn("install_prerelease_package")


def test_moving_to_the_prerelease_shows_the_way_back() -> None:
    confirm = _fn("confirm_install")
    assert 'prerelease_note "$(prerelease_docs_url)"' in confirm
    assert "print_way_back" in _fn("install_prerelease_package")
    assert "print_way_back" in _fn("install_docker_stack")


def test_print_way_back_names_the_copy(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    (tmp_path / "meshcore.db.schema87.before-5.0.0-rc.1.db").write_text("x")
    (tmp_path / "meshcore.db.schema144.before-5.0.0-rc.2.db").write_text("x")
    (tmp_path / "meshcore.db.copy-in-progress.lock").write_text("")
    out = _ok(
        ("prerelease_docs_url", "find_database_copies", "print_way_back"),
        f"""
        UPGRADE_KIND=upgrade
        ui_dim() {{ echo "$1"; }}
        print_way_back "{db}"
        """,
    )
    assert "meshcore.db.schema144" in out or "meshcore.db.schema87" in out
    assert "https://get.meshloom.app/next/docs/en" in out


# ── versions ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4.18.2", "4.18.2"),
        ("4.18.2-1", "4.18.2"),
        ("v4.18.2+abc", "4.18.2"),
        ("5.0.0-rc.1", "5.0.0-rc.1"),
        ("5.0.0~rc.1-1", "5.0.0-rc.1"),
        ("5.0.0~rc.10", "5.0.0-rc.10"),
    ],
)
def test_normalize_version(raw: str, expected: str) -> None:
    assert _ok(("normalize_version",), f'normalize_version "{raw}"') == expected


@pytest.mark.parametrize(
    ("a", "b", "greater"),
    [
        ("5.0.0-rc.1", "4.18.2", True),
        ("4.18.2", "5.0.0-rc.1", False),
        ("5.0.0", "5.0.0-rc.9", True),
        ("5.0.0-rc.9", "5.0.0", False),
        ("5.0.0-rc.10", "5.0.0-rc.9", True),
        ("5.0.0", "5.0.0", False),
        ("4.18.10", "4.18.2", True),
        ("5.1.0-rc.1", "5.0.0", True),
    ],
)
def test_version_gt(a: str, b: str, greater: bool) -> None:
    result = _bash(("version_gt",), f'version_gt "{a}" "{b}"')
    assert (result.returncode == 0) is greater, result.stderr


# ── the way back ────────────────────────────────────────────────────────────


def _sqlite_header(schema: int) -> bytes:
    header = bytearray(b"SQLite format 3\x00" + b"\x00" * 84)
    header[60:64] = schema.to_bytes(4, "big")
    return bytes(header)


def _copy(folder: Path, schema: int, version: str, written: int | None = None) -> Path:
    path = folder / f"meshcore.db.schema{schema}.before-{version}.db"
    path.write_bytes(_sqlite_header(written if written is not None else schema) + b"data")
    return path


_SELECT = ("find_database_copies", "copy_header_matches", "select_way_back_copy")


def test_the_copy_is_chosen_by_schema_not_by_version(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    want = _copy(tmp_path, 87, "5.0.0-rc.1")
    _copy(tmp_path, 150, "5.0.0-rc.2")
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == str(want)


def test_a_copy_of_an_older_schema_can_still_go_back(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    want = _copy(tmp_path, 80, "5.0.0")
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == str(want)


def test_a_copy_of_a_newer_schema_is_never_chosen(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    _copy(tmp_path, 144, "5.0.0-rc.2")
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == ""


def test_a_file_whose_contents_contradict_its_name_is_not_a_copy(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    _copy(tmp_path, 87, "5.0.0", written=144)
    (tmp_path / "meshcore.db.schema86.before-5.0.0.db").write_text("not sqlite")
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == ""


def test_an_unknown_stable_is_not_guessed(tmp_path: Path) -> None:
    result = _bash(_SELECT, f'select_way_back_copy "{tmp_path}/meshcore.db" 5.0.0')
    assert result.returncode == 2


def test_restore_puts_the_copy_back_and_keeps_the_current_one(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    db.write_text("current")
    (tmp_path / "meshcore.db-wal").write_text("wal")
    (tmp_path / "meshcore.db-shm").write_text("shm")
    (tmp_path / "meshcore.db.migration-in-progress").write_text("marker")
    lock = tmp_path / "meshcore.db.copy-in-progress.lock"
    lock.write_text("")
    copy = _copy(tmp_path, 87, "5.0.0")
    aside = tmp_path / "meshcore.db.aside-x"
    _ok(("restore_database",), f'restore_database "{db}" "{copy}" "{aside}"')
    assert db.read_bytes() == copy.read_bytes()
    assert copy.exists(), "the copy stays: it is the way back again"
    assert not (tmp_path / "meshcore.db-wal").exists()
    assert not (tmp_path / "meshcore.db-shm").exists()
    assert not (tmp_path / "meshcore.db.migration-in-progress").exists()
    assert lock.exists(), "harmless, and not ours to remove"
    assert aside.read_text() == "current"
    assert (tmp_path / "meshcore.db.aside-x-wal").read_text() == "wal"
    assert aside.stat().st_mode & 0o777 == 0o600


def test_restore_keeps_secrets_key_when_the_copy_has_none(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    db.write_text("current")
    (tmp_path / "secrets.key").write_text("5.0 key")
    copy = _copy(tmp_path, 87, "5.0.0")
    _ok(("restore_database",), f'restore_database "{db}" "{copy}" "{tmp_path}/aside"')
    assert (tmp_path / "secrets.key").read_text() == "5.0 key"


def test_restore_puts_the_key_back_when_the_copy_has_one(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    db.write_text("current")
    (tmp_path / "secrets.key").write_text("new key")
    copy = _copy(tmp_path, 144, "5.1.0")
    (tmp_path / "meshcore.db.schema144.before-5.1.0.secrets.key").write_text("old key")
    aside = tmp_path / "aside"
    _ok(("restore_database",), f'restore_database "{db}" "{copy}" "{aside}"')
    assert (tmp_path / "secrets.key").read_text() == "old key"
    assert (tmp_path / "aside.secrets.key").read_text() == "new key"


def test_restore_without_a_copy_leaves_no_database(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    db.write_text("current")
    _ok(("restore_database",), f'restore_database "{db}" "" "{tmp_path}/aside"')
    assert not db.exists()
    assert (tmp_path / "aside").read_text() == "current"


def _fake_release(tmp_path: Path, names: list[str]) -> str:
    lines = "\n".join(f"{'0' * 64}  {n}" for n in names)
    return f"""
    fetch_signed_manifest() {{ printf '%s\\n' "{lines}" > "$2/SHA256SUMS"; }}
    run_soft() {{ shift; while [ $# -gt 0 ]; do case "$1" in -o) printf 'x' > "$2"; shift;; esac; shift; done; }}
    pkg_file_is_valid() {{ :; }}
    sha256_of() {{ printf '%064d' 0; }}
    """


def test_the_old_package_comes_from_the_signed_release_assets(tmp_path: Path) -> None:
    names = [
        "install.sh",
        "meshloom-4.18.2-1.aarch64.rpm",
        "meshloom-4.18.2-1.x86_64.rpm",
        "meshloom_4.18.2-1_amd64.deb",
        "meshloom_4.18.2-1_arm64.deb",
        "meshloom_4.18.2-1_armhf.deb",
    ]
    for manager, arch, ext in (("apt", "aarch64", "deb"), ("dnf", "x86_64", "rpm")):
        out = _ok(
            ("host_arch", "rpm_arch", "fetch_stable_package"),
            _fake_release(tmp_path, names)
            + f"""
            PKG_MGR={manager}
            uname() {{ echo {arch}; }}
            fetch_stable_package 4.18.2 "{tmp_path}"
            """,
        )
        assert out == f"{tmp_path}/meshloom.{ext}"


def test_a_checksum_mismatch_stops_the_way_back(tmp_path: Path) -> None:
    result = _bash(
        ("host_arch", "rpm_arch", "fetch_stable_package"),
        _fake_release(tmp_path, ["meshloom_4.18.2-1_amd64.deb"])
        + f"""
        sha256_of() {{ printf '%064d' 1; }}
        PKG_MGR=apt
        uname() {{ echo x86_64; }}
        fetch_stable_package 4.18.2 "{tmp_path}"
        """,
    )
    assert result.returncode != 0


def test_stable_release_tag_is_strict() -> None:
    for location, tag in (
        ("https://github.com/TwinRocket/meshloom/releases/tag/4.18.2", "4.18.2"),
        ("https://github.com/TwinRocket/meshloom/releases/tag/5.0.0-rc.1", None),
        ("https://evil.example/releases/tag/4.18.2", None),
        ("", None),
    ):
        result = _bash(
            ("stable_release_tag",),
            f"""
            curl() {{ printf '%s' "{location}"; }}
            stable_release_tag
            """,
        )
        assert (result.stdout if result.returncode == 0 else None) == tag


# ── the channel file ────────────────────────────────────────────────────────


def test_publication_signs_and_verifies_the_channel_file() -> None:
    text = PUBLISH.read_text(encoding="utf-8")
    sign = text.index("public/channels/testing.txt.asc public/channels/testing.txt")
    assert "sign --detach-sign -a -o public/channels/testing.txt.asc" in text
    assert sign < text.index("- name: Erase the key")
    verify = text.index('gpgv --keyring "$K" public/channels/testing.txt.asc')
    assert verify > text.index("- name: Verify published signatures")


@pytest.mark.skipif(shutil.which("gpgv") is None, reason="needs gpgv")
def test_testing_channel_version_believes_only_the_signature(tmp_path: Path) -> None:
    """Real gpgv with a throwaway key; the curl stub serves the two files."""
    if shutil.which("gpg") is None:
        pytest.skip("needs gpg")
    home = tmp_path / "home"
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
    key = tmp_path / "key"
    key.mkdir()
    (key / "meshloom.gpg").write_bytes(
        subprocess.run(
            ["gpg", "--batch", "--export"], env=env, check=True, capture_output=True
        ).stdout
    )
    served = tmp_path / "served"
    served.mkdir()

    def publish(text: str, sign_text: str | None = None) -> None:
        (served / "testing.txt").write_text(sign_text or text)
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--yes",
                "--armor",
                "--detach-sign",
                "-o",
                str(served / "testing.txt.asc"),
                str(served / "testing.txt"),
            ],
            env=env,
            check=True,
            capture_output=True,
        )
        (served / "testing.txt").write_text(text)

    def run() -> subprocess.CompletedProcess[str]:
        return _bash(
            ("run_soft", "ensure_log", "testing_channel_version"),
            f"""
            CHANNEL_FILE_URL=https://x/testing.txt
            RELEASE_KEY_DIR="{key}"
            curl() {{ local o; while [ $# -gt 0 ]; do case "$1" in -o) o=$2; shift;; https://*) u=$1;; esac; shift; done; cp "{served}/${{u##*/}}" "$o"; }}
            testing_channel_version
            """,
        )

    good = "schema=1\nchannel=testing\nversion=5.0.0-rc.1\nstable=4.18.2\nissued_at=1\n"
    publish(good)
    assert run().stdout == "5.0.0-rc.1"
    publish(good.replace("rc.1", "rc.9"), sign_text=good)
    assert run().returncode != 0
    publish(good.replace("5.0.0-rc.1", "4.18.2"))
    assert run().returncode != 0, "no rc in the file: nothing to install from it"
    publish(good.replace("channel=testing", "channel=stable"))
    assert run().returncode != 0


# ── the way back with Docker, with a stub for docker ────────────────────────


_COMPOSE_NAMES = (
    "version_gt",
    "find_database_copies",
    "copy_header_matches",
    "select_way_back_copy",
    "restore_database",
    "write_unmanaged_env",
    "return_compose",
)


def _compose_stack(tmp_path: Path, *, pull_exit: int = 0) -> tuple[Path, str]:
    stack = tmp_path / "stack"
    (stack / "data").mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n")
    (stack / ".env").write_text("MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:5.0.0-rc.1@sha256:aa\n")
    (stack / "data" / "meshcore.db").write_text("rc database")
    _copy(stack / "data", 87, "5.0.0-rc.1")
    calls = tmp_path / "calls"
    stubs = f"""
    compose_cmd() {{ echo fake_compose; }}
    fake_compose() {{
        echo "$1 db=$(cut -c1-2 data/meshcore.db) image=$(sed -n 's/^MESHLOOM_IMAGE=//p' .env)" >> "{calls}"
        [ "$1" != pull ] || return {pull_exit}
    }}
    compose_image_version() {{ echo 5.0.0-rc.1; }}
    compose_helper_possible() {{ return 1; }}
    return_screen() {{ :; }}
    run_quiet() {{ "$@"; }}
    run_soft() {{ "$@"; }}
    phase() {{ :; }}
    phase_ok() {{ :; }}
    ui_ok() {{ :; }}
    ui_wrap() {{ :; }}
    ui_yesno() {{ return 0; }}
    persist_installer_state() {{ :; }}
    """
    return stack, stubs


def test_docker_goes_back_pulling_first_and_restoring_while_stopped(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path)
    _ok(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    calls = (tmp_path / "calls").read_text().splitlines()
    verbs = [line.split()[0] for line in calls]
    assert verbs == ["pull", "stop", "up"], calls
    assert all("image=ghcr.io/twinrocket/meshloom:latest" in line for line in calls), calls
    assert "db=rc" in calls[0], "the database is untouched until the image is there"
    assert "db=SQ" in calls[2], "the container starts on the copy"
    assert (stack / ".env").read_text() == "MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:latest\n"
    assert list((stack / "data").glob("meshcore.db.aside-5.0.0-rc.1-*"))


def test_docker_changes_nothing_when_the_stable_image_cannot_be_pulled(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path, pull_exit=1)
    before = (stack / ".env").read_text()
    result = _bash(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    assert result.returncode == 1
    assert (stack / ".env").read_text() == before
    assert (stack / "data" / "meshcore.db").read_text() == "rc database"
    assert [line.split()[0] for line in (tmp_path / "calls").read_text().splitlines()] == ["pull"]
