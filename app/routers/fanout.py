"""REST API for fanout config CRUD."""

import logging
import re
import string
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth.permissions import require
from app.fanout.manager import fanout_manager, is_reserved_fanout_id
from app.net.apprise_urls import (
    AppriseUrlRefused,
    parse_apprise_urls,
    split_apprise_urls,
    validate_apprise_urls,
)
from app.net.egress_guard import EgressBlocked, egress_detail, validate_destination
from app.notifications.fanout_convert import CONVERSION_KEYS, CONVERTED_TO
from app.plugins import bot_conversion
from app.repository.fanout import FanoutConfigRepository
from app.secret_fields import (
    REDACTED,
    SecretRequiredError,
    derived_keys,
    merge_secrets,
    redact_config,
    secret_keys,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fanout", tags=["fanout"])

_VALID_TYPES = {
    "mqtt_private",
    "mqtt_community",
    "mqtt_ha",
    "webhook",
    "apprise",
    "sqs",
    "map_upload",
}

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE = "meshcore/{IATA}/{PUBLIC_KEY}/packets"
_DEFAULT_COMMUNITY_MQTT_BROKER_HOST = "mqtt-us-v1.letsmesh.net"
_DEFAULT_COMMUNITY_MQTT_BROKER_PORT = 443
_DEFAULT_COMMUNITY_MQTT_TRANSPORT = "websockets"
_DEFAULT_COMMUNITY_MQTT_AUTH_MODE = "token"
_COMMUNITY_MQTT_TEMPLATE_FIELD_CANONICAL = {
    "iata": "IATA",
    "public_key": "PUBLIC_KEY",
}
_ALLOWED_COMMUNITY_MQTT_TRANSPORTS = {"tcp", "websockets"}
_ALLOWED_COMMUNITY_MQTT_AUTH_MODES = {"token", "password", "none"}

# Types whose destination is a user-chosen URL, checked by the egress guard
# (app/net/AGENTS.md), and the config fields that name it.
_GUARDED_TYPES: dict[str, tuple[str, ...]] = {
    "webhook": ("url",),
    "apprise": ("urls",),
    "sqs": ("endpoint_url",),
    "map_upload": ("api_url",),
}


def _normalize_community_topic_template(topic_template: str) -> str:
    """Normalize Community MQTT topic template placeholders to canonical uppercase form."""
    template = topic_template.strip() or _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE
    parts: list[str] = []
    try:
        parsed = string.Formatter().parse(template)
        for literal_text, field_name, format_spec, conversion in parsed:
            parts.append(literal_text)
            if field_name is None:
                continue
            normalized_field = _COMMUNITY_MQTT_TEMPLATE_FIELD_CANONICAL.get(field_name.lower())
            if normalized_field is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"topic_template may only use {{IATA}} and {{PUBLIC_KEY}}; got {field_name}"
                    ),
                )
            replacement = ["{", normalized_field]
            if conversion:
                replacement.extend(["!", conversion])
            if format_spec:
                replacement.extend([":", format_spec])
            replacement.append("}")
            parts.append("".join(replacement))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid topic_template: {exc}") from None

    return "".join(parts)


class FanoutConfigCreate(BaseModel):
    type: str = Field(description="Integration type: 'mqtt_private' or 'mqtt_community'")
    name: str = Field(min_length=1, description="User-assigned label")
    config: dict = Field(default_factory=dict, description="Type-specific config blob")
    scope: dict = Field(default_factory=dict, description="Scope controls")
    enabled: bool = Field(default=True, description="Whether enabled on creation")


class FanoutConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, description="Updated label")
    config: dict | None = Field(default=None, description="Updated config blob")
    scope: dict | None = Field(default=None, description="Updated scope controls")
    enabled: bool | None = Field(default=None, description="Enable/disable toggle")


def _validate_and_normalize_config(config_type: str, config: dict) -> dict:
    """Validate a config blob and return the canonical persisted form.

    No network access: the egress guard's DNS check is ``_check_destinations``.
    """
    normalized = dict(config)
    if config_type in _GUARDED_TYPES:
        normalized["allow_private_network"] = bool(normalized.get("allow_private_network", False))

    if config_type == "mqtt_private":
        _validate_mqtt_private_config(normalized)
    elif config_type == "mqtt_community":
        _validate_mqtt_community_config(normalized)
    elif config_type == "webhook":
        _validate_webhook_config(normalized)
    elif config_type == "apprise":
        _validate_apprise_config(normalized)
    elif config_type == "sqs":
        _validate_sqs_config(normalized)
    elif config_type == "map_upload":
        _validate_map_upload_config(normalized)
    elif config_type == "mqtt_ha":
        _validate_mqtt_ha_config(normalized)

    return normalized


def _validate_mqtt_private_config(config: dict) -> None:
    """Validate mqtt_private config blob."""
    if not config.get("broker_host"):
        raise HTTPException(status_code=400, detail="broker_host is required for mqtt_private")
    port = config.get("broker_port", 1883)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")


def _validate_mqtt_community_config(config: dict) -> None:
    """Validate mqtt_community config blob. Normalizes IATA to uppercase."""
    broker_host = str(config.get("broker_host", _DEFAULT_COMMUNITY_MQTT_BROKER_HOST)).strip()
    if not broker_host:
        broker_host = _DEFAULT_COMMUNITY_MQTT_BROKER_HOST
    config["broker_host"] = broker_host

    port = config.get("broker_port", _DEFAULT_COMMUNITY_MQTT_BROKER_PORT)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")
    config["broker_port"] = port

    transport = str(config.get("transport", _DEFAULT_COMMUNITY_MQTT_TRANSPORT)).strip().lower()
    if transport not in _ALLOWED_COMMUNITY_MQTT_TRANSPORTS:
        raise HTTPException(
            status_code=400,
            detail="transport must be 'websockets' or 'tcp'",
        )
    config["transport"] = transport
    config["use_tls"] = bool(config.get("use_tls", True))
    config["tls_verify"] = bool(config.get("tls_verify", True))

    auth_mode = str(config.get("auth_mode", _DEFAULT_COMMUNITY_MQTT_AUTH_MODE)).strip().lower()
    if auth_mode not in _ALLOWED_COMMUNITY_MQTT_AUTH_MODES:
        raise HTTPException(
            status_code=400,
            detail="auth_mode must be 'token', 'password', or 'none'",
        )
    config["auth_mode"] = auth_mode
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if auth_mode == "password" and (not username or not password):
        raise HTTPException(
            status_code=400,
            detail="username and password are required when auth_mode is 'password'",
        )
    config["username"] = username
    config["password"] = password

    token_audience = str(config.get("token_audience", "")).strip()
    config["token_audience"] = token_audience

    websocket_path = str(config.get("websocket_path") or "/").strip() or "/"
    if not websocket_path.startswith("/") or any(c.isspace() for c in websocket_path):
        raise HTTPException(
            status_code=400,
            detail="websocket_path must start with '/' and contain no whitespace",
        )
    config["websocket_path"] = websocket_path

    email = str(config.get("email") or "").strip()
    if email and not _is_plausible_email(email):
        raise HTTPException(status_code=400, detail="email must be a valid email address")
    config["email"] = email

    iata = config.get("iata", "").upper().strip()
    if not iata or not _IATA_RE.fullmatch(iata):
        raise HTTPException(
            status_code=400,
            detail="IATA code is required and must be exactly 3 uppercase alphabetic characters",
        )
    config["iata"] = iata

    topic_template = str(
        config.get("topic_template", _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE)
    ).strip()
    if not topic_template:
        topic_template = _DEFAULT_COMMUNITY_MQTT_TOPIC_TEMPLATE

    config["topic_template"] = _normalize_community_topic_template(topic_template)


def _is_plausible_email(email: str) -> bool:
    """One ``@``, no whitespace, a dot inside the domain. No regex: CodeQL flags the
    equivalent pattern as polynomial backtracking."""
    if len(email) > 254 or any(c.isspace() for c in email) or email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain[1:-1]


def _validate_apprise_config(config: dict) -> None:
    """Validate apprise config blob."""
    urls = config.get("urls", "")
    if not isinstance(urls, str) or not urls.strip():
        raise HTTPException(status_code=400, detail="At least one Apprise URL is required")
    try:
        parse_apprise_urls(split_apprise_urls(urls))
    except AppriseUrlRefused as exc:
        raise HTTPException(status_code=400, detail=exc.detail()) from None

    from app.fanout.apprise_mod import FORMAT_VARIABLES, _apply_format

    dummy_vars: dict[str, str] = dict.fromkeys(FORMAT_VARIABLES, "test")
    for field in ("body_format_dm", "body_format_channel"):
        value = config.get(field)
        if value is not None and not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{field} must be a string")
        if isinstance(value, str) and value.strip():
            try:
                _apply_format(value, dummy_vars)
            except Exception:
                raise HTTPException(
                    status_code=400, detail=f"Invalid format string in {field}"
                ) from None

    config["markdown_format"] = bool(config.get("markdown_format", True))
    config["include_outgoing"] = bool(config.get("include_outgoing", False))


def _validate_webhook_config(config: dict) -> None:
    """Validate webhook config blob."""
    url = config.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="url is required for webhook")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must start with http:// or https://")
    method = config.get("method", "POST").upper()
    if method not in ("POST", "PUT", "PATCH"):
        raise HTTPException(status_code=400, detail="method must be POST, PUT, or PATCH")
    headers = config.get("headers", {})
    if not isinstance(headers, dict):
        raise HTTPException(status_code=400, detail="headers must be a JSON object")


def _validate_sqs_config(config: dict) -> None:
    """Validate sqs config blob."""
    queue_url = str(config.get("queue_url", "")).strip()
    if not queue_url:
        raise HTTPException(status_code=400, detail="queue_url is required for sqs")
    if not queue_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="queue_url must start with http:// or https://")

    endpoint_url = str(config.get("endpoint_url", "")).strip()
    if endpoint_url and not endpoint_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=400,
            detail="endpoint_url must start with http:// or https://",
        )

    access_key_id = str(config.get("access_key_id", "")).strip()
    secret_access_key = str(config.get("secret_access_key", "")).strip()
    session_token = str(config.get("session_token", "")).strip()
    has_static_keypair = bool(access_key_id) and bool(secret_access_key)
    has_partial_keypair = bool(access_key_id) != bool(secret_access_key)

    if has_partial_keypair:
        raise HTTPException(
            status_code=400,
            detail="access_key_id and secret_access_key must be set together for sqs",
        )
    if session_token and not has_static_keypair:
        raise HTTPException(
            status_code=400,
            detail="session_token requires access_key_id and secret_access_key for sqs",
        )


def _validate_map_upload_config(config: dict) -> None:
    """Validate and normalize map_upload config blob."""
    api_url = str(config.get("api_url", "")).strip()
    if api_url and not api_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="api_url must start with http:// or https://",
        )
    # Persist the cleaned value (empty string means use the module default)
    config["api_url"] = api_url
    config["dry_run"] = bool(config.get("dry_run", True))
    config["geofence_enabled"] = bool(config.get("geofence_enabled", False))
    try:
        radius = float(config.get("geofence_radius_km", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="geofence_radius_km must be a number") from None
    if radius < 0:
        raise HTTPException(status_code=400, detail="geofence_radius_km must be >= 0")
    config["geofence_radius_km"] = radius


def _validate_mqtt_ha_config(config: dict) -> None:
    """Validate mqtt_ha config blob."""
    if not config.get("broker_host"):
        raise HTTPException(status_code=400, detail="broker_host is required for mqtt_ha")
    port = config.get("broker_port", 1883)
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise HTTPException(status_code=400, detail="broker_port must be between 1 and 65535")
    for field in ("tracked_contacts", "tracked_repeaters"):
        value = config.get(field)
        if value is not None and not isinstance(value, list):
            raise HTTPException(status_code=400, detail=f"{field} must be a list of public keys")


async def _check_destinations(config_type: str, config: dict) -> None:
    """Run the egress guard on the user-chosen destinations of a normalized config.

    Resolves the host names: 400 ``egress_*`` (Apprise: with ``params.line``)
    before anything is stored. The modules check again before each send.
    """
    allow_private = bool(config.get("allow_private_network", False))
    try:
        if config_type == "webhook":
            await validate_destination(str(config["url"]), allow_private_network=allow_private)
        elif config_type == "sqs":
            endpoint_url = str(config.get("endpoint_url", "")).strip()
            if endpoint_url:
                await validate_destination(endpoint_url, allow_private_network=allow_private)
        elif config_type == "map_upload":
            api_url = str(config.get("api_url", "")).strip()
            if api_url:
                await validate_destination(api_url, allow_private_network=allow_private)
        elif config_type == "apprise":
            await validate_apprise_urls(
                split_apprise_urls(str(config.get("urls", ""))),
                allow_private_network=allow_private,
            )
    except EgressBlocked as exc:
        raise HTTPException(status_code=400, detail=egress_detail(exc.code)) from None
    except AppriseUrlRefused as exc:
        raise HTTPException(status_code=400, detail=exc.detail()) from None


def allows_private_network(config_type: str, config: dict) -> bool:
    """A guarded row whose LAN opt-in (``allow_private_network``) is on."""
    return config_type in _GUARDED_TYPES and config.get("allow_private_network") is True


def _carry_grandfathered_flag(config_type: str, config: dict, previous: dict | None) -> None:
    """Keep ``private_network_grandfathered`` (set by migration 100 or a restore of
    an older backup) only while the destination and its opt-in are unchanged.

    The UI shows a one-time banner while it is set; its "Got it" sends false.
    The grandfathered opt-in covers the destination it was granted for: a new
    destination sent with the flag still true (the opt-in echoed back, never
    confirmed) drops ``allow_private_network``. Ticking the box again in the UI
    sends the flag as false, which keeps the opt-in. Only that echo drops it:
    an API client that omits the flag while it changes the destination keeps
    its explicit ``allow_private_network`` (and the flag is cleared).
    """
    if config_type not in _GUARDED_TYPES:
        return
    grandfathered = previous is not None and bool(previous.get("private_network_grandfathered"))
    same_destination = previous is not None and all(
        config.get(f) == previous.get(f) for f in _GUARDED_TYPES[config_type]
    )
    if (
        grandfathered
        and not same_destination
        and config.get("private_network_grandfathered") is True
    ):
        config["allow_private_network"] = False
    keep = (
        grandfathered
        and same_destination
        and config.get("private_network_grandfathered") is not False
        and config.get("allow_private_network") is True
    )
    if keep:
        config["private_network_grandfathered"] = True
    else:
        config.pop("private_network_grandfathered", None)


def _enforce_scope(config_type: str, scope: dict) -> dict:
    """Enforce type-specific scope constraints. Returns normalized scope."""
    if config_type == "mqtt_community":
        return {"messages": "none", "raw_packets": "all"}
    if config_type == "map_upload":
        return {"messages": "none", "raw_packets": "all"}
    if config_type in ("webhook", "apprise", "mqtt_ha"):
        messages = scope.get("messages", "all")
        if messages not in ("all", "none") and not isinstance(messages, dict):
            raise HTTPException(
                status_code=400,
                detail="scope.messages must be 'all', 'none', or a filter object",
            )
        return {"messages": messages, "raw_packets": "none"}
    # For mqtt_private and sqs, validate scope values
    messages = scope.get("messages", "all")
    if messages not in ("all", "none") and not isinstance(messages, dict):
        raise HTTPException(
            status_code=400,
            detail="scope.messages must be 'all', 'none', or a filter object",
        )
    raw_packets = scope.get("raw_packets", "all")
    if raw_packets not in ("all", "none"):
        raise HTTPException(
            status_code=400,
            detail="scope.raw_packets must be 'all' or 'none'",
        )
    return {"messages": messages, "raw_packets": raw_packets}


def present_fanout_config(cfg: dict) -> dict:
    """The API form of a stored row: its secrets write-only (``app/secret_fields.py``)."""
    return {**cfg, "config": redact_config(cfg["type"], cfg.get("config") or {})}


@router.get("", dependencies=[Depends(require("admin"))])
async def list_fanout_configs() -> list[dict]:
    """List all fanout configs."""
    configs = await FanoutConfigRepository.get_all()
    return [present_fanout_config(cfg) for cfg in configs if not is_reserved_fanout_id(cfg["id"])]


@router.get("/{config_id}", dependencies=[Depends(require("admin"))])
async def get_fanout_config(config_id: str) -> dict:
    """One fanout config."""
    existing = (
        None if is_reserved_fanout_id(config_id) else await FanoutConfigRepository.get(config_id)
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")
    return present_fanout_config(existing)


def prepare_fanout_config(
    config_type: str, config: dict, scope: dict, *, restored: bool = False
) -> tuple[dict, dict]:
    """Type check, config validation/normalisation and scope enforcement.

    Shared by the create route and the backup restore (``services/backup.py``).
    Raises ``HTTPException(400)``. No DNS lookup: the create route adds
    ``_check_destinations``; a restored row is checked by its module before
    each send. ``restored``: a row from a backup made before the egress guard
    (no ``allow_private_network`` key) keeps working, grandfathered like
    migration 100 does.
    """
    if config_type not in _VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{config_type}'. Must be one of: {', '.join(sorted(_VALID_TYPES))}",
        )
    legacy = restored and config_type in _GUARDED_TYPES and "allow_private_network" not in config
    normalized = _validate_and_normalize_config(
        config_type, {**config, "allow_private_network": True} if legacy else config
    )
    if legacy:
        normalized["private_network_grandfathered"] = True
    # A restore keeps the file's flag (the row was grandfathered where it came
    # from); a new row never is.
    _carry_grandfathered_flag(config_type, normalized, normalized if restored else None)
    return normalized, _enforce_scope(config_type, scope)


def prepare_refused_apprise_config(config: dict, scope: dict) -> tuple[dict, dict]:
    """The stored form of a restored Apprise row whose URLs are refused here.

    A backup made before the Apprise allowlist may hold a URL Meshloom no
    longer sends to (``egress_scheme``), or a Telegram URL with no chat id.
    Migration 100 keeps such rows; a restore keeps them too, and the caller
    stores them disabled. No URL is parsed: enabling or editing the row runs
    the full check (``prepare_fanout_config`` and ``_check_destinations``).
    """
    normalized = dict(config)
    if "allow_private_network" not in normalized:
        normalized["allow_private_network"] = True
        normalized["private_network_grandfathered"] = True
    normalized["allow_private_network"] = bool(normalized["allow_private_network"])
    normalized["markdown_format"] = bool(normalized.get("markdown_format", True))
    normalized["include_outgoing"] = bool(normalized.get("include_outgoing", False))
    _carry_grandfathered_flag("apprise", normalized, normalized)
    return normalized, _enforce_scope("apprise", scope)


# Bots became local plugins in 5.0.0 (migration 122, app/plugins/bot_conversion.py).
_BOT_RETIRED_DETAIL = {
    "code": "bot_type_retired",
    "message": (
        "Bots are extensions since Meshloom 5.0: manage them in Settings > Extensions "
        "(your bots were converted there)."
    ),
}


def _bot_retired() -> HTTPException:
    return HTTPException(status_code=410, detail=_BOT_RETIRED_DETAIL)


@router.post("", dependencies=[Depends(require("admin"))])
async def create_fanout_config(body: FanoutConfigCreate, response: Response) -> dict:
    """Create a new fanout config. ``type: "bot"`` answers 410 ``bot_type_retired``.

    ``type: "apprise"`` is deprecated (MSHL-134, removed in 5.2.0): still accepted, with a
    ``Deprecation: true`` header; notification channels replace it."""
    if body.type == "bot":
        raise _bot_retired()
    if body.type not in _VALID_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid type '{body.type}'. Must be one of: {', '.join(sorted(_VALID_TYPES))}",
        )

    if body.type == "apprise":
        response.headers["Deprecation"] = "true"
        logger.warning("Fanout type apprise is deprecated: use a notification channel")

    # A new row has no stored secret: a sentinel sent back reads as unset.
    config = merge_secrets(body.type, None, body.config)
    # Only a conversion marks a row converted (a restore keeps the mark: backup.py).
    for key in CONVERSION_KEYS:
        config.pop(key, None)
    normalized_config, scope = prepare_fanout_config(body.type, config, body.scope)
    if body.enabled:
        await _check_destinations(body.type, normalized_config)

    cfg = await FanoutConfigRepository.create(
        config_type=body.type,
        name=body.name,
        config=normalized_config,
        scope=scope,
        enabled=body.enabled,
    )

    # Start the module if enabled
    if cfg["enabled"]:
        await fanout_manager.reload_config(cfg["id"])

    logger.info("Created fanout config %s (type=%s, name=%s)", cfg["id"], body.type, body.name)
    return present_fanout_config(cfg)


def _is_new_secret(value: Any) -> bool:
    """Whether a secret value in a PATCH types something new (not kept with ``********``)."""
    if value is None or value == REDACTED:
        return False
    if isinstance(value, dict):
        return not any(v == REDACTED for v in value.values())
    return True


def _sends_unreadable_secrets(config: dict | None, unreadable: list[str]) -> bool:
    """Whether ``config`` types a new value for every secret the key cannot open."""
    if config is None:
        return False
    return all(key in config and _is_new_secret(config[key]) for key in unreadable)


def _config_edited(existing: dict, config: dict) -> bool:
    """Whether ``config`` changes anything in ``existing``: a non-secret setting, or a
    secret typed anew (the editor sends a kept secret back as ``********``)."""
    secret = set(secret_keys(existing["type"]))
    shown_only = set(derived_keys(existing["type"]))
    current = existing.get("config") or {}
    return any(
        _is_new_secret(v) if k in secret else current.get(k) != v
        for k, v in config.items()
        if k not in shown_only
    )


@router.patch("/{config_id}", dependencies=[Depends(require("admin"))])
async def update_fanout_config(
    config_id: str,
    body: FanoutConfigUpdate,
) -> dict:
    """Update a fanout config. Triggers module reload. A bot converted by the 5.0.0
    switch answers 410 ``bot_type_retired``."""
    if is_reserved_fanout_id(config_id):
        raise HTTPException(status_code=403, detail="Reserved system module")
    existing = await FanoutConfigRepository.get(config_id)
    if existing is None:
        if await bot_conversion.is_archived_bot(config_id):
            raise _bot_retired()
        raise HTTPException(status_code=404, detail="Fanout config not found")
    if existing["type"] == "bot":
        raise _bot_retired()

    kwargs = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if body.scope is not None:
        kwargs["scope"] = _enforce_scope(existing["type"], body.scope)

    unreadable = existing.get("secret_unreadable") or []
    if unreadable and not _sends_unreadable_secrets(body.config, unreadable):
        # A stored secret the key cannot open (MSHL-133): the config cannot be checked
        # without it. An edit that leaves the config as it is (name, switch, scope) is
        # saved; the module stays stopped with ``secret_unreadable`` until the secret is
        # typed again. Changing other settings needs the secret too: 409.
        if body.config is not None and _config_edited(existing, body.config):
            raise HTTPException(status_code=409, detail="secret_unreadable")
        updated = await FanoutConfigRepository.update(config_id, **kwargs)
        if updated is None:
            raise HTTPException(status_code=404, detail="Fanout config not found")
        await fanout_manager.reload_config(config_id)
        logger.info("Updated fanout config %s (unreadable secret kept)", config_id)
        return present_fanout_config(updated)

    # Secrets are write-only: ``********`` or an omitted key keeps the stored
    # value, ``""`` clears it. Merged before validation, so the grandfathered
    # same-destination check compares the real URLs.
    # A kept secret never follows a destination change: 400 secret_required.
    try:
        config_to_validate = (
            merge_secrets(existing["type"], existing["config"], body.config)
            if body.config is not None
            else existing["config"]
        )
    except SecretRequiredError as exc:
        raise HTTPException(status_code=400, detail=exc.detail()) from None
    normalized_config = _validate_and_normalize_config(existing["type"], config_to_validate)
    _carry_grandfathered_flag(existing["type"], normalized_config, existing["config"])
    # A converted (or converting) row stays marked (MSHL-134) whatever config an edit sends.
    for key in CONVERSION_KEYS:
        if existing["config"].get(key):
            normalized_config[key] = existing["config"][key]
        else:
            normalized_config.pop(key, None)
    # A disabled row sends nothing: it can be saved (or turned off) while its
    # host does not resolve. Enabling it runs the check.
    will_be_enabled = body.enabled if body.enabled is not None else bool(existing["enabled"])
    if will_be_enabled:
        await _check_destinations(existing["type"], normalized_config)
    kwargs["config"] = normalized_config

    updated = await FanoutConfigRepository.update(config_id, **kwargs)
    if updated is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    # Reload the module to pick up changes
    await fanout_manager.reload_config(config_id)

    logger.info("Updated fanout config %s", config_id)
    return present_fanout_config(updated)


class ConvertBody(BaseModel):
    """``channel_id``: attach the row to this existing channel (the duplicate prompt)."""

    channel_id: str | None = Field(default=None, min_length=1, max_length=64)


@router.post(
    "/{config_id}/convert-to-notification-channel", dependencies=[Depends(require("admin"))]
)
async def convert_to_notification_channel(
    config_id: str, request: Request, body: ConvertBody | None = None
) -> dict:
    """Convert a fanout Apprise row into a notification channel (MSHL-134,
    ``app/notifications/fanout_convert.py``). Idempotent. The response carries the channel
    and the row by id, name, type and state only (no ``config``, so no URL, secret or
    redacted URL), ``already_converted`` and ``notes``."""
    from app.notifications import api_support
    from app.notifications.channels import ChannelError
    from app.notifications.fanout_convert import ConversionError, convert_fanout_row
    from app.routers.notifications import _reconcile_heartbeats

    try:
        result = await convert_fanout_row(config_id, into=body.channel_id if body else None)
    except ConversionError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "params": exc.params}
        ) from None
    except ChannelError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail()) from None
    if result.created:
        await api_support.announce_channel_change(request, "created", result.channel)
        await _reconcile_heartbeats()
    channel, row = result.channel, result.fanout
    row_config = row.get("config") or {}
    return {
        "channel": {
            "id": channel.id,
            "name": channel.name,
            "type": channel.type,
            "preset": channel.preset,
            "enabled": channel.enabled,
        },
        "fanout": {
            "id": str(row["id"]),
            "name": str(row.get("name") or ""),
            "type": row.get("type"),
            "enabled": bool(row.get("enabled")),
            CONVERTED_TO: row_config.get(CONVERTED_TO),
        },
        "already_converted": result.already_converted,
        "notes": result.notes,
    }


@router.delete("/{config_id}", dependencies=[Depends(require("admin"))])
async def delete_fanout_config(config_id: str) -> dict:
    """Delete a fanout config."""
    if is_reserved_fanout_id(config_id):
        raise HTTPException(status_code=403, detail="Reserved system module")
    existing = await FanoutConfigRepository.get(config_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Fanout config not found")

    # Stop the module first
    await fanout_manager.remove_config(config_id)
    await FanoutConfigRepository.delete(config_id)

    logger.info("Deleted fanout config %s", config_id)
    return {"deleted": True}
