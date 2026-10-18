"""Web Push at send time through the egress guard (MSHL-67).

A fake resolver answers the push host, a local TLS push server records what it
receives: delivery reaches the validated IP with the original SNI and ``Host``,
the certificate is checked against the host name, the host is resolved once,
and a stored endpoint the guard now refuses is skipped (deleted when no setting
can lift the refusal), logged without the endpoint's secret parts.
"""

import asyncio
import base64
import contextlib
import datetime
import ipaddress
import logging
import os
import socket
import ssl

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import app.push.send as push_send
from app.net.egress_guard import EgressBlocked
from app.push.manager import push_manager
from app.push.send import send_push
from app.repository.push_subscriptions import PushSubscriptionRepository

PUSH_HOST = "push.example.test"
SECRET_PATH = "wpush/v2/gAAAAAsecret-token-of-the-subscription"


class FakeResolver:
    """``socket.getaddrinfo`` answering ``PUSH_HOST`` from a list of answers, one per lookup."""

    def __init__(self, monkeypatch, *answers: list[str]) -> None:
        self.answers = list(answers)
        self.lookups = 0
        self._real = socket.getaddrinfo
        monkeypatch.setattr(socket, "getaddrinfo", self)

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        if host != PUSH_HOST:
            return self._real(host, port, family, type, proto, flags)
        self.lookups += 1
        ips = self.answers[min(self.lookups, len(self.answers)) - 1]
        if not ips:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in ips:
            if ipaddress.ip_address(ip).version == 4:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
            else:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)))
        return out


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _vapid_private_key() -> str:
    return _b64(_ec_key().private_numbers().private_value.to_bytes(32, "big"))


def _subscription_keys() -> dict[str, str]:
    public = (
        _ec_key()
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return {"p256dh": _b64(public), "auth": _b64(os.urandom(16))}


def _write_pki(tmp_path, server_name: str) -> tuple[str, str, str]:
    """A CA and a leaf certificate for ``server_name``: (CA file, cert file, key file)."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = _ec_key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test push CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = _ec_key()
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, server_name)]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(server_name)]), False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_file, cert_file, key_file = (tmp_path / n for n in ("ca.pem", "leaf.pem", "leaf.key"))
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(ca_file), str(cert_file), str(key_file)


@contextlib.asynccontextmanager
async def _tls_push_server(cert_file: str, key_file: str, status: int = 201):
    """A local HTTPS push service recording the SNI, the request head and the body."""
    seen: dict[str, list] = {"sni": [], "requests": []}
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert_file, key_file)
    ctx.sni_callback = lambda _sock, name, _ctx: seen["sni"].append(name)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            head = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            body = await reader.readexactly(length)
            seen["requests"].append((head, body))
            writer.write(
                f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=ctx)
    try:
        yield server.sockets[0].getsockname()[1], seen
    finally:
        server.close()
        await server.wait_closed()


def _trust(monkeypatch, ca_file: str) -> None:
    """Make the pinned session trust the test CA (and nothing else changes)."""
    real = push_send._build_pinned_session

    def build(addresses):
        session = real(addresses)
        session.verify = ca_file
        return session

    monkeypatch.setattr(push_send, "_build_pinned_session", build)


def _header(head: bytes, name: str) -> str | None:
    for line in head.decode("latin-1").split("\r\n")[1:]:
        key, _, value = line.partition(":")
        if key.strip().lower() == name:
            return value.strip()
    return None


async def _send(endpoint: str, **kwargs) -> int:
    return await send_push(
        subscription_info={"endpoint": endpoint, "keys": _subscription_keys()},
        payload='{"title": "t"}',
        vapid_private_key=_vapid_private_key(),
        vapid_claims={"sub": "mailto:test@example.org"},
        **kwargs,
    )


class TestPinnedTlsDelivery:
    @pytest.mark.asyncio
    async def test_delivery_reaches_the_validated_ip_with_the_original_sni(
        self, monkeypatch, tmp_path
    ):
        # A second lookup would answer the metadata address: it must never happen.
        dns = FakeResolver(monkeypatch, ["127.0.0.1"], ["169.254.169.254"])
        ca_file, cert_file, key_file = _write_pki(tmp_path, PUSH_HOST)
        _trust(monkeypatch, ca_file)
        async with _tls_push_server(cert_file, key_file) as (port, seen):
            status = await _send(
                f"https://{PUSH_HOST}:{port}/{SECRET_PATH}", allow_private_network=True
            )
        assert status == 201
        assert dns.lookups == 1
        assert seen["sni"] == [PUSH_HOST]
        [(head, body)] = seen["requests"]
        assert head.startswith(f"POST /{SECRET_PATH} HTTP/1.1".encode())
        assert _header(head, "host") == f"{PUSH_HOST}:{port}"
        assert _header(head, "content-encoding") == "aes128gcm"
        assert body  # the encrypted payload

    @pytest.mark.asyncio
    async def test_a_certificate_for_another_name_is_refused(self, monkeypatch, tmp_path):
        FakeResolver(monkeypatch, ["127.0.0.1"])
        ca_file, cert_file, key_file = _write_pki(tmp_path, "other.example.test")
        _trust(monkeypatch, ca_file)
        async with _tls_push_server(cert_file, key_file) as (port, seen):
            with pytest.raises(requests.exceptions.SSLError):
                await _send(f"https://{PUSH_HOST}:{port}/{SECRET_PATH}", allow_private_network=True)
        # The IPv4 retry may try a second handshake: same name, same refusal.
        assert seen["sni"] and set(seen["sni"]) == {PUSH_HOST}
        assert seen["requests"] == []

    @pytest.mark.asyncio
    async def test_an_untrusted_certificate_is_refused(self, monkeypatch, tmp_path):
        """No test CA: the system store does not know it, so nothing is delivered."""
        FakeResolver(monkeypatch, ["127.0.0.1"])
        _ca_file, cert_file, key_file = _write_pki(tmp_path, PUSH_HOST)
        async with _tls_push_server(cert_file, key_file) as (port, seen):
            with pytest.raises(requests.exceptions.SSLError):
                await _send(f"https://{PUSH_HOST}:{port}/{SECRET_PATH}", allow_private_network=True)
        assert seen["requests"] == []

    @pytest.mark.asyncio
    async def test_a_private_answer_is_refused_before_any_connection(self, monkeypatch):
        FakeResolver(monkeypatch, ["93.184.215.14", "10.0.0.7"])
        connects: list = []
        monkeypatch.setattr(
            push_send, "_create_pinned_connection", lambda *a, **k: connects.append(a)
        )
        with pytest.raises(EgressBlocked) as exc_info:
            await _send(f"https://{PUSH_HOST}/{SECRET_PATH}")
        assert exc_info.value.code == "egress_private_address"
        assert not exc_info.value.permanent  # the opt-in would lift it
        assert connects == []


class TestRefusalPermanence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answers",
        [["169.254.169.254"], ["192.0.2.10"], ["2001:db8::1"], ["::ffff:169.254.169.254"]],
    )
    async def test_forbidden_whatever_the_setting_is_permanent(self, monkeypatch, answers):
        FakeResolver(monkeypatch, answers)
        with pytest.raises(EgressBlocked) as exc_info:
            await _send(f"https://{PUSH_HOST}/x", allow_private_network=True)
        assert exc_info.value.permanent

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint",
        ["ftp://push.example.test/x", "https://metadata.google.internal/x", "not a url", ""],
    )
    async def test_bad_urls_and_metadata_hosts_are_permanent(self, endpoint):
        with pytest.raises(EgressBlocked) as exc_info:
            await _send(endpoint, allow_private_network=True)
        assert exc_info.value.permanent

    @pytest.mark.asyncio
    async def test_unresolvable_is_not_permanent(self, monkeypatch):
        FakeResolver(monkeypatch, [])
        with pytest.raises(EgressBlocked) as exc_info:
            await _send(f"https://{PUSH_HOST}/x")
        assert exc_info.value.code == "egress_unresolvable"
        assert not exc_info.value.permanent


async def _dispatch(monkeypatch) -> list:
    """One dispatch to every stored subscription; returns the webpush calls."""
    monkeypatch.setattr("app.push.manager.get_vapid_private_key", lambda: _vapid_private_key())
    calls: list = []
    monkeypatch.setattr(push_send, "webpush", lambda **kw: calls.append(kw))
    await push_manager.dispatch_event({"event": "oss_update", "title": "t", "body": "b"})
    return calls


class TestStoredEndpointRevalidated:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("answers", [["169.254.169.254"], ["198.51.100.4"]])
    async def test_an_endpoint_now_forbidden_for_good_is_skipped_and_deleted(
        self, test_db, monkeypatch, caplog, answers
    ):
        # Valid when it was stored; the name now resolves to an address no setting allows.
        FakeResolver(monkeypatch, answers)
        sub = await PushSubscriptionRepository.create(
            endpoint=f"https://{PUSH_HOST}/{SECRET_PATH}", p256dh="p", auth="a"
        )
        with caplog.at_level(logging.DEBUG):
            calls = await _dispatch(monkeypatch)
        assert calls == []
        assert await PushSubscriptionRepository.get(sub["id"]) is None
        refusals = [r for r in caplog.records if "refused for good" in r.getMessage()]
        assert len(refusals) == 1
        assert sub["id"] in refusals[0].getMessage()
        assert "secret-token" not in caplog.text
        assert "wpush" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_bad_stored_url_is_deleted(self, test_db, monkeypatch):
        sub = await PushSubscriptionRepository.create(
            endpoint="file:///etc/passwd", p256dh="p", auth="a"
        )
        assert await _dispatch(monkeypatch) == []
        assert await PushSubscriptionRepository.get(sub["id"]) is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("answers", "code"),
        [(["192.168.1.20"], "egress_private_address"), ([], "egress_unresolvable")],
    )
    async def test_a_liftable_refusal_keeps_the_subscription_and_logs_once(
        self, test_db, monkeypatch, caplog, answers, code
    ):
        FakeResolver(monkeypatch, answers)
        sub = await PushSubscriptionRepository.create(
            endpoint=f"https://{PUSH_HOST}/{SECRET_PATH}", p256dh="p", auth="a"
        )
        with caplog.at_level(logging.DEBUG):
            assert await _dispatch(monkeypatch) == []
            assert await _dispatch(monkeypatch) == []
        stored = await PushSubscriptionRepository.get(sub["id"])
        assert stored is not None
        assert stored["failure_count"] == 0
        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "refused by the egress guard" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert code in warnings[0].getMessage()
        assert "secret-token" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_send_error_logs_the_type_only(self, test_db, monkeypatch, caplog):
        FakeResolver(monkeypatch, ["93.184.215.14"])
        sub = await PushSubscriptionRepository.create(
            endpoint=f"https://{PUSH_HOST}/{SECRET_PATH}", p256dh="p", auth="a"
        )
        monkeypatch.setattr("app.push.manager.get_vapid_private_key", lambda: _vapid_private_key())

        def boom(**kw):
            raise ValueError(f"failed for {kw['subscription_info']['endpoint']}")

        monkeypatch.setattr(push_send, "webpush", boom)
        with caplog.at_level(logging.DEBUG):
            await push_manager.dispatch_event({"event": "oss_update", "title": "t", "body": "b"})
        assert "ValueError" in caplog.text
        assert "secret-token" not in caplog.text
        assert await PushSubscriptionRepository.get(sub["id"]) is not None
