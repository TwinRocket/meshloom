"""The plugin `http.fetch` capability (`app/plugins/capabilities/http.py`).

Calls go through `capabilities.dispatch` with a stand-in instance; DNS is a
table (`socket.getaddrinfo` replaced) and httpx a `MockTransport` that records
every request, so "nothing is sent" is checked on the transport itself. The
RPC framing caps of `http.fetch` lines are at the end.
"""

import base64
import ipaddress
import json
import logging
import socket
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.plugins import capabilities
from app.plugins.capabilities import http as http_capability
from app.plugins.capabilities.base import CapabilityError
from meshloom_plugin import rpc
from meshloom_plugin.sdk import HttpResponse

# The real-child fixtures of the capability tests (`manager` needs `statuses`).
from tests.test_plugin_capabilities import manager, probe, run_test, statuses  # noqa: F401

DNS = {
    "api.example.org": ["93.184.215.14"],
    "cdn.example.org": ["93.184.215.15"],
    "evil.example.net": ["93.184.215.16"],
    "api.meshloom.app": ["104.21.1.1"],
    "rebind.example.org": ["10.0.0.5"],
}
HOSTS = ["api.example.org", "cdn.example.org", "api.meshloom.app", "rebind.example.org"]


class Dns:
    def __init__(self, monkeypatch) -> None:
        self.lookups: list[str] = []
        monkeypatch.setattr(socket, "getaddrinfo", self)

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.lookups.append(host)
        if host not in DNS:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in DNS[host]
            if ipaddress.ip_address(ip).version == 4
        ]


class _Stream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += len(chunk)
            yield chunk


class Transport:
    """Every `httpx.AsyncClient` answers through `handler`; requests are recorded."""

    def __init__(self, monkeypatch) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = lambda request: httpx.Response(200, text="ok")
        real = httpx.AsyncClient
        outer = self

        def record(request: httpx.Request) -> httpx.Response:
            outer.requests.append(request)
            response = outer.handler(request)
            if isinstance(response.stream, httpx.ByteStream):
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    stream=_Stream([response.content]),
                )
            return response

        class Recording(real):
            def __init__(self, **kwargs):
                assert kwargs["follow_redirects"] is False
                assert kwargs["trust_env"] is False
                kwargs["transport"] = httpx.MockTransport(record)
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Recording)

    def hosts(self) -> list[str]:
        return [r.headers["host"] for r in self.requests]


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def dns(monkeypatch):
    return Dns(monkeypatch)


@pytest.fixture
def net(monkeypatch, dns):
    return Transport(monkeypatch)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(
        http_capability, "QUOTA", http_capability.HourlyQuota(http_capability.PER_HOUR, clock)
    )
    return clock


def instance(plugin_id: str = "acme.weather", hosts: list[str] | None = None, **extra: Any):
    permissions = {"http": {"hosts": HOSTS if hosts is None else hosts}, **extra}
    return SimpleNamespace(
        instance_id=f"{plugin_id}#1", plugin_id=plugin_id, permissions=permissions
    )


async def fetch(params: dict[str, Any], inst=None) -> dict[str, Any]:
    return await capabilities.dispatch(inst or instance(), "http.fetch", params, None)


async def refused(params: dict[str, Any], inst=None) -> CapabilityError:
    with pytest.raises(CapabilityError) as info:
        await fetch(params, inst)
    return info.value


# --- hosts --------------------------------------------------------------------------


async def test_an_undeclared_host_is_refused_before_any_lookup(net, dns):
    err = await refused({"url": "https://evil.example.net/x"})
    assert err.refusal == "host_not_allowed"
    assert err.data == {"code": "host_not_allowed"}
    assert dns.lookups == [] and net.requests == []


async def test_an_undeclared_port_is_refused(net, dns):
    err = await refused({"url": "https://api.example.org:8443/x"})
    assert err.refusal == "host_not_allowed"
    assert net.requests == []


async def test_meshloom_app_is_refused_for_a_third_party_and_allowed_for_meshloom(net, dns):
    err = await refused({"url": "https://api.meshloom.app/v1"}, instance("acme.weather"))
    assert err.refusal == "host_not_allowed"
    assert dns.lookups == [] and net.requests == []

    answer = await fetch({"url": "https://api.meshloom.app/v1"}, instance("meshloom.echo"))
    assert answer["status"] == 200
    assert net.hosts() == ["api.meshloom.app"]


async def test_without_the_http_permission_nothing_is_sent(net, dns):
    inst = SimpleNamespace(instance_id="x#1", plugin_id="acme.x", permissions={})
    err = await refused({"url": "https://api.example.org/"}, inst)
    assert err.refusal == "permission_denied"
    assert dns.lookups == [] and net.requests == []


async def test_a_declared_host_resolving_to_a_private_address_is_refused(net, dns):
    err = await refused({"url": "https://rebind.example.org/"})
    assert err.refusal == "host_not_allowed"
    assert net.requests == []


async def test_a_redirect_to_an_undeclared_host_is_refused_at_the_redirect(net):
    net.handler = lambda r: httpx.Response(302, headers={"location": "https://evil.example.net/"})
    err = await refused({"url": "https://api.example.org/start"})
    assert err.refusal == "host_not_allowed"
    assert net.hosts() == ["api.example.org"]


async def test_redirects_to_declared_hosts_are_followed_up_to_three(net):
    def handler(request: httpx.Request) -> httpx.Response:
        hop = int(request.url.params.get("hop", "0"))
        if hop < 3:
            host = "cdn.example.org" if hop % 2 == 0 else "api.example.org"
            return httpx.Response(302, headers={"location": f"https://{host}/?hop={hop + 1}"})
        return httpx.Response(200, text="done")

    net.handler = handler
    answer = await fetch({"url": "https://api.example.org/?hop=0"})
    assert answer["body"] == "done"
    assert len(net.requests) == 4

    net.requests.clear()
    net.handler = lambda r: httpx.Response(302, headers={"location": "https://api.example.org/"})
    err = await refused({"url": "https://api.example.org/"})
    assert err.refusal == "fetch_failed"
    assert err.params == {"reason": "egress_too_many_redirects"}
    assert len(net.requests) == 4


# --- HTTPS --------------------------------------------------------------------------


async def test_plain_http_to_a_declared_public_host_is_refused(net, dns):
    err = await refused({"url": "http://api.example.org/x"})
    assert err.refusal == "https_required"
    assert dns.lookups == [] and net.requests == []


async def test_plain_http_to_an_undeclared_host_is_host_not_allowed(net):
    err = await refused({"url": "http://evil.example.net/x"})
    assert err.refusal == "host_not_allowed"


async def test_a_redirect_to_plain_http_is_refused(net):
    net.handler = lambda r: httpx.Response(301, headers={"location": "http://api.example.org/"})
    err = await refused({"url": "https://api.example.org/"})
    assert err.refusal == "https_required"
    assert len(net.requests) == 1


# --- sizes ---------------------------------------------------------------------------


async def test_a_2_mb_answer_is_cut_at_1_mb_while_streaming(net):
    stream = _Stream([b"a" * 65536] * 32)
    net.handler = lambda r: httpx.Response(
        200, headers={"content-length": str(2 * 1024**2)}, stream=stream
    )
    answer = await fetch({"url": "https://api.example.org/big"})
    assert answer["truncated"] is True
    assert len(answer["body"]) == http_capability.MAX_RESPONSE_BYTES
    # The rest is never read: the stream stops at the chunk that passed the cap.
    assert stream.read <= http_capability.MAX_RESPONSE_BYTES + 65536


async def test_an_answer_under_the_cap_is_not_truncated(net):
    answer = await fetch({"url": "https://api.example.org/"})
    assert answer == {"status": 200, "headers": answer["headers"], "body": "ok", "truncated": False}


async def test_a_300_kb_request_body_is_refused_before_anything_is_sent(net, dns):
    err = await refused(
        {"url": "https://api.example.org/", "method": "POST", "body": "x" * 300 * 1024}
    )
    assert err.refusal == "invalid_params"
    assert err.params == {"fields": ["body"], "limit": http_capability.MAX_BODY_BYTES}
    assert dns.lookups == [] and net.requests == []


async def test_a_256_kib_request_body_is_sent(net):
    body = "é" * (128 * 1024)
    answer = await fetch({"url": "https://api.example.org/", "method": "PUT", "body": body})
    assert answer["status"] == 200
    assert net.requests[0].content == body.encode("utf-8")
    assert net.requests[0].method == "PUT"


# --- quota ---------------------------------------------------------------------------


async def test_the_61st_request_in_an_hour_is_refused(net, dns, clock):
    clock.now = 3600 * 500_000 + 10
    for _ in range(60):
        await fetch({"url": "https://api.example.org/"})
    assert len(net.requests) == 60
    err = await refused({"url": "https://api.example.org/"})
    assert err.refusal == "quota_exceeded"
    assert err.params == {"period": "hour", "limit": 60, "retry_after": 3590}
    assert len(net.requests) == 60

    # Another instance has its own quota; the next hour starts again.
    await fetch({"url": "https://api.example.org/"}, instance("acme.other"))
    clock.now += 3590
    await fetch({"url": "https://api.example.org/"})
    assert len(net.requests) == 62


async def test_refused_requests_use_no_quota(net, clock):
    for _ in range(70):
        await refused({"url": "https://evil.example.net/"})
        await refused({"url": "http://api.example.org/"})
        await refused({"url": "https://api.example.org/", "method": "TRACE"})
    assert (await fetch({"url": "https://api.example.org/"}))["status"] == 200


# --- headers and logs ----------------------------------------------------------------


async def test_forbidden_headers_never_reach_the_transport(net):
    await fetch(
        {
            "url": "https://api.example.org/",
            "method": "POST",
            "body": "{}",
            "headers": {
                "Host": "evil.example.net",
                "Content-Length": "1",
                "Transfer-Encoding": "chunked",
                "Cookie": "session=1",
                "Proxy-Authorization": "Basic eA==",
                "Connection": "upgrade",
                "Authorization": "Bearer token",
                "Content-Type": "application/json",
            },
        }
    )
    sent = net.requests[0].headers
    assert sent["host"] == "api.example.org"
    assert sent["content-length"] == "2"
    assert "transfer-encoding" not in sent and "cookie" not in sent
    assert "proxy-authorization" not in sent
    assert sent["authorization"] == "Bearer token"
    assert sent["user-agent"] == http_capability.USER_AGENT


async def test_credentials_do_not_follow_a_redirect_to_another_host(net):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://cdn.example.org/file"})
        return httpx.Response(200, text="file")

    net.handler = handler
    await fetch(
        {
            "url": "https://api.example.org/start",
            "headers": {"Authorization": "Bearer t", "X-Api-Key": "k"},
        }
    )
    assert "authorization" not in net.requests[1].headers
    assert "x-api-key" not in net.requests[1].headers


async def test_credentials_in_the_url_are_refused(net, dns):
    err = await refused({"url": "https://user:pass@api.example.org/"})
    assert err.refusal == "invalid_params"
    assert dns.lookups == []


async def test_the_answer_keeps_only_harmless_headers(net):
    net.handler = lambda r: httpx.Response(
        200,
        headers={
            "content-type": "application/json",
            "set-cookie": "a=1",
            "x-secret": "s",
            "etag": '"1"',
        },
        text="{}",
    )
    answer = await fetch({"url": "https://api.example.org/"})
    assert answer["headers"] == {"content-type": "application/json", "etag": '"1"'}


async def test_log_lines_carry_no_query_string(net, caplog):
    caplog.set_level(logging.INFO)
    await fetch({"url": "https://api.example.org/w?key=SECRET#frag"})
    await refused({"url": "https://evil.example.net/w?key=SECRET"})
    await refused({"url": "http://api.example.org/w?key=SECRET"})
    text = caplog.text
    assert "https://api.example.org/w" in text and "evil.example.net/w" in text
    assert "SECRET" not in text and "frag" not in text
    # httpx's own request line too (`app/net/egress_guard.py` filter).
    assert any(r.name == "httpx" for r in caplog.records)


# --- bodies and params ---------------------------------------------------------------


async def test_binary_answers_come_back_in_base64(net):
    raw = bytes(range(256))
    net.handler = lambda r: httpx.Response(200, content=raw)
    answer = await fetch({"url": "https://api.example.org/"})
    assert answer["encoding"] == "base64"
    assert base64.b64decode(answer["body"]) == raw
    parsed = HttpResponse.from_result(answer)
    assert parsed.body == raw and parsed.truncated is False


async def test_text_that_escapes_badly_comes_back_in_base64(net):
    raw = "\x01" * 1000
    net.handler = lambda r: httpx.Response(200, text=raw)
    answer = await fetch({"url": "https://api.example.org/"})
    assert answer["encoding"] == "base64"
    assert base64.b64decode(answer["body"]).decode() == raw


async def test_an_http_error_status_is_an_answer(net):
    net.handler = lambda r: httpx.Response(503, text="down")
    answer = await fetch({"url": "https://api.example.org/"})
    assert answer["status"] == 503 and answer["body"] == "down"


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({}, "url"),
        ({"url": 5}, "url"),
        ({"url": "ftp://api.example.org/"}, "url"),
        ({"url": "https://api.example.org/" + "a" * 3000}, "url"),
        ({"url": "https://api.example.org/", "method": "TRACE"}, "method"),
        ({"url": "https://api.example.org/", "headers": {"X": 1}}, "headers"),
        ({"url": "https://api.example.org/", "headers": {"X": "a\r\nB: c"}}, "headers"),
        ({"url": "https://api.example.org/", "headers": {"Bad Name": "v"}}, "headers"),
        (
            {"url": "https://api.example.org/", "headers": {f"h{i}": "v" for i in range(40)}},
            "headers",
        ),
        ({"url": "https://api.example.org/", "body": {"a": 1}}, "body"),
        ({"url": "https://api.example.org/", "timeout": 0}, "timeout"),
        ({"url": "https://api.example.org/", "timeout": True}, "timeout"),
        ({"url": "https://api.example.org/", "timeout": float("nan")}, "timeout"),
    ],
)
async def test_invalid_params_are_refused(net, params, field):
    err = await refused(params)
    assert err.refusal == "invalid_params"
    assert err.params["fields"] == [field]
    assert net.requests == []


async def test_the_timeout_is_clamped_to_10_seconds(net, monkeypatch):
    seen: dict[str, Any] = {}
    real = http_capability.follow_checked_redirects

    async def spy(method, url, **kwargs):
        seen.update(kwargs)
        return await real(method, url, **kwargs)

    monkeypatch.setattr(http_capability, "follow_checked_redirects", spy)
    await fetch({"url": "https://api.example.org/", "timeout": 120})
    assert seen["total_timeout"] == 10.0
    await fetch({"url": "https://api.example.org/", "timeout": 2.5})
    assert seen["total_timeout"] == 2.5


async def test_a_transport_failure_is_fetch_failed_with_a_reason(net):
    def boom(request):
        raise httpx.ConnectError("refused")

    net.handler = boom
    err = await refused({"url": "https://api.example.org/"})
    assert err.refusal == "fetch_failed"
    assert err.params == {"reason": "connect_error"}


def test_the_new_codes_are_typed_refusals_with_wording():
    from pathlib import Path

    for code in ("host_not_allowed", "https_required", "fetch_failed"):
        assert code in capabilities.REFUSAL_CODES
    slices = Path(__file__).resolve().parent.parent / "frontend/src/i18n/locales/slices"
    for lang in ("en", "fr"):
        wording = json.loads((slices / f"aq.{lang}.json").read_text(encoding="utf-8"))
        errors = wording["plugins"]["capabilityErrors"]
        for code in ("host_not_allowed", "https_required", "fetch_failed"):
            assert errors[code], (lang, code)


# --- RPC framing of http.fetch lines --------------------------------------------------


def _line(message: dict[str, Any], max_bytes: int | None = None) -> bytes:
    return rpc.encode(message, max_bytes)


def test_the_host_reads_a_long_http_fetch_request_and_nothing_else_long():
    body = "x" * (256 * 1024)
    request = _line(rpc.request(1, "http.fetch", {"url": "https://a.example/", "body": body}))
    assert rpc.MAX_MESSAGE_BYTES < len(request) <= rpc.HTTP_FETCH_REQUEST_BYTES
    assert rpc.LineDecoder().feed(request) == [request[:-1]]

    for other in (
        rpc.request(2, "storage.set", {"key": "k", "value": body}),
        rpc.result(3, body),
        rpc.notification("http.fetch", {"body": body}),
    ):
        line = _line(other, rpc.HTTP_FETCH_REQUEST_BYTES)
        with pytest.raises(rpc.FramingError):
            rpc.LineDecoder().feed(line)

    too_long = _line(
        rpc.request(4, "http.fetch", {"body": "x" * rpc.HTTP_FETCH_REQUEST_BYTES}),
        rpc.HTTP_FETCH_RESULT_BYTES,
    )
    with pytest.raises(rpc.FramingError):
        rpc.LineDecoder().feed(too_long)


def test_the_child_reads_a_long_answer_and_no_long_request():
    body = base64.b64encode(b"\xff" * (1024 * 1024)).decode()
    answer = _line(
        rpc.result(7, {"status": 200, "body": body, "encoding": "base64"}),
        rpc.result_limit("http.fetch"),
    )
    assert rpc.MAX_MESSAGE_BYTES < len(answer) <= rpc.HTTP_FETCH_RESULT_BYTES
    assert rpc.LineDecoder(large=rpc.CHILD_INBOUND).feed(answer) == [answer[:-1]]
    # The host's own reader never takes an answer that long.
    with pytest.raises(rpc.FramingError):
        rpc.LineDecoder().feed(answer)

    event = _line(
        rpc.request(8, "event.message", {"text": body[:200_000]}), rpc.HTTP_FETCH_RESULT_BYTES
    )
    with pytest.raises(rpc.FramingError):
        rpc.LineDecoder(large=rpc.CHILD_INBOUND).feed(event)


def test_a_partial_line_is_bounded_by_the_larger_cap():
    decoder = rpc.LineDecoder(large=rpc.CHILD_INBOUND)
    decoder.feed(b"x" * rpc.HTTP_FETCH_RESULT_BYTES)
    with pytest.raises(rpc.FramingError):
        decoder.feed(b"x")
    with pytest.raises(rpc.FramingError):
        rpc.LineDecoder(large=None).feed(b"x" * (rpc.MAX_MESSAGE_BYTES + 1))


def test_encode_caps_per_kind():
    big = "x" * (200 * 1024)
    assert rpc.encode(rpc.request(1, "http.fetch", {"body": big}))
    with pytest.raises(ValueError):
        rpc.encode(rpc.request(1, "storage.set", {"value": big}))
    with pytest.raises(ValueError):
        rpc.encode(rpc.result(1, big))
    assert rpc.encode(rpc.result(1, big), rpc.result_limit("http.fetch"))
    assert rpc.result_limit("storage.get") == rpc.MAX_MESSAGE_BYTES


def test_the_largest_http_fetch_answer_fits_its_line():
    """1 MiB of the worst body, base64, plus every allowed header at full size."""
    answer = {
        "status": 200,
        "headers": dict.fromkeys(
            http_capability.RESPONSE_HEADERS, "v" * http_capability.MAX_RESPONSE_HEADER_CHARS
        ),
        **http_capability._encode_body(b"\x01" * http_capability.MAX_RESPONSE_BYTES),
        "truncated": True,
    }
    assert rpc.encode(rpc.result(2**31, answer), rpc.result_limit("http.fetch"))


# --- through a real child ------------------------------------------------------------


async def test_a_real_child_fetches_through_the_host(manager, tmp_path, net):  # noqa: F811
    net.handler = lambda r: httpx.Response(200, json={"t": 21})
    inst = await probe(manager, tmp_path, "fetcher", {"http": {"hosts": ["api.example.org"]}})
    out = await run_test(
        manager,
        inst,
        ("http.fetch", {"url": "https://api.example.org/w?k=1", "method": "GET", "timeout": 5}),
        ("http.fetch", {"url": "https://evil.example.net/"}),
    )
    assert out[0]["ok"]["status"] == 200
    assert json.loads(out[0]["ok"]["body"]) == {"t": 21}
    assert out[1] == {"error": "host_not_allowed", "params": None}
    assert net.hosts() == ["api.example.org"]


BIG_FETCHER = """
    import base64

    import meshloom_plugin

    def size(answer):
        body = answer["body"]
        raw = base64.b64decode(body) if answer.get("encoding") == "base64" else body.encode()
        return {"len": len(raw), "truncated": answer["truncated"], "first": raw[:1].hex()}

    async def on_message(params):
        url = "https://api.example.org/big"
        out = [size(await meshloom_plugin.host().call("http.fetch", {"url": url}))]
        # The blocking call of the legacy bot shim gets a long answer too.
        out.append(size(meshloom_plugin.host().call_sync("http.fetch", {"url": url})))
        # The largest body a plugin may send (256 KiB) fits its request line.
        posted = await meshloom_plugin.host().call(
            "http.fetch", {"url": url, "method": "POST", "body": "x" * (256 * 1024)}
        )
        out.append(posted["status"])
        return out
"""


async def test_a_real_child_gets_a_1_mib_answer_and_sends_a_256_kib_body(
    manager,  # noqa: F811
    tmp_path,
    net,
    monkeypatch,
):
    import tests.test_plugin_capabilities as capability_tests

    net.handler = lambda r: httpx.Response(200, content=b"\xff" * (2 * 1024 * 1024))
    monkeypatch.setattr(capability_tests, "PROBE", BIG_FETCHER)
    inst = await probe(manager, tmp_path, "big", {"http": {"hosts": ["api.example.org"]}})
    out = await run_test(manager, inst)
    assert out == [{"len": 1024 * 1024, "truncated": True, "first": "ff"}] * 2 + [200]

    assert net.requests[-1].method == "POST"
    assert net.requests[-1].content == b"x" * http_capability.MAX_BODY_BYTES
