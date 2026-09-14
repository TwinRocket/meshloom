import logging
import logging.config
from collections import deque
from threading import Lock
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shared with app.push.vapid so empty Docker/env interpolation cannot
# send a blank VAPID JWT ``sub`` to py-vapid.
DEFAULT_VAPID_SUBJECT = "mailto:noreply@meshcore.local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MESHCORE_")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_path: str = "data/meshcore.db"
    disable_bots: bool = False
    enable_message_poll_fallback: bool = False
    force_channel_slot_reconfigure: bool = False
    clowntown_do_clock_wraparound: bool = Field(
        default=False,
        validation_alias="__CLOWNTOWN_DO_CLOCK_WRAPAROUND",
    )
    enable_local_private_key_export: bool = False
    load_with_autoevict: bool = False
    skip_post_connect_sync: bool = False
    basic_auth_username: str = ""
    basic_auth_password: str = ""
    vapid_subject: str = DEFAULT_VAPID_SUBJECT

    @field_validator("vapid_subject", mode="before")
    @classmethod
    def empty_vapid_subject_uses_default(cls, value: object) -> object:
        """Treat blank ``MESHCORE_VAPID_SUBJECT`` as unset.

        Compose files often pass ``${MESHCORE_VAPID_SUBJECT}``, which becomes
        an empty string when the host/stack env is missing and would otherwise
        override this field's default.
        """
        if isinstance(value, str) and not value.strip():
            return DEFAULT_VAPID_SUBJECT
        return value

    @model_validator(mode="after")
    def validate_basic_auth_pairing(self) -> "Settings":
        if self.basic_auth_partially_configured:
            raise ValueError(
                "MESHCORE_BASIC_AUTH_USERNAME and MESHCORE_BASIC_AUTH_PASSWORD "
                "must be set together."
            )
        return self

    @property
    def basic_auth_enabled(self) -> bool:
        return bool(self.basic_auth_username and self.basic_auth_password)

    @property
    def basic_auth_partially_configured(self) -> bool:
        any_credentials_set = bool(self.basic_auth_username or self.basic_auth_password)
        return any_credentials_set and not self.basic_auth_enabled


settings = Settings()


class _RingBufferLogHandler(logging.Handler):
    """Keep a bounded in-memory tail of formatted log lines."""

    def __init__(self, max_lines: int = 1000) -> None:
        super().__init__()
        self._buffer: deque[str] = deque(maxlen=max_lines)
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with self._lock:
            self._buffer.append(line)

    def get_lines(self, limit: int = 1000) -> list[str]:
        with self._lock:
            if limit <= 0:
                return []
            return list(self._buffer)[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


_recent_log_handler = _RingBufferLogHandler(max_lines=1000)


def get_recent_log_lines(limit: int = 1000) -> list[str]:
    """Return recent formatted log lines from the in-memory ring buffer."""
    return _recent_log_handler.get_lines(limit)


def clear_recent_log_lines() -> None:
    """Clear the in-memory log ring buffer."""
    _recent_log_handler.clear()


class _UvicornLogHygiene(logging.Filter):
    """uvicorn.error is a general logger, not errors. WS chatter is traffic.

    See https://github.com/encode/uvicorn/issues/562 — handshake lines use
    that name at INFO. Rename it for %(name)s and drop the redundant
    accepted/open/closed trio (app.websocket already records connect).
    """

    _DROP_EXACT = frozenset({"connection open", "connection closed"})

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        msg = record.getMessage()
        if msg in self._DROP_EXACT:
            return False
        if "WebSocket " in msg and "[accepted]" in msg:
            return False
        return True


class _RepeatSquelch(logging.Filter):
    """Suppress rapid-fire identical messages and emit a summary instead.

    Attached to the ``meshcore`` library logger to catch its repeated
    "Serial Connection started" lines that flood the log when another
    process holds the serial port.
    """

    def __init__(self, threshold: int = 3) -> None:
        super().__init__()
        self._last_msg: str | None = None
        self._repeat_count: int = 0
        self._threshold = threshold

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if msg == self._last_msg:
            self._repeat_count += 1
            if self._repeat_count == self._threshold:
                record.msg = (
                    "%s (repeated %d times — possible serial port contention from another process)"
                )
                record.args = (msg, self._repeat_count)
                record.levelno = logging.WARNING
                record.levelname = "WARNING"
                return True
            # Suppress further repeats beyond the threshold
            return self._repeat_count < self._threshold
        else:
            self._last_msg = msg
            self._repeat_count = 1
            return True


def setup_logging() -> None:
    """Configure logging for the application."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "uvicorn_access": {
                    "()": "uvicorn.logging.AccessFormatter",
                    "fmt": '%(asctime)s - %(name)s - %(levelname)s - %(client_addr)s - "%(request_line)s" %(status_code)s',
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                    "use_colors": None,
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
                "uvicorn_access": {
                    "class": "logging.StreamHandler",
                    "formatter": "uvicorn_access",
                },
            },
            "root": {
                "level": settings.log_level,
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn": {
                    "level": settings.log_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.error": {
                    "level": settings.log_level,
                    "handlers": ["default"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": settings.log_level,
                    "handlers": ["uvicorn_access"],
                    "propagate": False,
                },
            },
        }
    )

    _recent_log_handler.setLevel(logging.DEBUG)
    _recent_log_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    for logger_name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        target = logging.getLogger(logger_name)
        if _recent_log_handler not in target.handlers:
            target.addHandler(_recent_log_handler)

    # Squelch repeated messages from the meshcore library (e.g. rapid-fire
    # "Serial Connection started" when the port is contended).
    logging.getLogger("meshcore").addFilter(_RepeatSquelch())
    uvicorn_hygiene = _UvicornLogHygiene()
    for logger_name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(logger_name).addFilter(uvicorn_hygiene)
