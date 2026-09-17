"""The Pi image must not pin Community off; unset means the product default (on)."""

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
