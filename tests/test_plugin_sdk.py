"""The plugin SDK and the legacy shim in a real child, with the test as the host.

`Runner` (tests/test_plugin_runner.py) plays the server over the pipes: it answers the
child's requests from a table and records its notifications.
"""

import textwrap
from pathlib import Path
from typing import Any

import pytest
from test_plugin_runner import Runner, make_plugin

from meshloom_plugin import rpc, sdk


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return tmp_path.resolve()  # macOS temp dirs go through the /var -> /private/var link


MESSAGE = {
    "sender_name": "Alice",
    "sender_key": "ab" * 32,
    "message_text": "!ping",
    "is_dm": False,
    "channel_key": "CD" * 16,
    "channel_name": "#test",
    "sender_timestamp": 1,
}


class Host(Runner):
    """A Runner whose requests are answered from `answers`; notifications are recorded."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.answers: dict[str, Any] = {}
        self.requests: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []

    def serve(self, msg_id: int, timeout: float = 15.0) -> dict[str, Any]:
        """Answer the child's requests until the response to `msg_id` arrives."""
        while True:
            message = self.read_message(timeout)
            if "method" not in message:
                assert message.get("id") == msg_id, f"unexpected response {message}"
                return message
            if "id" not in message:
                self.notifications.append(message)
                continue
            self.requests.append(message)
            answer = self.answers.get(message["method"])
            if isinstance(answer, dict) and "__error__" in answer:
                self.send(rpc.error(message["id"], -32010, "refused", answer["__error__"]))
            else:
                value = answer(message["params"]) if callable(answer) else answer
                self.send(rpc.result(message["id"], value))

    def call(self, method: str, params: Any = None, timeout: float = 15.0) -> dict[str, Any]:
        return self.serve(self.start(method, params), timeout)

    def methods(self) -> list[str]:
        return [r["method"] for r in self.requests]


def start(work: Path, source: str, entry: str = "plugin.py:P") -> Host:
    plugin_dir = make_plugin(work, source, entry)
    return Host(plugin_dir, work)


INIT = {
    "api": 1,
    "instance": {"id": "i1"},
    "config": {"city": "Paris", "api_key": "s3cret"},
    "lang": "fr",
    "granted": ["http"],
    "limits": {"message_bytes": 10},
}


@pytest.fixture
def host(work: Path):
    hosts: list[Host] = []

    def make(source: str, **kw: Any) -> Host:
        h = start(work, source, **kw)
        hosts.append(h)
        return h

    yield make
    for h in hosts:
        h.close()


# --- pure helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("hello", 10, "hello"),
        ("hello world", 5, "hello"),
        ("héllo", 2, "h"),  # é is 2 bytes: never cut inside it
        ("héllo", 3, "hé"),
        ("日本語", 7, "日本"),
        ("😀😀", 5, "😀"),
        ("abc", 0, ""),
    ],
)
def test_fit_cuts_on_a_utf8_boundary(text: str, limit: int, expected: str) -> None:
    out = sdk.fit(text, limit)
    assert out == expected
    assert len(out.encode("utf-8")) <= limit


def test_fit_rejects_a_negative_limit() -> None:
    with pytest.raises(ValueError):
        sdk.fit("x", -1)


def test_message_from_params_maps_message_text() -> None:
    msg = sdk.Message.from_params({**MESSAGE, "scoped": True, "region": "eu"})
    assert (msg.text, msg.region, msg.scoped, msg.channel_key) == ("!ping", "eu", True, "CD" * 16)
    assert msg.raw["sender_name"] == "Alice"


# --- in the child -------------------------------------------------------------------

PLUGIN = """
    from meshloom_plugin import HostError, Message, Plugin

    class P(Plugin):
        async def on_start(self):
            self.started = dict(self.config)

        async def on_message(self, msg: Message):
            assert isinstance(msg, Message)
            if msg.text == "!fit":
                return self.fit("héllo wörld")
            if msg.text == "!reply":
                await self.reply(msg, "pong")
            elif msg.text == "!regional":
                await self.reply(msg, "pong", region=None)
            elif msg.text == "!send":
                await self.send("hi", contact_key="ef" * 32)
            elif msg.text == "!storage":
                await self.storage.set("n", 3)
                return [await self.storage.get("n"), await self.storage.get("zz", "dflt"),
                        await self.storage.list("n")]
            elif msg.text == "!http":
                data = await self.http.get_json("https://a.example/x?key=K")
                posted = await self.http.post_json("https://a.example/y", {"a": 1})
                return [data, posted]
            elif msg.text == "!denied":
                try:
                    await self.storage.get("k")
                except HostError as exc:
                    from meshloom_plugin.sdk import error_code
                    return error_code(exc)
            elif msg.text == "!misc":
                await self.timer(60)
                await self.set_status("ok")
                self.log("warning", "careful")
                return self.started
            return None

        async def on_timer(self, params):
            return "ticked"
"""


def test_init_gives_the_plugin_its_config_and_limits(host) -> None:
    h = host(PLUGIN)
    assert h.call("plugin.init", INIT)["result"] is None
    assert h.call("event.message", {**MESSAGE, "message_text": "!misc"})["result"] == {
        "city": "Paris",
        "api_key": "s3cret",
    }
    assert h.methods() == ["timer.set", "status.set"]
    assert h.requests[0]["params"] == {"seconds": 60}
    assert h.requests[1]["params"] == {"summary": "ok"}
    assert h.notifications[0]["params"] == {"level": "warning", "message": "careful"}


def test_fit_uses_the_limit_from_init(host) -> None:
    h = host(PLUGIN)
    h.call("plugin.init", INIT)
    out = h.call("event.message", {**MESSAGE, "message_text": "!fit"})["result"]
    assert out == "héllo wö"  # exactly 10 bytes


def test_fit_without_init_limit_defaults_to_the_dm_limit(host) -> None:
    h = host(PLUGIN)
    h.call("plugin.init", {"api": 1})
    out = h.call("event.message", {**MESSAGE, "message_text": "!fit"})["result"]
    assert out == "héllo wörld"


def test_reply_in_a_channel_and_in_a_dm(host) -> None:
    h = host(PLUGIN)
    h.call("plugin.init", INIT)
    h.call("event.message", {**MESSAGE, "message_text": "!reply"})
    h.call(
        "event.message", {**MESSAGE, "message_text": "!reply", "is_dm": True, "channel_key": None}
    )
    h.call("event.message", {**MESSAGE, "message_text": "!regional"})
    h.call("event.message", {**MESSAGE, "message_text": "!send"})
    assert [r["params"] for r in h.requests] == [
        {"to": {"channel_key": "CD" * 16}, "text": "pong"},
        {"to": {"contact_key": "ab" * 32}, "text": "pong"},
        {"to": {"channel_key": "CD" * 16}, "text": "pong", "region": None},
        {"to": {"contact_key": "ef" * 32}, "text": "hi"},
    ]


def test_storage_helpers(host) -> None:
    h = host(PLUGIN)
    h.call("plugin.init", INIT)
    h.answers["storage.get"] = lambda p: 3 if p["key"] == "n" else None
    h.answers["storage.list"] = ["n"]
    out = h.call("event.message", {**MESSAGE, "message_text": "!storage"})["result"]
    assert out == [3, "dflt", ["n"]]
    assert h.requests[0]["params"] == {"key": "n", "value": 3}


def test_http_helpers(host) -> None:
    h = host(PLUGIN)
    h.call("plugin.init", INIT)
    h.answers["http.fetch"] = lambda p: {
        "status": 200,
        "headers": {},
        "body": '{"ok": true}' if p["method"] == "GET" else '{"posted": 1}',
    }
    out = h.call("event.message", {**MESSAGE, "message_text": "!http"})["result"]
    assert out == [{"ok": True}, {"posted": 1}]
    post = h.requests[1]["params"]
    assert post["method"] == "POST"
    assert post["body"] == '{"a":1}'
    assert post["headers"]["Content-Type"] == "application/json"


def test_a_typed_host_refusal_is_catchable(host) -> None:
    h = host(PLUGIN)
    h.call("plugin.init", INIT)
    h.answers["storage.get"] = {"__error__": {"code": "permission_denied"}}
    assert h.call("event.message", {**MESSAGE, "message_text": "!denied"})["result"] == (
        "permission_denied"
    )


def test_on_timer_and_missing_handlers(host) -> None:
    h = host(PLUGIN)
    h.call("plugin.init", INIT)
    assert h.call("event.timer", {})["result"] == "ticked"
    assert h.call("event.health", {})["result"] is None
    assert h.call("config.changed", {"config": {"city": "Lyon"}})["result"] is None


# --- the shim in a child: synchronous host calls ------------------------------------

LEGACY = """
    from meshloom_plugin.compat import legacy_bot

    P = legacy_bot(BOT)
"""

HTTP_BOT = textwrap.dedent(
    """
    def bot(**kwargs):
        text = kwargs["message_text"]
        if text == "!w":
            return str(kwargs["http"].get_json("https://api.example.org/w")["t"])
        if text == "!n":
            n = kwargs["storage"].get("n", 0) + 1
            kwargs["storage"].set("n", n)
            return str(n)
        return None
    """
)


def legacy(host, bot: str = HTTP_BOT) -> Host:
    return host(LEGACY.replace("BOT", repr(bot)))


def test_legacy_bot_http_goes_through_the_host_with_a_blocking_call(host) -> None:
    h = legacy(host)
    h.call("plugin.init", {"api": 1})
    h.answers["http.fetch"] = {"status": 200, "headers": {}, "body": '{"t": 21}'}
    h.call("event.message", {**MESSAGE, "message_text": "!w"})
    assert h.methods() == ["http.fetch", "messages.send"]
    assert h.requests[1]["params"] == {"to": {"channel_key": "CD" * 16}, "text": "21"}


def test_legacy_bot_without_the_http_permission_logs_a_readable_error(host) -> None:
    h = legacy(host)
    h.call("plugin.init", {"api": 1})
    h.answers["http.fetch"] = {"__error__": {"code": "permission_denied"}}
    assert h.call("event.message", {**MESSAGE, "message_text": "!w"})["result"] is None
    assert h.methods() == ["http.fetch"]
    logs = [n["params"]["message"] for n in h.notifications if n["method"] == "log"]
    expected = (
        "Bot function execution failed: PermissionError: the 'http' permission is not granted "
    )
    assert any(m.startswith(expected + "to this bot for api.example.org: ") for m in logs)


def test_legacy_storage_persists_through_the_host(host) -> None:
    h = legacy(host)
    h.call("plugin.init", {"api": 1})
    store: dict[str, Any] = {}
    h.answers["storage.get"] = lambda p: store.get(p["key"])
    h.answers["storage.set"] = lambda p: store.__setitem__(p["key"], p["value"])
    h.call("event.message", {**MESSAGE, "message_text": "!n"})
    h.call("event.message", {**MESSAGE, "message_text": "!n"})
    sent = [r["params"]["text"] for r in h.requests if r["method"] == "messages.send"]
    assert sent == ["1", "2"]
