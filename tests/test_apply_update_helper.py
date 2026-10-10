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
PATH_UNIT = PKG / "meshloom-update.path"
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
    assert "update || fail" in active
    assert "install -y --only-upgrade meshloom" in active
    assert re.search(r"(?m)install -y meshloom$", active)
    assert "dnf -y --refresh --disablerepo='*' --enablerepo=meshloom" in active
    assert '"$DNF_ACTION" meshloom' in active
    assert "APT::Status-Fd" in active
    assert "PHASE=restarting" in active
    assert "systemctl enable meshloom || true" in active
    assert "systemctl is-active --quiet meshloom" in active
    assert "systemctl enable meshloom || fail" not in active


def test_apply_update_never_touches_app_files() -> None:
    """Root reads, deletes and chowns nothing the app can write."""
    text = SCRIPT.read_text(encoding="utf-8")
    active = _active_shell(text)
    assert "/var/lib/meshloom/" not in active
    assert "/var/lib/meshloom\n" not in active
    assert "request-update" not in active
    assert "update-job.json" not in active
    assert "MESHLOOM_UPDATE_JOB_PATH" not in active
    assert "chown" not in active
    assert "/var/lib/meshloom-update" in active
    # Status is published atomically in a root directory.
    mktemp_at = text.index('tmp=$(mktemp "$STATE_DIR/.status.XXXXXX")')
    chmod_at = text.index('chmod 0644 "$tmp"', mktemp_at)
    mv_at = text.index('mv -f "$tmp" "$STATUS_PATH"', chmod_at)
    assert mktemp_at < chmod_at < mv_at
    # The FIFO lives in the unit's RuntimeDirectory.
    assert 'FIFO="$RUN_DIR/apt-status.$$"' in text
    # Test root override only behind the explicit flag.
    assert 'if [ "${MESHLOOM_HELPER_TESTING:-}" = 1 ]; then' in text


def test_apply_update_fails_closed_on_unsigned_sources() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "require_signed_sources apt_source_problems" in text
    assert "require_signed_sources dnf_source_problems" in text
    assert "(trusted|allow-insecure|allow-weak|allow-downgrade-to-insecure)=yes" in text
    assert "signed-by=" in text
    assert "repo_gpgcheck" in text
    assert 'Pin: origin "twinrocket.github.io"' in text
    assert "APT::Get::AllowUnauthenticated=false" in text
    assert text.index("require_signed_sources apt_source_problems") < text.index(
        "apt-get $APT_SECURE update"
    )


def test_update_unit_and_polkit_are_start_only() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    polkit = POLKIT.read_text(encoding="utf-8")
    nfpm = NFPM.read_text(encoding="utf-8")

    assert "ExecStart=/usr/lib/meshloom/apply-update" in service
    assert "After=network-online.target" in service
    assert "User=root" in service
    assert "Type=oneshot" in service
    for line in (
        # No start limit: hitting it would kill the .path unit for good.
        "StartLimitIntervalSec=0",
        "StateDirectory=meshloom-update",
        "StateDirectoryMode=0755",
        "RuntimeDirectory=meshloom-update",
        "UMask=0022",
        "PrivateTmp=yes",
        "ProtectHome=yes",
    ):
        assert line in service

    assert 'subject.user == "meshloom"' in polkit
    assert 'action.lookup("unit") == "meshloom-update.service"' in polkit
    assert 'action.lookup("verb") == "start"' in polkit
    assert "stop" not in polkit
    assert "restart" not in polkit
    assert "manage-unit-files" not in polkit

    assert "dst: /usr/lib/meshloom/apply-update" in nfpm
    assert "mode: 0755" in nfpm
    assert "dst: /usr/lib/systemd/system/meshloom-update.service" in nfpm
    assert "dst: /usr/lib/systemd/system/meshloom-update.path" in nfpm
    assert "dst: /usr/share/polkit-1/rules.d/60-meshloom-update.rules" in nfpm

    path_unit = PATH_UNIT.read_text(encoding="utf-8")
    assert "PathExists" not in path_unit
    assert "PathChanged=/var/lib/meshloom/request-update" in path_unit
    assert "Unit=meshloom-update.service" in path_unit
