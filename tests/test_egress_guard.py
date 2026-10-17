"""The shared egress guard: address classes, resolution and IP pinning (SSRF).

Every class is tested on its own address: ``ipaddress.is_private`` differs
between Python 3.11 and 3.13, and the guard must not depend on it.
"""

import asyncio
import contextlib
import ipaddress
import socket

import httpx
import pytest
import requests

from app.net.egress_guard import (
    EgressBlocked,
    error_code,
    pinned_async_client,
    validate_destination,
)
from app.push.send import _build_pinned_session, send_push

HOST = "hook.example.test"

PRIVATE = [
    "0.0.0.0",
    "10.1.2.3",
    "100.64.0.1",
    "100.127.255.254",
    "127.0.0.1",
    "127.8.9.10",
    "169.254.10.20",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.1.10",
    # IETF protocol assignments, benchmarking (fake-IP pool of local DNS proxies)
    "192.0.0.170",
    "198.18.0.1",
    "198.19.255.254",
    "224.0.0.251",
    "239.255.255.250",
    "255.255.255.255",
    "::",
    "::1",
    "fe80::1",
    "fec0::1",
    "fc00::1",
    "fd12:3456::1",
    "ff02::1",
    # IPv4-mapped IPv6 of private IPv4
    "::ffff:10.0.0.1",
    "::ffff:127.0.0.1",
    "::ffff:192.168.1.10",
    "::ffff:100.64.0.1",
    # NAT64, 6to4 and Teredo are refused as whole classes
    "64:ff9b::808:808",
    "64:ff9b:1::a00:1",
    "2002:c0a8:10a::1",
    "2001:0:4136:e378:8000:63bf:f7f7:f7f7",
    # deprecated IPv4-compatible
    "::7f00:1",
]
PUBLIC = [
    "8.8.8.8",
    "1.1.1.1",
    "100.63.255.255",
    "100.128.0.1",
    "172.32.0.1",
    "192.169.0.1",
    "192.0.1.1",
    "198.17.255.254",
    "198.20.0.1",
    "203.0.114.1",
    "2606:4700:4700::1111",
    "2a00:1450:4007:80b::200e",
    "::ffff:8.8.8.8",
]
METADATA = [
    "169.254.169.254",
    "fd00:ec2::254",
    "::ffff:169.254.169.254",
    "64:ff9b::a9fe:a9fe",
    "64:ff9b:1::a9fe:a9fe",
    "2002:a9fe:a9fe::1",  # 6to4
    "::a9fe:a9fe",  # IPv4-compatible
    "2001:0:4136:e378:8000:63bf:5601:5601",  # Teredo (client address inverted)
    "100.100.100.200",
    "::ffff:100.100.100.200",
]
DOCUMENTATION = [
    "192.0.2.1",
    "198.51.100.7",
    "203.0.113.9",
    "2001:db8::1",
    "::ffff:203.0.113.9",
    "2002:c000:204::1",  # 6to4 of 192.0.2.4
    "64:ff9b::cb00:7109",
]


def _url(ip: str, scheme: str = "https") -> str:
    host = f"[{ip}]" if ":" in ip else ip
    return f"{scheme}://{host}/hook"


class FakeDns:
    """``socket.getaddrinfo`` answering ``HOST`` from a table that tests can change."""

    def __init__(self, monkeypatch, answers: list[str]) -> None:
        self.answers = answers
        self.lookups: list[str] = []
        self._real = socket.getaddrinfo
        monkeypatch.setattr(socket, "getaddrinfo", self)

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        if host != HOST:
            return self._real(host, port, family, type, proto, flags)
        self.lookups.append(host)
        if not self.answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in self.answers:
            if ipaddress.ip_address(ip).version == 4:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
            else:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)))
        return out


class TestAddressClasses:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("ip", PRIVATE)
    async def test_private_classes_refused_without_the_flag(self, ip):
        with pytest.raises(EgressBlocked) as caught:
            await validate_destination(_url(ip))
        assert caught.value.code == "egress_private_address"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ip", PRIVATE)
    async def test_private_classes_allowed_with_the_flag(self, ip):
        dest = await validate_destination(_url(ip), allow_private_network=True)
        assert dest.ips == (ipaddress.ip_address(ip),)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ip", PUBLIC)
    async def test_public_addresses_pass(self, ip):
        dest = await validate_destination(_url(ip))
        assert dest.ips == (ipaddress.ip_address(ip),)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ip", METADATA)
    async def test_metadata_refused_even_with_the_flag(self, ip):
        with pytest.raises(EgressBlocked) as caught:
            await validate_destination(_url(ip), allow_private_network=True)
        assert caught.value.code == "egress_private_address"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ip", DOCUMENTATION)
    async def test_documentation_ranges_refused_even_with_the_flag(self, ip):
        with pytest.raises(EgressBlocked) as caught:
            await validate_destination(_url(ip), allow_private_network=True)
        assert caught.value.code == "egress_private_address"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url", ["http://metadata.google.internal/x", "http://METADATA.google.internal./x"]
    )
    async def test_metadata_host_refused_without_a_lookup(self, url, monkeypatch):
        dns = FakeDns(monkeypatch, ["8.8.8.8"])
        with pytest.raises(EgressBlocked):
            await validate_destination(url, allow_private_network=True)
        assert dns.lookups == []

    @pytest.mark.asyncio
    async def test_loopback_hostname_follows_the_flag(self):
        with pytest.raises(EgressBlocked):
            await validate_destination("http://localhost:8123/api")
        dest = await validate_destination("http://localhost:8123/api", allow_private_network=True)
        assert all(ip.is_loopback for ip in dest.ips)


class TestResolution:
    @pytest.mark.asyncio
    async def test_a_host_resolving_to_public_and_private_is_refused(self, monkeypatch):
        FakeDns(monkeypatch, ["93.184.215.14", "192.168.1.10"])
        with pytest.raises(EgressBlocked) as caught:
            await validate_destination(f"https://{HOST}/hook")
        assert caught.value.code == "egress_private_address"

    @pytest.mark.asyncio
    async def test_a_v6_private_answer_taints_a_public_v4(self, monkeypatch):
        FakeDns(monkeypatch, ["93.184.215.14", "::ffff:127.0.0.1"])
        with pytest.raises(EgressBlocked):
            await validate_destination(f"https://{HOST}/hook")

    @pytest.mark.asyncio
    async def test_mixed_answer_allowed_with_the_flag_but_not_metadata(self, monkeypatch):
        dns = FakeDns(monkeypatch, ["93.184.215.14", "192.168.1.10"])
        dest = await validate_destination(f"https://{HOST}/hook", allow_private_network=True)
        assert [str(ip) for ip in dest.ips] == ["93.184.215.14", "192.168.1.10"]
        assert (dest.scheme, dest.host, dest.port) == ("https", HOST, 443)
        dns.answers = ["93.184.215.14", "169.254.169.254"]
        with pytest.raises(EgressBlocked):
            await validate_destination(f"https://{HOST}/hook", allow_private_network=True)

    @pytest.mark.asyncio
    async def test_unresolvable(self, monkeypatch):
        FakeDns(monkeypatch, [])
        with pytest.raises(EgressBlocked) as caught:
            await validate_destination(f"https://{HOST}/hook")
        assert caught.value.code == "egress_unresolvable"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "hook.example.test/path",
            "ftp://8.8.8.8/x",
            "file:///etc/passwd",
            "gopher://8.8.8.8/",
            "http://",
            "https://8.8.8.8:99999/",
            "https://8.8.8.8:0/",
            "javascript:alert(1)",
        ],
    )
    async def test_scheme_and_malformed_urls(self, url):
        with pytest.raises(EgressBlocked) as caught:
            await validate_destination(url, allow_private_network=True)
        assert caught.value.code == "egress_scheme"

    @pytest.mark.asyncio
    async def test_custom_schemes(self):
        with pytest.raises(EgressBlocked):
            await validate_destination("http://8.8.8.8/", schemes=frozenset({"https"}))
        dest = await validate_destination("https://8.8.8.8/", schemes=frozenset({"https"}))
        assert dest.port == 443


@contextlib.asynccontextmanager
async def _recording_server(status: int = 204, extra_headers: str = ""):
    """A local HTTP server recording each request's header block."""
    seen: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            seen.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\n{extra_headers}"
            "Connection: close\r\n\r\n".encode()
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], seen
    finally:
        server.close()
        await server.wait_closed()


def _host_header(raw: bytes) -> str:
    for line in raw.decode().split("\r\n"):
        if line.lower().startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("no Host header")


class TestPinnedClient:
    @pytest.mark.asyncio
    async def test_dns_change_after_validation_does_not_move_the_connection(self, monkeypatch):
        """Rebinding: the second answer is never looked up, the TCP connection
        goes to the validated IP, and Host carries the hostname."""
        dns = FakeDns(monkeypatch, ["127.0.0.1"])
        async with _recording_server() as (port, seen):
            dest = await validate_destination(
                f"http://{HOST}:{port}/hook?t=1", allow_private_network=True
            )
            dns.answers = ["10.255.255.1"]
            lookups = len(dns.lookups)
            async with pinned_async_client(dest, timeout=5) as client:
                resp = await client.post(content=b"{}", headers={"X-Test": "1"})
        assert resp.status_code == 204
        assert len(dns.lookups) == lookups == 1
        assert len(seen) == 1
        assert seen[0].startswith(b"POST /hook?t=1 HTTP/1.1")
        assert _host_header(seen[0]) == f"{HOST}:{port}"

    @pytest.mark.asyncio
    async def test_request_targets_a_validated_ip_with_host_and_sni(self, monkeypatch):
        dns = FakeDns(monkeypatch, ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

        real = httpx.AsyncClient

        class Recording(real):
            def __init__(self, **kwargs):
                assert kwargs["follow_redirects"] is False
                assert kwargs["trust_env"] is False
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(**kwargs)

        dest = await validate_destination(f"https://user:pw@{HOST}:8443/a/b?x=1")
        dns.answers = ["127.0.0.1"]
        monkeypatch.setattr(httpx, "AsyncClient", Recording)
        async with pinned_async_client(dest, timeout=5) as client:
            resp = await client.post(content=b"{}")
        # The redirect is returned, never followed.
        assert resp.status_code == 302
        assert len(captured) == 1
        request = captured[0]
        assert request.url.host == "93.184.215.14"
        assert request.url.port == 8443
        assert request.url.raw_path == b"/a/b?x=1"
        assert request.headers["host"] == f"{HOST}:8443"
        assert request.headers["authorization"].startswith("Basic ")
        assert request.extensions["sni_hostname"] == HOST
        assert dns.lookups == [HOST]

    @pytest.mark.asyncio
    async def test_idn_host_resolved_and_sent_as_the_same_idna2008_label(self, monkeypatch):
        """DNS, SNI and Host all use the IDNA2008 A-label: getaddrinfo on the
        Unicode name would apply IDNA 2003 and resolve fass.de instead."""
        lookups: list[str] = []

        def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            lookups.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        captured: list[httpx.Request] = []
        real = httpx.AsyncClient

        class Recording(real):
            def __init__(self, **kwargs):
                kwargs["transport"] = httpx.MockTransport(
                    lambda request: captured.append(request) or httpx.Response(204)
                )
                super().__init__(**kwargs)

        dest = await validate_destination("https://Faß.de/hook")
        assert dest.host == "xn--fa-hia.de"
        assert lookups == ["xn--fa-hia.de"]
        monkeypatch.setattr(httpx, "AsyncClient", Recording)
        async with pinned_async_client(dest, timeout=5) as client:
            await client.post(content=b"{}")
        assert captured[0].headers["host"] == "xn--fa-hia.de"
        assert captured[0].extensions["sni_hostname"] == "xn--fa-hia.de"

    @pytest.mark.asyncio
    async def test_next_validated_ip_after_a_connect_failure(self, monkeypatch):
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.url.host)
            if len(attempts) == 1:
                raise httpx.ConnectError("refused")
            return httpx.Response(200)

        real = httpx.AsyncClient

        class Recording(real):
            def __init__(self, **kwargs):
                kwargs["transport"] = httpx.MockTransport(handler)
                super().__init__(**kwargs)

        FakeDns(monkeypatch, ["2606:4700:4700::1111", "1.1.1.1"])
        dest = await validate_destination(f"https://{HOST}/")
        monkeypatch.setattr(httpx, "AsyncClient", Recording)
        async with pinned_async_client(dest, timeout=5) as client:
            assert (await client.post()).status_code == 200
        assert attempts == ["2606:4700:4700::1111", "1.1.1.1"]


class TestPinnedPushSession:
    @pytest.mark.asyncio
    async def test_session_connects_only_to_the_pinned_address(self, monkeypatch):
        dns = FakeDns(monkeypatch, ["10.255.255.1"])
        async with _recording_server(201) as (port, seen):
            session = _build_pinned_session(["127.0.0.1"])
            try:
                resp = await asyncio.to_thread(
                    session.post, f"http://{HOST}:{port}/push", data=b"x", timeout=5
                )
            finally:
                session.close()
        assert resp.status_code == 201
        assert dns.lookups == []
        assert _host_header(seen[0]) == f"{HOST}:{port}"

    @pytest.mark.asyncio
    async def test_session_follows_no_redirect(self):
        async with _recording_server(307, "Location: http://127.0.0.1:1/x\r\n") as (port, seen):
            session = _build_pinned_session(["127.0.0.1"])
            try:
                with pytest.raises(requests.exceptions.TooManyRedirects):
                    await asyncio.to_thread(
                        session.post, f"http://{HOST}:{port}/push", data=b"x", timeout=5
                    )
            finally:
                session.close()
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_send_push_refuses_before_any_connection(self, monkeypatch):
        called = []
        monkeypatch.setattr("app.push.send.webpush", lambda **kw: called.append(kw))
        with pytest.raises(EgressBlocked):
            await send_push(
                subscription_info={"endpoint": "http://192.168.1.10/push", "keys": {}},
                payload="{}",
                vapid_private_key="k",
                vapid_claims={"sub": "mailto:a@b.c"},
            )
        assert called == []


def test_error_codes():
    request = httpx.Request("POST", "https://h/")
    assert error_code(EgressBlocked("egress_scheme")) == "egress_scheme"
    assert error_code(httpx.ReadTimeout("t", request=request)) == "timeout"
    assert error_code(TimeoutError()) == "timeout"
    status = httpx.HTTPStatusError("e", request=request, response=httpx.Response(500))
    assert error_code(status) == "service_error"
    import ssl

    tls = httpx.ConnectError("tls")
    tls.__cause__ = ssl.SSLError(1, "bad")
    assert error_code(tls) == "tls_error"
    assert error_code(httpx.ConnectError("refused")) == "connect_error"
