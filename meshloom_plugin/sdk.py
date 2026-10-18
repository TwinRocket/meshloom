"""Plugin SDK: what a plugin author imports (docs/dev/plugins.md, "SDK").

```python
from meshloom_plugin import Message, Plugin

class Weather(Plugin):
    async def on_message(self, msg: Message) -> None:
        if msg.text.startswith("!weather"):
            data = await self.http.get_json(f"https://api.open-meteo.com/v1/forecast?{self.config['query']}")
            await self.reply(msg, self.fit(f"{data['current']['temperature_2m']} °C"))
```

Every helper is a JSON-RPC request to the server (`meshloom_plugin.host()`), awaited
until its result arrives; the server checks permissions and quotas, the SDK only
shapes the calls. Standard library only: this runs in the plugin child.
"""

import functools
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import meshloom_plugin
from meshloom_plugin.errors import HostError

# A direct message carries at most this many bytes of text; `plugin.init` may
# announce another limit (`limits.message_bytes`), which `fit()` then uses.
DEFAULT_MESSAGE_BYTES = 156
DEFAULT_HTTP_TIMEOUT = 10.0

LOG_LEVELS = ("debug", "info", "warning", "error")

# Region argument of reply()/send(): leave it out for "the channel's default".
_DEFAULT: Any = object()


def fit(text: str, limit: int = DEFAULT_MESSAGE_BYTES) -> str:
    """Truncate `text` to at most `limit` UTF-8 bytes, on a character boundary."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def error_code(exc: HostError) -> str | None:
    """The typed code of a host refusal (`permission_denied`, `quota_exceeded`...), if any."""
    error = exc.error
    if not isinstance(error, dict):
        return None
    data = error.get("data")
    if isinstance(data, dict) and isinstance(data.get("code"), str):
        return data["code"]
    code = error.get("code")
    return code if isinstance(code, str) else None


class HttpStatusError(Exception):
    """`get_json`/`post_json` got an HTTP status of 400 or more."""

    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} from {safe_url(url)}")
        self.status = status


def safe_url(url: str) -> str:
    """`scheme://host[:port]/path`: no userinfo, query or fragment.

    An URL may carry an API key or a password in any of those: never put one
    in a message or a log.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "an unreadable URL"
    if not host:
        return "an unreadable URL"
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return f"{parts.scheme}://{netloc}{parts.path}" if parts.scheme else netloc + parts.path


@dataclass(frozen=True)
class Message:
    """An incoming message, as `event.message` delivers it.

    `text` is already stripped of the `"Name: "` prefix channel messages carry.
    `sender_key` is set for direct messages, `channel_key` for channel ones.
    """

    text: str
    sender_name: str | None = None
    sender_key: str | None = None
    is_dm: bool = False
    channel_key: str | None = None
    channel_name: str | None = None
    sender_timestamp: int | None = None
    path: str | None = None
    path_bytes_per_hop: int | None = None
    packet_hash: str | None = None
    region: str | None = None
    scoped: bool = False
    is_outgoing: bool = False
    conversation_key: str | None = None
    is_public_channel: bool = False
    received_at: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_params(cls, params: Any) -> "Message":
        if not isinstance(params, dict):
            raise TypeError("event.message params must be an object")
        text = params.get("message_text", params.get("text"))
        return cls(
            text=text if isinstance(text, str) else "",
            sender_name=params.get("sender_name"),
            sender_key=params.get("sender_key"),
            is_dm=bool(params.get("is_dm", False)),
            channel_key=params.get("channel_key"),
            channel_name=params.get("channel_name"),
            sender_timestamp=params.get("sender_timestamp"),
            path=params.get("path"),
            path_bytes_per_hop=params.get("path_bytes_per_hop"),
            packet_hash=params.get("packet_hash"),
            region=params.get("region"),
            scoped=bool(params.get("scoped", False)),
            is_outgoing=bool(params.get("is_outgoing", False)),
            conversation_key=params.get("conversation_key"),
            is_public_channel=bool(params.get("is_public_channel", False)),
            received_at=params.get("received_at"),
            raw=params,
        )


@dataclass(frozen=True)
class HttpResponse:
    """Answer of `http.fetch`: `body` is the raw bytes, `text` the UTF-8 decoding."""

    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    @classmethod
    def from_result(cls, result: Any) -> "HttpResponse":
        """Parse a `http.fetch` result: `{status, headers, body, encoding?}`."""
        if not isinstance(result, dict) or type(result.get("status")) is not int:
            raise HostError({"code": -32603, "message": "http.fetch returned no status"})
        body = result.get("body", "")
        if not isinstance(body, str):
            raise HostError({"code": -32603, "message": "http.fetch returned a bad body"})
        if result.get("encoding") == "base64":
            import base64
            import binascii

            try:
                raw = base64.b64decode(body, validate=True)
            except (binascii.Error, ValueError):
                raise HostError(
                    {"code": -32603, "message": "http.fetch body is not base64"}
                ) from None
        else:
            raw = body.encode("utf-8")
        headers = result.get("headers")
        return cls(
            status=result["status"],
            headers=headers if isinstance(headers, dict) else {},
            body=raw,
        )


def fetch_params(
    url: str,
    method: str,
    headers: dict[str, str] | None,
    body: str | None,
    timeout: float,
) -> dict[str, Any]:
    params: dict[str, Any] = {"url": url, "method": method.upper(), "timeout": timeout}
    if headers:
        params["headers"] = dict(headers)
    if body is not None:
        params["body"] = body
    return params


def json_body(data: Any, headers: dict[str, str] | None) -> tuple[str, dict[str, str]]:
    merged = {"Content-Type": "application/json", **(headers or {})}
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")), merged


class Http:
    """`self.http`: outbound requests, made by the server under the `http` permission."""

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> HttpResponse:
        result = await meshloom_plugin.host().call(
            "http.fetch", fetch_params(url, method, headers, body, timeout)
        )
        return HttpResponse.from_result(result)

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> Any:
        response = await self.fetch(url, headers=headers, timeout=timeout)
        if response.status >= 400:
            raise HttpStatusError(response.status, url)
        return response.json()

    async def post_json(
        self,
        url: str,
        data: Any,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> Any:
        body, merged = json_body(data, headers)
        response = await self.fetch(url, method="POST", headers=merged, body=body, timeout=timeout)
        if response.status >= 400:
            raise HttpStatusError(response.status, url)
        return response.json()


class Storage:
    """`self.storage`: this instance's key/value store (JSON values, persistent).

    A stored JSON `null` reads back as the default: absent and `null` are the same.
    """

    async def get(self, key: str, default: Any = None) -> Any:
        value = await meshloom_plugin.host().call("storage.get", {"key": key})
        return default if value is None else value

    async def set(self, key: str, value: Any) -> None:
        await meshloom_plugin.host().call("storage.set", {"key": key, "value": value})

    async def delete(self, key: str) -> None:
        await meshloom_plugin.host().call("storage.delete", {"key": key})

    async def list(self, prefix: str = "") -> list[str]:
        keys = await meshloom_plugin.host().call("storage.list", {"prefix": prefix})
        return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []


def log(level: str, message: str) -> None:
    """Send one line to the instance's log (and stderr when there is no host)."""
    if level not in LOG_LEVELS:
        level = "info"
    try:
        meshloom_plugin.host().notify("log", {"level": level, "message": str(message)})
    except (RuntimeError, ValueError, OSError):
        print(f"[{level}] {message}", file=sys.stderr)


def _takes_message(function: Any) -> Any:
    @functools.wraps(function)
    def wrapper(self: "Plugin", params: Any) -> Any:
        message = params if isinstance(params, Message) else Message.from_params(params)
        return function(self, message)

    return wrapper


class Plugin:
    """Base class of a plugin: override the `on_*` handlers you subscribed to.

    The handlers are called by the runner with the raw event parameters, except
    `on_message`, which gets a `Message`. A handler may be sync or async, and
    its return value is the event result (`None` when there is nothing to say).
    """

    api: int = 1
    config: dict[str, Any]
    instance: dict[str, Any]
    lang: str = "en"
    granted: tuple[str, ...] = ()
    limits: dict[str, Any]
    isolation: str = "none"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # The runner calls `on_message(params)` with the raw event dict: a subclass
        # writes `on_message(self, msg: Message)`, so wrap it to convert first.
        handler = cls.__dict__.get("on_message")
        if handler is not None:
            cls.on_message = _takes_message(handler)  # type: ignore[method-assign]

    def __init__(self) -> None:
        self.config = {}
        self.instance = {}
        self.limits = {}
        self.http = Http()
        self.storage = Storage()

    # Lifecycle, called by the runner.

    async def on_init(self, params: Any) -> None:
        """Keep what `plugin.init` announces, then call `on_start()`."""
        if isinstance(params, dict):
            self.api = params["api"] if type(params.get("api")) is int else self.api
            self.config = dict(params["config"]) if isinstance(params.get("config"), dict) else {}
            self.instance = (
                dict(params["instance"]) if isinstance(params.get("instance"), dict) else {}
            )
            self.lang = params["lang"] if isinstance(params.get("lang"), str) else self.lang
            granted = params.get("granted")
            self.granted = (
                tuple(g for g in granted if isinstance(g, str)) if isinstance(granted, list) else ()
            )
            self.limits = dict(params["limits"]) if isinstance(params.get("limits"), dict) else {}
            if isinstance(params.get("isolation"), str):
                self.isolation = params["isolation"]
        await self.on_start()

    async def on_start(self) -> None:
        """Called once `self.config` and the other init fields are set."""

    async def on_shutdown(self, params: Any) -> None:
        """Called before the process exits."""

    async def on_config_changed(self, params: Any) -> None:
        """New configuration: the base class replaces `self.config`; extend it to react."""
        if isinstance(params, dict) and isinstance(params.get("config"), dict):
            self.config = dict(params["config"])

    # Event handlers: override what the manifest subscribes to.
    # The runner calls `on_message(params)`; the wrapper below builds the Message.

    async def on_message(self, msg: Message) -> Any:
        return None

    async def on_raw_packet(self, params: Any) -> Any:
        return None

    async def on_contact(self, params: Any) -> Any:
        return None

    async def on_telemetry(self, params: Any) -> Any:
        return None

    async def on_health(self, params: Any) -> Any:
        return None

    async def on_timer(self, params: Any) -> Any:
        return None

    # Helpers.

    @property
    def message_limit(self) -> int:
        limit = self.limits.get("message_bytes")
        return limit if type(limit) is int and limit > 0 else DEFAULT_MESSAGE_BYTES

    def fit(self, text: str) -> str:
        """Truncate to the server's message limit (`limits.message_bytes` of `plugin.init`)."""
        return fit(text, self.message_limit)

    async def reply(self, msg: Message, text: str, *, region: Any = _DEFAULT) -> None:
        """Answer in the conversation `msg` came from.

        `region` only applies to channel replies: leave it out for the channel's
        default, `None` to send unscoped, a name to scope this send to it.
        """
        event_id = msg.raw.get("event_id")
        if msg.is_dm:
            if not msg.sender_key:
                raise ValueError("cannot reply: the direct message has no sender_key")
            await self.send(contact_key=msg.sender_key, text=text, event_id=event_id)
        else:
            if not msg.channel_key:
                raise ValueError("cannot reply: the message has no channel_key")
            await self.send(
                channel_key=msg.channel_key, text=text, region=region, event_id=event_id
            )

    async def send(
        self,
        text: str,
        *,
        channel_key: str | None = None,
        contact_key: str | None = None,
        region: Any = _DEFAULT,
        event_id: int | None = None,
    ) -> None:
        """Send `text` to a channel or a contact (the manifest's permission decides where).

        `event_id` names the event this send answers (`reply()` passes the one the
        host put in the message), so the host ties the send to it: a test event's
        sends are captured, never sent.
        """
        if (channel_key is None) == (contact_key is None):
            raise ValueError("give exactly one of channel_key and contact_key")
        params: dict[str, Any] = {
            "to": {"channel_key": channel_key} if channel_key else {"contact_key": contact_key},
            "text": text,
        }
        if region is not _DEFAULT and channel_key:
            params["region"] = region
        if type(event_id) is int:
            params["event_id"] = event_id
        await meshloom_plugin.host().call("messages.send", params)

    async def timer(self, seconds: float) -> None:
        """Ask for one `on_timer` call in `seconds` (the server enforces a minimum)."""
        await meshloom_plugin.host().call("timer.set", {"seconds": seconds})

    def log(self, level: str, message: str) -> None:
        log(level, message)

    async def set_status(self, summary: str) -> None:
        """One line shown next to the instance in the UI."""
        await meshloom_plugin.host().call("status.set", {"summary": summary})
