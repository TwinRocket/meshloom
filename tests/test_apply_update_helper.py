"""Safety checks for the privileged nfpm apply-update helper.

Reads packaging files only. The helper must upgrade the meshloom package
alone and must never run a system-wide apt/dnf upgrade.
"""

from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "pkg" / "nfpm"
SCRIPT = PKG / "apply-update"
SERVICE = PKG / "meshloom-update.service"
POLKIT = PKG / "meshloom-update.polkit"
NFPM = PKG / "nfpm.yaml.tmpl"

_SYSTEM_UPGRADE = re.compile(
    r"(?:^|[;&|]|\n)\s*(?:apt-get\s+(?:dist-)?upgrade|apt\s+(?:dist-)?upgrade|dnf\s+upgrade)\b",
    re.MULTILINE,
)


def _active_shell(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        code = raw.split("#", 1)[0].strip()
        if code:
            lines.append(code)
    return "\n".join(lines)


def test_apply_update_is_meshloom_only() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    active = _active_shell(text)

    assert _SYSTEM_UPGRADE.search(active) is None
    assert "apt-get update" in active
    assert "install -y --only-upgrade meshloom" in active
    assert re.search(r"(?m)install -y meshloom$", active)
    assert "dnf install -y meshloom" in active
    assert "MESHLOOM_UPDATE_JOB_PATH" in active
    assert "/var/lib/meshloom/update-job.json" in active
    assert "mkdir -p /var/lib/meshloom" in active
    assert "APT::Status-Fd" in active


def test_update_unit_and_polkit_are_start_only() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    polkit = POLKIT.read_text(encoding="utf-8")
    nfpm = NFPM.read_text(encoding="utf-8")

    assert "ExecStart=/usr/lib/meshloom/apply-update" in service
    assert "After=network-online.target" in service
    assert "User=root" in service
    assert "Type=oneshot" in service

    assert 'subject.user == "meshloom"' in polkit
    assert 'action.lookup("unit") == "meshloom-update.service"' in polkit
    assert 'action.lookup("verb") == "start"' in polkit
    assert "stop" not in polkit
    assert "restart" not in polkit
    assert "manage-unit-files" not in polkit

    assert "dst: /usr/lib/meshloom/apply-update" in nfpm
    assert "mode: 0755" in nfpm
    assert "dst: /usr/lib/systemd/system/meshloom-update.service" in nfpm
    assert "dst: /usr/share/polkit-1/rules.d/60-meshloom-update.rules" in nfpm
