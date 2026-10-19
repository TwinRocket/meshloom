"""Notification channels: the models and the type registry.

A channel is one destination of a given type (``web_push``, ``email``, ``webhook``,
``apprise``; ``ntfy``, ``discord`` and the presets come with their own tickets). Each
type declares a Pydantic config model; ``model_json_schema()`` of it is the JSON Schema
the shared schema-driven form renders. Secret fields are ``writeOnly``: they are stored
apart (``notification_channels.secrets``) and no read path returns them, only the names
of the ones that hold a value (``Channel.secret_fields_set``).

Labels and help texts live in the frontend: the schema only carries i18n keys
(``label_key``/``help_key`` through ``json_schema_extra``).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

IncludeMessageText = Literal["always", "public_only", "never"]
ChannelLanguage = Literal["auto", "fr", "en"]

BUILTIN_PUSH_ID = "builtin:push"


class ChannelError(Exception):
    """A refused channel operation. ``code`` is stable (translated by the UI) and
    ``status`` the HTTP status the route answers. Never carries a secret."""

    def __init__(self, code: str, status: int = 400, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "params": self.params}


def _field(
    label: str,
    *,
    default: Any = ...,
    secret: bool = False,
    help_key: str | None = None,
    **kwargs: Any,
) -> Any:
    extra: dict[str, Any] = {"label_key": f"notifications.channels.fields.{label}"}
    if help_key:
        extra["help_key"] = f"notifications.channels.help.{help_key}"
    if secret:
        extra["writeOnly"] = True
    return Field(default, json_schema_extra=extra, **kwargs)


class _ConfigBase(BaseModel):
    # Secret fields are SecretStr: a ValidationError must not echo the input.
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid", populate_by_name=True)


class WebPushConfig(_ConfigBase):
    """Web Push has no settings of its own: the browsers' subscriptions are the targets."""


class EmailConfig(_ConfigBase):
    host: str = _field("host", default="", max_length=255)
    port: int = _field("port", default=587, ge=1, le=65535)
    mode: Literal["none", "starttls", "ssl"] = _field("mode", default="starttls")
    user: str = _field("user", default="", max_length=255)
    password: SecretStr | None = _field("password", default=None, secret=True, max_length=1024)
    from_address: str = Field(
        "",
        alias="from",
        max_length=255,
        json_schema_extra={"label_key": "notifications.channels.fields.from"},
    )
    # Recipients, one per line (commas and semicolons work too); a single rejected
    # recipient fails the whole delivery.
    to: str = _field("to", default="", help_key="email_to", max_length=2048)
    cc: str = _field("cc", default="", help_key="email_to", max_length=2048)


class WebhookConfig(_ConfigBase):
    url: SecretStr | None = _field(
        "url", default=None, secret=True, help_key="webhook_url", max_length=2048
    )
    hmac_secret: SecretStr | None = _field(
        "hmac_secret", default=None, secret=True, max_length=1024
    )
    # Set by the migration for a webhook saved before the egress guard (one-time banner).
    private_network_grandfathered: bool = Field(
        False, json_schema_extra={"readOnly": True, "hidden": True}
    )


class AppriseConfig(_ConfigBase):
    urls: SecretStr | None = _field(
        "urls", default=None, secret=True, help_key="apprise_urls", max_length=8192
    )
    format: Literal["text", "markdown"] = _field("format", default="text")


@dataclass(frozen=True)
class Limits:
    """Default send limits; ``None`` means no cap for that window."""

    per_min: int | None
    per_hour: int | None
    per_day: int | None


@dataclass(frozen=True)
class ChannelType:
    id: str
    config_model: type[_ConfigBase]
    default_limits: Limits
    # Fields that must hold a value when a channel is created.
    required: tuple[str, ...] = ()
    # A third-party service (include_message_text defaults to ``public_only``).
    cloud: bool = False
    quiet_hours_exempt_default: bool = False
    singleton_id: str | None = None
    # The minimal limiter's gap between two sends of one channel (the dispatcher).
    min_interval_s: float = 0.0
    # (config field, ...) a kept secret must not follow a change of: see ``destination_keys``.
    destination_keys: tuple[str, ...] = ()
    secret_follows: tuple[str, ...] = ()
    # Decides ``cloud`` from the public config, for a type whose destination is
    # chosen by the user (e-mail: a public SMTP relay is a third party).
    cloud_check: Callable[[dict[str, Any]], bool] | None = None

    @property
    def secret_fields(self) -> frozenset[str]:
        return frozenset(
            name
            for name, field in self.config_model.model_fields.items()
            if (field.json_schema_extra or {}).get("writeOnly")  # type: ignore[union-attr]
        )

    @property
    def include_message_text_default(self) -> IncludeMessageText:
        return "public_only" if self.cloud else "always"

    def include_message_text_for(self, public: dict[str, Any]) -> IncludeMessageText:
        """The default text policy of a new channel with this public config."""
        cloud = self.cloud_check(public) if self.cloud_check else self.cloud
        return "public_only" if cloud else "always"

    def json_schema(self) -> dict[str, Any]:
        """The JSON Schema served to the form: secret fields are ``writeOnly``."""
        return self.config_model.model_json_schema(by_alias=True)


def email_is_cloud(public: dict[str, Any]) -> bool:
    """A mail channel is a third party unless its SMTP host is clearly local: a private
    or loopback address, a single-label name, or a ``.local``/``.lan``/``.internal``
    style suffix. No DNS lookup: this only picks the default text policy."""
    host = str(public.get("host") or "").strip().strip("[]").lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        local_suffixes = (".local", ".lan", ".home", ".internal", ".home.arpa", ".localdomain")
        return "." in host and not host.endswith(local_suffixes)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local)


REGISTRY: dict[str, ChannelType] = {
    entry.id: entry
    for entry in (
        ChannelType(
            "web_push",
            WebPushConfig,
            Limits(60, 600, None),
            singleton_id=BUILTIN_PUSH_ID,
        ),
        ChannelType(
            "email",
            EmailConfig,
            Limits(6, 60, 200),
            required=("host", "to"),
            min_interval_s=10.0,
            destination_keys=("host", "port", "mode"),
            secret_follows=("password",),
            cloud_check=email_is_cloud,
        ),
        ChannelType(
            "webhook",
            WebhookConfig,
            Limits(60, 600, None),
            required=("url",),
            quiet_hours_exempt_default=True,
            destination_keys=("url",),
            secret_follows=("hmac_secret",),
        ),
        ChannelType(
            "apprise",
            AppriseConfig,
            Limits(30, 300, None),
            required=("urls",),
            min_interval_s=2.0,
        ),
    )
}


# The minimal limiter's gap for a preset (an Apprise service) or a native type that
# is not registered yet; a preset wins over its type.
PRESET_MIN_INTERVAL_S: dict[str, float] = {"telegram": 3.0, "discord": 2.0, "ntfy": 1.0}


def min_interval_s(type_id: str, preset: str | None = None) -> float:
    """Seconds between two sends of one channel (the dispatcher's minimal limiter)."""
    if preset in PRESET_MIN_INTERVAL_S:
        return PRESET_MIN_INTERVAL_S[preset]
    entry = REGISTRY.get(type_id)
    if entry is not None:
        return entry.min_interval_s
    return PRESET_MIN_INTERVAL_S.get(type_id, 0.0)


def get_type(type_id: str) -> ChannelType:
    entry = REGISTRY.get(type_id)
    if entry is None:
        raise ChannelError("channel_type_unknown", type=type_id)
    return entry


def types_schema() -> list[dict[str, Any]]:
    """What ``GET /api/notifications/types`` serves: one entry per registered type."""
    return [
        {
            "type": entry.id,
            "schema": entry.json_schema(),
            "cloud": entry.cloud,
            "singleton": entry.singleton_id is not None,
            "preset": False,
            "default_limits": {
                "per_min": entry.default_limits.per_min,
                "per_hour": entry.default_limits.per_hour,
                "per_day": entry.default_limits.per_day,
            },
            "quiet_hours_exempt_default": entry.quiet_hours_exempt_default,
            "include_message_text_default": entry.include_message_text_default,
        }
        for entry in REGISTRY.values()
    ]


def is_builtin(channel_id: str) -> bool:
    return channel_id.startswith("builtin:")


class Channel(BaseModel):
    """A channel as every read path returns it: no secret value, ever."""

    id: str
    type: str
    preset: str | None = None
    name: str
    enabled: bool = True
    language: ChannelLanguage = "auto"
    config: dict[str, Any] = Field(default_factory=dict)
    secret_fields_set: list[str] = Field(default_factory=list)
    allow_private_network: bool = False
    include_message_text: IncludeMessageText = "always"
    verify_tls: bool = True
    quiet_hours_exempt: bool = False
    # ``None``: the registry default of the type.
    rate_per_min: int | None = None
    rate_per_hour: int | None = None
    rate_per_day: int | None = None
    sort_order: int = 0
    created_at: int = 0
    last_ok_at: int | None = None
    last_error: str | None = None
    failure_count: int = 0
    paused_until: int | None = None
    deletable: bool = True


class ChannelCreate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    type: str
    name: str = Field(min_length=1, max_length=80)
    id: str | None = None
    preset: str | None = None
    enabled: bool = True
    language: ChannelLanguage = "auto"
    # Public and secret fields together; the repository splits them by the registry.
    config: dict[str, Any] = Field(default_factory=dict)
    allow_private_network: bool = False
    include_message_text: IncludeMessageText | None = None
    verify_tls: bool = True
    quiet_hours_exempt: bool | None = None
    rate_per_min: int | None = Field(None, ge=1)
    rate_per_hour: int | None = Field(None, ge=1)
    rate_per_day: int | None = Field(None, ge=1)


class ChannelUpdate(BaseModel):
    """PATCH body. Omitted fields stay; in ``config`` an omitted or null secret is kept
    and an empty string clears it. ``type`` is accepted only to be refused when it
    differs (400 ``type_immutable``)."""

    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    type: str | None = None
    name: str | None = Field(None, min_length=1, max_length=80)
    enabled: bool | None = None
    language: ChannelLanguage | None = None
    config: dict[str, Any] | None = None
    allow_private_network: bool | None = None
    include_message_text: IncludeMessageText | None = None
    verify_tls: bool | None = None
    quiet_hours_exempt: bool | None = None
    rate_per_min: int | None = Field(None, ge=1)
    rate_per_hour: int | None = Field(None, ge=1)
    rate_per_day: int | None = Field(None, ge=1)
