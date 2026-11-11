"""The installer served by get.meshloom.app/next (MSHL-466).

Runs the functions of scripts/setup/install.sh for real, with `as_root` replaced by a
plain call. The stable path is checked against its literal output.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "setup" / "install.sh"
STAGE = REPO / "scripts" / "build" / "stage_install_sh.sh"
RELEASE = REPO / ".github" / "workflows" / "release.yml"
PAGES = "https://twinrocket.github.io/meshloom"
KEYRING = "/usr/share/keyrings/meshloom-archive-keyring.gpg"
KEYRING_ASC = "/usr/share/keyrings/meshloom-archive-keyring.asc"
DIGEST = "sha256:" + "ab" * 32


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
INSTALLER_PRERELEASE=""
APT_SUITE=stable
RPM_PREFIX=""
PRERELEASE_DIGEST=""
ML_LANG=en
NEXT_URL="https://get.meshloom.app/next"
as_root() { "$@"; }
ui_err() { printf '%s\\n' "$1" >&2; }
t() { printf '%s' "$*"; }
on_rc() { INSTALLER_PRERELEASE=5.0.0-rc.1; APT_SUITE=testing; RPM_PREFIX=testing/; }
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


_SOURCE_STUBS = """
install_release_key() { :; }
_embed_apt_pin() { echo PIN; }
install() { :; }
mkdir() { :; }
chmod() { :; }
exec 3>&1
tee() { command cat >&3; }
"""


# ── the stable path is what it was ──────────────────────────────────────────


def test_the_file_in_the_repository_names_no_prerelease() -> None:
    assert '\nINSTALLER_PRERELEASE=""\n' in INSTALL_SH.read_text(encoding="utf-8")


def test_stable_writes_the_same_apt_source_as_before() -> None:
    out = _ok(("add_signed_repo",), f"PKG_MGR=apt\n{_SOURCE_STUBS}\nadd_signed_repo")
    assert out.strip() == f"deb [signed-by={KEYRING}] {PAGES}/apt stable main"


def test_stable_writes_the_same_dnf_source_as_before() -> None:
    out = _ok(("add_signed_repo",), f"PKG_MGR=dnf\n{_SOURCE_STUBS}\nadd_signed_repo")
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


def test_stable_docker_is_pinned_by_the_helper_as_before() -> None:
    body = _fn("install_compose_update_helper")
    assert "/usr/lib/meshloom/compose-update --bootstrap" in body
    assert body.index("INSTALLER_PRERELEASE") < body.index("--bootstrap")


def test_only_the_refusal_runs_on_a_stable_install() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    for name in (
        "prepare_prerelease",
        "resolve_prerelease_digest",
        "pin_prerelease_image",
        "install_prerelease_package",
    ):
        calls = list(re.finditer(rf"^[ \t]+{name}\b", text, re.MULTILINE))
        assert calls, name
        for match in calls:
            lines = text[: match.start()].splitlines()
            before = next(line for line in reversed(lines) if not line.lstrip().startswith("#"))
            assert 'if [ -n "$INSTALLER_PRERELEASE" ]' in before, name


def test_the_installer_fits_in_one_argument() -> None:
    """Linux refuses one argument of 128 KiB (MAX_ARG_STRLEN): the one-liner would not start.

    The limit below leaves room. When it is hit, shorten: never go past 131071.
    """
    assert len(INSTALL_SH.read_bytes()) < 126_000


# ── what an rc attaches ─────────────────────────────────────────────────────


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


def test_an_rc_attaches_the_file_with_only_its_version_line_changed(tmp_path: Path) -> None:
    assert _stage(tmp_path, "5.0.0-rc.1").returncode == 0
    staged = (tmp_path / "install.sh").read_text(encoding="utf-8").splitlines()
    source = INSTALL_SH.read_text(encoding="utf-8").splitlines()
    assert len(staged) == len(source)
    diff = [(a, b) for a, b in zip(source, staged, strict=True) if a != b]
    assert diff == [('INSTALLER_PRERELEASE=""', 'INSTALLER_PRERELEASE="5.0.0-rc.1"')]
    subprocess.run(["bash", "-n", str(tmp_path / "install.sh")], check=True)


def test_stage_leaves_no_partial_file_when_it_fails(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "sed").write_text("#!/bin/sh\necho partial\nexit 1\n")
    (stubs / "sed").chmod(0o755)
    output = tmp_path / "install.sh"
    result = subprocess.run(
        ["bash", str(STAGE), "--version", "5.0.0-rc.1", "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0
    assert list(tmp_path.glob("install.sh*")) == []


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


# ── moving to an rc ─────────────────────────────────────────────────────────


def test_an_rc_writes_the_testing_sources() -> None:
    apt = _ok(("add_signed_repo",), f"on_rc\nPKG_MGR=apt\n{_SOURCE_STUBS}\nadd_signed_repo")
    assert apt.strip() == f"deb [signed-by={KEYRING}] {PAGES}/apt testing main"
    dnf = _ok(("add_signed_repo",), f"on_rc\nPKG_MGR=dnf\n{_SOURCE_STUBS}\nadd_signed_repo")
    assert f"baseurl={PAGES}/rpm/testing/$basearch\n" in dnf
    assert "repo_gpgcheck=1" in dnf


def test_an_rc_targets_itself_and_not_the_latest_release() -> None:
    out = _ok(
        ("detect_target_version",),
        """
        on_rc
        INSTALL_MODE=service
        curl() { echo SHOULD-NOT-ASK; }
        detect_target_version
        echo "$TARGET_VERSION"
        """,
    )
    assert out.strip() == "5.0.0-rc.1"


def test_docker_without_the_helper_pins_the_rc_by_digest(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MESHLOOM_IMAGE=x:latest\nOTHER=1\n")
    _ok(
        ("write_unmanaged_env",),
        f"""
        on_rc
        PRERELEASE_DIGEST={DIGEST}
        write_unmanaged_env "{tmp_path}"
        """,
    )
    assert (tmp_path / ".env").read_text() == (
        f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:5.0.0-rc.1@{DIGEST}\nOTHER=1\n"
    )


def test_docker_with_the_helper_pins_the_rc(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "# Managed by meshloom-compose-update. x\nMESHLOOM_IMAGE=a\nB=1\n"
    )
    _ok(
        ("pin_prerelease_image",),
        f"""
        on_rc
        PRERELEASE_DIGEST={DIGEST}
        pin_prerelease_image "{tmp_path}"
        """,
    )
    assert (tmp_path / ".env").read_text() == (
        "# Managed by meshloom-compose-update. MESHLOOM_IMAGE is pinned by digest.\n"
        f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:5.0.0-rc.1@{DIGEST}\nB=1\n"
    )


def _pull(tmp_path: Path, pull_exit: int, managed: int = 1):
    (tmp_path / ".env").write_text("MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.2@old\nB=1\n")
    (tmp_path / "update-status").mkdir()
    (tmp_path / "update-status" / "status.json").write_text("{}")
    return _bash(
        (
            "save_env_before_prerelease",
            "pin_prerelease_image",
            "pull_prerelease_or_put_back",
        ),
        f"""
        on_rc
        PRERELEASE_DIGEST={DIGEST}
        INSTALL_LOG=/dev/null
        run_soft() {{ "$@"; }}
        enable_compose_update_helper() {{ echo ENABLED; }}
        fake_dc() {{ return {pull_exit}; }}
        cd "{tmp_path}"
        save_env_before_prerelease .
        pin_prerelease_image .
        pull_prerelease_or_put_back fake_dc {managed}
        """,
    )


def test_a_failed_pull_puts_the_old_pin_and_the_status_back(tmp_path: Path) -> None:
    result = _pull(tmp_path, 1)
    assert result.returncode == 1
    assert "prerelease_pull_failed" in result.stderr
    assert (
        (tmp_path / ".env")
        .read_text()
        .startswith("MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.2@old\n")
    )
    assert not (tmp_path / ".env.before-prerelease").exists()
    assert (tmp_path / "update-status" / "status.json").exists()
    assert "ENABLED" in result.stdout


def test_a_pull_that_works_keeps_the_pin_and_forgets_the_old_status(tmp_path: Path) -> None:
    result = _pull(tmp_path, 0)
    assert result.returncode == 0, result.stderr
    assert f"5.0.0-rc.1@{DIGEST}" in (tmp_path / ".env").read_text()
    assert not (tmp_path / ".env.before-prerelease").exists()
    assert not (tmp_path / "update-status" / "status.json").exists()


def test_docker_with_the_helper_never_bootstraps_on_an_rc() -> None:
    body = _fn("install_compose_update_helper")
    assert re.search(
        r'if \[ -n "\$INSTALLER_PRERELEASE" \]; then\n(?:\s*#[^\n]*\n)?\s+pin_prerelease_image',
        body,
    )
    assert re.search(r"elif ! run_soft [^\n]*--bootstrap", body)


def test_an_image_that_cannot_be_verified_stops_before_any_write() -> None:
    result = _bash(
        ("resolve_prerelease_digest",),
        """
        on_rc
        ensure_log() { :; }
        ensure_cmd() { :; }
        ensure_gpgv() { :; }
        prepare_release_key() { :; }
        signed_image_digest() { return 1; }
        resolve_prerelease_digest
        echo SHOULD-NOT-GET-HERE
        """,
    )
    assert result.returncode == 1
    assert "prerelease_image_failed" in result.stderr
    assert "SHOULD-NOT-GET-HERE" not in result.stdout
    body = _fn("install_docker_stack")
    assert body.index("resolve_prerelease_digest") < body.index('mkdir -p "$INSTALL_DIR"')


@pytest.mark.parametrize(
    ("arch", "manager", "message"),
    [
        ("armv7l", "apt", "prerelease_armhf"),
        ("x86_64", "", "prerelease_no_package"),
        ("riscv64", "apt", "prerelease_no_package"),
    ],
)
def test_a_service_install_refuses_what_has_no_package(arch, manager, message) -> None:
    result = _bash(
        ("host_arch", "prerelease_docs_url", "prepare_prerelease"),
        f"""
        on_rc
        INSTALL_MODE=service
        PKG_MGR={manager!r}
        uname() {{ echo {arch}; }}
        prepare_prerelease
        """,
    )
    assert result.returncode == 1
    assert message in result.stderr


def test_docker_on_armhf_is_still_possible() -> None:
    _ok(
        ("host_arch", "prerelease_docs_url", "prepare_prerelease"),
        """
        on_rc
        INSTALL_MODE=docker
        PKG_MGR=apt
        uname() { echo armv7l; }
        prepare_prerelease
        """,
    )


def test_a_prerelease_package_has_no_fallback() -> None:
    body = _fn("install_native_service")
    rc = body.index('if [ -n "$INSTALLER_PRERELEASE" ]')
    assert rc < body.index("pages_apt_has_host_arch")
    assert rc < body.index("install_from_release_asset")
    assert rc < body.index("run_service_from_source")
    assert "install_from_release_asset" not in _fn("install_prerelease_package")


# ── dnf: an installed meshloom is upgraded, never "installed" (dnf5 no-op) ────

_DNF_STUBS = """
dnf() { echo "dnf $*"; }
rpm() { [ "$2" = bubblewrap ] && [ -n "${HAS_BWRAP:-}" ] && return 0; [ "$2" = meshloom ] && [ -n "${HAS_MESHLOOM:-}" ]; }
run_soft() { "$@"; }
run_quiet() { "$@"; }
PKG_MGR=dnf
"""


@pytest.mark.parametrize(
    ("installed", "action"), [("1", "upgrade"), ("", "install")], ids=["installed", "absent"]
)
def test_dnf_upgrades_an_installed_meshloom(installed: str, action: str) -> None:
    out = _ok(
        ("dnf_meshloom",),
        f"{_DNF_STUBS}\nHAS_MESHLOOM={installed}\ndnf_meshloom",
    )
    assert out.splitlines() == [f"dnf -y --refresh {action} meshloom"]


def test_every_dnf_package_path_goes_through_dnf_meshloom() -> None:
    for name in (
        "install_from_pages",
        "install_prerelease_package",
        "_restore_package_update_helper",
    ):
        body = _fn(name)
        assert "dnf_meshloom" in body, name
        assert "dnf install -y meshloom" not in body, name


@pytest.mark.parametrize(("has", "expected"), [("1", []), ("", ["dnf -y install bubblewrap"])])
def test_bubblewrap_is_added_on_rpm_hosts_without_it(has: str, expected: list[str]) -> None:
    out = _ok(("ensure_bubblewrap_rpm",), f"{_DNF_STUBS}\nHAS_BWRAP={has}\nensure_bubblewrap_rpm")
    assert out.splitlines() == expected


def test_bubblewrap_is_left_alone_on_apt_hosts() -> None:
    out = _ok(("ensure_bubblewrap_rpm",), f"{_DNF_STUBS}\nPKG_MGR=apt\nensure_bubblewrap_rpm")
    assert out == ""


def test_the_summary_tells_to_copy_the_data_first_in_both_languages() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = _fn("confirm_install")
    assert body.index("upgrade_keeps_data") < body.index("prerelease_copy_first")
    for key in (
        "prerelease_copy_first",
        "prerelease_rerun",
        "prerelease_docs",
        "prerelease_above",
        "prerelease_older",
        "prerelease_armhf",
        "prerelease_no_package",
        "prerelease_image_failed",
    ):
        assert f"en:{key})" in text
        assert f"fr:{key})" in text


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


# ── the regular command on a machine on an rc ───────────────────────────────


@pytest.mark.parametrize(
    ("script_rc", "installed", "target", "message"),
    [
        ("", "5.0.0-rc.1", "4.18.2", "prerelease_above"),
        ("", "5.1.0-rc.1", "5.0.0", "prerelease_above"),
        ("", "5.0.0-rc.1", "5.0.0", ""),
        ("", "4.18.2", "4.18.2", ""),
        ("", "4.18.1", "4.18.2", ""),
        ("", "4.18.2", "4.18.1", ""),
        ("", "", "4.18.2", ""),
        ("5.0.0-rc.1", "5.0.0-rc.2", "5.0.0-rc.1", "prerelease_older"),
        ("5.0.0-rc.1", "5.0.0", "5.0.0-rc.1", "prerelease_older"),
        ("5.0.0-rc.2", "5.0.0-rc.1", "5.0.0-rc.2", ""),
        ("5.0.0-rc.1", "4.18.2", "5.0.0-rc.1", ""),
        ("", "edge", "4.18.2", ""),
        ("", "5.0.0-rc.1", "edge", ""),
    ],
)
def test_the_installer_stops_before_going_below_an_rc(
    script_rc: str, installed: str, target: str, message: str
) -> None:
    result = _bash(
        ("version_gt", "prerelease_docs_url", "refuse_going_below_a_prerelease"),
        f"""
        INSTALLER_PRERELEASE="{script_rc}"
        INSTALLED_VERSION="{installed}"
        TARGET_VERSION="{target}"
        refuse_going_below_a_prerelease
        """,
    )
    assert (result.returncode != 0) is bool(message), result.stderr
    assert message in result.stderr
    assert "integer expression" not in result.stderr


def test_the_refusal_comes_before_any_write() -> None:
    body = _fn("confirm_install")
    assert body.index("refuse_going_below_a_prerelease") < body.index("ui_screen")
    assert _fn("install_native_service").index("confirm_install") < _fn(
        "install_native_service"
    ).index("install_prerelease_package")
    docker = _fn("install_docker_stack")
    assert docker.index("confirm_install") < docker.index('mkdir -p "$INSTALL_DIR"')
