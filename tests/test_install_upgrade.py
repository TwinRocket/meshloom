"""Installer upgrade detection and language persistence helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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


def test_compose_yaml_is_never_rewritten() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "rewrite_compose_image_tag" not in text
    assert 'sub(/:[^[:space:]]+$/, ":" tag)' not in text
    assert "awk -v tag" not in text


def test_compose_image_version_reads_env_pin(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("    image: ${MESHLOOM_IMAGE:?pin}\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.0@sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    assert _bash(f'compose_image_version "{compose}"') == "4.18.0"


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
    assert "MESHLOOM_UPDATE_STATUS_PATH: /app/update-status/status.json" in text
    assert "./update-status:/app/update-status:ro" in text
    assert "image: \\${MESHLOOM_IMAGE:?" in text
    assert "ensure_update_helper" in text
    assert "install_compose_update_helper" in text
    assert "meshloom-compose-update.path" in text
    assert "compose-update --bootstrap" in text
    assert "docker-compose.yml.bak-" in text
    assert "MESHLOOM_INSTALL_KIND=package" in text
    assert "MESHCORE_DISABLE_BOTS" not in text
    assert "_env_ensure_key" in text
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
        # A Pi 1 and the original Zero are ARMv6; the armhf package is ARMv7.
        ("armv6l", "unknown"),
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


def _build_script_error(args: list[str]) -> str:
    """Run the packaging script with neither tool on PATH and return what it says."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "build" / "build_nfpm_packages.sh"
    result = subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )
    return result.stderr


def test_each_half_asks_only_for_the_tool_it_uses() -> None:
    """Assembly happens in an emulated armv7 container, where nFPM does not exist.

    Asking for it there would fail the armhf build, and only a release runs that
    path, so nothing else would have noticed.
    """
    staging = _build_script_error(
        ["--version", "9.9.9", "--arch", "armhf", "--stage-dir", "/tmp/x", "--stage-only"]
    )
    assert "nFPM is required" not in staging

    deps = _build_script_error(
        ["--version", "9.9.9", "--arch", "armhf", "--stage-dir", "/tmp/x", "--deps-only"]
    )
    assert "nFPM is required" not in deps
    assert "uv is required" in deps

    skip_deps = _build_script_error(
        [
            "--version",
            "9.9.9",
            "--arch",
            "armhf",
            "--stage-dir",
            "/tmp/x",
            "--skip-deps",
            "--skip-frontend",
        ]
    )
    assert "nFPM is required" not in skip_deps
    assert "uv is required" not in skip_deps

    packaging = _build_script_error(
        ["--version", "9.9.9", "--arch", "armhf", "--stage-dir", "/tmp/x", "--package-only"]
    )
    assert "nFPM is required" in packaging


def test_the_exclusive_options_are_refused_together() -> None:
    exclusive_pairs = (
        ("--stage-only", "--package-only"),
        ("--deps-only", "--package-only"),
        ("--skip-deps", "--package-only"),
        ("--deps-only", "--skip-deps"),
        ("--deps-only", "--stage-only"),
        ("--skip-deps", "--stage-only"),
    )
    for left, right in exclusive_pairs:
        error = _build_script_error(
            ["--version", "9.9.9", "--arch", "amd64", left, right, "--stage-dir", "/tmp/x"]
        )
        assert "exclusive" in error, (left, right)


def test_deps_split_needs_a_stage_dir() -> None:
    deps = _build_script_error(["--version", "9.9.9", "--arch", "amd64", "--deps-only"])
    skip = _build_script_error(["--version", "9.9.9", "--arch", "amd64", "--skip-deps"])
    assert "stage-dir" in deps
    assert "stage-dir" in skip


def test_skip_deps_does_not_redownload_python() -> None:
    """The cached layer is the compiled tree; --skip-deps must not fetch it again."""
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "build" / "build_nfpm_packages.sh"
    ).read_text(encoding="utf-8")
    skip_at = script.index('elif [ "$SKIP_DEPS" -eq 1 ]; then')
    else_at = script.index("\nelse\n", skip_at)
    skip_block = script[skip_at:else_at]
    assert "download_standalone_python" not in skip_block
    assert "copy_project_files" in skip_block


def test_armhf_workflow_caches_neutralized_deps() -> None:
    """A version bump must not recompile the armhf tree; the cache key is the lock."""
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile.nfpm-armhf").read_text(
        encoding="utf-8"
    )
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "nfpm-armhf.yml"
    ).read_text(encoding="utf-8")
    assert "neutralize_project_version.py" in dockerfile
    assert "--deps-only" in dockerfile
    assert "--skip-deps" in dockerfile
    assert "buildcache-nfpm-armhf" in workflow
    assert "type=local,dest=stage-armhf" in workflow
    assert "docker create" not in workflow
    release = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    assert "gh workflow run nfpm-armhf.yml" in release
    assert "linux/arm/v7" not in release
    nfpm_pr = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "nfpm.yml"
    ).read_text(encoding="utf-8")
    assert "--deps-only" in nfpm_pr
    assert "--skip-deps" in nfpm_pr
    assert "linux/arm/v7" not in nfpm_pr


def test_packaging_works_from_a_relative_stage_dir(tmp_path: Path) -> None:
    """The assembly cds into the tree, so a relative path stops resolving there.

    This is the half a release runs for armhf and nothing else exercises, so it
    is checked here with nFPM stubbed rather than discovered when publishing.
    """
    script = Path(__file__).resolve().parents[1] / "scripts" / "build" / "build_nfpm_packages.sh"

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "nfpm-calls"
    nfpm = fake_bin / "nfpm"
    nfpm.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n', encoding="utf-8")
    nfpm.chmod(0o755)

    interpreter = tmp_path / "stage" / "opt" / "meshloom" / "python" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(script),
            "--version",
            "9.9.9",
            "--arch",
            "armhf",
            "--stage-dir",
            "stage",
            "--package-only",
            "--output-dir",
            "out",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    invocations = calls.read_text(encoding="utf-8")
    assert "--packager deb" in invocations
    # Raspberry Pi OS is Debian, and no distribution ships an armv7 rpm any more.
    assert "--packager rpm" not in invocations


def test_a_release_asks_the_site_to_rebuild() -> None:
    """meshloom.app pulls docs/user/ from the latest X.Y.Z tag when it builds.

    Nothing here used to build it, so a correction stayed invisible until
    someone redeployed the site by hand. Rebuilding on a push to main does not
    help either: the site would only pull the previous tag again. The release
    workflow calls docs-site.yml after `publish`, because a release published
    with the GITHUB_TOKEN never triggers on.release.
    """
    workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    site = (workflows / "docs-site.yml").read_text(encoding="utf-8")
    assert "DOCS_SITE_DEPLOY_HOOK" in site
    assert "workflow_call:" in site
    assert "workflow_dispatch:" in site
    assert "docs/user/**" not in site

    release = (workflows / "release.yml").read_text(encoding="utf-8")
    job = release.split("\n  docs-site:\n", 1)[1].split("\n\n", 1)[0]
    assert "needs: [publish]" in job
    assert "uses: ./.github/workflows/docs-site.yml" in job
    assert "DOCS_SITE_DEPLOY_HOOK: ${{ secrets.DOCS_SITE_DEPLOY_HOOK }}" in job


# What the 4.17 installer wrote (write_docker_compose + its tag rewrite).
_COMPOSE_417 = """# Generated by Meshloom install.sh
services:
  meshloom:
    image: ghcr.io/twinrocket/meshloom:4.17.0
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      # Host D-Bus socket (BlueZ). Extra caps such as NET_ADMIN may still be needed for BLE.
      - /run/dbus/system_bus_socket:/run/dbus/system_bus_socket:ro
    devices:
      - /dev/ttyACM0:/dev/meshcore-radio
    environment:
      MESHCORE_DATABASE_PATH: "data/meshcore.db"
      MESHLOOM_INSTALL_KIND: compose
      MESHLOOM_UPDATE_HELPER: compose
      MESHLOOM_UPDATE_JOB_PATH: /app/data/update-job.json
    restart: unless-stopped
"""

_COMPOSE_FNS = (
    "yaml_quote",
    "write_docker_compose",
    "read_compose_mappings",
    "normalize_generated_compose",
    "compose_is_installer_generated",
)


def _compose_check(path: Path) -> subprocess.CompletedProcess[str]:
    return _bash_fns(
        _COMPOSE_FNS,
        't() { echo "$1"; }\nui_warn() { echo "$1" >&2; }\n'
        "SERIAL_COMPOSE_HOST_PATH=; DBUS_SOCKET=; TRANSPORT=; SERIAL_PORT=\n"
        f'read_compose_mappings "{path}"\n'
        f'compose_is_installer_generated "{path}" && echo "same $SERIAL_COMPOSE_HOST_PATH $DBUS_SOCKET"',
    )


def test_a_417_compose_file_is_recognised_and_keeps_its_mappings(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_COMPOSE_417, encoding="utf-8")
    result = _compose_check(compose)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "same /dev/ttyACM0 /run/dbus/system_bus_socket"


def test_an_edited_compose_file_is_never_overwritten(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        _COMPOSE_417.replace('      - "8000:8000"', '      - "8080:8000"'), encoding="utf-8"
    )
    result = _compose_check(compose)
    assert result.returncode != 0
    assert '+      - "8080:8000"' in result.stderr
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "MESHLOOM_COMPOSE_OVERWRITE" in text


def test_compose_dir_falls_back_to_the_old_helper_config() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = text.split("saved_compose_dir() {", 1)[1].split("\n}\n", 1)[0]
    assert "/etc/meshloom/compose-update.env" in body
    assert "com.docker.compose.project.working_dir" in body


_PATCH_FNS = (
    "compose_cmd",
    "patch_compose_file",
    "compose_owned_lines",
    "build_patched_compose",
)


def _has_docker_compose() -> bool:
    try:
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)
    except FileNotFoundError:
        return False
    return result.returncode == 0


needs_compose = pytest.mark.skipif(
    not _has_docker_compose(), reason="docker compose is not installed"
)

_COMPOSE_417_EDITED = _COMPOSE_417.replace(
    '      - "8000:8000"\n', '      - "8000:8000"\n      - "5000:5000"\n'
)


def _patch(path: Path, managed: int) -> subprocess.CompletedProcess[str]:
    return _bash_fns(_PATCH_FNS, f'patch_compose_file "{path}" {managed}')


def _build(directory: Path, managed: int) -> subprocess.CompletedProcess[str]:
    out = directory / ".docker-compose.yml.meshloom-new"
    return _bash_fns(_PATCH_FNS, f'build_patched_compose "{directory}" {managed} "{out}"')


def test_an_edited_417_file_keeps_its_edits_and_gets_the_helper_lines(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_COMPOSE_417_EDITED, encoding="utf-8")
    result = _patch(compose, 1)
    assert result.returncode == 0, result.stderr
    expected = (
        _COMPOSE_417_EDITED.replace(
            "    image: ghcr.io/twinrocket/meshloom:4.17.0",
            "    image: ${MESHLOOM_IMAGE:?run the Meshloom installer to pin the image in .env}",
        )
        .replace(
            "      - ./data:/app/data\n",
            "      - ./data:/app/data\n"
            "      # Written by the root update helper; read-only for the container.\n"
            "      - ./update-status:/app/update-status:ro\n",
        )
        .replace(
            "      MESHLOOM_UPDATE_JOB_PATH: /app/data/update-job.json\n",
            "      MESHLOOM_UPDATE_JOB_PATH: /app/data/update-job.json\n"
            "      MESHLOOM_UPDATE_STATUS_PATH: /app/update-status/status.json\n",
        )
    )
    assert result.stdout == expected


def test_patching_twice_changes_nothing(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_COMPOSE_417_EDITED, encoding="utf-8")
    once = _patch(compose, 1).stdout
    compose.write_text(once, encoding="utf-8")
    assert _patch(compose, 1).stdout == once


def test_an_unmanaged_stack_drops_the_old_helper_lines(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_COMPOSE_417_EDITED, encoding="utf-8")
    result = _patch(compose, 0)
    assert result.returncode == 0, result.stderr
    assert "MESHLOOM_UPDATE_" not in result.stdout
    assert "update-status" not in result.stdout
    assert '"5000:5000"' in result.stdout
    assert "image: ${MESHLOOM_IMAGE:?" in result.stdout


def test_only_the_meshloom_service_is_patched(tmp_path: Path) -> None:
    other = (
        "  other:\n"
        "    image: nginx:1.27\n"
        "    volumes:\n"
        "      - ./data:/app/data\n"
        "    environment:\n"
        "      MESHLOOM_INSTALL_KIND: compose\n"
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_COMPOSE_417_EDITED + other, encoding="utf-8")
    result = _patch(compose, 1)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(other)
    assert result.stdout.count("update-status:/app/update-status") == 1


def test_a_file_without_the_anchors_is_refused(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        _COMPOSE_417_EDITED.replace("      - ./data:/app/data", '      - "./data:/app/data"'),
        encoding="utf-8",
    )
    assert _patch(compose, 1).returncode != 0
    compose.write_text(_COMPOSE_417_EDITED.replace("  meshloom:", "  radio:"), encoding="utf-8")
    assert _patch(compose, 1).returncode != 0


@needs_compose
def test_the_patched_file_is_checked_by_docker_compose(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_COMPOSE_417_EDITED, encoding="utf-8")
    result = _build(tmp_path, 1)
    assert result.returncode == 0, result.stderr
    patched = (tmp_path / ".docker-compose.yml.meshloom-new").read_text(encoding="utf-8")
    assert patched == _patch(compose, 1).stdout
    assert compose.read_text(encoding="utf-8") == _COMPOSE_417_EDITED


@needs_compose
def test_a_file_compose_rejects_is_left_alone(tmp_path: Path) -> None:
    # A file Docker Compose rejects: the anchors are there, the check stops it.
    listed = _COMPOSE_417_EDITED.replace(
        '      MESHCORE_DATABASE_PATH: "data/meshcore.db"\n      MESHLOOM_INSTALL_KIND: compose\n',
        "      - MESHCORE_DATABASE_PATH=data/meshcore.db\n      MESHLOOM_INSTALL_KIND: compose\n",
    )
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(listed, encoding="utf-8")
    assert _build(tmp_path, 1).returncode != 0
    assert compose.read_text(encoding="utf-8") == listed
    assert not (tmp_path / ".docker-compose.yml.meshloom-new").exists()


def test_edits_are_kept_by_default_and_no_dead_end_is_offered() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = text.split("install_docker_stack() {", 1)[1].split("\n}\n", 1)[0]
    assert 'ui_yesno "$(t compose_keep_q)" y' in body
    assert 'ui_yesno "$(t compose_regen_q)" n' in body
    assert "compose_custom_stop" not in text
    assert "MESHLOOM_COMPOSE_OVERWRITE" in body


def test_a_stack_only_root_can_read_stops_a_non_root_run() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    saved = text.split("saved_compose_dir() {", 1)[1].split("\n}\n", 1)[0]
    assert '! is_root && [ ! -x "$p" ]' in saved
    body = text.split("install_docker_stack() {", 1)[1].split("\n}\n", 1)[0]
    assert '$(t compose_needs_root "$saved")' in body


def test_a_custom_image_or_helper_value_is_never_rewritten(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    for edited in (
        _COMPOSE_417_EDITED.replace(
            "ghcr.io/twinrocket/meshloom:4.17.0", "ghcr.io/someone/meshloom-fork:dev"
        ),
        _COMPOSE_417_EDITED.replace(
            "MESHLOOM_UPDATE_HELPER: compose", "MESHLOOM_UPDATE_HELPER: none"
        ),
        _COMPOSE_417_EDITED.replace(
            "      - ./data:/app/data\n",
            "      - ./data:/app/data\n      - ./update-status:/app/update-status-mine:rw\n",
        ),
    ):
        compose.write_text(edited, encoding="utf-8")
        assert _patch(compose, 1).returncode != 0, edited


def test_a_top_level_network_named_meshloom_is_not_the_service(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    extra = "networks:\n  meshloom:\n    driver: bridge\n"
    compose.write_text(_COMPOSE_417_EDITED + extra, encoding="utf-8")
    result = _patch(compose, 1)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(extra)


@needs_compose
def test_the_patched_file_differs_only_by_the_installer_lines(tmp_path: Path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(_COMPOSE_417_EDITED, encoding="utf-8")
    out = tmp_path / "out"
    script = (
        f'patch_compose_file() {{ sed "s/5000:5000/5001:5000/" "$1"; }}\n'
        f'build_patched_compose "{tmp_path}" 1 "{out}"'
    )
    names = ("compose_cmd", "compose_owned_lines", "build_patched_compose")
    assert _bash_fns(names, script).returncode != 0
    assert not out.exists()


def test_a_deleted_stack_folder_does_not_block_a_non_root_run(tmp_path: Path) -> None:
    gone = tmp_path / "gone" / "meshloom"
    script = (
        "is_root() { return 1; }\n"
        f'conf_get() {{ echo "{gone}"; }}\n'
        "system_installer_conf() { :; }; user_installer_conf() { :; }\n"
        'echo "[$(saved_compose_dir)]"'
    )
    result = _bash_fns(("saved_compose_dir",), script)
    assert result.stdout.strip() == "[]", result.stderr
