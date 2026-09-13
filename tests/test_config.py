"""Tests for configuration validation.

Radio transport is no longer part of Settings. These tests cover remaining
Settings env validation (basic auth pairing and experimental aliases).
"""

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_VAPID_SUBJECT, Settings


class TestBasicAuthConfiguration:
    """Ensure basic auth credentials are configured as a pair."""

    def test_basic_auth_disabled_by_default(self):
        s = Settings()
        assert s.basic_auth_enabled is False

    def test_basic_auth_enabled_when_both_credentials_are_set(self):
        s = Settings(
            basic_auth_username="mesh",
            basic_auth_password="secret",
        )
        assert s.basic_auth_enabled is True

    def test_basic_auth_requires_password_with_username(self):
        with pytest.raises(ValidationError, match="MESHCORE_BASIC_AUTH_USERNAME"):
            Settings(
                basic_auth_username="mesh",
                basic_auth_password="",
            )

    def test_basic_auth_requires_username_with_password(self):
        with pytest.raises(ValidationError, match="MESHCORE_BASIC_AUTH_USERNAME"):
            Settings(
                basic_auth_username="",
                basic_auth_password="secret",
            )


class TestExperimentalAliases:
    """Ensure exact-name experimental env vars still map into settings."""

    def test_clowntown_wraparound_alias_reads_exact_env_var(self, monkeypatch):
        monkeypatch.setenv("__CLOWNTOWN_DO_CLOCK_WRAPAROUND", "true")
        s = Settings()
        assert s.clowntown_do_clock_wraparound is True


class TestVapidSubjectEnv:
    """Blank MESHCORE_VAPID_SUBJECT must not override the built-in default."""

    def test_default_subject(self, monkeypatch):
        monkeypatch.delenv("MESHCORE_VAPID_SUBJECT", raising=False)
        s = Settings()
        assert s.vapid_subject == DEFAULT_VAPID_SUBJECT

    def test_empty_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "")
        s = Settings()
        assert s.vapid_subject == DEFAULT_VAPID_SUBJECT

    def test_whitespace_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("MESHCORE_VAPID_SUBJECT", "   ")
        s = Settings()
        assert s.vapid_subject == DEFAULT_VAPID_SUBJECT


class TestTransportRemovedFromSettings:
    """Transport env vars are leftover-only and must not live on Settings."""

    def test_settings_has_no_transport_fields(self):
        s = Settings()
        assert not hasattr(s, "serial_port")
        assert not hasattr(s, "serial_baudrate")
        assert not hasattr(s, "tcp_host")
        assert not hasattr(s, "tcp_port")
        assert not hasattr(s, "ble_address")
        assert not hasattr(s, "ble_pin")
        assert not hasattr(s, "connection_type")
