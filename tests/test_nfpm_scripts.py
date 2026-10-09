"""Packaged upgrades must leave meshloom.service running."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "pkg" / "nfpm"
APPLY = (PKG / "apply-update").read_text(encoding="utf-8")
PRERM = (PKG / "preremove.sh").read_text(encoding="utf-8")
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
    kill_at = POSTINST.index("kill --kill-whom=all -s SIGKILL meshloom.service")
    start_at = POSTINST.index("systemctl start meshloom || true")
    wait_at = POSTINST.index("systemctl is-active --quiet meshloom")
    # enable runs even without a running systemd (RPi image bake, chroots).
    assert enable_at < running_at < reload_at < start_path_at < kill_at < start_at < wait_at
    assert "request-update" not in POSTINST
    assert "activating" not in POSTINST
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


def test_postinstall_hard_kills_before_start() -> None:
    kill_at = POSTINST.index("kill --kill-whom=all -s SIGKILL meshloom.service")
    start_at = POSTINST.index("systemctl start meshloom || true")
    assert kill_at < start_at
    assert "zz-meshloom-upgrade-kill.conf" not in POSTINST
    assert "systemctl restart meshloom" not in POSTINST


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
    assert "ReadWritePaths=/opt/meshloom /var/lib/meshloom\n" in service
