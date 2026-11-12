"""Packaged upgrades must leave meshloom.service running."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "pkg" / "nfpm"
APPLY = (PKG / "apply-update").read_text(encoding="utf-8")
PRERM = (PKG / "preremove.sh").read_text(encoding="utf-8")
POSTRM = PKG / "postremove.sh"
POSTINST = (PKG / "postinstall.sh").read_text(encoding="utf-8")
POSTTRANS = (PKG / "posttrans.sh").read_text(encoding="utf-8")
NFPM = (PKG / "nfpm.yaml.tmpl").read_text(encoding="utf-8")
INSTALL = (ROOT / "scripts" / "setup" / "install.sh").read_text(encoding="utf-8")


def test_preremove_skips_disable_on_upgrade() -> None:
    skip_at = PRERM.index("upgrade | 1 | deconfigure")
    disable_path_at = PRERM.index("disable --now meshloom-update.path")
    disable_at = PRERM.index("disable --now meshloom 2>")
    assert skip_at < disable_path_at < disable_at
    assert PRERM.index("exit 0") < disable_path_at


def test_postinstall_restarts_on_upgrade_only() -> None:
    assert "systemctl enable meshloom" in POSTINST
    assert "systemctl start meshloom" in POSTINST
    assert '"$1" = "configure"' in POSTINST
    assert '[ -n "$2" ]' in POSTINST


def test_postinstall_enables_path_unit_offline_then_restarts_watcher() -> None:
    enable_at = POSTINST.index("systemctl enable meshloom-update.path || true")
    running_at = POSTINST.index("if [ -d /run/systemd/system ]")
    reload_at = POSTINST.index("systemctl daemon-reload")
    start_path_at = POSTINST.index("systemctl start meshloom-update.path")
    kill_at = POSTINST.index("        stop_old_server\n")
    start_at = POSTINST.index("systemctl start meshloom || true")
    wait_at = POSTINST.index("systemctl is-active --quiet meshloom")
    # enable runs even without a running systemd (RPi image bake, chroots).
    assert enable_at < running_at < reload_at < start_path_at < kill_at < start_at < wait_at
    assert "request-update" not in POSTINST
    # Only the stop loop names a state; the start wait never accepts "activating".
    assert "activating" not in POSTINST.replace("deactivating", "")
    assert "dst: /usr/lib/systemd/system/meshloom-update.path" in NFPM


def test_postinstall_migrates_existing_installs() -> None:
    assert "rm -f /var/lib/meshloom/update-job.json" in POSTINST
    assert "chown root:meshloom /etc/meshloom" in POSTINST
    assert "chmod 0750 /etc/meshloom" in POSTINST
    assert "/var/lib/meshloom-update" in POSTINST
    # Only the official URL is rewritten to a signed source.
    assert 'OFFICIAL_URL="https://twinrocket.github.io/meshloom"' in POSTINST
    assert "signed-by=${KEYRING}" in POSTINST
    assert "repo_gpgcheck=1" in POSTINST
    tmpfiles = (PKG / "meshloom.tmpfiles").read_text(encoding="utf-8")
    assert "update-job.json" not in tmpfiles
    assert "d /etc/meshloom 0750 root meshloom" in tmpfiles
    assert "d /var/lib/meshloom-update 0755 root root" in tmpfiles


def test_postinstall_stops_the_old_server_before_start() -> None:
    stop_at = POSTINST.index("        stop_old_server\n")
    start_at = POSTINST.index("systemctl start meshloom || true")
    assert stop_at < start_at
    assert "zz-meshloom-upgrade-kill.conf" not in POSTINST
    assert "systemctl restart meshloom" not in POSTINST


def _shell_fn(text: str, name: str) -> str:
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _run_with_fake_systemctl(
    tmp_path: Path,
    body: str,
    states: list[str],
    enabled: bool = False,
    blocking_update: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Runs postinstall functions against systemctl and pgrep stubs.

    `is-active` answers the next entry of `states` (the last one repeats).
    `pgrep` finds a blocking `systemctl start meshloom-update.service` only
    when `blocking_update` is set.
    """
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    state_file = tmp_path / "states"
    state_file.write_text("\n".join(states) + "\n", encoding="utf-8")
    systemctl = stubs / "systemctl"
    systemctl.write_text(
        f"""#!/bin/sh
echo "systemctl $*" >>"{log}"
case "$1" in
    is-active)
        state=$(head -n 1 "{state_file}")
        if [ "$(wc -l <"{state_file}")" -gt 1 ]; then
            tail -n +2 "{state_file}" >"{state_file}.n" && mv "{state_file}.n" "{state_file}"
        fi
        echo "$state"
        [ "$state" = active ]
        ;;
    is-enabled) [ "{1 if enabled else 0}" = 1 ] ;;
esac
""",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    pgrep = stubs / "pgrep"
    pgrep.write_text(
        f"""#!/bin/sh
echo "pgrep $*" >>"{log}"
[ "{1 if blocking_update else 0}" = 1 ]
""",
        encoding="utf-8",
    )
    pgrep.chmod(0o755)
    (stubs / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stubs / "sleep").chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", body],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={"PATH": f"{stubs}:/usr/bin:/bin"},
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, calls


STOP_OLD_SERVER = (
    _shell_fn(POSTINST, "old_server_waits_on_update")
    + _shell_fn(POSTINST, "stop_old_server")
    + "stop_old_server\n"
)
BLOCKING_PGREP = "pgrep -x -f systemctl start meshloom-update.service"


def test_stop_old_server_is_graceful_when_the_server_stops(tmp_path: Path) -> None:
    result, calls = _run_with_fake_systemctl(
        tmp_path, STOP_OLD_SERVER, ["active", "deactivating", "inactive"]
    )
    assert result.returncode == 0, result.stderr
    assert calls[0] == BLOCKING_PGREP
    assert calls[1] == "systemctl stop --no-block meshloom.service"
    assert not [c for c in calls if "SIGKILL" in c]


def test_stop_old_server_kills_a_server_that_does_not_stop(tmp_path: Path) -> None:
    result, calls = _run_with_fake_systemctl(tmp_path, STOP_OLD_SERVER, ["deactivating"])
    assert result.returncode == 0, result.stderr
    assert calls[-1] == "systemctl kill --kill-whom=all -s SIGKILL meshloom.service"
    assert len([c for c in calls if c.startswith("systemctl is-active")]) == 20


def test_stop_old_server_kills_at_once_during_a_blocking_in_app_update(tmp_path: Path) -> None:
    # 4.12.0 to 4.13.4 wait on `systemctl start meshloom-update.service`. Under
    # KillMode=control-group a SIGTERM would reach that client and the server
    # would answer the open page with a false "update failed".
    result, calls = _run_with_fake_systemctl(
        tmp_path, STOP_OLD_SERVER, ["active"], blocking_update=True
    )
    assert result.returncode == 0, result.stderr
    assert calls == [
        BLOCKING_PGREP,
        "systemctl kill --kill-whom=all -s SIGKILL meshloom.service",
    ]


def test_blocking_update_check_matches_only_the_pre_4_13_5_call() -> None:
    # 4.13.5 and later pass --no-block: an exact full-command-line match skips them.
    fn = _shell_fn(POSTINST, "old_server_waits_on_update")
    assert "pgrep -x -f 'systemctl start meshloom-update.service'" in fn
    assert '"systemctl start meshloom-update.service "' in fn
    assert "--no-block" not in fn
    old_kill = POSTINST.index("if old_server_waits_on_update; then")
    assert old_kill < POSTINST.index("systemctl stop --no-block meshloom.service")


def _tail_messages(tmp_path: Path, args: str, enabled: bool) -> str:
    tail = POSTINST[POSTINST.index("# An upgrade keeps the radio") :]
    body = _shell_fn(POSTINST, "is_upgrade") + f"set -- {args}\n" + tail
    result, _ = _run_with_fake_systemctl(tmp_path, body, ["inactive"], enabled=enabled)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("args", ["configure 4.18.3-1", "2", "upgrade"])
def test_postinstall_upgrade_does_not_say_to_start_meshloom(tmp_path: Path, args: str) -> None:
    out = _tail_messages(tmp_path, args, enabled=True)
    assert "Start Meshloom with" not in out
    assert "Settings > Radio" not in out
    assert "UI: http://localhost:8000" in out


@pytest.mark.parametrize("args", ["configure ''", "1"])
def test_postinstall_fresh_install_says_how_to_start(tmp_path: Path, args: str) -> None:
    out = _tail_messages(tmp_path, args, enabled=False)
    assert "Start Meshloom with: sudo systemctl enable --now meshloom" in out
    assert "Settings > Radio" in out


def test_postinstall_fresh_install_already_enabled_skips_the_start_hint(tmp_path: Path) -> None:
    out = _tail_messages(tmp_path, "1", enabled=True)
    assert "Start Meshloom with" not in out
    assert "Settings > Radio" in out


@pytest.mark.parametrize(
    ("arg", "said"),
    [
        ("remove", True),
        ("0", True),
        ("purge", False),
        ("upgrade", False),
        ("1", False),
        ("failed-upgrade", False),
    ],
)
def test_postremove_mentions_kept_data_only_on_removal(arg: str, said: bool) -> None:
    result = subprocess.run(
        ["sh", str(POSTRM), arg], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    assert ("left in place" in result.stdout) is said


def test_apply_update_starts_service_after_packages() -> None:
    apt_at = APPLY.index("apt-get install")
    start_at = APPLY.index("systemctl start meshloom")
    succeed_at = APPLY.index("\nsucceed\n")
    assert apt_at < start_at < succeed_at


def test_posttrans_recovers_disabled_upgrade() -> None:
    assert "meshcore.db" in POSTTRANS
    assert "systemctl start meshloom" in POSTTRANS
    # nFPM rejects posttrans under overrides.rpm.scripts (generic Scripts).
    assert "\nrpm:\n  scripts:\n    posttrans: __PKGDIR__/posttrans.sh" in NFPM
    overrides = NFPM[NFPM.index("overrides:") : NFPM.index("\nrpm:")]
    assert "posttrans" not in overrides


def test_install_sh_never_writes_its_own_package_helper() -> None:
    assert "_install_package_update_helper_fallback" not in INSTALL
    assert "trusted=yes" not in INSTALL
    assert "gpgcheck=0" not in INSTALL


def test_service_cannot_write_etc_meshloom() -> None:
    service = (PKG / "meshloom.service").read_text(encoding="utf-8")
    # The kiosk device token directory is optional ("-"): kiosk hosts only.
    assert "ReadWritePaths=/var/lib/meshloom -/var/lib/meshloom-kiosk\n" in service


def test_service_caps_the_unit_task_count() -> None:
    service = (PKG / "meshloom.service").read_text(encoding="utf-8")
    assert "\nTasksMax=512\n" in service


def test_service_keeps_the_state_directory_from_other_accounts() -> None:
    service = (PKG / "meshloom.service").read_text(encoding="utf-8")
    # Without these, StateDirectory= resets the directory to 0755 at every start
    # and SQLite creates the database 0644.
    assert "\nStateDirectoryMode=0750\n" in service
    assert "\nUMask=0027\n" in service
    assert "d /var/lib/meshloom 0750 meshloom meshloom" in (PKG / "meshloom.tmpfiles").read_text(
        encoding="utf-8"
    )


def test_postinstall_never_writes_the_kiosk_marker_through_a_link() -> None:
    assert ": >/var/lib/meshloom/kiosk" not in POSTINST
    assert "chmod 0644 /var/lib/meshloom/kiosk" not in POSTINST
    assert POSTINST.index("mkdir -p /var/lib/meshloom-update") < POSTINST.index(
        "mktemp /var/lib/meshloom-update/kiosk."
    )
    assert 'mv -fT "$marker" /var/lib/meshloom/kiosk' in POSTINST


def test_postinstall_closes_the_state_directory_of_an_existing_install() -> None:
    chmod_at = POSTINST.index('chmod 0750 "$state_dir"')
    # After the kiosk marker, which may create the directory, and before any restart.
    assert POSTINST.index("mark_kiosk_host || true") < chmod_at
    assert chmod_at < POSTINST.index("systemctl start meshloom || true")
    # Root never walks or follows what the meshloom user owns.
    assert '[ ! -L "$state_dir" ]' in POSTINST
    assert "chmod -R" not in POSTINST
