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
    skip_at = PRERM.index("upgrade")
    disable_at = PRERM.index("disable --now meshloom")
    assert skip_at < disable_at
    assert "exit 0" in PRERM


def test_postinstall_restarts_on_upgrade_only() -> None:
    assert "systemctl enable meshloom" in POSTINST
    assert "systemctl restart meshloom" in POSTINST
    assert '"$1" = "configure"' in POSTINST
    assert '[ -n "$2" ]' in POSTINST


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
