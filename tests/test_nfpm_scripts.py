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


def test_postinstall_clears_request_before_enabling_path() -> None:
    rm_at = POSTINST.index("rm -f /var/lib/meshloom/request-update")
    reload_at = POSTINST.index("systemctl daemon-reload")
    enable_now_at = POSTINST.index("systemctl enable --now meshloom-update.path")
    kill_at = POSTINST.index("kill --kill-whom=all -s SIGKILL meshloom.service")
    start_at = POSTINST.index("systemctl start meshloom || true")
    wait_at = POSTINST.index("systemctl is-active --quiet meshloom")
    assert rm_at < reload_at < enable_now_at < kill_at < start_at < wait_at
    assert "activating" not in POSTINST
    assert "dst: /usr/lib/systemd/system/meshloom-update.path" in NFPM


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


def test_install_sh_fallback_helper_starts_service() -> None:
    helper = INSTALL[INSTALL.index("_install_package_update_helper_fallback") :]
    assert "systemctl start meshloom" in helper
    assert "meshloom-update.path" in helper
    assert 'rm -f "$REQUEST_PATH"' in helper
    chown_at = helper.index('chown meshloom:meshloom "$tmp"')
    mv_at = helper.index('mv -f "$tmp" "$JOB_PATH"')
    assert chown_at < mv_at


def test_helper_returns_job_file_to_meshloom() -> None:
    chown_at = APPLY.index('chown meshloom:meshloom "$tmp"')
    mv_at = APPLY.index('mv -f "$tmp" "$JOB_PATH"')
    assert chown_at < mv_at
    assert "\nsucceed\n" in APPLY
    assert "\nfail()\n" in APPLY or "fail() {" in APPLY
    assert 'rm -f "$JOB_PATH"' not in APPLY
    tmpfiles = (PKG / "meshloom.tmpfiles").read_text(encoding="utf-8")
    assert "z /var/lib/meshloom/update-job.json 0644 meshloom meshloom" in tmpfiles
