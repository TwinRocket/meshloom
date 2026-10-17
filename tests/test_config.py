"""Tests for configuration validation.

Radio transport is no longer part of Settings. These tests cover remaining
Settings env validation (basic auth pairing and experimental aliases).
"""

import logging

import pytest
from pydantic import ValidationError

from app.config import Settings, _UvicornLogHygiene


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


class TestRetiredVariables:
    """Settings no longer read the variables whose settings moved to the database."""

    def test_retired_variables_are_not_settings_fields(self, monkeypatch):
        for name in (
            "MESHCORE_VAPID_SUBJECT",
            "MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK",
            "MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE",
            "MESHCORE_LOAD_WITH_AUTOEVICT",
            "__CLOWNTOWN_DO_CLOCK_WRAPAROUND",
        ):
            monkeypatch.setenv(name, "true")
        s = Settings()
        for field in (
            "vapid_subject",
            "enable_message_poll_fallback",
            "force_channel_slot_reconfigure",
            "load_with_autoevict",
            "clowntown_do_clock_wraparound",
            "auto_clock_wraparound",
        ):
            assert not hasattr(s, field)


class TestUvicornLogHygiene:
    def test_renames_error_logger_and_drops_ws_chatter(self):
        filt = _UvicornLogHygiene()
        open_rec = logging.LogRecord(
            "uvicorn.error", logging.INFO, __file__, 0, "connection open", (), None
        )
        assert filt.filter(open_rec) is False

        accepted = logging.LogRecord(
            "uvicorn.error",
            logging.INFO,
            __file__,
            0,
            '192.168.42.240:35468 - "WebSocket /api/ws" [accepted]',
            (),
            None,
        )
        assert filt.filter(accepted) is False

        startup = logging.LogRecord(
            "uvicorn.error",
            logging.INFO,
            __file__,
            0,
            "Application startup complete.",
            (),
            None,
        )
        assert filt.filter(startup) is True
        assert startup.name == "uvicorn"


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
