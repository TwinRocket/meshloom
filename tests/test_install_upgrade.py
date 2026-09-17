"""Installer upgrade detection and language persistence helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "setup" / "install.sh"

_HELPERS = (
    "ui_norm",
    "normalize_version",
    "is_installer_lang",
    "conf_get",
    "write_installer_conf",
    "read_project_version",
    "compose_image_version",
)


def _extract_fn(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    marker = f"{name}() {{"
    start = text.index(marker)
    brace_at = start + len(marker) - 1
    depth = 0
    for index, char in enumerate(text[brace_at:], start=brace_at):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unclosed function {name}")


def _bash(call: str) -> str:
    source = "\n\n".join(_extract_fn(name) for name in _HELPERS)
    result = subprocess.run(
        ["bash", "-c", f"{source}\n{call}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_normalize_version_strips_prefix_and_package_revision() -> None:
    assert _bash('normalize_version "v4.1.2"') == "4.1.2"
    assert _bash('normalize_version "V4.1.2-1"') == "4.1.2"
    assert _bash('normalize_version "4.1.2+abc"') == "4.1.2"


def test_read_project_version_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "4.1.2"\n', encoding="utf-8")
    assert _bash(f'read_project_version "{tmp_path}"') == "4.1.2"


def test_read_project_version_prefers_build_info(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.0.1"\n', encoding="utf-8")
    (tmp_path / "build_info.json").write_text('{"version": "4.2.0"}\n', encoding="utf-8")
    assert _bash(f'read_project_version "{tmp_path}"') == "4.2.0"


def test_compose_image_version_reads_tag(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("    image: ghcr.io/twinrocket/meshloom:4.1.2\n", encoding="utf-8")
    assert _bash(f'compose_image_version "{compose}"') == "4.1.2"


def test_rewrite_compose_image_tag_is_defined() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "rewrite_compose_image_tag() {" in text
    assert 'sub(/:[^[:space:]]+$/, ":" tag)' in text
    assert text.count('sub(/:[^[:space:]]+$/, ":" tag)') >= 2


def test_compose_image_version_ignores_latest(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("    image: ghcr.io/twinrocket/meshloom:latest\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"{_extract_fn('ui_norm')}\n{_extract_fn('normalize_version')}\n"
            f"{_extract_fn('compose_image_version')}\n"
            f'compose_image_version "{compose}"',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def test_installer_conf_round_trip(tmp_path: Path) -> None:
    dest = tmp_path / "installer.conf"
    _bash(f'write_installer_conf "{dest}" fr 4.1.2')
    assert _bash(f'conf_get "{dest}" lang') == "fr"
    assert _bash(f'conf_get "{dest}" version') == "4.1.2"


def test_is_installer_lang() -> None:
    assert _bash("is_installer_lang fr && echo yes") == "yes"
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"{_extract_fn('is_installer_lang')}\nis_installer_lang de",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def test_upgrade_confirm_messages_are_present() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "Upgrade from $1 to $2?" in text
    assert "Mettre à jour de $1 vers $2 ?" in text
    assert "installer.conf" in text
    assert "MESHLOOM_LANG" in text


def test_installer_writes_compose_kind_and_ensure_helper() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "MESHLOOM_INSTALL_KIND: compose" in text
    assert "MESHLOOM_UPDATE_HELPER: compose" in text
    assert "MESHLOOM_UPDATE_JOB_PATH: /app/data/update-job.json" in text
    assert "ensure_update_helper package" in text
    assert "ensure_update_helper compose" in text
    assert "meshloom-compose-update.path" in text
    assert "docker compose pull" in text
    assert "PathChanged=" in text
    assert "could not resolve image tag" in text
    assert "write_job applying restarting" in text
    assert "_install_package_update_helper_fallback" in text
    assert "MESHLOOM_INSTALL_KIND=package" in text
    assert "MESHCORE_DISABLE_BOTS" not in text
    assert "_env_ensure_key" in text
    assert "rewrite_compose_image_tag" in text
    assert 'sub(/:[^[:space:]]+$/, ":" tag)' in text
    assert "apt upgrade" not in text or "apt-get install" in text


def _bash_fns(names: tuple[str, ...], script: str) -> subprocess.CompletedProcess[str]:
    source = "\n\n".join(_extract_fn(name) for name in names)
    return subprocess.run(
        ["bash", "-c", f"{source}\n{script}"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_32_bit_raspberry_pi_is_named_rather_than_unknown() -> None:
    """Calling it unknown is what sent it down a path built for other machines."""
    for machine, expected in (
        ("x86_64", "amd64"),
        ("aarch64", "arm64"),
        ("armv7l", "armhf"),
        ("armv6l", "armhf"),
        ("riscv64", "unknown"),
    ):
        result = _bash_fns(
            ("host_arch",),
            f'uname() {{ [ "$1" = "-m" ] && echo "{machine}" || command uname "$@"; }}\nhost_arch',
        )
        assert result.stdout.strip() == expected, machine


def test_the_apt_repository_is_offered_only_where_it_has_packages() -> None:
    """The published repository holds amd64 and arm64.

    Offering it to a 32-bit Raspberry Pi added a source apt could not satisfy and
    skipped the source install, which does work there. Read from the Release file
    rather than hard-coded, so publishing armhf packages opens this on its own.
    """
    stub = (
        'PAGES_BASE="http://example.invalid"\n'
        "curl() { printf '%s\\n' 'Suite: stable' 'Architectures: amd64 arm64' 'Components: main'; }\n"
    )
    for machine, served in (("x86_64", True), ("aarch64", True), ("armv7l", False)):
        result = _bash_fns(
            ("host_arch", "pages_apt_has_host_arch"),
            stub
            + f'uname() {{ [ "$1" = "-m" ] && echo "{machine}" || command uname "$@"; }}\n'
            + "pages_apt_has_host_arch && echo served || echo skipped",
        )
        assert result.stdout.strip() == ("served" if served else "skipped"), machine


def test_the_build_script_knows_the_three_architectures() -> None:
    """armhf was rejected outright, which is why no package existed for a Pi."""
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "build" / "build_nfpm_packages.sh"
    ).read_text(encoding="utf-8")
    assert "armv7-unknown-linux-gnueabihf" in script
    # nFPM names it after the Go architecture and turns it into armhf for deb.
    assert 'NFPM_ARCH="arm7"' in script
    assert "__NFPM_ARCH__|$NFPM_ARCH" in script


def test_the_apt_repository_lists_what_it_holds() -> None:
    """The architecture list used to be written in three places.

    One of them missing armhf is a repository that offers itself to a machine it
    cannot serve, which is the failure this whole change is about.
    """
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-linux-repo.yml"
    ).read_text(encoding="utf-8")
    assert 'Architectures="amd64 arm64"' not in workflow
    assert "dpkg-deb -f" in workflow
