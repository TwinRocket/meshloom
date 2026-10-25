"""Write-only secrets of the fanout configs and the notification destinations.

One table per kind says which config keys are secrets. The API never returns
them: a set secret reads as ``REDACTED`` (``********``), an unset one as ``""``.
A write merges with the stored value: a key omitted, ``None`` or ``REDACTED``
keeps the stored secret, ``""`` clears it, anything else replaces it. A
stored secret is kept only for the destination it was set for: when a key of
``FANOUT_DESTINATION_KEYS`` changes, keeping it raises ``SecretRequiredError``
(the PATCH answers 400 ``secret_required``; a restore drops the secret). The
private-network flags (``allow_private_network``, ``private_network_grandfathered``)
are plain settings, never secrets.

Apprise URLs are a secret as a whole (the whole ``urls`` field): their tokens sit
anywhere in the URL. ``urls_redacted`` gives the UI one line per URL with only
its scheme and host (``redact_apprise_url``); it is derived, never stored. A
webhook ``url`` is a secret too (Discord, Slack and many others carry the token
in the path) and its own destination: ``********`` keeps it, and
``url_redacted`` (``redact_url``) shows its scheme and host.

A secret that is a JSON object (the webhook ``headers``) keeps its names in
clear: each value reads ``********``. On a write, a ``********`` value keeps the
stored value of that name (refused when the destination moved), any other value
replaces it, and a name left out is removed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from app.net.apprise_urls import (
    apprise_url_secret_parts,
    redact_apprise_url,
    redact_url,
    split_apprise_urls,
    url_secret_parts,
)

REDACTED = "********"

FANOUT_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "apprise": ("urls",),
    "webhook": ("url", "headers", "hmac_secret"),
    "mqtt_private": ("password",),
    "mqtt_ha": ("password",),
    "mqtt_community": ("password",),
    "sqs": ("access_key_id", "secret_access_key", "session_token"),
    "map_upload": (),
    "bot": (),
}

# The keys that say where a secret is sent (with their defaults, so a row saved
# before a key existed compares equal to the editor's default). A stored secret
# never follows a change of one of them: an MQTT password would reach the new
# broker, an SQS session token the new endpoint.
_MQTT_DESTINATION = {
    "broker_host": "",
    "broker_port": 1883,
    "use_tls": False,
    "tls_insecure": False,
}
FANOUT_DESTINATION_KEYS: dict[str, dict[str, Any]] = {
    "webhook": {"url": ""},
    "mqtt_private": _MQTT_DESTINATION,
    "mqtt_ha": _MQTT_DESTINATION,
    "mqtt_community": {
        "broker_host": "mqtt-us-v1.letsmesh.net",
        "broker_port": 443,
        "transport": "websockets",
        "use_tls": True,
        "tls_verify": True,
        "websocket_path": "/",
    },
    "sqs": {"queue_url": "", "endpoint_url": "", "region_name": ""},
}

# The secrets that are JSON objects: their names stay in clear, their values not.
_OBJECT_SECRET_KEYS: dict[str, tuple[str, ...]] = {"webhook": ("headers",)}

# The secrets that are URLs: an error text keeps their host (``url_secret_parts``).
_URL_SECRET_KEYS: dict[str, tuple[str, ...]] = {"webhook": ("url",)}

NOTIFICATION_SECRET_KEYS: dict[str, tuple[str, ...]] = {
    "email": ("password",),
    "webhook": ("url", "hmac_secret"),
    "apprise": ("urls",),
}

# Same rule for the notification destinations (merge_notification_destinations):
# a kept SMTP password never follows a new host, port or TLS mode, a kept HMAC
# secret never follows a new webhook URL. Apprise URLs and the webhook URL are
# their own destination: ``********`` keeps them, so it is no move.
NOTIFICATION_DESTINATION_KEYS: dict[str, dict[str, Any]] = {
    "email": {"host": "", "port": 587, "mode": "starttls"},
    "webhook": {"url": ""},
}

# Display-only fields added by redact_config, dropped from any incoming config.
_DERIVED_KEYS: dict[str, tuple[str, ...]] = {
    "apprise": ("urls_redacted",),
    "webhook": ("url_redacted",),
}

# Shorter fragments are too common to scrub from error text without mangling it.
_MIN_FRAGMENT_LEN = 3


class SecretRequiredError(ValueError):
    """A stored secret would be kept for a destination it was not set for."""

    def __init__(self, keys: list[str]) -> None:
        super().__init__(f"secret required: {', '.join(keys)}")
        self.keys = keys

    def detail(self) -> dict[str, Any]:
        """The 400 body: the secrets to type again."""
        return {
            "code": "secret_required",
            "message": "The destination changed: enter the secret again, or clear it.",
            "params": {"fields": ", ".join(self.keys)},
        }


def _destination_value(value: Any, default: Any) -> str:
    return str(default if value is None else value).strip()


def destination_changed(
    kind: str,
    stored: Mapping[str, Any],
    incoming: Mapping[str, Any],
    table: Mapping[str, Mapping[str, Any]] = FANOUT_DESTINATION_KEYS,
) -> bool:
    """Whether ``incoming`` sends this type's secrets somewhere else than ``stored``."""
    keys = table.get(kind, {})
    return any(
        _destination_value(incoming.get(key), default)
        != _destination_value(stored.get(key), default)
        for key, default in keys.items()
    )


def secret_keys(kind: str) -> tuple[str, ...]:
    """The secret keys of a fanout type (``FANOUT_SECRET_KEYS``)."""
    return FANOUT_SECRET_KEYS.get(kind, ())


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {name: _redact_value(item) for name, item in value.items()}
    return REDACTED if value else ""


def redact_secrets(config: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """A copy with each secret key set to ``REDACTED`` when set, ``""`` otherwise.

    An object secret keeps its names, each value redacted the same way.
    """
    redacted = dict(config)
    for key in keys:
        if key in redacted:
            redacted[key] = _redact_value(redacted[key])
    return redacted


def redacted_url(url: Any) -> str:
    """The display form of a secret URL (``url_redacted``): scheme and host, or ``""``."""
    return redact_url(url) if isinstance(url, str) and url.strip() else ""


def redact_config(kind: str, config: Mapping[str, Any]) -> dict[str, Any]:
    """The API form of a fanout config: secrets redacted, secret URLs summarised."""
    redacted = redact_secrets(config, secret_keys(kind))
    if kind == "apprise":
        urls = config.get("urls")
        lines = split_apprise_urls(urls) if isinstance(urls, str) else []
        redacted["urls_redacted"] = [redact_apprise_url(line) for line in lines]
    if kind == "webhook":
        redacted["url_redacted"] = redacted_url(config.get("url"))
    return redacted


def _is_kept(value: Any) -> bool:
    return value is None or value == REDACTED


def _merge_object_secret(
    incoming: Mapping[str, Any], stored: Any, moved: bool
) -> tuple[dict[str, Any], bool]:
    """An object secret from a write: ``(merged, refused)``.

    A ``REDACTED`` value keeps the stored value of that name (none: the name is
    dropped); refused when the destination moved. A name left out is removed.
    """
    previous = stored if isinstance(stored, Mapping) else {}
    merged: dict[str, Any] = {}
    refused = False
    for name, value in incoming.items():
        if not _is_kept(value):
            merged[name] = value
            continue
        kept = previous.get(name)
        if kept and moved:
            refused = True
        elif kept:
            merged[name] = kept
    return merged, refused


def merge_secrets(kind: str, stored: Mapping[str, Any] | None, incoming: Mapping[str, Any]) -> dict:
    """The config to store from an incoming one (a PATCH, a restore).

    Omitted, ``None`` or ``REDACTED`` keeps the stored secret (none: ``""``);
    ``""`` clears it; another value replaces it. Keeping a secret while the
    destination changes (``destination_changed``) raises ``SecretRequiredError``
    naming those keys.
    """
    merged = {k: v for k, v in incoming.items() if k not in _DERIVED_KEYS.get(kind, ())}
    previous = stored or {}
    destinations = FANOUT_DESTINATION_KEYS.get(kind, {})
    object_keys = _OBJECT_SECRET_KEYS.get(kind, ())
    # A secret that is its own destination (the webhook URL) resolves first: kept,
    # it names the same place, so it is no move.
    for key in secret_keys(kind):
        if key in destinations and _is_kept(merged.get(key)):
            if previous.get(key):
                merged[key] = previous[key]
            elif key in merged:
                merged[key] = ""
    moved = bool(previous) and destination_changed(kind, previous, merged)
    refused: list[str] = []
    for key in secret_keys(kind):
        if key in destinations:
            continue
        value = merged.get(key)
        if key in object_keys and isinstance(value, Mapping):
            merged[key], object_refused = _merge_object_secret(value, previous.get(key), moved)
            if object_refused:
                refused.append(key)
        elif key in object_keys and value == "":
            # Cleared (a restore dropping it): an object secret empties to {}.
            merged[key] = {}
        elif _is_kept(value):
            kept = previous.get(key)
            if kept and moved:
                refused.append(key)
            elif kept:
                merged[key] = kept
            elif key in merged:
                merged[key] = {} if key in object_keys else ""
    if refused:
        raise SecretRequiredError(refused)
    return merged


def config_secret_values(kind: str, config: Mapping[str, Any]) -> list[str]:
    """The secret strings of a config, for ``scrub_error``.

    Apprise URLs add their secret parts (``apprise_url_secret_parts``): the
    user, password, path and query values of ``json://user:pw@host/path``, the
    whole token of ``tgram://123:ABC/456`` and its pieces. A webhook URL adds
    its user, password, path segments and query values (``url_secret_parts``),
    an object secret its values. The host stays readable, so ``last_error``
    still says which server failed.
    """
    values: list[str] = []
    for key in secret_keys(kind):
        value = config.get(key)
        if isinstance(value, Mapping):
            values.extend(item for item in value.values() if isinstance(item, str) and item)
            continue
        if not isinstance(value, str) or not value:
            continue
        if kind == "apprise" and key == "urls":
            for line in split_apprise_urls(value):
                values.extend(apprise_url_secret_parts(line))
        elif key in _URL_SECRET_KEYS.get(kind, ()):
            values.extend(url_secret_parts(value))
        else:
            values.append(value)
    return values


def scrub_error(text: str | None, secrets: Iterable[str]) -> str | None:
    """``text`` with every secret replaced by ``****``, longest first (for a URL secret, its
    last path segment too)."""
    if not text:
        return text
    unique = set()
    for secret in secrets:
        if not isinstance(secret, str):
            continue
        unique.add(secret)
        if secret.startswith(("http://", "https://")):
            # A pasted webhook: a compiled address may quote only a slice of it (Slack's
            # ``slack://T/B/X``), and the last path segment is the part that is secret.
            last = urlsplit(secret).path.rstrip("/").rsplit("/", 1)[-1]
            unique.add(last)
    unique = {s for s in unique if len(s) >= _MIN_FRAGMENT_LEN}
    for secret in sorted(unique, key=len, reverse=True):
        text = text.replace(secret, "****")
    return text
