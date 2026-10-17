"""Capture every log line at DEBUG (records and the redacting ring buffer) and
assert that no form of a secret reached any of them.

The ``secret_debug_logs`` fixture (tests/conftest.py) wraps ``debug_capture``.
Same setup as ``debug_logs`` in tests/test_log_redaction.py, for other lots.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import _recent_log_handler, clear_recent_log_lines, get_recent_log_lines
from app.log_redaction import install_meshcore_log_redaction, install_redacting_formatter


@contextmanager
def debug_capture(caplog) -> Iterator[None]:
    install_meshcore_log_redaction()
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="meshcore")
    root = logging.getLogger()
    added = _recent_log_handler not in root.handlers
    if added:
        root.addHandler(_recent_log_handler)
    previous_level = _recent_log_handler.level
    if _recent_log_handler.formatter is None:
        _recent_log_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    _recent_log_handler.setLevel(logging.DEBUG)
    install_redacting_formatter(_recent_log_handler)
    clear_recent_log_lines()
    try:
        yield
    finally:
        clear_recent_log_lines()
        _recent_log_handler.setLevel(previous_level)
        if added:
            root.removeHandler(_recent_log_handler)


def secret_forms(secret: str | bytes) -> list[str]:
    raw = secret.encode() if isinstance(secret, str) else secret
    forms = [raw.hex(), raw.hex().upper(), repr(raw)[2:-1]]
    if isinstance(secret, str):
        forms.append(secret)
    return [form for form in forms if form]


def captured_text(caplog) -> list[str]:
    texts: list[str] = []
    for record in caplog.records:
        texts.append(record.getMessage())
        if record.exc_text:
            texts.append(record.exc_text)
        if record.stack_info:
            texts.append(record.stack_info)
    texts.extend(get_recent_log_lines())
    return texts


def assert_no_secret_logged(caplog, *secrets: str | bytes) -> None:
    texts = captured_text(caplog)
    for secret in secrets:
        for form in secret_forms(secret):
            for text in texts:
                assert form not in text, f"secret form {form!r} leaked in {text!r}"
