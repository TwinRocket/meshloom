"""The installer and the pre-release channel (MSHL-466).

Runs the functions of scripts/setup/install.sh for real, with `as_root` replaced by a
plain call. The stable path is checked against its literal output.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import time
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


def _copy(folder: Path, schema: int, version: str, written: int | None = None) -> Path:
    path = folder / f"meshcore.db.schema{schema}.before-{version}.db"
    connection = sqlite3.connect(path)
    connection.execute("create table t (v)")
    connection.execute("insert into t values ('old')")
    connection.execute(f"pragma user_version = {written if written is not None else schema}")
    connection.commit()
    connection.close()
    return path


_SELECT = (
    "find_database_copies",
    "copy_header_matches",
    "stable_schema",
    "select_way_back_copy",
)
_RESTORE = ("data_dir_owner", "as_owner", "restore_database", "undo_restore_database")


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
    _ok(_RESTORE, f'restore_database "{db}" "{copy}" "{aside}"')
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
    _ok(_RESTORE, f'restore_database "{db}" "{copy}" "{tmp_path}/aside"')
    assert (tmp_path / "secrets.key").read_text() == "5.0 key"


def test_restore_puts_the_key_back_when_the_copy_has_one(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    db.write_text("current")
    (tmp_path / "secrets.key").write_text("new key")
    copy = _copy(tmp_path, 144, "5.1.0")
    (tmp_path / "meshcore.db.schema144.before-5.1.0.secrets.key").write_text("old key")
    aside = tmp_path / "aside"
    _ok(_RESTORE, f'restore_database "{db}" "{copy}" "{aside}"')
    assert (tmp_path / "secrets.key").read_text() == "old key"
    assert (tmp_path / "aside.secrets.key").read_text() == "new key"


def test_restore_without_a_copy_leaves_no_database(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    db.write_text("current")
    _ok(_RESTORE, f'restore_database "{db}" "" "{tmp_path}/aside"')
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


# ── a planted link never reaches anything ───────────────────────────────────

_CHECK = (*_RESTORE, "return_paths_unsafe", "check_return_paths")


def _data_folder(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    data.mkdir()
    db = data / "meshcore.db"
    db.write_text("current")
    copy = _copy(data, 87, "5.0.0-rc.1")
    victim = tmp_path / "victim"
    victim.write_text("ORIGINAL")
    victim.chmod(0o644)
    return data, db, copy


@pytest.mark.parametrize(
    "planted",
    [
        "meshcore.db",
        "meshcore.db-wal",
        "meshcore.db-shm",
        "meshcore.db.migration-in-progress",
        "secrets.key",
        "meshcore.db.schema87.before-5.0.0-rc.1.secrets.key",
        "aside",
        "aside-wal",
        "aside-shm",
        "aside.secrets.key",
    ],
)
def test_a_planted_link_stops_the_return_with_nothing_changed(tmp_path: Path, planted: str) -> None:
    data, db, copy = _data_folder(tmp_path)
    victim = tmp_path / "victim"
    target = data / planted
    target.unlink(missing_ok=True)
    target.symlink_to(victim)
    before = sorted(p.name for p in data.iterdir())
    for script in (
        f'check_return_paths "{db}" "{copy}" "{data}/aside"',
        f'restore_database "{db}" "{copy}" "{data}/aside"',
    ):
        assert _bash(_CHECK, script).returncode != 0, script
    assert victim.read_text() == "ORIGINAL"
    assert victim.stat().st_mode & 0o777 == 0o644
    assert sorted(p.name for p in data.iterdir()) == before
    assert target.is_symlink()


def test_a_copy_that_is_a_link_is_not_a_copy(tmp_path: Path) -> None:
    data, db, copy = _data_folder(tmp_path)
    real = tmp_path / "elsewhere.db"
    copy.rename(real)
    copy.symlink_to(real)
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == ""
    assert _bash(_RESTORE, f'restore_database "{db}" "{copy}" "{data}/aside"').returncode != 0
    assert db.read_text() == "current"


def test_a_folder_that_is_a_link_is_refused(tmp_path: Path) -> None:
    data, db, copy = _data_folder(tmp_path)
    link = tmp_path / "linked"
    link.symlink_to(data)
    result = _bash(
        _CHECK, f'check_return_paths "{link}/meshcore.db" "{link}/{copy.name}" "{link}/aside"'
    )
    assert result.returncode == 1
    assert "return_unsafe" in result.stderr
    assert db.read_text() == "current"


def test_the_folder_is_handled_with_its_owner_rights_only() -> None:
    out = _ok(
        ("as_owner",),
        """
        setpriv() { printf '%s\\n' "setpriv $*"; }
        as_owner 12345 54321 sh -c true
        """,
    )
    assert out.strip() == "setpriv --reuid=12345 --regid=54321 --clear-groups -- sh -c true"
    source = _fn("restore_database") + _fn("undo_restore_database")
    assert source.count("as_owner $owner sh -c") == 2


def test_without_a_way_to_drop_rights_nothing_runs() -> None:
    result = _bash(
        ("as_owner",),
        """
        command() { return 1; }
        as_root() { echo RAN-AS-ROOT; }
        as_owner 12345 54321 sh -c true
        """,
    )
    assert result.returncode == 127
    assert "RAN-AS-ROOT" not in result.stdout


def test_a_failure_before_the_swap_changes_nothing(tmp_path: Path) -> None:
    data, db, copy = _data_folder(tmp_path)
    (data / "meshcore.db-wal").write_text("wal")
    copy.chmod(0)
    if os.access(copy, os.R_OK):
        pytest.skip("runs as root")
    result = _bash(_RESTORE, f'restore_database "{db}" "{copy}" "{data}/aside"')
    assert result.returncode != 0
    assert db.read_text() == "current"
    assert (data / "meshcore.db-wal").read_text() == "wal"
    assert not list(data.glob("*.restoring.*"))
    assert not (data / "aside").exists()
    # And undoing that does not touch the database still in place.
    _ok(_RESTORE, f'undo_restore_database "{db}" "{copy}" "{data}/aside"')
    assert db.read_text() == "current"


def test_undo_brings_back_what_was_running(tmp_path: Path) -> None:
    data, db, copy = _data_folder(tmp_path)
    (data / "meshcore.db-wal").write_text("wal")
    (data / "secrets.key").write_text("new key")
    copy.with_name(copy.name.removesuffix(".db") + ".secrets.key").write_text("old key")
    aside = data / "aside"
    _ok(_RESTORE, f'restore_database "{db}" "{copy}" "{aside}"')
    assert (data / "secrets.key").read_text() == "old key"
    _ok(_RESTORE, f'undo_restore_database "{db}" "{copy}" "{aside}"')
    assert db.read_text() == "current"
    assert (data / "meshcore.db-wal").read_text() == "wal"
    assert (data / "secrets.key").read_text() == "new key"
    assert not list(data.glob("aside*"))


# ── which copy, and whether it holds ────────────────────────────────────────


def test_a_file_without_the_sqlite_magic_is_not_a_copy(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    copy = _copy(tmp_path, 87, "5.0.0")
    data = bytearray(copy.read_bytes())
    data[:16] = b"not sqlite here\x00"
    copy.write_bytes(bytes(data))
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == ""


def test_the_most_recent_of_two_copies_of_a_schema_is_chosen(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    first = _copy(tmp_path, 87, "5.0.0-rc.2")
    second = _copy(tmp_path, 87, "5.0.0-rc.1")
    now = time.time()
    os.utime(first, (now - 100, now - 100))
    os.utime(second, (now, now))
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == str(second)
    os.utime(first, (now + 100, now + 100))
    assert _ok(_SELECT, f'select_way_back_copy "{db}" 4.18.2') == str(first)


_INTACT = ("data_dir_owner", "as_owner", "copy_is_intact")


def _large_copy(folder: Path) -> Path:
    copy = _copy(folder, 87, "5.0.0")
    connection = sqlite3.connect(copy)
    connection.executemany("insert into t values (?)", [("x" * 500,) for _ in range(200)])
    connection.commit()
    connection.close()
    return copy


def test_a_sound_copy_passes(tmp_path: Path) -> None:
    assert _bash(_INTACT, f'copy_is_intact "{_large_copy(tmp_path)}"').returncode == 0


def test_a_truncated_copy_is_refused(tmp_path: Path) -> None:
    copy = _large_copy(tmp_path)
    copy.write_bytes(copy.read_bytes()[:8192])
    assert _bash(_INTACT, f'copy_is_intact "{copy}"').returncode == 1


def test_a_truncated_copy_is_refused_with_no_sqlite_on_the_machine(tmp_path: Path) -> None:
    copy = _large_copy(tmp_path)
    copy.write_bytes(copy.read_bytes()[:8192])
    script = f'command() {{ return 1; }}\ncopy_is_intact "{copy}"'
    assert _bash(_INTACT, script).returncode == 1


def test_a_copy_damaged_inside_is_refused(tmp_path: Path) -> None:
    copy = _large_copy(tmp_path)
    data = bytearray(copy.read_bytes())
    data[4096:8192] = b"\xff" * 4096
    copy.write_bytes(bytes(data))
    assert _bash(_INTACT, f'copy_is_intact "{copy}"').returncode == 1


# ── the usual command on a machine on an rc ─────────────────────────────────


@pytest.mark.parametrize(
    ("channel", "installed", "target", "refused", "message"),
    [
        ("stable", "5.0.0-rc.1", "4.18.2", True, "return_refuse_newer"),
        ("stable", "5.1.0-rc.1", "5.0.0", True, "return_refuse_newer"),
        ("stable", "5.0.0-rc.1", "5.0.0", False, ""),
        ("stable", "4.18.2", "4.18.2", False, ""),
        ("stable", "4.18.1", "4.18.2", False, ""),
        ("stable", "4.18.2", "4.18.1", False, ""),
        ("stable", "", "4.18.2", False, ""),
        ("testing", "5.0.0-rc.2", "5.0.0-rc.1", True, "prerelease_older"),
        ("testing", "5.0.0-rc.1", "5.0.0-rc.2", False, ""),
    ],
)
def test_the_installer_stops_before_going_below_an_rc(
    channel: str, installed: str, target: str, refused: bool, message: str
) -> None:
    result = _bash(
        ("version_gt", "prerelease_return_command", "refuse_going_below_a_prerelease"),
        f"""
        CHANNEL={channel}
        INSTALLED_VERSION="{installed}"
        TARGET_VERSION="{target}"
        refuse_going_below_a_prerelease
        """,
    )
    assert (result.returncode != 0) is refused, result.stderr
    assert message in result.stderr


def test_the_refusal_gives_the_command_and_comes_before_any_write() -> None:
    result = _bash(
        ("version_gt", "prerelease_return_command", "refuse_going_below_a_prerelease"),
        """
        CHANNEL=stable
        INSTALLED_VERSION=5.0.0-rc.1
        TARGET_VERSION=4.18.2
        refuse_going_below_a_prerelease
        """,
    )
    assert "https://get.meshloom.app/next" in result.stderr
    assert "--channel stable" in result.stderr
    body = _fn("confirm_install")
    assert body.index("refuse_going_below_a_prerelease") < body.index("ui_screen")
    for name in ("install_native_service", "install_docker_stack"):
        text = _fn(name)
        assert text.index("confirm_install") < text.index(
            "install_prerelease_package" if name == "install_native_service" else "mkdir -p"
        )


def _migrated(path: Path, schema: int) -> None:
    connection = sqlite3.connect(path)
    connection.execute("create table if not exists t (v)")
    connection.execute(f"pragma user_version = {schema}")
    connection.commit()
    connection.close()


_ABOVE = ("database_schema", "stable_schema", "database_above_stable")


def test_the_schema_of_the_database_decides_too(tmp_path: Path) -> None:
    db = tmp_path / "meshcore.db"
    check = f'database_above_stable "{db}" 4.18.2'
    assert _bash(_ABOVE, check).returncode == 1, "no database"
    _migrated(db, 87)
    assert _bash(_ABOVE, check).returncode == 1
    _migrated(db, 144)
    assert _bash(_ABOVE, check).returncode == 0
    assert _bash(_ABOVE, f'database_above_stable "{db}" 5.0.0').returncode == 1, "unknown stable"
    db.write_text("not sqlite" * 20)
    assert _bash(_ABOVE, check).returncode == 1


def test_going_back_by_hand_points_where_the_steps_are() -> None:
    assert (
        _ok(("install_paths_docs_url",), "install_paths_docs_url")
        == "https://meshloom.app/en/docs/deep/install-paths/"
    )
    assert (
        _ok(("install_paths_docs_url",), "ML_LANG=fr; install_paths_docs_url")
        == "https://meshloom.app/docs/deep/install-paths/"
    )
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'return_manual "$(prerelease_docs_url)"' not in text
    for page in ("en", "fr"):
        doc = REPO / "docs" / "user" / page / "deep" / "install-paths.md"
        assert "schema" in doc.read_text(encoding="utf-8")


# ── the way back with Docker, with a stub for docker ────────────────────────


_COMPOSE_NAMES = (
    *_CHECK,
    "version_gt",
    "find_database_copies",
    "copy_header_matches",
    "database_schema",
    "stable_schema",
    "database_above_stable",
    "select_way_back_copy",
    "copy_is_intact",
    "write_unmanaged_env",
    "compose_pin_env",
    "compose_pull_stable",
    "compose_save_state",
    "compose_put_back",
    "compose_commit_stable",
    "return_compose",
)
_DIGEST = "sha256:" + "ab" * 32
_RC_PIN = "MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:5.0.0-rc.1@sha256:aa\n"
_STABLE_PIN = f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.2@{_DIGEST}"


def _compose_stack(
    tmp_path: Path,
    *,
    pull_exit: int = 0,
    digest_exit: int = 0,
    pin: str = _RC_PIN,
    schema: int = 144,
) -> tuple[Path, str]:
    stack = tmp_path / "stack"
    data = stack / "data"
    data.mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n")
    (stack / ".env").write_text(pin + "OTHER=1\n")
    _migrated(data / "meshcore.db", schema)
    _copy(data, 87, "5.0.0-rc.1")
    calls = tmp_path / "calls"
    fake = tmp_path / "fake_compose"
    fake.write_text(
        "#!/bin/bash\n"
        f"echo \"$1 env=${{MESHLOOM_IMAGE:-}} file=$(sed -n 's/^MESHLOOM_IMAGE=//p' .env) "
        f'schema=$(od -An -tu1 -j 62 -N 2 data/meshcore.db | awk \'{{print $1*256+$2}}\')" >> "{calls}"\n'
        f'[ "$1" != pull ] || exit {pull_exit}\n'
    )
    fake.chmod(0o755)
    stubs = f"""
    compose_cmd() {{ echo "{fake}"; }}
    compose_image_version() {{ sed -n 's/^MESHLOOM_IMAGE=.*meshloom:\\([^@]*\\).*/\\1/p' "$(dirname "$1")/.env"; }}
    compose_helper_possible() {{ return 1; }}
    return_screen() {{ :; }}
    run_quiet() {{ "$@"; }}
    run_soft() {{ "$@"; }}
    phase() {{ :; }}
    phase_ok() {{ :; }}
    ui_ok() {{ :; }}
    ui_wrap() {{ :; }}
    ui_yesno() {{ return 0; }}
    ensure_gpgv() {{ :; }}
    prepare_release_key() {{ :; }}
    signed_image_digest() {{ [ {digest_exit} = 0 ] && echo "{_DIGEST}"; }}
    persist_installer_state() {{ :; }}
    enable_compose_update_helper() {{ echo "enable" >> "{calls}"; }}
    """
    return stack, stubs


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def test_docker_pulls_before_writing_and_restores_while_stopped(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path)
    _ok(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    calls = _calls(tmp_path)
    assert [line.split()[0] for line in calls] == ["pull", "pull", "stop", "up"], calls
    assert f"env={_STABLE_PIN.split('=', 1)[1]}" in calls[0], "the signed image, by digest"
    assert "file=ghcr.io/twinrocket/meshloom:5.0.0-rc.1" in calls[0], ".env not touched yet"
    assert "schema=144" in calls[2], "the database is untouched until the image is there"
    assert "schema=87" in calls[3], "the container starts on the copy"
    assert list((stack / "data").glob("meshcore.db.aside-5.0.0-rc.1-*"))


def test_docker_changes_nothing_when_the_image_cannot_be_pulled(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path, pull_exit=1)
    before = (stack / ".env").read_text()
    result = _bash(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    assert result.returncode == 1
    assert "return_pull_failed" in result.stderr
    assert (stack / ".env").read_text() == before
    assert sqlite3.connect(stack / "data/meshcore.db").execute(
        "pragma user_version"
    ).fetchone() == (144,)
    assert [line.split()[0] for line in _calls(tmp_path)] == ["pull"]


def test_docker_changes_nothing_when_the_image_cannot_be_verified(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path, digest_exit=1)
    before = (stack / ".env").read_text()
    result = _bash(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    assert result.returncode == 1
    assert "return_image_unverified" in result.stderr
    assert (stack / ".env").read_text() == before
    assert _calls(tmp_path) == []


def _managed_stubs(tmp_path: Path, stack: Path, helper_exit: int) -> str:
    helper_env = tmp_path / "compose-update.env"
    helper_env.write_text("MESHLOOM_UPDATE_CHANNEL=testing\n")
    status = stack / "update-status"
    status.mkdir()
    (status / "status.json").write_text('{"version":"5.0.0-rc.1"}')
    return f"""
    compose_helper_possible() {{ return 0; }}
    install_compose_update_helper() {{
        echo "MESHLOOM_COMPOSE_DIR=x" > "{helper_env}"
        echo '{{"version":"4.18.2"}}' > "{status}/status.json"
        return {helper_exit}
    }}
    # The real path is outside the test's reach; the rest of the function is the real one.
    {_real_paths_to(helper_env)}
    """


def _real_paths_to(helper_env: Path) -> str:
    return f'as_root() {{ local a=(); for x in "$@"; do a+=("${{x//\\/etc\\/meshloom\\/compose-update.env/{helper_env}}}"); done; "${{a[@]}}"; }}'


def _helper_test_script(stubs: str, managed: str, stack: Path) -> str:
    # return_compose tests the real path with -f, so the managed run goes through a copy of it.
    body = _fn("return_compose").replace("[ -f /etc/meshloom/compose-update.env ] && ", "")
    return f'{stubs}\n{managed}\n{body}\nreturn_compose 4.18.2 "{stack}"'


def test_docker_puts_channel_and_status_back_when_the_helper_fails(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path)
    managed = _managed_stubs(tmp_path, stack, helper_exit=1)
    before = (stack / ".env").read_text()
    result = _bash(_COMPOSE_NAMES, _helper_test_script(stubs, managed, stack))
    assert result.returncode == 1, result.stderr
    assert "return_image_unverified" in result.stderr
    assert (stack / ".env").read_text() == before
    assert (tmp_path / "compose-update.env").read_text() == "MESHLOOM_UPDATE_CHANNEL=testing\n"
    assert (stack / "update-status/status.json").read_text() == '{"version":"5.0.0-rc.1"}'
    assert [line.split()[0] for line in _calls(tmp_path)] == ["pull", "enable"]


def test_docker_managed_pins_the_digest_then_restores(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path)
    managed = _managed_stubs(tmp_path, stack, helper_exit=0)
    result = _bash(_COMPOSE_NAMES, _helper_test_script(stubs, managed, stack))
    assert result.returncode == 0, result.stderr
    assert (stack / ".env").read_text() == f"{_STABLE_PIN}\nOTHER=1\n"
    calls = _calls(tmp_path)
    assert [line.split()[0] for line in calls] == ["pull", "stop", "up", "enable"], calls
    assert "schema=87" in calls[2]


def test_docker_on_a_stable_pin_over_a_migrated_database_still_restores(tmp_path: Path) -> None:
    """What the usual command of 4.18.2 leaves behind."""
    pin = "MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.2@sha256:aa\n"
    stack, stubs = _compose_stack(tmp_path, pin=pin)
    _ok(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    calls = _calls(tmp_path)
    assert "schema=87" in calls[-1], calls
    assert list((stack / "data").glob("meshcore.db.aside-4.18.2-*"))


def test_docker_with_no_pin_over_a_migrated_database_still_restores(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path, pin="")
    _ok(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    assert "schema=87" in _calls(tmp_path)[-1]
    assert list((stack / "data").glob("meshcore.db.aside-unknown-*"))


def test_docker_on_stable_with_a_stable_database_only_moves_the_channel(tmp_path: Path) -> None:
    pin = "MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.2@sha256:aa\n"
    stack, stubs = _compose_stack(tmp_path, pin=pin, schema=87)
    _ok(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    assert [line.split()[0] for line in _calls(tmp_path)] == ["pull", "pull", "up"]
    assert not list((stack / "data").glob("*aside*"))


def test_docker_refuses_a_damaged_copy_before_anything(tmp_path: Path) -> None:
    stack, stubs = _compose_stack(tmp_path)
    copy = next((stack / "data").glob("*.before-*.db"))
    data = bytearray(copy.read_bytes())
    data[28:32] = (99).to_bytes(4, "big")
    copy.write_bytes(bytes(data))
    result = _bash(_COMPOSE_NAMES, f'{stubs}\nreturn_compose 4.18.2 "{stack}"')
    assert result.returncode == 1
    assert "return_copy_damaged" in result.stderr
    assert _calls(tmp_path) == []


# ── the way back with a package, with stubs for the package manager ─────────


_PACKAGE_NAMES = (
    *_CHECK,
    "version_gt",
    "find_database_copies",
    "copy_header_matches",
    "database_schema",
    "stable_schema",
    "database_above_stable",
    "select_way_back_copy",
    "copy_is_intact",
    "package_source_file",
    "save_package_source",
    "restore_package_source",
    "switch_source_to_stable",
    "return_package_channel_only",
    "return_package",
    "return_package_undo",
)


def _package_host(
    tmp_path: Path,
    *,
    update_exit: int = 0,
    install_exit: int = 0,
    after: str = "5.0.0-rc.1",
    installed: str = "5.0.0-rc.1",
) -> tuple[Path, Path, str]:
    data = tmp_path / "data"
    data.mkdir()
    db = data / "meshcore.db"
    _migrated(db, 144)
    (data / "secrets.key").write_text("key")
    _copy(data, 87, "5.0.0-rc.1")
    source = tmp_path / "meshloom.list"
    source.write_text("testing\n")
    calls = tmp_path / "calls"
    state = tmp_path / "version"
    state.write_text(installed)
    stubs = f"""
    PKG_MGR=apt
    package_installed_version() {{ cat "{state}"; }}
    package_db_path() {{ echo "{db}"; }}
    package_source_file() {{ echo "{source}"; }}
    add_signed_repo() {{ echo stable > "{source}"; }}
    return_screen() {{ :; }}
    ensure_gpgv() {{ :; }}
    prepare_release_key() {{ :; }}
    fetch_stable_package() {{ touch "$2/f.deb"; echo "$2/f.deb"; }}
    run_soft() {{ "$@"; }}
    phase() {{ :; }}
    phase_ok() {{ :; }}
    ui_ok() {{ :; }}
    ui_wrap() {{ :; }}
    ui_yesno() {{ return 0; }}
    persist_installer_state() {{ :; }}
    start_meshloom_unit() {{ echo "start-unit" >> "{calls}"; }}
    systemctl() {{ echo "systemctl $1" >> "{calls}"; }}
    apt-get() {{
        case "$1" in
            update) echo "update" >> "{calls}"; return {update_exit} ;;
            *) echo "install" >> "{calls}"; echo "{after}" > "{state}"; return {install_exit} ;;
        esac
    }}
    """
    return data, source, stubs


def _pragma(db: Path) -> int:
    return sqlite3.connect(db).execute("pragma user_version").fetchone()[0]


def test_a_source_that_fails_is_put_back_before_anything_else(tmp_path: Path) -> None:
    data, source, stubs = _package_host(tmp_path, update_exit=1)
    result = _bash(_PACKAGE_NAMES, f"{stubs}\nreturn_package 4.18.2")
    assert result.returncode == 1
    assert "return_source_failed" in result.stderr
    assert source.read_text() == "testing\n"
    assert _pragma(data / "meshcore.db") == 144
    assert "systemctl stop" not in _calls(tmp_path)


def test_a_package_that_fails_puts_everything_back(tmp_path: Path) -> None:
    data, source, stubs = _package_host(tmp_path, install_exit=1)
    result = _bash(_PACKAGE_NAMES, f"{stubs}\nreturn_package 4.18.2")
    assert result.returncode == 1
    assert "return_rolled_back" in result.stderr
    assert source.read_text() == "testing\n"
    assert _pragma(data / "meshcore.db") == 144
    assert not list(data.glob("*aside*"))
    assert "systemctl start" in _calls(tmp_path)


def test_a_package_left_half_way_says_the_state_and_how_to_repair(tmp_path: Path) -> None:
    data, source, stubs = _package_host(tmp_path, install_exit=1, after="4.18.2")
    result = _bash(_PACKAGE_NAMES, f"{stubs}\nreturn_package 4.18.2")
    assert result.returncode == 1
    assert "return_midway" in result.stderr
    assert source.read_text() == "stable\n"
    assert _pragma(data / "meshcore.db") == 87
    assert list(data.glob("*aside*"))


def test_the_package_way_back_ends_on_the_copy(tmp_path: Path) -> None:
    data, source, stubs = _package_host(tmp_path, after="4.18.2")
    _ok(_PACKAGE_NAMES, f"{stubs}\nreturn_package 4.18.2")
    assert source.read_text() == "stable\n"
    assert _pragma(data / "meshcore.db") == 87
    calls = _calls(tmp_path)
    assert calls.index("systemctl stop") < calls.index("install") < calls.index("start-unit")


def test_a_stable_package_over_a_migrated_database_restores_without_a_package(
    tmp_path: Path,
) -> None:
    data, _, stubs = _package_host(tmp_path, installed="4.18.2", after="4.18.2")
    _ok(_PACKAGE_NAMES, f"{stubs}\nreturn_package 4.18.2")
    assert _pragma(data / "meshcore.db") == 87
    assert "install" not in _calls(tmp_path)


def test_a_damaged_copy_leaves_the_package_way_back_at_the_start(tmp_path: Path) -> None:
    data, source, stubs = _package_host(tmp_path)
    copy = next(data.glob("*.before-*.db"))
    raw = bytearray(copy.read_bytes())
    raw[28:32] = (99).to_bytes(4, "big")
    copy.write_bytes(bytes(raw))
    result = _bash(_PACKAGE_NAMES, f"{stubs}\nreturn_package 4.18.2")
    assert result.returncode == 1
    assert "return_copy_damaged" in result.stderr
    assert source.read_text() == "testing\n"
    assert _pragma(data / "meshcore.db") == 144
    assert _calls(tmp_path) == []
