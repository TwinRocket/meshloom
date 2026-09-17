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
