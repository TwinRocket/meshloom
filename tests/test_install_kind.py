"""Install-kind detection: declared env, then helper, then container."""

from __future__ import annotations

import pytest

from app.services.install_kind import detect_install_kind


def test_addon_is_never_apply_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    assert detect_install_kind() == ("addon", False)


def test_declared_package_needs_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    monkeypatch.setattr("app.services.install_kind._package_helper_present", lambda: False)
    assert detect_install_kind() == ("package", False)

    monkeypatch.setattr("app.services.install_kind._package_helper_present", lambda: True)
    assert detect_install_kind() == ("package", True)


def test_declared_compose_needs_helper_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "compose")
    monkeypatch.setattr("app.services.install_kind._compose_helper_present", lambda: False)
    assert detect_install_kind() == ("compose", False)

    monkeypatch.setattr("app.services.install_kind._compose_helper_present", lambda: True)
    assert detect_install_kind() == ("compose", True)


def test_undeclared_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MESHLOOM_INSTALL_KIND", raising=False)
    monkeypatch.setattr("app.services.install_kind._package_helper_present", lambda: False)
    monkeypatch.setattr("app.services.install_kind._compose_helper_present", lambda: False)
    monkeypatch.setattr("app.services.install_kind._in_container", lambda: True)
    assert detect_install_kind() == ("container", False)


def test_supervisor_token_is_not_addon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "not-a-signal")
    monkeypatch.delenv("MESHLOOM_INSTALL_KIND", raising=False)
    monkeypatch.setattr("app.services.install_kind._package_helper_present", lambda: False)
    monkeypatch.setattr("app.services.install_kind._compose_helper_present", lambda: False)
    monkeypatch.setattr("app.services.install_kind._in_container", lambda: False)
    assert detect_install_kind() == ("source", False)


def test_undeclared_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MESHLOOM_INSTALL_KIND", raising=False)
    monkeypatch.setattr("app.services.install_kind._package_helper_present", lambda: False)
    monkeypatch.setattr("app.services.install_kind._compose_helper_present", lambda: False)
    monkeypatch.setattr("app.services.install_kind._in_container", lambda: False)
    assert detect_install_kind() == ("source", False)


def test_installer_can_turn_apply_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """MESHLOOM_UPDATE_HELPER=none: no signed repository carries this architecture."""
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    monkeypatch.setenv("MESHLOOM_UPDATE_HELPER", "none")
    monkeypatch.setattr("app.services.install_kind._package_helper_present", lambda: True)
    assert detect_install_kind() == ("package", False)


# ── package channel (the suite the signed repository source follows) ─────────

_PAGES = "https://twinrocket.github.io/meshloom"
_KEY = "/usr/share/keyrings/meshloom-archive-keyring.gpg"


def _channel(monkeypatch: pytest.MonkeyPatch, tmp_path, apt: str | None, yum: str | None):
    from app.services import install_kind

    apt_path = tmp_path / "meshloom.list"
    yum_path = tmp_path / "meshloom.repo"
    if apt is not None:
        apt_path.write_text(apt, encoding="utf-8")
    if yum is not None:
        yum_path.write_text(yum, encoding="utf-8")
    monkeypatch.setattr(install_kind, "APT_SOURCE", apt_path)
    monkeypatch.setattr(install_kind, "YUM_REPO", yum_path)
    return install_kind.package_channel()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"deb [signed-by={_KEY}] {_PAGES}/apt stable main\n", "stable"),
        (f"deb [signed-by={_KEY}] {_PAGES}/apt testing main\n", "testing"),
        (
            f"# deb {_PAGES}/apt testing main\ndeb [signed-by={_KEY}] {_PAGES}/apt stable main\n",
            "stable",
        ),
        ("deb http://deb.debian.org/debian trixie main\n", None),
        ("", None),
    ],
)
def test_apt_channel(monkeypatch, tmp_path, line: str, expected) -> None:
    assert _channel(monkeypatch, tmp_path, line, None) == expected


@pytest.mark.parametrize(
    ("baseurl", "expected"),
    [
        (f"{_PAGES}/rpm/$basearch", "stable"),
        (f"{_PAGES}/rpm/testing/$basearch", "testing"),
    ],
)
def test_yum_channel(monkeypatch, tmp_path, baseurl: str, expected) -> None:
    repo = f"[meshloom]\nname=Meshloom\nbaseurl={baseurl}\nenabled=1\ngpgcheck=1\n"
    assert _channel(monkeypatch, tmp_path, None, repo) == expected


def test_no_source_means_unknown(monkeypatch, tmp_path) -> None:
    assert _channel(monkeypatch, tmp_path, None, None) is None
