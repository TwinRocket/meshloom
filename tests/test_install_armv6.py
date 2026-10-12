"""install.sh refuses ARMv6 (Pi 1, original Zero) before touching anything."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "setup" / "install.sh"


def _run(tmp_path: Path, machine: str, lang: str) -> subprocess.CompletedProcess[str]:
    """Run the whole installer with a fake `uname -m` and a tripwire `sudo`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uname").write_text(
        f'#!/bin/sh\nif [ "$1" = "-m" ]; then echo {machine}; else echo Linux; fi\n',
        encoding="utf-8",
    )
    (bin_dir / "sudo").write_text(
        f'#!/bin/sh\ntouch "{tmp_path}/sudo-called"\nexit 1\n', encoding="utf-8"
    )
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "LANG": lang,
        "TERM": "dumb",
    }
    return subprocess.run(
        ["bash", str(INSTALL_SH)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("machine", ["armv6l", "armv6"])
def test_armv6_is_refused_before_any_change(tmp_path: Path, machine: str) -> None:
    result = _run(tmp_path, machine, "en_GB.UTF-8")

    assert result.returncode == 1
    assert "Unsupported hardware" in result.stderr
    assert "Nothing was changed" in result.stderr
    assert "Pi 2, Pi 3, Pi 4, Pi 5, Zero 2 W" in result.stderr
    # Refused before the language prompt, the TTY check and any sudo.
    assert "Language / Langue" not in result.stdout + result.stderr
    assert not (tmp_path / "sudo-called").exists()
    assert list((tmp_path / "home").iterdir()) == []


def test_armv6_message_follows_the_locale(tmp_path: Path) -> None:
    result = _run(tmp_path, "armv6l", "fr_FR.UTF-8")

    assert result.returncode == 1
    assert "Matériel non pris en charge" in result.stderr
    assert "https://meshloom.app/docs/rpi/" in result.stderr


@pytest.mark.parametrize("machine", ["armv7l", "aarch64", "x86_64"])
def test_supported_machines_go_past_the_check(tmp_path: Path, machine: str) -> None:
    # No TTY on stdin, so the installer stops at its next step, the TTY check.
    result = _run(tmp_path, machine, "en_GB.UTF-8")

    assert "Unsupported hardware" not in result.stderr
    assert result.returncode == 1
    assert "terminal" in (result.stdout + result.stderr).lower()
