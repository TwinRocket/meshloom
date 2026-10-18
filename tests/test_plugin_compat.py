"""Legacy bots through the shim (`meshloom_plugin.compat`), against `execute_bot_code`.

The cases of `tests/test_bot.py` that pin call conventions and return shapes run
twice: through the 4.18.x executor and through `legacy_bot()` with a fake host, and
the replies must agree.
"""

import textwrap
from typing import Any

import pytest

import meshloom_plugin
from app.fanout.bot_exec import BotReply, execute_bot_code
from meshloom_plugin import compat, sdk
from meshloom_plugin.errors import HostError

FULL = (
    "sender_name, sender_key, message_text, is_dm, channel_key, channel_name, "
    "sender_timestamp, path"
)


class FakeHost:
    """The child end of the RPC channel, answering from a table."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.sync_calls: list[tuple[str, Any]] = []
        self.logs: list[tuple[str, str]] = []
        self.answers: dict[str, Any] = {}

    async def call(self, method: str, params: Any = None) -> Any:
        self.calls.append((method, params))
        return None

    def call_sync(self, method: str, params: Any = None) -> Any:
        self.sync_calls.append((method, params))
        answer = self.answers.get(method)
        if isinstance(answer, Exception):
            raise answer
        return answer(params) if callable(answer) else answer

    def notify(self, method: str, params: Any = None) -> None:
        if method == "log":
            self.logs.append((params["level"], params["message"]))


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> FakeHost:
    fake = FakeHost()
    monkeypatch.setattr(meshloom_plugin, "_host", fake)
    return fake


MESSAGE: dict[str, Any] = {
    "sender_name": "Alice",
    "sender_key": "abc123",
    "message_text": "Hi",
    "is_dm": True,
    "channel_key": None,
    "channel_name": None,
    "sender_timestamp": 1700000000,
    "path": "aabb",
    "path_bytes_per_hop": 2,
    "packet_hash": "H" * 16,
    "region": "us-west",
    "scoped": True,
    "is_outgoing": False,
}


async def run_shim(code: str, host: FakeHost, **overrides: Any) -> list[dict[str, Any]]:
    """Feed one message to the shim; return the `messages.send` params it made."""
    plugin = compat.legacy_bot(textwrap.dedent(code))()
    await plugin.on_init({"api": 1, "config": {}})
    await plugin.on_message({**MESSAGE, **overrides})
    return [params for method, params in host.calls if method == "messages.send"]


def texts(sends: list[dict[str, Any]]) -> list[str]:
    return [s["text"] for s in sends]


# --- call conventions -----------------------------------------------------------

CONVENTIONS = {
    "legacy": f"def bot({FULL}):\n    return f'{{sender_name}}|{{message_text}}|{{path}}'",
    "positional_9": f"def bot({FULL}, is_outgoing):\n    return f'{{is_outgoing}}'",
    "positional_10": f"def bot({FULL}, is_outgoing, path_bytes_per_hop):\n    return f'{{path_bytes_per_hop}}'",
    "positional_11": (
        f"def bot({FULL}, is_outgoing, path_bytes_per_hop, packet_hash):\n"
        "    return f'{path_bytes_per_hop}{packet_hash}'"
    ),
    "keyword_all": (
        "def bot(**kwargs):\n"
        "    return ','.join(sorted(k for k in kwargs if k not in ('http', 'storage')))"
    ),
    "keyword_named": f"def bot({FULL}, *, region, scoped):\n    return f'{{region}}:{{scoped}}'",
    "keyword_required_kwonly": (f"def bot({FULL}, *, region):\n    return f'{{region}}'"),
    "mixed_keyword": f"def bot({FULL}, path_bytes_per_hop=None, region=None):\n    return f'{{path_bytes_per_hop}}{{region}}'",
    "varargs": "def bot(*args):\n    return str(len(args))",
}


def legacy_reply(code: str) -> list[str] | None:
    reply = execute_bot_code(
        code=textwrap.dedent(code),
        sender_name=MESSAGE["sender_name"],
        sender_key=MESSAGE["sender_key"],
        message_text=MESSAGE["message_text"],
        is_dm=MESSAGE["is_dm"],
        channel_key=MESSAGE["channel_key"],
        channel_name=MESSAGE["channel_name"],
        sender_timestamp=MESSAGE["sender_timestamp"],
        path=MESSAGE["path"],
        is_outgoing=MESSAGE["is_outgoing"],
        path_bytes_per_hop=MESSAGE["path_bytes_per_hop"],
        packet_hash=MESSAGE["packet_hash"],
        region=MESSAGE["region"],
        scoped=MESSAGE["scoped"],
    )
    if reply is None:
        return None
    if isinstance(reply, BotReply):
        return reply.messages
    return [reply] if isinstance(reply, str) else reply


@pytest.mark.parametrize("name", list(CONVENTIONS))
async def test_call_convention_matches_the_executor(name: str, host: FakeHost) -> None:
    code = CONVENTIONS[name]
    expected = legacy_reply(code)
    assert expected is not None
    assert texts(await run_shim(code, host)) == expected


@pytest.mark.parametrize(
    ("code", "plan"),
    [
        ("def bot(a, b, c, d, e, f, g, h):\n    pass", "legacy"),
        ("def bot(a, b, c, d, e, f, g, h, i):\n    pass", "positional_9"),
        ("def bot(a, b, c, d, e, f, g, h, i, j):\n    pass", "positional_10"),
        ("def bot(a, b, c, d, e, f, g, h, i, j, k):\n    pass", "positional_11"),
        (CONVENTIONS["keyword_all"], "keyword"),
        (CONVENTIONS["keyword_named"], "keyword"),
        (CONVENTIONS["mixed_keyword"], "keyword"),
    ],
)
def test_call_plan_styles(code: str, plan: str) -> None:
    ns: dict[str, Any] = {}
    exec(code, ns)
    assert compat.analyze_bot_signature(ns["bot"]).call_style == plan


def test_unsupported_signature_is_refused_with_the_same_guidance() -> None:
    ns: dict[str, Any] = {}
    exec(f"def bot({FULL}, *, extra_required):\n    pass", ns)
    with pytest.raises(ValueError, match="Unsupported required keyword-only"):
        compat.analyze_bot_signature(ns["bot"])


def test_http_and_storage_are_shim_only_keywords() -> None:
    ns: dict[str, Any] = {}
    exec(f"def bot({FULL}, *, http):\n    pass", ns)
    with pytest.raises(ValueError):
        compat.analyze_bot_signature(ns["bot"])
    assert compat.analyze_bot_signature(ns["bot"], shim=True).keyword_args == (
        "sender_name",
        "sender_key",
        "message_text",
        "is_dm",
        "channel_key",
        "channel_name",
        "sender_timestamp",
        "path",
        "http",
    )


# --- return shapes ----------------------------------------------------------------

SHAPES = {
    "none": "def bot(**k):\n    return None",
    "str": "def bot(**k):\n    return 'one'",
    "blank_str": "def bot(**k):\n    return '   '",
    "list": "def bot(**k):\n    return ['a', 'b']",
    "list_filtered": "def bot(**k):\n    return ['a', '', 3, '  ', 'b']",
    "list_empty": "def bot(**k):\n    return []",
    "list_blank": "def bot(**k):\n    return ['', ' ']",
    "dict_list": "def bot(**k):\n    return {'message': ['x', '', 'y']}",
    "dict_empty": "def bot(**k):\n    return {'message': ''}",
    "dict_invalid": "def bot(**k):\n    return {'message': 5}",
    "int": "def bot(**k):\n    return 42",
    "raises": "def bot(**k):\n    raise RuntimeError('boom')",
    "syntax_error": "def bot(:\n",
    "no_bot": "x = 1",
    "not_callable": "bot = 'nope'",
    "empty": "",
}


@pytest.mark.parametrize("name", list(SHAPES))
async def test_return_shape_matches_the_executor(name: str, host: FakeHost) -> None:
    code = SHAPES[name]
    expected = legacy_reply(code) or []
    assert texts(await run_shim(code, host)) == expected


async def test_broken_bots_are_logged_not_raised(host: FakeHost) -> None:
    await run_shim(SHAPES["raises"], host)
    assert any("RuntimeError: boom" in message for _, message in host.logs)
    host.logs.clear()
    await run_shim(SHAPES["syntax_error"], host)
    assert any("failed to load" in message for _, message in host.logs)


async def test_dict_without_region_uses_the_channel_default(host: FakeHost) -> None:
    sends = await run_shim(
        "def bot(**k):\n    return {'message': 'hi'}", host, is_dm=False, channel_key="C" * 32
    )
    assert sends == [{"to": {"channel_key": "C" * 32}, "text": "hi"}]


async def test_dict_region_none_is_explicit_unscoped(host: FakeHost) -> None:
    sends = await run_shim(
        "def bot(**k):\n    return {'region': None, 'message': 'hi'}",
        host,
        is_dm=False,
        channel_key="C" * 32,
    )
    assert sends == [{"to": {"channel_key": "C" * 32}, "text": "hi", "region": None}]


async def test_dict_region_string_goes_to_the_host_to_normalise(host: FakeHost) -> None:
    sends = await run_shim(
        "def bot(**k):\n    return {'region': 'us-west', 'message': ['a', 'b']}",
        host,
        is_dm=False,
        channel_key="C" * 32,
    )
    assert [s["region"] for s in sends] == ["us-west", "us-west"]
    assert texts(sends) == ["a", "b"]


async def test_dm_reply_goes_to_the_sender_and_drops_the_region(host: FakeHost) -> None:
    sends = await run_shim("def bot(**k):\n    return {'region': 'x', 'message': 'hi'}", host)
    assert sends == [{"to": {"contact_key": "abc123"}, "text": "hi"}]


async def test_bot_globals_persist_across_messages_of_one_instance(host: FakeHost) -> None:
    code = (
        "def bot(**k):\n"
        "    _bot_globals['n'] = _bot_globals.get('n', 0) + 1\n"
        "    return str(_bot_globals['n'])\n"
    )
    plugin = compat.legacy_bot(code)()
    await plugin.on_init({"api": 1})
    await plugin.on_message(MESSAGE)
    await plugin.on_message(MESSAGE)
    assert [p["text"] for m, p in host.calls if m == "messages.send"] == ["1", "2"]
    other = compat.legacy_bot(code)()
    await other.on_message(MESSAGE)
    assert host.calls[-1][1]["text"] == "1"  # not shared between instances


# --- http and storage -------------------------------------------------------------

HTTP_BOT = (
    f"def bot({FULL}, *, http):\n"
    "    data = http.get_json('https://api.example.org/w?key=SECRET')\n"
    "    return str(data['t'])\n"
)


async def test_http_without_the_permission_logs_a_readable_error(host: FakeHost) -> None:
    host.answers["http.fetch"] = HostError(
        {"code": -32010, "message": "permission_denied", "data": {"code": "permission_denied"}}
    )
    assert await run_shim(HTTP_BOT, host) == []
    (level, message) = host.logs[-1]
    assert level == "error"
    assert message.startswith(
        "Bot function execution failed: PermissionError: the 'http' permission is not granted "
        "to this bot for api.example.org: "
    )
    assert "SECRET" not in message


async def test_http_host_not_allowed_names_the_host(host: FakeHost) -> None:
    host.answers["http.fetch"] = HostError(
        {"code": -32010, "message": "x", "data": {"code": "host_not_allowed"}}
    )
    await run_shim(HTTP_BOT, host)
    assert host.logs[-1][1].startswith(
        "Bot function execution failed: PermissionError: api.example.org is not in the hosts"
    )


async def test_http_with_the_permission_goes_through_the_host(host: FakeHost) -> None:
    host.answers["http.fetch"] = {"status": 200, "headers": {}, "body": '{"t": 21}'}
    sends = await run_shim(HTTP_BOT, host)
    assert texts(sends) == ["21"]
    assert host.sync_calls == [
        (
            "http.fetch",
            {"url": "https://api.example.org/w?key=SECRET", "method": "GET", "timeout": 10.0},
        )
    ]


async def test_http_error_status_raises_without_the_query(host: FakeHost) -> None:
    host.answers["http.fetch"] = {"status": 503, "headers": {}, "body": ""}
    await run_shim(HTTP_BOT, host)
    message = host.logs[-1][1]
    assert "HTTP 503" in message
    assert "SECRET" not in message


async def test_http_fetch_returns_status_headers_and_body(host: FakeHost) -> None:
    host.answers["http.fetch"] = {
        "status": 201,
        "headers": {"x": "y"},
        "body": "aGk=",
        "encoding": "base64",
    }
    code = (
        "def bot(http, **k):\n"
        "    r = http.fetch('https://a.example/', method='POST', body='{}', headers={'A': 'b'})\n"
        "    return f'{r.status} {r.headers[\"x\"]} {r.text}'\n"
    )
    assert texts(await run_shim(code, host)) == ["201 y hi"]
    assert host.sync_calls[0][1] == {
        "url": "https://a.example/",
        "method": "POST",
        "timeout": 10.0,
        "headers": {"A": "b"},
        "body": "{}",
    }


async def test_storage_round_trip(host: FakeHost) -> None:
    store: dict[str, Any] = {}
    host.answers["storage.get"] = lambda p: store.get(p["key"])
    host.answers["storage.set"] = lambda p: store.__setitem__(p["key"], p["value"])
    code = (
        "def bot(storage, **k):\n"
        "    n = storage.get('n', 0) + 1\n"
        "    storage.set('n', n)\n"
        "    return str(n)\n"
    )
    plugin = compat.legacy_bot(code)()
    await plugin.on_message(MESSAGE)
    await plugin.on_message(MESSAGE)
    assert [p["text"] for m, p in host.calls if m == "messages.send"] == ["1", "2"]
    assert store == {"n": 2}


async def test_kwargs_bot_also_receives_http_and_storage(host: FakeHost) -> None:
    code = "def bot(**kwargs):\n    return ','.join(sorted(k for k in kwargs if k in ('http','storage')))"
    assert texts(await run_shim(code, host)) == ["http,storage"]


async def test_legacy_positional_bot_is_unaffected_by_the_new_kwargs(host: FakeHost) -> None:
    assert texts(await run_shim(CONVENTIONS["legacy"], host)) == ["Alice|Hi|aabb"]


def test_shim_message_type() -> None:
    assert sdk.Message.from_params(MESSAGE).text == "Hi"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:s3cret@api.example.org/w?k=SECRET#TOKEN",
        "https://api.example.org:8443/w#TOKEN",
        "https://tok@api.example.org/w",
    ],
)
async def test_logs_never_carry_userinfo_query_or_fragment(url: str, host: FakeHost) -> None:
    code = f"def bot(http, **k):\n    http.get_json({url!r})\n"
    for answer in (
        {"status": 500, "headers": {}, "body": ""},
        HostError({"code": -32010, "message": "x", "data": {"code": "permission_denied"}}),
        HostError({"code": -32010, "message": "x", "data": {"code": "host_not_allowed"}}),
    ):
        host.answers["http.fetch"] = answer
        host.logs.clear()
        await run_shim(code, host)
        message = host.logs[-1][1]
        for secret in ("s3cret", "SECRET", "TOKEN", "tok@", "user"):
            assert secret not in message
        assert message.startswith("Bot function execution failed: ")


def test_safe_url_shapes() -> None:
    assert sdk.safe_url("https://u:p@a.example:81/x/y?q=1#f") == "https://a.example:81/x/y"
    assert sdk.safe_url("not a url") == "an unreadable URL"
    assert sdk.safe_url("http://[::1]:80/p") == "http://[::1]:80/p"
