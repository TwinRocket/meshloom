"""Permission registry: names, parameter schemas and caps.

Reserved names are refused with a clear error until implemented (M4). The `http`
host rules live here: `hosts` are public hosts (HTTPS only), `lan_hosts` the devices
of the user's local network the plugin may reach (MSHL-282; local names and LAN IP
literals only, MSHL-330; plain HTTP allowed; the egress guard checks that each one
resolves to a LAN address that is not the Meshloom host itself; in the Home Assistant
add-on the supervisor network is refused too, here and in the guard, MSHL-350). The
`*.meshloom.app` rule depends on the plugin id and is applied by the manifest model.
"""

import ipaddress
import re
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.net.egress_guard import (
    is_documentation_address,
    is_lan_address,
    is_metadata_address,
    is_private_address,
    is_supervisor_network_address,
)

MESSAGES_SEND_MAX_PER_HOUR = 60
MESSAGES_SEND_MAX_PER_DAY = 500
HTTP_MAX_HOSTS = 20
HTTP_MAX_LAN_HOSTS = 10
MESHLOOM_DOMAIN = "meshloom.app"

RESERVED_PERMISSIONS: frozenset[str] = frozenset(
    {"notifications.send", "feed.post", "ui.panel", "channels.manage", "contacts.manage"}
)

_HOST_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOSTNAME_RE = re.compile(rf"{_HOST_LABEL}(\.{_HOST_LABEL})*")
# The last label of a hostname starts with a letter: numeric and hex forms such as
# `127.1`, `2130706433` or `0x7f.1` are IP spellings that resolvers accept.
_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa", ".internal")
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
        # A LAN device is declared in `lan_hosts`, never here.
        if (
            is_private_address(ip)
            or is_metadata_address(ip)
            or is_documentation_address(ip)
            or (ip.version == 6 and ip.ipv4_mapped is not None)
        ):
            raise PydanticCustomError(
                "host_private_ip",
                "private, loopback, link-local and reserved IP literals are not allowed "
                "(declare a device of your network in lan_hosts)",
            )
        return value
    if not _HOSTNAME_RE.fullmatch(value) or not _TLD_RE.fullmatch(value.rsplit(".", 1)[-1]):
        raise PydanticCustomError(
            "host_invalid", "host must be a lowercase hostname or an IP literal without port"
        )
    if "." not in value or value == "localhost" or value.endswith(_LOCAL_SUFFIXES):
        # Single-label and local-network names belong in `lan_hosts`.
        raise PydanticCustomError(
            "host_private_name",
            "local-network host names are not allowed here (declare them in lan_hosts)",
        )
    return value


_PORT_RE = re.compile(r"[1-9][0-9]{0,4}")
_LOOPBACK_NAMES = ("localhost",)
_METADATA_NAMES = frozenset({"metadata.google.internal"})
# The names a local network answers itself (mDNS, the router's DNS, RFC 8375, the
# ICANN-reserved private TLD). Anything else with a dot is a public DNS name.
_LAN_NAME_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal")


def _lan_port(value: str) -> int:
    if not _PORT_RE.fullmatch(value) or not 0 < int(value) < 65536:
        raise PydanticCustomError("lan_host_invalid", "the port must be 1 to 65535")
    return int(value)


def split_lan_host(value: str) -> tuple[str, int | None]:
    """`host`, `host:port`, `v4:port`, a bare IPv6 or `[v6]:port` -> (host, port)."""
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or value[end + 1 : end + 2] != ":":
            raise PydanticCustomError(
                "lan_host_invalid", "write an IPv6 with a port as [address]:port"
            )
        return value[1:end], _lan_port(value[end + 2 :])
    if value.count(":") == 1:
        host, port = value.split(":")
        return host, _lan_port(port)
    return value, None


def check_lan_host(value: str) -> str:
    """A device of the user's local network: an exact lowercase local host name (a
    single label, or ending in `.local`, `.lan`, `.home.arpa` or `.internal`) or a LAN
    IP literal (10/8, 172.16/12, 192.168/16, 100.64/10 for a VPN such as Tailscale,
    fc00::/7), optionally with `:port` (`[v6]:port` for IPv6: a bare `fd00::1:80` is
    the address `fd00::1:80`, no port). Never a public DNS name (its owner would choose
    the LAN address), a wildcard, a range, a URL, `localhost` (the Meshloom machine
    itself), a loopback, link-local, metadata or public address. In the Home Assistant
    add-on, never an address of the supervisor network (172.30.32.0/23,
    fd0c:ac1e:2100::/48: the Supervisor, the other add-ons), so this check depends on
    the install there (MSHL-350); a name that resolves into it is refused by the egress
    guard at fetch time."""
    if len(value) > 260:
        raise PydanticCustomError("lan_host_invalid", "host is too long")
    if "*" in value:
        raise PydanticCustomError(
            "host_wildcard", "wildcard hosts are not allowed, list exact hosts"
        )
    if "/" in value:
        raise PydanticCustomError(
            "host_range", "IP ranges and URLs are not allowed, list exact hosts"
        )
    host, _port = split_lan_host(value)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if str(ip) != host or "%" in host:
            raise PydanticCustomError(
                "lan_host_invalid",
                "write the IP literal in its canonical lowercase form, no scope id",
            )
        if not is_lan_address(ip):
            raise PydanticCustomError(
                "lan_host_not_lan",
                "a LAN IP literal is in 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, "
                "100.64.0.0/10 or fc00::/7",
            )
        if is_supervisor_network_address(ip):
            raise PydanticCustomError(
                "lan_host_supervisor",
                "the Home Assistant Supervisor's internal network (Supervisor, other "
                "add-ons) is not a LAN device",
            )
        return value
    if (
        len(host) > 253
        or not _HOSTNAME_RE.fullmatch(host)
        or not _TLD_RE.fullmatch(host.rsplit(".", 1)[-1])
    ):
        raise PydanticCustomError(
            "lan_host_invalid", "host must be a lowercase hostname or a LAN IP literal"
        )
    if host in _LOOPBACK_NAMES or host.endswith(".localhost") or host in _METADATA_NAMES:
        raise PydanticCustomError(
            "lan_host_not_lan", "localhost and metadata names are not LAN devices"
        )
    if "." in host and not host.endswith(_LAN_NAME_SUFFIXES):
        # A public DNS name is answered by its owner's servers: whoever owns
        # `nas.example.com` would pick which device of the user's network it reaches
        # (MSHL-330). Only names the user's own network answers are accepted.
        raise PydanticCustomError(
            "lan_host_public_name",
            "a LAN host name is a single label or ends in .local, .lan, .home.arpa or "
            ".internal; a public DNS name is not a LAN device",
        )
    return value


def is_meshloom_domain(host: str) -> bool:
    return host == MESHLOOM_DOMAIN or host.endswith("." + MESHLOOM_DOMAIN)


Host = Annotated[str, AfterValidator(check_host)]
LanHost = Annotated[str, Field(max_length=260), AfterValidator(check_lan_host)]


def lan_host_name(entry: str) -> str:
    """The host part of a validated `lan_hosts` entry (no port, no brackets)."""
    return split_lan_host(entry)[0]


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
    hosts: list[Host] = Field(
        default_factory=list,
        max_length=HTTP_MAX_HOSTS,
        description="Public hosts, reached over HTTPS only.",
    )
    lan_hosts: list[LanHost] = Field(
        default_factory=list,
        max_length=HTTP_MAX_LAN_HOSTS,
        description=(
            "Devices of the user's local network: a local name (single label, .local, "
            ".lan, .home.arpa, .internal) or a LAN IP, optionally with :port "
            "([v6]:port for IPv6); HTTP or HTTPS; each must resolve to a LAN address "
            "that is not the Meshloom host."
        ),
    )

    @model_validator(mode="after")
    def _unique(self) -> "HttpParams":
        if not self.hosts and not self.lan_hosts:
            raise PydanticCustomError(
                "http_hosts_required", "list at least one host in hosts or lan_hosts"
            )
        if len(set(self.hosts)) != len(self.hosts) or len(set(self.lan_hosts)) != len(
            self.lan_hosts
        ):
            raise PydanticCustomError("host_duplicate", "hosts must be unique")
        # Defence in depth: the per-host validators already make the two lists
        # disjoint (a public name is never a local one), so no manifest reaches this
        # today; it guards a future relaxation of either validator.
        if set(self.hosts) & {lan_host_name(h) for h in self.lan_hosts}:
            raise PydanticCustomError(
                "lan_host_overlap", "a host is either public (hosts) or local (lan_hosts)"
            )
        return self

    @model_serializer(mode="wrap")
    def _drop_empty(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # One list may be empty: left out, so a grant written before `lan_hosts`
        # existed ({"hosts": [...]}) still equals the declaration.
        return {k: v for k, v in handler(self).items() if v}


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
