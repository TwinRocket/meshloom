"""Write-only secrets of the fanout configs and the notification destinations.

One table per kind says which config keys are secrets. The API never returns
them: a set secret reads as ``REDACTED`` (``********``), an unset one as ``""``.
A write merges with the stored value: a key omitted, ``None`` or ``REDACTED``
keeps the stored secret, ``""`` clears it, anything else replaces it. The
private-network flags (``allow_private_network``, ``private_network_grandfathered``)
are plain settings, never secrets.

Apprise URLs are a secret as a whole (the whole ``urls`` field): their tokens sit
anywhere in the URL. ``urls_redacted`` gives the UI one line per URL with only
its scheme and host (``redact_apprise_url``); it is derived, never stored.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.net.apprise_urls import redact_apprise_url, split_apprise_urls

REDACTED = "********"

FANOUT_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "apprise": ("urls",),
    "webhook": ("hmac_secret",),
    "mqtt_private": ("password",),
    "mqtt_ha": ("password",),
    "mqtt_community": ("password",),
    "sqs": ("secret_access_key", "session_token"),
    "map_upload": (),
    "bot": (),
}

NOTIFICATION_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "email": ("password",),
    "webhook": ("hmac_secret",),
}

# Display-only fields added by redact_config, dropped from any incoming config.
_DERIVED_KEYS: dict[str, tuple[str, ...]] = {"apprise": ("urls_redacted",)}

# Shorter fragments are too common to scrub from error text without mangling it.
_MIN_FRAGMENT_LEN = 3
_URL_FRAGMENT_SPLIT = re.compile(r"[/:@?&=#,;]+")


def secret_keys(kind: str) -> tuple[str, ...]:
    """The secret keys of a fanout type (``FANOUT_SECRET_KEYS``)."""
    return FANOUT_SECRET_KEYS.get(kind, ())


def redact_secrets(config: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """A copy with each secret key set to ``REDACTED`` when set, ``""`` otherwise."""
    redacted = dict(config)
    for key in keys:
        if key in redacted:
            redacted[key] = REDACTED if redacted[key] else ""
    return redacted


def redact_config(kind: str, config: Mapping[str, Any]) -> dict[str, Any]:
    """The API form of a fanout config: secrets redacted, Apprise URLs summarised."""
    redacted = redact_secrets(config, secret_keys(kind))
    if kind == "apprise":
        urls = config.get("urls")
        lines = split_apprise_urls(urls) if isinstance(urls, str) else []
        redacted["urls_redacted"] = [redact_apprise_url(line) for line in lines]
    return redacted


def merge_secrets(kind: str, stored: Mapping[str, Any] | None, incoming: Mapping[str, Any]) -> dict:
    """The config to store from an incoming one (a PATCH, a restore).

    Omitted, ``None`` or ``REDACTED`` keeps the stored secret (none: ``""``);
    ``""`` clears it; another value replaces it.
    """
    merged = {k: v for k, v in incoming.items() if k not in _DERIVED_KEYS.get(kind, ())}
    previous = stored or {}
    for key in secret_keys(kind):
        value = merged.get(key)
        if value is None or value == REDACTED:
            kept = previous.get(key)
            if kept:
                merged[key] = kept
            elif key in merged:
                merged[key] = ""
    return merged


def config_secret_values(kind: str, config: Mapping[str, Any]) -> list[str]:
    """The secret strings of a config, for ``scrub_error``.

    Apprise URLs add each URL and its fragments (``tgram://123:ABC/456`` gives
    ``123:ABC/456``, ``123``, ``ABC``, ``456``): an error may quote a part of one.
    """
    values: list[str] = []
    for key in secret_keys(kind):
        value = config.get(key)
        if not isinstance(value, str) or not value:
            continue
        if kind == "apprise" and key == "urls":
            for line in split_apprise_urls(value):
                values.append(line)
                rest = line.split("://", 1)[-1]
                values.append(rest)
                values.extend(_URL_FRAGMENT_SPLIT.split(rest))
        else:
            values.append(value)
    return values


def scrub_error(text: str | None, secrets: Iterable[str]) -> str | None:
    """``text`` with every secret replaced by ``****``, longest first."""
    if not text:
        return text
    unique = {s for s in secrets if isinstance(s, str) and len(s) >= _MIN_FRAGMENT_LEN}
    for secret in sorted(unique, key=len, reverse=True):
        text = text.replace(secret, "****")
    return text
