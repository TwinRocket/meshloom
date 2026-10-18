"""Permission registry: names, parameter schemas and caps.

Reserved names are refused with a clear error until implemented (M4). The `http`
host rules live here; the `*.meshloom.app` rule depends on the plugin id and is
applied by the manifest model.
"""

import ipaddress
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

MESSAGES_SEND_MAX_PER_HOUR = 60
MESSAGES_SEND_MAX_PER_DAY = 500
HTTP_MAX_HOSTS = 20
MESHLOOM_DOMAIN = "meshloom.app"

RESERVED_PERMISSIONS: frozenset[str] = frozenset(
    {"notifications.send", "feed.post", "ui.panel", "channels.manage", "contacts.manage"}
)

_HOST_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOSTNAME_RE = re.compile(rf"{_HOST_LABEL}(\.{_HOST_LABEL})*")
# The last label of a hostname starts with a letter: numeric and hex forms such as
# `127.1`, `2130706433` or `0x7f.1` are IP spellings that resolvers accept.
_TLD_RE = re.compile(r"[a-z][a-z0-9-]*")


def check_host(value: str) -> str:
    """An exact lowercase hostname or an IP literal; never a wildcard, range, port or URL."""
    if len(value) > 253:
        raise PydanticCustomError("host_invalid", "host is too long")
    if "*" in value:
        raise PydanticCustomError(
            "host_wildcard", "wildcard hosts are not allowed, list exact hosts"
        )
    if "/" in value:
        raise PydanticCustomError(
            "host_range", "IP ranges and URLs are not allowed, list exact hosts"
        )
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        ip = None
    if ip is not None:
        if str(ip) != value or "%" in value:
            raise PydanticCustomError(
                "host_invalid", "write the IP literal in its canonical lowercase form, no scope id"
            )
        # Fail closed until a LAN host can be declared and consented to (MSHL-282).
        if not ip.is_global or (ip.version == 6 and ip.ipv4_mapped is not None):
            raise PydanticCustomError(
                "host_private_ip",
                "private, loopback, link-local and reserved IP literals are not allowed",
            )
        return value
    if not _HOSTNAME_RE.fullmatch(value) or not _TLD_RE.fullmatch(value.rsplit(".", 1)[-1]):
        raise PydanticCustomError(
            "host_invalid", "host must be a lowercase hostname or an IP literal without port"
        )
    return value


def is_meshloom_domain(host: str) -> bool:
    return host == MESHLOOM_DOMAIN or host.endswith("." + MESHLOOM_DOMAIN)


Host = Annotated[str, AfterValidator(check_host)]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MessagesReadParams(_Params):
    channels: Literal["public", "all"] = "public"
    direct: bool = False
    outgoing: bool = False


class MessagesSendParams(_Params):
    targets: Literal["reply", "scope", "any"] = "reply"
    per_hour: int | None = Field(default=None, ge=1, le=MESSAGES_SEND_MAX_PER_HOUR)
    per_day: int | None = Field(default=None, ge=1, le=MESSAGES_SEND_MAX_PER_DAY)


class NoParams(_Params):
    pass


class HttpParams(_Params):
    hosts: list[Host] = Field(min_length=1, max_length=HTTP_MAX_HOSTS)

    @model_validator(mode="after")
    def _unique(self) -> "HttpParams":
        if len(set(self.hosts)) != len(self.hosts):
            raise PydanticCustomError("host_duplicate", "hosts must be unique")
        return self


class Permissions(BaseModel):
    """The declared permissions: each key is a registry name, each value its parameters."""

    model_config = ConfigDict(extra="forbid", populate_by_name=False, strict=True)

    messages_read: MessagesReadParams | None = Field(default=None, alias="messages.read")
    messages_send: MessagesSendParams | None = Field(default=None, alias="messages.send")
    raw_packets_read: NoParams | None = Field(default=None, alias="raw_packets.read")
    contacts_read: NoParams | None = Field(default=None, alias="contacts.read")
    channels_read: NoParams | None = Field(default=None, alias="channels.read")
    telemetry_read: NoParams | None = Field(default=None, alias="telemetry.read")
    storage: NoParams | None = None
    http: HttpParams | None = None
    timer: NoParams | None = None

    @model_validator(mode="before")
    @classmethod
    def _reserved(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in data:
                if key in RESERVED_PERMISSIONS:
                    raise PydanticCustomError(
                        "permission_reserved",
                        "permission '{name}' is reserved and not available yet",
                        {"name": key},
                    )
        return data

    def declared(self) -> dict[str, Any]:
        """The declared permissions by registry name (what the consent screen lists)."""
        return self.model_dump(by_alias=True, exclude_none=True)
