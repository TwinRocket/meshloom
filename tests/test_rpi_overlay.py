"""The Pi image must not pin Community off; unset means the product default (on)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_rpi_overlay_does_not_set_community_env() -> None:
    for path in (
        REPO / "pkg/nfpm/meshloom.env",
        REPO / "pkg/rpi/meshloom-console",
        REPO / "scripts/build/build_rpi_image.sh",
    ):
        text = path.read_text(encoding="utf-8")
        assert "MESHLOOM_COMMUNITY=0" not in text
        assert "MESHLOOM_COMMUNITY=false" not in text
        assert "MESHLOOM_COMMUNITY=off" not in text


def test_apply_update_helper_never_upgrades_the_os() -> None:
    helper = (REPO / "pkg/nfpm/apply-update").read_text(encoding="utf-8")
    assert "install -y --only-upgrade meshloom" in helper
    assert "dnf install -y meshloom" in helper
    assert "apt-get upgrade" not in helper
    assert "dnf upgrade" not in helper


def test_rpi_console_script_is_executable_text() -> None:
    script = (REPO / "pkg/rpi/meshloom-console").read_text(encoding="utf-8")
    assert "meshloom.local" in script or ".local:8000" in script
    assert "8000" in script


def test_rpi_image_bake_is_release_safe() -> None:
    script = (REPO / "scripts/build/build_rpi_image.sh").read_text(encoding="utf-8")
    workflow = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    rpi_workflow = (REPO / ".github/workflows/rpi-image.yml").read_text(encoding="utf-8")
    manifest = (REPO / "pkg/rpi/os-list.rpi-imager-manifest.tmpl").read_text(encoding="utf-8")

    assert "write_meshloom_apt_source" in script
    assert "signed-by=/etc/apt/keyrings/meshloom.gpg" in script
    assert "github.io/meshloom/meshloom.gpg" not in script
    assert "Writing an unsigned Meshloom apt source" in script
    assert 'umount "$BOOTMNT"' in script
    assert "default_branch" in rpi_workflow
    assert "apt-get update -y || true" not in script
    assert "publish-linux-repo.yml" in workflow
    assert "kpartx -avs" in script
    assert "growpart" in script
    assert "df -h" in script
    assert "meshloom.rpi-imager-manifest" in script
    assert "--version" in script
    assert "raspios_lite_arm64-2026-06-19" in script
    assert "acff736ca7945e3b305f07cda4abdb870910e12634991da69783611756e381b3" in script
    assert "raspios_lite_arm64_latest" not in script
    assert "MIN_FREE_KB" in script
    assert "reclaim_runner_disk" in script
    assert "sha256sum -c" in script
    assert '[ -n "$have" ] || return 0' not in script
    assert "Could not measure free disk" in script
    assert "image_download_sha256" in manifest
    assert "init_format" in manifest
    assert "Zero 2 W" in manifest
    assert "pi3-64bit" in manifest
    assert "cloud-guest-utils" in workflow
    assert "meshloom.rpi-imager-manifest" in workflow


def test_rpi_image_script_parses() -> None:
    bash = shutil.which("bash")
    if bash is None or os.name == "nt":
        return
    result = subprocess.run(
        [bash, "-n", str(REPO / "scripts/build/build_rpi_image.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
