"""Custom domain test of Meshloom Remote (MSHL-428): a personal domain that URL-redirects
to ``<slug>.meshloom.link``.

The probe goes to the user's domain through the egress guard (DNS table and
``httpx.AsyncClient`` recorder of ``tests/share_fakes.py``): HTTPS then HTTP, no redirect
followed, private addresses refused, nothing sent to the relay.
"""

from __future__ import annotations

import httpx
import pytest

from app.repository.remote import RemoteRepository
from app.services import remote
from tests.share_fakes import PUBLIC_IP, Dns, Recorder

HOSTNAME = "k3xwq2m7ta.meshloom.link"
DOMAIN = "chat.example.org"


@pytest.fixture
async def registered(test_db):
    await RemoteRepository.update_state(enabled=True, slug="k3xwq2m7ta", hostname=HOSTNAME)


def _redirect(location: str, status: int = 301):
    return lambda request: httpx.Response(status, headers={"Location": location})


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("chat.example.org", "chat.example.org"),
        (" HTTPS://Chat.Example.org/ ", "chat.example.org"),
        ("bücher.example", "xn--bcher-kva.example"),
        ("example", None),
        ("1.2.3.4", None),
        ("chat.example.org:8080", None),
        ("chat.example.org/path", None),
        ("a b.example", None),
        ("-bad.example", None),
    ],
)
def test_normalize_domain(typed, expected):
    assert remote.normalize_domain(typed) == expected


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_ok(registered, monkeypatch, status):
    Dns(monkeypatch, {DOMAIN: [PUBLIC_IP]})
    rec = Recorder(monkeypatch, _redirect(f"https://{HOSTNAME}/some/path", status))
    check = await remote.check_custom_domain(DOMAIN)
    assert (check.result, check.target_host, check.status_code) == ("ok", HOSTNAME, status)
    assert rec.hosts() == [DOMAIN]  # one request, the relay never reached, redirect not followed
    assert rec.requests[0].url.scheme == "https"


async def test_no_redirect(registered, monkeypatch):
    Dns(monkeypatch, {DOMAIN: [PUBLIC_IP]})
    Recorder(monkeypatch, lambda r: httpx.Response(200, content=b"parked"))
    check = await remote.check_custom_domain(DOMAIN)
    assert (check.result, check.status_code) == ("no_redirect", 200)


async def test_redirect_without_location_is_no_redirect(registered, monkeypatch):
    Dns(monkeypatch, {DOMAIN: [PUBLIC_IP]})
    Recorder(monkeypatch, lambda r: httpx.Response(301))
    assert (await remote.check_custom_domain(DOMAIN)).result == "no_redirect"


@pytest.mark.parametrize(
    "location",
    [
        "https://other.meshloom.link/",
        "https://example.com/",
        f"https://{HOSTNAME}.evil.example/",
        "/relative",
    ],
)
async def test_wrong_target(registered, monkeypatch, location):
    Dns(monkeypatch, {DOMAIN: [PUBLIC_IP]})
    Recorder(monkeypatch, _redirect(location))
    check = await remote.check_custom_domain(DOMAIN)
    assert check.result == "wrong_target"


async def test_https_failure_falls_back_to_http(registered, monkeypatch):
    Dns(monkeypatch, {DOMAIN: [PUBLIC_IP]})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            raise httpx.ConnectError("refused")
        return httpx.Response(302, headers={"Location": f"https://{HOSTNAME}/"})

    rec = Recorder(monkeypatch, handler)
    assert (await remote.check_custom_domain(DOMAIN)).result == "ok"
    assert [r.url.scheme for r in rec.requests] == ["https", "http"]


async def test_unreachable(registered, monkeypatch):
    Dns(monkeypatch, {DOMAIN: [PUBLIC_IP]})

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    Recorder(monkeypatch, handler)
    check = await remote.check_custom_domain(DOMAIN)
    assert (check.result, check.detail) == ("unreachable", "connect_error")


async def test_unresolvable(registered, monkeypatch):
    Dns(monkeypatch, {})
    rec = Recorder(monkeypatch)
    check = await remote.check_custom_domain(DOMAIN)
    assert (check.result, check.detail) == ("unreachable", "egress_unresolvable")
    assert rec.requests == []


@pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.1.10", "169.254.169.254"])
async def test_private_addresses_are_refused(registered, monkeypatch, ip):
    Dns(monkeypatch, {DOMAIN: [ip]})
    rec = Recorder(monkeypatch)
    check = await remote.check_custom_domain(DOMAIN)
    assert check.result == "unreachable" and check.detail == "egress_private_address"
    assert rec.requests == []


async def test_invalid_domain_sends_nothing(registered, monkeypatch):
    rec = Recorder(monkeypatch)
    assert (await remote.check_custom_domain("10.0.0.1")).result == "invalid_domain"
    assert rec.requests == []


async def test_needs_remote_on_and_registered(test_db, monkeypatch):
    rec = Recorder(monkeypatch)
    with pytest.raises(remote.RemoteError) as exc:
        await remote.check_custom_domain(DOMAIN)
    assert exc.value.code == "remote_disabled"
    await RemoteRepository.update_state(enabled=True)
    with pytest.raises(remote.RemoteError) as exc:
        await remote.check_custom_domain(DOMAIN)
    assert exc.value.code == "remote_not_registered"
    assert rec.requests == []
