"""The egress guard's extensions for fixed-host callers (Store client, plugin ``http``).

Host allowlists, redirects re-validated hop by hop, request and streaming
response caps, dropped headers and the total timeout. The guard's own rules
are covered by ``tests/test_egress_guard.py``.
"""

import asyncio
import ipaddress
import socket

import httpx
import pytest

from app.net.egress_guard import (
    DROPPED_REQUEST_HEADERS,
    EgressBlocked,
    HostAllowlist,
    error_code,
    filter_request_headers,
    follow_checked_redirects,
    validate_destination,
)

GITHUB = HostAllowlist.of("github.com", "objects.githubusercontent.com")
PUBLIC = {
    "github.com": ["140.82.121.4"],
    "objects.githubusercontent.com": ["185.199.108.133"],
    "evil.example.org": ["93.184.215.14"],
    "lan.example.org": ["10.0.0.5"],
}


class Dns:
    """``socket.getaddrinfo`` answering from a table, recording every lookup."""

    def __init__(self, monkeypatch, table: dict[str, list[str]]) -> None:
        self.table = table
        self.lookups: list[str] = []
        monkeypatch.setattr(socket, "getaddrinfo", self)

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.lookups.append(host)
        if host not in self.table:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in self.table[host]:
            if ipaddress.ip_address(ip).version == 4:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
            else:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)))
        return out


class Transport:
    """Routes ``httpx.AsyncClient`` to a handler and records every request it gets."""

    def __init__(self, monkeypatch, handler) -> None:
        self.requests: list[httpx.Request] = []
        self.clients = 0
        real = httpx.AsyncClient
        outer = self

        def record(request: httpx.Request) -> httpx.Response:
            outer.requests.append(request)
            return handler(request)

        class Recording(real):
            def __init__(self, **kwargs):
                assert kwargs["follow_redirects"] is False
                assert kwargs["trust_env"] is False
                outer.clients += 1
                kwargs["transport"] = httpx.MockTransport(record)
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Recording)

    def hosts(self) -> list[str]:
        return [r.headers["host"] for r in self.requests]


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected request to {request.url.host}")


class TestHostAllowlist:
    @pytest.mark.parametrize(
        "entry",
        [
            "*.github.com",
            "*",
            "10.0.0.0/8",
            "fd00::/8",
            "https://github.com",
            "github.com/path",
            "user@github.com",
            "",
            "  ",
            "git hub.com",
            "github.com:0",
            "github.com:99999",
            "github.com:http",
        ],
    )
    def test_refuses_anything_but_an_exact_host(self, entry):
        with pytest.raises(ValueError):
            HostAllowlist.of(entry)

    def test_entries_are_normalised_like_the_url_host(self):
        allow = HostAllowlist.of("GitHub.com.", "Faß.de", "[::1]:8443", "::2", "192.168.1.10:8123")
        assert allow.allows("https", "github.com", 443)
        assert allow.allows("https", "xn--fa-hia.de", 443)
        assert allow.allows("https", "::1", 8443)
        assert allow.allows("https", "::2", 443)
        assert allow.allows("http", "192.168.1.10", 8123)

    def test_an_entry_without_port_allows_only_the_default_port(self):
        allow = HostAllowlist.of("github.com", "api.example.org:8443")
        assert allow.allows("https", "github.com", 443)
        assert allow.allows("http", "github.com", 80)
        assert not allow.allows("https", "github.com", 8443)
        assert not allow.allows("http", "github.com", 443)
        assert allow.allows("https", "api.example.org", 8443)
        assert not allow.allows("https", "api.example.org", 443)

    def test_an_empty_list_allows_nothing(self):
        assert not HostAllowlist.of().allows("https", "github.com", 443)

    @pytest.mark.asyncio
    async def test_host_outside_the_list_is_refused_before_dns(self, monkeypatch):
        dns = Dns(monkeypatch, PUBLIC)
        for url in (
            "https://evil.example.org/",
            "https://github.com.evil.example.org/",
            "https://github.com:8443/",
            "https://93.184.215.14/",
            "https://sub.github.com/",
        ):
            with pytest.raises(EgressBlocked) as err:
                await validate_destination(url, allowed_hosts=GITHUB)
            assert err.value.code == "egress_host_not_allowed"
        assert dns.lookups == []

    @pytest.mark.asyncio
    async def test_an_allowed_host_still_meets_the_address_rules(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        allow = HostAllowlist.of("lan.example.org", "169.254.169.254")
        with pytest.raises(EgressBlocked) as err:
            await validate_destination("https://lan.example.org/", allowed_hosts=allow)
        assert err.value.code == "egress_private_address"
        # Metadata stays refused even when listed and private networks are allowed.
        with pytest.raises(EgressBlocked) as err:
            await validate_destination(
                "http://169.254.169.254/", allowed_hosts=allow, allow_private_network=True
            )
        assert err.value.code == "egress_private_address"

    @pytest.mark.asyncio
    async def test_an_ip_literal_entry_matches_any_spelling(self, monkeypatch):
        Dns(monkeypatch, {})
        allow = HostAllowlist.of("[2606:4700:4700::1111]")
        dest = await validate_destination(
            "https://[2606:4700:4700:0:0:0:0:1111]/", allowed_hosts=allow
        )
        assert dest.host == "2606:4700:4700::1111"


class TestRedirects:
    @pytest.mark.asyncio
    async def test_github_release_redirect_is_followed_pinned_hop_by_hop(self, monkeypatch):
        dns = Dns(monkeypatch, PUBLIC)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers["host"] == "github.com":
                return httpx.Response(
                    302,
                    headers={
                        "Location": "https://objects.githubusercontent.com/a/plugin.zip?sig=1",
                        "Set-Cookie": "logged_in=no; Domain=140.82.121.4",
                    },
                )
            return httpx.Response(200, content=b"zip")

        transport = Transport(monkeypatch, handler)
        resp = await follow_checked_redirects(
            "GET",
            "https://github.com/o/n/releases/latest/download/plugin.zip",
            allowed_hosts=GITHUB,
            schemes=frozenset({"https"}),
            max_redirects=3,
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.content == b"zip"
        assert resp.redirects == 1
        assert resp.url == "https://objects.githubusercontent.com/a/plugin.zip?sig=1"
        assert dns.lookups == ["github.com", "objects.githubusercontent.com"]
        assert [r.url.host for r in transport.requests] == ["140.82.121.4", "185.199.108.133"]
        assert transport.hosts() == ["github.com", "objects.githubusercontent.com"]
        assert transport.requests[1].extensions["sni_hostname"] == "objects.githubusercontent.com"
        assert transport.requests[1].url.raw_path == b"/a/plugin.zip?sig=1"
        # One client per hop: nothing carried over, cookies included.
        assert transport.clients == 2
        assert "cookie" not in transport.requests[1].headers

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("location", "code"),
        [
            ("https://evil.example.org/x", "egress_host_not_allowed"),
            ("https://127.0.0.1/admin", "egress_host_not_allowed"),
            ("https://[::1]/admin", "egress_host_not_allowed"),
            ("https://github.com:8443/", "egress_host_not_allowed"),
            ("http://objects.githubusercontent.com/x", "egress_scheme"),
            ("file:///etc/passwd", "egress_scheme"),
            ("https://lan.example.org/x", "egress_private_address"),
        ],
    )
    async def test_a_refused_hop_stops_the_chain(self, monkeypatch, location, code):
        allow = HostAllowlist.of("github.com", "objects.githubusercontent.com", "lan.example.org")
        dns = Dns(monkeypatch, PUBLIC)
        transport = Transport(
            monkeypatch, lambda request: httpx.Response(302, headers={"Location": location})
        )
        with pytest.raises(EgressBlocked) as err:
            await follow_checked_redirects(
                "GET",
                "https://github.com/o/n/releases/latest",
                allowed_hosts=allow,
                schemes=frozenset({"https"}),
                max_redirects=5,
                timeout=5,
            )
        assert err.value.code == code
        assert transport.hosts() == ["github.com"]
        assert "evil.example.org" not in dns.lookups

    @pytest.mark.asyncio
    async def test_without_allowlist_a_hop_to_a_private_address_is_refused(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        transport = Transport(
            monkeypatch,
            lambda request: httpx.Response(302, headers={"Location": "http://10.0.0.1/"}),
        )
        with pytest.raises(EgressBlocked) as err:
            await follow_checked_redirects(
                "GET", "https://evil.example.org/", max_redirects=2, timeout=5
            )
        assert err.value.code == "egress_private_address"
        assert len(transport.requests) == 1

    @pytest.mark.asyncio
    async def test_a_redirect_is_returned_as_is_when_none_is_allowed(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        transport = Transport(
            monkeypatch,
            lambda request: httpx.Response(302, headers={"Location": "http://127.0.0.1/"}),
        )
        resp = await follow_checked_redirects("GET", "https://github.com/", timeout=5)
        assert resp.status_code == 302
        assert resp.redirects == 0
        assert len(transport.requests) == 1

    @pytest.mark.asyncio
    async def test_too_many_redirects(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        transport = Transport(
            monkeypatch,
            lambda request: httpx.Response(302, headers={"Location": "/again"}),
        )
        with pytest.raises(EgressBlocked) as err:
            await follow_checked_redirects(
                "GET", "https://github.com/", allowed_hosts=GITHUB, max_redirects=2, timeout=5
            )
        assert err.value.code == "egress_too_many_redirects"
        assert error_code(err.value) == "egress_too_many_redirects"
        assert len(transport.requests) == 3

    @pytest.mark.asyncio
    async def test_relative_location_is_joined_to_the_hostname_not_the_ip(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/a/start":
                return httpx.Response(307, headers={"Location": "next?x=1#frag"})
            return httpx.Response(200)

        transport = Transport(monkeypatch, handler)
        resp = await follow_checked_redirects(
            "GET", "https://github.com/a/start", allowed_hosts=GITHUB, max_redirects=1, timeout=5
        )
        assert resp.url == "https://github.com/a/next?x=1"
        assert transport.hosts() == ["github.com", "github.com"]

    @pytest.mark.asyncio
    async def test_303_turns_a_post_into_a_get_without_body(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(303, headers={"Location": "/done"})
            return httpx.Response(200)

        transport = Transport(monkeypatch, handler)
        await follow_checked_redirects(
            "POST",
            "https://github.com/form",
            allowed_hosts=GITHUB,
            max_redirects=1,
            content=b"secret=1",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=5,
        )
        second = transport.requests[1]
        assert second.method == "GET"
        assert second.content == b""
        assert "content-type" not in second.headers

    @pytest.mark.asyncio
    async def test_307_keeps_method_and_body(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/a":
                return httpx.Response(307, headers={"Location": "/b"})
            return httpx.Response(200)

        transport = Transport(monkeypatch, handler)
        await follow_checked_redirects(
            "PUT",
            "https://github.com/a",
            allowed_hosts=GITHUB,
            max_redirects=1,
            content=b"body",
            timeout=5,
        )
        assert [(r.method, r.content) for r in transport.requests] == [
            ("PUT", b"body"),
            ("PUT", b"body"),
        ]

    @pytest.mark.asyncio
    async def test_authorization_is_dropped_when_a_hop_leaves_the_origin(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/a":
                return httpx.Response(302, headers={"Location": "/b"})
            if request.url.path == "/b":
                return httpx.Response(
                    302, headers={"Location": "https://objects.githubusercontent.com/c"}
                )
            return httpx.Response(200)

        transport = Transport(monkeypatch, handler)
        await follow_checked_redirects(
            "GET",
            "https://github.com/a",
            allowed_hosts=GITHUB,
            max_redirects=2,
            headers={"Authorization": "Bearer t"},
            timeout=5,
        )
        assert [r.headers.get("authorization") for r in transport.requests] == [
            "Bearer t",
            "Bearer t",
            None,
        ]


def _chunks(counter: list[int], n: int, size: int = 1024):
    async def body():
        for _ in range(n):
            counter.append(1)
            yield b"x" * size

    return body()


class TestCaps:
    @pytest.mark.asyncio
    async def test_response_is_aborted_while_streaming(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        produced: list[int] = []
        Transport(monkeypatch, lambda request: httpx.Response(200, content=_chunks(produced, 1000)))
        with pytest.raises(EgressBlocked) as err:
            await follow_checked_redirects(
                "GET", "https://github.com/big", max_response_bytes=4096, timeout=5
            )
        assert err.value.code == "egress_response_too_large"
        # Stopped at the first chunk past the cap, not after the whole body.
        assert len(produced) == 5

    @pytest.mark.asyncio
    async def test_declared_length_above_the_cap_is_refused_unread(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        produced: list[int] = []
        Transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, headers={"Content-Length": "1048577"}, content=_chunks(produced, 3)
            ),
        )
        with pytest.raises(EgressBlocked) as err:
            await follow_checked_redirects(
                "GET", "https://github.com/big", max_response_bytes=1048576, timeout=5
            )
        assert err.value.code == "egress_response_too_large"
        assert produced == []

    @pytest.mark.asyncio
    async def test_a_body_at_the_cap_is_returned(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        Transport(monkeypatch, lambda request: httpx.Response(200, content=_chunks([], 4)))
        resp = await follow_checked_redirects(
            "GET", "https://github.com/ok", max_response_bytes=4096, timeout=5
        )
        assert len(resp.content) == 4096

    @pytest.mark.asyncio
    async def test_compression_is_not_requested_by_default(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        transport = Transport(monkeypatch, lambda request: httpx.Response(200))
        await follow_checked_redirects("GET", "https://github.com/", timeout=5)
        assert transport.requests[0].headers["accept-encoding"] == "identity"

    @pytest.mark.asyncio
    async def test_request_body_above_the_cap_is_refused_before_dns(self, monkeypatch):
        dns = Dns(monkeypatch, PUBLIC)
        Transport(monkeypatch, _never)
        with pytest.raises(EgressBlocked) as err:
            await follow_checked_redirects(
                "POST",
                "https://github.com/",
                content=b"x" * 262145,
                max_request_bytes=262144,
                timeout=5,
            )
        assert err.value.code == "egress_request_too_large"
        assert dns.lookups == []

    @pytest.mark.asyncio
    async def test_total_timeout_bounds_a_dripping_server(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)

        async def drip():
            while True:
                await asyncio.sleep(0.05)
                yield b"x"

        Transport(monkeypatch, lambda request: httpx.Response(200, content=drip()))
        with pytest.raises(TimeoutError) as err:
            await follow_checked_redirects(
                "GET", "https://github.com/", timeout=5, total_timeout=0.3
            )
        assert error_code(err.value) == "timeout"


class TestHeaders:
    @pytest.mark.asyncio
    async def test_filtered_headers_never_reach_the_transport(self, monkeypatch):
        Dns(monkeypatch, PUBLIC)
        transport = Transport(monkeypatch, lambda request: httpx.Response(200))
        await follow_checked_redirects(
            "POST",
            "https://github.com/x",
            content=b"{}",
            headers={
                "host": "169.254.169.254",
                "Content-Length": "999",
                "Transfer-Encoding": "chunked",
                "Cookie": "session=1",
                "Connection": "upgrade",
                "Upgrade": "websocket",
                "Proxy-Authorization": "Basic x",
                "X-Plugin": "kept",
            },
            timeout=5,
        )
        sent = transport.requests[0].headers
        assert sent.get_list("host") == ["github.com"]
        assert sent["content-length"] == "2"
        assert "transfer-encoding" not in sent
        assert "cookie" not in sent
        assert "upgrade" not in sent
        assert "proxy-authorization" not in sent
        assert sent["x-plugin"] == "kept"

    def test_the_hook_can_drop_more_but_never_restore(self):
        kept = filter_request_headers(
            {"Cookie": "a", "Authorization": "b", "X-Ok": "c"},
            header_filter=lambda name, value: (
                name.lower() != "authorization" or name.lower() == "cookie"
            ),
        )
        assert kept == {"X-Ok": "c"}
        assert {"host", "content-length", "transfer-encoding", "cookie"} <= DROPPED_REQUEST_HEADERS
