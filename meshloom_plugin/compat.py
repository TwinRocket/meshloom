"""Legacy bot compatibility: a 4.18.x `bot(...)` function runs as a plugin.

`legacy_bot(source)` returns a `Plugin` class that executes the bot source with
the same call conventions (`BotCallPlan`, moved from `app/fanout/bot_exec.py`,
which imports it back) and the same return shapes (`None`, `str`, `list[str]`,
`{"region", "message"}`). What changes, on purpose (docs/dev/plugins.md,
"Legacy bots"):

- `_bot_globals` is a dictionary of this instance only, lost when the process
  restarts (bots no longer share state);
- no network without the `http` permission, no disk, no threads;
- two new keyword arguments, delivered to a bot that names them or takes
  `**kwargs`: `http` (`get_json`, `fetch`) and `storage` (`get`, `set`,
  `delete`, `list`, JSON values, persistent). Both are synchronous: they
  block the child until the server answers (`Channel.call_sync`).

The 2 s settle delay (echo deduplication) stays on the server, before the
event is sent. Standard library only: this runs in the plugin child, and the
server imports it for the signature check and the call plans.
"""

import inspect
from dataclasses import dataclass
from typing import Any

import meshloom_plugin
from meshloom_plugin import sdk
from meshloom_plugin.errors import HostError

# Keyword arguments a bot may name beyond the eight historical ones.
OPTIONAL_KWARGS = ("is_outgoing", "path_bytes_per_hop", "packet_hash", "region", "scoped")
# New in 5.0: delivered like the optional ones, by name or through **kwargs.
SHIM_KWARGS = ("http", "storage")

REGION_UNSET: Any = object()  # no "region" key in the reply: use the channel default


@dataclass(frozen=True)
class BotCallPlan:
    """How to call a validated bot() function."""

    call_style: str
    keyword_args: tuple[str, ...] = ()


def analyze_bot_signature(bot_func_or_sig: Any, *, shim: bool = False) -> BotCallPlan:
    """Validate bot() signature and return a supported call plan.

    `shim` also accepts and plans the 5.0 keyword arguments `http` and `storage`,
    which only the plugin shim can deliver; the server-side bot executor leaves it off.
    """
    shim_kwargs = SHIM_KWARGS if shim else ()
    try:
        sig = (
            bot_func_or_sig
            if isinstance(bot_func_or_sig, inspect.Signature)
            else inspect.signature(bot_func_or_sig)
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("Bot function signature could not be inspected") from exc

    params = sig.parameters
    param_values = tuple(params.values())
    positional_params = [
        p
        for p in param_values
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in param_values)
    has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in param_values)
    explicit_optional_names = tuple(name for name in OPTIONAL_KWARGS if name in params)
    unsupported_required_kwonly = [
        p.name
        for p in param_values
        if p.kind == inspect.Parameter.KEYWORD_ONLY
        and p.default is inspect.Parameter.empty
        and p.name not in OPTIONAL_KWARGS
        and p.name not in shim_kwargs
    ]
    if unsupported_required_kwonly:
        raise ValueError(
            "Bot function signature is not supported. Unsupported required keyword-only "
            "parameters: " + ", ".join(unsupported_required_kwonly)
        )

    positional_capacity = len(positional_params)
    base_args = [object()] * 8
    base_keyword_args: dict[str, object] = {
        "sender_name": object(),
        "sender_key": object(),
        "message_text": object(),
        "is_dm": object(),
        "channel_key": object(),
        "channel_name": object(),
        "sender_timestamp": object(),
        "path": object(),
    }
    candidate_specs: list[tuple[str, list[object], dict[str, object]]] = []
    keyword_args = dict(base_keyword_args)
    if has_kwargs or "is_outgoing" in params:
        keyword_args["is_outgoing"] = False
    if has_kwargs or "path_bytes_per_hop" in params:
        keyword_args["path_bytes_per_hop"] = 1
    if has_kwargs or "packet_hash" in params:
        keyword_args["packet_hash"] = ""
    if has_kwargs or "region" in params:
        keyword_args["region"] = None
    if has_kwargs or "scoped" in params:
        keyword_args["scoped"] = False
    for name in shim_kwargs:
        if has_kwargs or name in params:
            keyword_args[name] = None
    candidate_specs.append(("keyword", [], keyword_args))

    if not has_kwargs and explicit_optional_names:
        kwargs: dict[str, object] = {}
        if has_kwargs or "is_outgoing" in params:
            kwargs["is_outgoing"] = False
        if has_kwargs or "path_bytes_per_hop" in params:
            kwargs["path_bytes_per_hop"] = 1
        if has_kwargs or "packet_hash" in params:
            kwargs["packet_hash"] = ""
        if has_kwargs or "region" in params:
            kwargs["region"] = None
        if has_kwargs or "scoped" in params:
            kwargs["scoped"] = False
        candidate_specs.append(("mixed_keyword", base_args, kwargs))

    if has_varargs or positional_capacity >= 11:
        candidate_specs.append(("positional_11", base_args + [False, 1, ""], {}))
    if has_varargs or positional_capacity >= 10:
        candidate_specs.append(("positional_10", base_args + [False, 1], {}))
    if has_varargs or positional_capacity >= 9:
        candidate_specs.append(("positional_9", base_args + [False], {}))
    if has_varargs or positional_capacity >= 8:
        candidate_specs.append(("legacy", base_args, {}))

    for call_style, args, kwargs in candidate_specs:
        try:
            sig.bind(*args, **kwargs)
        except TypeError:
            continue
        if call_style in {"keyword", "mixed_keyword"}:
            return BotCallPlan(call_style="keyword", keyword_args=tuple(kwargs.keys()))
        return BotCallPlan(call_style=call_style)

    raise ValueError(
        "Bot function signature is not supported. Use the default bot template as a reference. "
        "Supported trailing parameters are: path; path + is_outgoing; "
        "path + path_bytes_per_hop; path + is_outgoing + path_bytes_per_hop; "
        "path + is_outgoing + path_bytes_per_hop + packet_hash; "
        "or use **kwargs for forward compatibility (which also receives region and scoped)."
    )


_POSITIONAL_ORDER = (
    "sender_name",
    "sender_key",
    "message_text",
    "is_dm",
    "channel_key",
    "channel_name",
    "sender_timestamp",
    "path",
    "is_outgoing",
    "path_bytes_per_hop",
    "packet_hash",
)
_POSITIONAL_COUNT = {"legacy": 8, "positional_9": 9, "positional_10": 10, "positional_11": 11}


def call_bot(bot_func: Any, plan: BotCallPlan, fields: dict[str, Any]) -> Any:
    """Call `bot_func` the way `plan` says, taking its values from `fields`."""
    count = _POSITIONAL_COUNT.get(plan.call_style)
    if count is not None:
        return bot_func(*(fields[name] for name in _POSITIONAL_ORDER[:count]))
    return bot_func(**{name: fields[name] for name in plan.keyword_args})


@dataclass(frozen=True)
class BotResult:
    """A bot return value in its normal form.

    `region` is `REGION_UNSET` (no region key: the channel's default), `None`
    (send unscoped) or the name the bot gave, not yet normalised.
    """

    messages: list[str]
    region: Any = REGION_UNSET


def coerce_result(result: Any, debug: Any = None) -> BotResult | None:
    """Normalise a bot return value (`None`, `str`, `list[str]`, dict) or give `None`.

    `debug`, when given, is called with the reason a value was dropped.
    """

    def drop(reason: str) -> None:
        if debug is not None:
            debug(reason)

    if result is None:
        return None
    if isinstance(result, dict):
        raw = result.get("message")
        if isinstance(raw, str):
            messages = [raw] if raw.strip() else []
        elif isinstance(raw, list):
            messages = [m for m in raw if isinstance(m, str) and m.strip()]
        else:
            drop("Bot dict response 'message' must be a string or list of strings")
            return None
        if not messages:
            return None
        if "region" not in result:
            return BotResult(messages)
        region = result["region"]
        if region is None or isinstance(region, str):
            return BotResult(messages, region)
        drop("Bot dict response 'region' must be a string or None")
        return None
    if isinstance(result, str):
        return BotResult([result]) if result.strip() else None
    if isinstance(result, list):
        valid = [m for m in result if isinstance(m, str) and m.strip()]
        return BotResult(valid) if valid else None
    drop(f"Bot function returned unsupported type: {type(result)}")
    return None


# --- the shim, child side ------------------------------------------------------


class LegacyHttp:
    """`http` for a legacy bot: synchronous, through the server, under the `http` permission."""

    def get_json(
        self, url: str, headers: dict[str, str] | None = None, timeout: float = 10.0
    ) -> Any:
        response = self.fetch(url, headers=headers, timeout=timeout)
        if response.status >= 400:
            raise sdk.HttpStatusError(response.status, url)
        return response.json()

    def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout: float = 10.0,
    ) -> sdk.HttpResponse:
        params = sdk.fetch_params(url, method, headers, body, timeout)
        return sdk.HttpResponse.from_result(_call_sync("http.fetch", params, "http", url))


class LegacyStorage:
    """`storage` for a legacy bot: JSON values, persistent, synchronous."""

    def get(self, key: str, default: Any = None) -> Any:
        value = _call_sync("storage.get", {"key": key}, "storage")
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        _call_sync("storage.set", {"key": key, "value": value}, "storage")

    def delete(self, key: str) -> None:
        _call_sync("storage.delete", {"key": key}, "storage")

    def list(self, prefix: str = "") -> list[str]:
        keys = _call_sync("storage.list", {"prefix": prefix}, "storage")
        return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []


def _call_sync(method: str, params: dict[str, Any], permission: str, url: str = "") -> Any:
    try:
        return meshloom_plugin.host().call_sync(method, params)
    except HostError as exc:
        code = sdk.error_code(exc)
        if code == "permission_denied":
            raise PermissionError(
                f"the '{permission}' permission is not granted to this bot"
                + (f" for {_host_of(url)}" if url else "")
                + ": grant it in the plugin's permissions"
            ) from None
        if code == "host_not_allowed":
            raise PermissionError(
                f"{_host_of(url)} is not in the hosts this bot may reach: "
                "add it to the 'http' permission"
            ) from None
        raise


def _host_of(url: str) -> str:
    """`host[:port]` of `url` for a message: no userinfo, path, query or fragment."""
    shown = sdk.safe_url(url)
    rest = shown.partition("://")[2] or shown
    return rest.partition("/")[0] or "this host"


def compile_bot(source: str) -> tuple[Any, BotCallPlan, dict[str, Any]]:
    """Run the bot source; return `(bot function, call plan, namespace)`.

    Raises `ValueError` with a readable reason when the source does not define a
    callable `bot()` with a supported signature.
    """
    if not source or not source.strip():
        raise ValueError("empty bot code")
    namespace: dict[str, Any] = {"_bot_globals": {}}
    try:
        exec(compile(source, "<bot>", "exec"), namespace)
    except Exception as exc:  # noqa: BLE001 - the user's code: report whatever it raised
        raise ValueError(f"bot code failed to load: {type(exc).__name__}: {exc}") from None
    bot_func = namespace.get("bot")
    if not callable(bot_func):
        raise ValueError("bot code does not define a callable 'bot' function")
    return bot_func, analyze_bot_signature(bot_func, shim=True), namespace


def legacy_bot(source: str) -> type[sdk.Plugin]:
    """Wrap a 4.18.x bot's source in a `Plugin` class (the entry point of a migrated bot).

    ```python
    # plugin.py of a migrated bot
    from meshloom_plugin.compat import legacy_bot
    Bot = legacy_bot(open("bot.py", encoding="utf-8").read())   # manifest entry "plugin.py:Bot"
    ```
    """

    class LegacyBot(sdk.Plugin):
        BOT_SOURCE = source

        def __init__(self) -> None:
            super().__init__()
            self._bot: Any = None
            self._plan: BotCallPlan | None = None
            self._error: str | None = None
            self.bot_globals: dict[str, Any] = {}
            try:
                self._bot, self._plan, namespace = compile_bot(self.BOT_SOURCE)
                self.bot_globals = namespace["_bot_globals"]
            except ValueError as exc:
                # Reported at each message, not by crashing the start: the user sees it in the log.
                self._error = str(exc)
            self._http = LegacyHttp()
            self._storage = LegacyStorage()

        async def on_start(self) -> None:
            if self._error:
                self.log("error", self._error)

        async def on_message(self, msg: sdk.Message) -> None:
            if self._bot is None or self._plan is None:
                self.log("error", self._error or "bot is not loaded")
                return None
            fields = {
                "sender_name": msg.sender_name,
                "sender_key": msg.sender_key,
                "message_text": msg.text,
                "is_dm": msg.is_dm,
                "channel_key": msg.channel_key,
                "channel_name": msg.channel_name,
                "sender_timestamp": msg.sender_timestamp,
                "path": msg.path,
                "is_outgoing": msg.is_outgoing,
                "path_bytes_per_hop": msg.path_bytes_per_hop,
                "packet_hash": msg.packet_hash,
                "region": msg.region,
                "scoped": msg.scoped,
                "http": self._http,
                "storage": self._storage,
            }
            try:
                result = call_bot(self._bot, self._plan, fields)
            except Exception as exc:  # noqa: BLE001 - the user's code: log it, keep serving
                self.log("error", f"Bot function execution failed: {type(exc).__name__}: {exc}")
                return None
            reply = coerce_result(result, lambda reason: self.log("debug", reason))
            if reply is None:
                return None
            for text in reply.messages:
                if reply.region is REGION_UNSET:
                    await self.reply(msg, text)
                else:
                    await self.reply(msg, text, region=reply.region)
            return None

    LegacyBot.__name__ = LegacyBot.__qualname__ = "LegacyBot"
    return LegacyBot
