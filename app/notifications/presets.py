"""Presets: Apprise services described as data.

A preset is an Apprise URL template with named fields (``tgram://{bot_token}/{chat_id}``),
no code. Everything else derives from it: the Pydantic config model (hence the JSON
Schema the shared form renders, secret fields ``writeOnly``), the registry entry
(limits, ``cloud``, the secrets that follow a destination), the URL validation (the
generated URL goes through ``app.net.apprise_urls``, so the egress guard) and the
send (``AppriseTransport`` compiles the URL from the stored fields).

A channel of a preset is stored as type ``apprise`` with ``preset = <id>``: its public
fields in ``config``, its secret ones in ``secrets``, never the URL. The URL exists only
inside ``compile_url`` at validation and send time.

Template syntax:

- ``{name}``: the field's value, percent-encoded as one path segment or query value
  (a field left empty takes its ``default``);
- ``{name.scheme}``, ``{name.netloc}``, ``{name.path}``: the parts of a ``url`` field
  (``scheme`` through the field's ``scheme_map``);
- ``[...]``: an optional group, left out when a placeholder inside it is empty.

To add a service, append a ``Preset`` to ``PRESETS`` and its texts to the
``settings.notifications.types.<id>`` and ``notifications.presets.<id>`` keys of the
frontend (``tests/test_apprise_presets.py`` fails on a preset without them).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, create_model, field_validator

from app.notifications.channels import ChannelError, ChannelType, Limits, _ConfigBase

FieldKind = Literal["text", "url", "choice"]
URL_PARTS = ("scheme", "netloc", "path")


@dataclass(frozen=True)
class PresetField:
    name: str
    kind: FieldKind = "text"
    secret: bool = False
    required: bool = False
    # ``re.fullmatch`` pattern of the value (text fields), ASCII only: write ``[0-9]`` and
    # ``[A-Za-z0-9_]``, never ``\d`` or ``\w`` (Pydantic's JSON Schema pattern is Unicode).
    pattern: str | None = None
    max_length: int = 256
    default: str = ""
    # ``choice``: the allowed values.
    choices: tuple[str, ...] = ()
    # ``url``: the Apprise scheme for each URL scheme (``{"http": "gotify", ...}``).
    scheme_map: Mapping[str, str] = field(default_factory=dict)
    # ``url``: the service has no sub-path (a path is refused, not dropped).
    no_path: bool = False
    # A change of this field must not keep a stored secret (``secret_follows``).
    destination: bool = False
    # The field has a help text (``notifications.presets.<id>.help.<name>``).
    help: bool = False
    # A dummy value that passes: the placeholder of the form and the generic tests' input.
    example: str = ""


@dataclass(frozen=True)
class Preset:
    id: str
    template: str
    fields: tuple[PresetField, ...]
    default_limits: Limits
    format: Literal["text", "markdown"] = "text"
    cloud: bool = False
    # Seconds between two sends of a channel of this preset (the dispatcher).
    min_interval_s: float = 0.0
    wiki_url: str = ""
    base_type: str = "apprise"
    # The starting text policy of a new channel; ``None``: ``public_only`` when ``cloud``,
    # else ``always``. SMS presets start on ``never`` (a title fits one SMS).
    text_policy: Literal["always", "public_only", "never"] | None = None

    @property
    def help_key(self) -> str:
        return f"notifications.presets.{self.id}.about"


PRESETS: tuple[Preset, ...] = (
    Preset(
        "telegram",
        "tgram://{bot_token}/{chat_id}[:{topic_id}]",
        (
            PresetField(
                "bot_token",
                secret=True,
                required=True,
                pattern=r"[0-9]+:[A-Za-z0-9_-]+",
                max_length=130,
                help=True,
                example="123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            ),
            # A user id (positive), a group (negative) or a public ``@channel``.
            PresetField(
                "chat_id",
                required=True,
                pattern=r"-?[0-9]{1,32}|@[A-Za-z][A-Za-z0-9_]{3,63}",
                max_length=64,
                help=True,
                example="-1001234567",
            ),
            PresetField("topic_id", pattern=r"[0-9]{1,12}", max_length=12, help=True, example="7"),
        ),
        # 15 a minute: Telegram allows about 20 messages a minute to one group.
        Limits(15, 200, None),
        cloud=True,
        min_interval_s=3.0,
        wiki_url="https://core.telegram.org/bots/tutorial",
    ),
    Preset(
        "gotify",
        "{server.scheme}://{server.netloc}{server.path}/{token}/?priority={priority}",
        (
            PresetField(
                "server",
                kind="url",
                required=True,
                max_length=255,
                scheme_map={"http": "gotify", "https": "gotifys"},
                destination=True,
                help=True,
                example="https://gotify.example.org",
            ),
            PresetField(
                "token",
                secret=True,
                required=True,
                pattern=r"[A-Za-z0-9_.-]{4,128}",
                help=True,
                example="Ab12cd34Ef56",
            ),
            PresetField(
                "priority",
                kind="choice",
                choices=("low", "moderate", "normal", "high", "emergency"),
                default="normal",
            ),
        ),
        Limits(60, None, None),
        wiki_url="https://gotify.net/docs/pushmsg",
    ),
    Preset(
        "home_assistant",
        "{url.scheme}://{url.netloc}/{service}?accesstoken={token}",
        (
            PresetField(
                "url",
                kind="url",
                required=True,
                max_length=255,
                scheme_map={"http": "hassio", "https": "hassios"},
                no_path=True,
                destination=True,
                help=True,
                example="http://homeassistant.local:8123",
            ),
            PresetField(
                "token",
                secret=True,
                required=True,
                max_length=2048,
                help=True,
                example="eyJhbGciOiJIUzI1NiJ9.dummy-payload.dummy-signature",
            ),
            # ``notify.mobile_app_<device>``; the ``notify`` domain is the default.
            PresetField(
                "service",
                required=True,
                pattern=r"[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?",
                max_length=130,
                help=True,
                example="mobile_app_phone",
            ),
        ),
        Limits(60, None, None),
        wiki_url="https://www.home-assistant.io/integrations/notify/",
    ),
)

_PHONE = r"\+[0-9]{6,15}"
_DEVICE = r"[A-Za-z0-9_-]{1,25}"
_PRIORITIES = ("low", "moderate", "normal", "high", "emergency")
_SLACK_PART = r"[A-Za-z0-9]{6,64}"

PRESETS = PRESETS + (
    Preset(
        "pushover",
        "pover://{user_key}@{app_token}[/{devices}]?priority={priority}&sound={sound}",
        (
            PresetField(
                "user_key",
                secret=True,
                required=True,
                pattern=r"[A-Za-z0-9]{30}",
                help=True,
                example="uQiRzpo4DXghDmr9QzzfQu27cmVRsG",
            ),
            PresetField(
                "app_token",
                secret=True,
                required=True,
                pattern=r"[A-Za-z0-9]{30}",
                help=True,
                example="azGDORePK8gMaC0QOYAMyEEuzJnyUi",
            ),
            PresetField(
                "devices",
                pattern=rf"{_DEVICE}(,{_DEVICE}){{0,9}}",
                max_length=260,
                help=True,
                example="phone,tablet",
            ),
            PresetField("priority", kind="choice", choices=_PRIORITIES, default="normal"),
            PresetField("sound", pattern=r"[A-Za-z]{1,12}", max_length=12, default="pushover"),
        ),
        # 10 a minute and 100 an hour keep a busy mesh well under the application's monthly
        # quota (10 000 messages on the free plan, see the help text).
        Limits(10, 100, None),
        cloud=True,
        min_interval_s=1.0,
        wiki_url="https://pushover.net/api",
    ),
    Preset(
        "matrix",
        "{server.scheme}://[{username}:]{credential}@{server.netloc}/{room}",
        (
            PresetField(
                "server",
                kind="url",
                required=True,
                max_length=255,
                scheme_map={"http": "matrix", "https": "matrixs"},
                no_path=True,
                destination=True,
                help=True,
                example="https://matrix.example.org",
            ),
            PresetField("username", max_length=255, destination=True, help=True, example="bot"),
            # An access token alone, or the password of ``username``.
            PresetField(
                "credential",
                secret=True,
                required=True,
                max_length=2048,
                help=True,
                example="syt_dummy_access_token",
            ),
            PresetField(
                "room",
                required=True,
                pattern=r"[!#][A-Za-z0-9._=/+-]{1,128}:[A-Za-z0-9.:-]{1,255}",
                max_length=400,
                destination=True,
                help=True,
                example="!AbCdEfGh:matrix.example.org",
            ),
        ),
        Limits(30, None, None),
        format="markdown",
        cloud=True,
        wiki_url="https://spec.matrix.org/latest/client-server-api/",
    ),
    Preset(
        "signal",
        "{server.scheme}://[{username}:{password}@]{server.netloc}/{from_number}/{to}",
        (
            PresetField(
                "server",
                kind="url",
                required=True,
                max_length=255,
                scheme_map={"http": "signal", "https": "signals"},
                no_path=True,
                destination=True,
                help=True,
                example="http://signal-api.lan:8080",
            ),
            # Only when a reverse proxy in front of the REST server asks for basic auth.
            PresetField("username", max_length=128, destination=True, example="meshloom"),
            PresetField(
                "password",
                secret=True,
                max_length=256,
                help=True,
                example="dummy-password",
            ),
            PresetField(
                "from_number",
                required=True,
                pattern=_PHONE,
                max_length=16,
                destination=True,
                help=True,
                example="+33600000000",
            ),
            PresetField(
                "to",
                required=True,
                pattern=rf"{_PHONE}(,{_PHONE}){{0,9}}",
                max_length=170,
                help=True,
                example="+33611111111",
            ),
        ),
        Limits(20, 200, None),
        wiki_url="https://bbernhard.github.io/signal-cli-rest-api/",
    ),
    Preset(
        "slack",
        "slack://{token_a}/{token_b}/{token_c}",
        (
            # The workspace and the webhook ids are identifiers; the last part is the secret.
            PresetField(
                "token_a",
                required=True,
                pattern=_SLACK_PART,
                max_length=64,
                help=True,
                example="T0AAAAAAA",
            ),
            PresetField(
                "token_b",
                required=True,
                pattern=_SLACK_PART,
                max_length=64,
                example="B0BBBBBBB",
            ),
            PresetField(
                "token_c",
                secret=True,
                required=True,
                pattern=_SLACK_PART,
                help=True,
                example="Xx1Yy2Zz3Aa4Bb5Cc6Dd7Ee8",
            ),
        ),
        # Slack accepts about one message a second on an incoming webhook.
        Limits(30, None, None),
        cloud=True,
        min_interval_s=1.0,
        wiki_url="https://api.slack.com/messaging/webhooks",
    ),
    Preset(
        "mattermost",
        "{server.scheme}://{server.netloc}{server.path}/{token}",
        (
            PresetField(
                "server",
                kind="url",
                required=True,
                max_length=255,
                scheme_map={"http": "mmost", "https": "mmosts"},
                destination=True,
                help=True,
                example="https://mattermost.example.org",
            ),
            PresetField(
                "token",
                secret=True,
                required=True,
                pattern=r"[A-Za-z0-9]{6,64}",
                help=True,
                example="abcdefghijklmnopqrstuvwxyz",
            ),
        ),
        Limits(30, None, None),
        format="markdown",
        wiki_url="https://developers.mattermost.com/integrate/webhooks/incoming/",
    ),
    Preset(
        "rocketchat",
        "{server.scheme}://{username}:{password}@{server.netloc}/{room}?mode={auth}",
        (
            PresetField(
                "server",
                kind="url",
                required=True,
                max_length=255,
                scheme_map={"http": "rocket", "https": "rockets"},
                no_path=True,
                destination=True,
                help=True,
                example="https://chat.example.org",
            ),
            PresetField(
                "username", required=True, max_length=128, destination=True, example="meshloom"
            ),
            # The account's password, or a personal access token (``auth`` = token).
            PresetField(
                "password",
                secret=True,
                required=True,
                max_length=256,
                help=True,
                example="dummy-password",
            ),
            PresetField(
                "room",
                required=True,
                pattern=r"[#@]?[A-Za-z0-9._-]{1,128}",
                max_length=130,
                help=True,
                example="#general",
            ),
            PresetField("auth", kind="choice", choices=("basic", "token"), default="basic"),
        ),
        Limits(30, None, None),
        wiki_url="https://developer.rocket.chat/apidocs/",
    ),
    Preset(
        "sms_free",
        "freemobile://{user}@{api_key}",
        (
            PresetField(
                "user",
                required=True,
                pattern=r"[0-9]{8}",
                max_length=8,
                help=True,
                example="12345678",
            ),
            PresetField(
                "api_key",
                secret=True,
                required=True,
                pattern=r"[A-Za-z0-9]{8,64}",
                help=True,
                example="AbCdEf1234567",
            ),
        ),
        # Free Mobile refuses a burst: keep to a few SMS a minute and a few dozen a day.
        Limits(2, 20, 100),
        cloud=True,
        min_interval_s=10.0,
        text_policy="never",
        wiki_url="https://mobile.free.fr/account/mes-options/notifications-sms",
    ),
    Preset(
        "sms_sfr",
        "sfr://{username}:{password}@{space_id}/{to}",
        (
            PresetField("username", required=True, max_length=128, help=True, example="meshloom"),
            PresetField(
                "password", secret=True, required=True, max_length=256, example="dummy-password"
            ),
            PresetField(
                "space_id",
                required=True,
                pattern=r"[A-Za-z0-9_-]{1,64}",
                max_length=64,
                destination=True,
                help=True,
                example="space-1",
            ),
            PresetField(
                "to",
                required=True,
                pattern=rf"{_PHONE}(,{_PHONE}){{0,9}}",
                max_length=170,
                help=True,
                example="+33600000000",
            ),
        ),
        Limits(2, 20, 100),
        cloud=True,
        min_interval_s=10.0,
        text_policy="never",
        wiki_url="https://www.sfr.fr/",
    ),
)

PRESETS_BY_ID: dict[str, Preset] = {preset.id: preset for preset in PRESETS}

_PLACEHOLDER = re.compile(r"\{(?P<name>[a-z_]+)(?:\.(?P<part>[a-z]+))?\}")
_OPTIONAL = re.compile(r"\[([^\[\]]*)\]")


class PresetError(Exception):
    """A preset that cannot be compiled (a missing or invalid value). Carries no value."""

    def __init__(self, code: str, fields: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.fields = fields or []


def _full(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value, re.ASCII) is not None


def split_url(value: str) -> tuple[str, str, str]:
    """``(scheme, netloc, path)`` of a validated ``url`` field value."""
    parts = urlsplit(value)
    return parts.scheme, parts.netloc, parts.path.rstrip("/")


def _check_url(value: str, spec: PresetField) -> str:
    value = value.strip()
    if not value:
        return value
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises on an invalid port
    except ValueError:
        raise ValueError("not a valid URL") from None
    if parts.scheme not in spec.scheme_map or not parts.hostname:
        raise ValueError("must be an http(s) URL")
    if parts.username is not None or parts.password is not None:
        # The config is returned by the API: credentials have their own write-only field.
        raise ValueError("must not carry a user name or password")
    if parts.query or parts.fragment or ";" in parts.path:
        raise ValueError("must not have a query or a fragment")
    if ".." in parts.path or "%" in parts.path:
        # The path is percent-encoded once when the URL is built: a typed "%" would be
        # encoded twice, and ".." would climb out of the prefix the user meant.
        raise ValueError("must not contain '..' or '%' in its path")
    if spec.no_path and parts.path.rstrip("/"):
        raise ValueError("must not have a path")
    return value.rstrip("/")


def _check_value(value: str, spec: PresetField) -> str:
    if spec.kind == "url":
        return _check_url(value, spec)
    value = value.strip()
    if spec.kind == "choice":
        if value and value not in spec.choices:
            raise ValueError("not an allowed value")
        return value
    if value and spec.pattern and not _full(spec.pattern, value):
        raise ValueError("not a valid value")
    return value


def _json_schema_pattern(spec: PresetField) -> str | None:
    if spec.pattern is None or spec.secret:
        return None
    return f"^(?:{spec.pattern})$" if spec.required else f"^(?:|{spec.pattern})$"


def _model_field(preset: Preset, spec: PresetField) -> tuple[Any, Any]:
    extra: dict[str, Any] = {"label_key": f"notifications.presets.{preset.id}.fields.{spec.name}"}
    if spec.help:
        extra["help_key"] = f"notifications.presets.{preset.id}.help.{spec.name}"
    if spec.secret:
        extra["writeOnly"] = True
        # Required secrets are checked by the registry (``required``), like the natives'.
        return SecretStr | None, Field(None, max_length=spec.max_length, json_schema_extra=extra)
    kwargs: dict[str, Any] = {"max_length": spec.max_length, "json_schema_extra": extra}
    if spec.kind == "url":
        extra["format"] = "uri"
    if spec.kind == "choice":
        extra["enum"] = list(spec.choices)
    pattern = _json_schema_pattern(spec)
    if pattern and spec.kind == "text":
        kwargs["pattern"] = pattern
    if spec.required:
        # A missing public required field is an ``invalid_config`` naming it.
        return str, Field(..., min_length=1, **kwargs)
    return str, Field(spec.default, **kwargs)


def build_config_model(preset: Preset) -> type[_ConfigBase]:
    """The Pydantic model of a preset: its schema is what the shared form renders."""
    specs = {spec.name: spec for spec in preset.fields}

    def validate(cls: type, value: Any, info: Any) -> Any:
        spec = specs[info.field_name]
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
            if raw and spec.pattern and not _full(spec.pattern, raw.strip()):
                raise ValueError("not a valid value")
            return SecretStr(raw.strip()) if raw else value
        return _check_value(value, spec)

    definitions = {spec.name: _model_field(preset, spec) for spec in preset.fields}
    model = create_model(
        f"{preset.id.title().replace('_', '')}Config",
        __base__=_ConfigBase,
        __validators__={
            "_check": field_validator(*specs, mode="after")(classmethod(validate)),  # type: ignore[dict-item]
        },
        **definitions,  # type: ignore[arg-type]
    )
    return model


def preset_channel_type(preset: Preset) -> ChannelType:
    """The registry entry of a preset: the data above, nothing more."""
    return ChannelType(
        preset.id,
        build_config_model(preset),
        preset.default_limits,
        required=tuple(spec.name for spec in preset.fields if spec.required and spec.secret),
        cloud=preset.cloud,
        min_interval_s=preset.min_interval_s,
        destination_keys=tuple(spec.name for spec in preset.fields if spec.destination),
        secret_follows=tuple(spec.name for spec in preset.fields if spec.secret),
        preset=preset,
    )


def _value_of(spec: PresetField, values: Mapping[str, Any]) -> str:
    raw = values.get(spec.name)
    if isinstance(raw, SecretStr):
        raw = raw.get_secret_value()
    text = str(raw).strip() if raw is not None else ""
    return text or spec.default


def _render(preset: Preset, text: str, values: Mapping[str, Any]) -> str:
    specs = {spec.name: spec for spec in preset.fields}
    missing: list[str] = []

    def one(match: re.Match[str]) -> str:
        spec = specs.get(match["name"])
        if spec is None:
            raise PresetError("preset_template_invalid")
        value = _value_of(spec, values)
        part = match["part"]
        if not value:
            missing.append(spec.name)
            return ""
        if part is None:
            # ":" stays: a Telegram token reads ``123:ABC`` in the URL's host position.
            return quote(value, safe=":")
        if spec.kind != "url" or part not in URL_PARTS:
            raise PresetError("preset_template_invalid")
        scheme, netloc, path = split_url(value)
        if part == "scheme":
            return spec.scheme_map[scheme]
        if part == "netloc":
            return netloc
        return quote(path, safe="/")

    out = text
    while True:
        groups = _OPTIONAL.search(out)
        if groups is None:
            break
        before = len(missing)
        inner = _PLACEHOLDER.sub(one, groups.group(1))
        dropped = len(missing) > before
        del missing[before:]
        out = out[: groups.start()] + ("" if dropped else inner) + out[groups.end() :]
    rendered = _PLACEHOLDER.sub(one, out)
    if missing:
        raise PresetError("config_required", sorted(set(missing)))
    return rendered


def compile_url(preset: Preset, values: Mapping[str, Any]) -> str:
    """The Apprise URL of ``preset`` for ``values`` (public and secret fields together).

    Every value is validated again and percent-encoded; ``PresetError`` (with the field
    names only, never a value) when one is missing or invalid. Called at validation and at
    send time, the result is never stored or logged.
    """
    specs = {spec.name: spec for spec in preset.fields}
    invalid: list[str] = []
    checked: dict[str, str] = {}
    for name, spec in specs.items():
        raw = _value_of(spec, values)
        try:
            checked[name] = _check_value(raw, spec) if raw else ""
        except ValueError:
            invalid.append(name)
    if invalid:
        raise PresetError("invalid_config", invalid)
    return _render(preset, preset.template, checked)


def preset_for(preset_id: str | None) -> Preset | None:
    return PRESETS_BY_ID.get(preset_id) if preset_id else None


def _build_registry() -> dict[str, ChannelType]:
    return {preset.id: preset_channel_type(preset) for preset in PRESETS}


PRESET_TYPES: dict[str, ChannelType] = _build_registry()


def get_preset_type(preset_id: str) -> ChannelType:
    entry = PRESET_TYPES.get(preset_id)
    if entry is None:
        raise ChannelError("channel_preset_unknown", preset=preset_id[:40])
    return entry
