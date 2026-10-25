"""Declared LAN hosts in the egress guard (`lan_hosts`, MSHL-282).

A declared device may be reached over plain HTTP, but only if every address its
name resolves to is a plain LAN address (10/8, 172.16/12, 192.168/16, fc00::/7):
a name re-pointed at the Meshloom machine, a link-local or metadata address, a
transition form or the Internet is refused (DNS rebinding).
"""

import ipaddress

import pytest

from app.net.egress_guard import (
    EGRESS_MESSAGES,
    EgressBlocked,
    HostAllowlist,
    is_lan_address,
    validate_destination,
)
from tests.test_egress_guard import DOCUMENTATION, HOST, METADATA, PUBLIC, FakeDns

LAN = HostAllowlist.of(HOST, "192.168.1.20", "[fd00::20]:8080")
PUBLIC_HOSTS = HostAllowlist.of("api.example.org")
HTTPS = frozenset({"https"})

LAN_ADDRESSES = [
    "10.0.0.1",
    "10.255.255.254",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.0.1",
    "192.168.255.254",
    "fc00::1",
    "fd12:3456::1",
]
NOT_LAN = [
    "127.0.0.1",
    "127.8.9.10",
    "0.0.0.0",
    "169.254.10.20",
    "100.64.0.1",
    "198.18.0.1",
    "224.0.0.251",
    "255.255.255.255",
    "172.32.0.1",
    "192.169.0.1",
    "::",
    "::1",
    "fe80::1",
    "fec0::1",
    "ff02::1",
    # An IPv4-mapped or transition form of a LAN address is not that address.
    "::ffff:192.168.1.10",
    "::ffff:10.0.0.1",
    "::ffff:127.0.0.1",
    "64:ff9b::c0a8:10a",
    "2002:c0a8:10a::1",
    "::c0a8:10a",
    *PUBLIC,
    *METADATA,
]


@pytest.mark.parametrize("ip", LAN_ADDRESSES)
def test_lan_addresses(ip):
    assert is_lan_address(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", NOT_LAN)
def test_other_addresses_are_not_lan(ip):
    assert not is_lan_address(ipaddress.ip_address(ip))


async def _check(url: str, **kwargs):
    return await validate_destination(
        url, schemes=HTTPS, allowed_hosts=PUBLIC_HOSTS, lan_hosts=LAN, **kwargs
    )


@pytest.mark.parametrize("ip", ["192.168.1.30", "10.0.0.7", "fd00::7"])
async def test_a_declared_name_resolving_to_a_lan_address_is_reached_over_http(monkeypatch, ip):
    FakeDns(monkeypatch, [ip])
    dest = await _check(f"http://{HOST}/status")
    assert dest.scheme == "http" and dest.port == 80
    assert dest.ips == (ipaddress.ip_address(ip),)
    dest = await _check(f"https://{HOST}/status")
    assert dest.port == 443


@pytest.mark.parametrize("ip", NOT_LAN)
async def test_a_declared_name_resolving_elsewhere_is_refused(monkeypatch, ip):
    FakeDns(monkeypatch, [ip])
    with pytest.raises(EgressBlocked) as info:
        await _check(f"http://{HOST}/")
    assert info.value.code in ("egress_lan_address", "egress_private_address")
    if ip in METADATA:
        assert info.value.code == "egress_private_address" and info.value.always_refused


@pytest.mark.parametrize("ip", DOCUMENTATION)
async def test_a_declared_name_resolving_to_documentation_is_always_refused(monkeypatch, ip):
    FakeDns(monkeypatch, [ip])
    with pytest.raises(EgressBlocked) as info:
        await _check(f"http://{HOST}/")
    assert info.value.code == "egress_private_address" and info.value.always_refused


async def test_one_answer_outside_the_lan_taints_the_name(monkeypatch):
    FakeDns(monkeypatch, ["192.168.1.30", "127.0.0.1"])
    with pytest.raises(EgressBlocked) as info:
        await _check(f"http://{HOST}/")
    assert info.value.code == "egress_lan_address"
    FakeDns(monkeypatch, ["fd00::30", "2606:4700:4700::1111"])
    with pytest.raises(EgressBlocked) as info:
        await _check(f"http://{HOST}/")
    assert info.value.code == "egress_lan_address"


async def test_a_declared_lan_literal_needs_no_lookup(monkeypatch):
    dns = FakeDns(monkeypatch, [])
    dest = await _check("http://192.168.1.20/")
    assert dest.ips == (ipaddress.ip_address("192.168.1.20"),)
    dest = await _check("http://[fd00::20]:8080/")
    assert dest.port == 8080
    assert dns.lookups == []


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.21/",  # another device of the same network
        "http://10.0.0.1/",
        "http://192.168.1.20:8080/",  # declared without a port: the default port only
        "http://[fd00::20]/",  # declared with a port: that port only
        "http://[fd00::21]:8080/",
        "http://[::ffff:192.168.1.20]/",  # the mapped spelling of the declared literal
        "http://other.example.test/",
        "http://127.0.0.1/",
        "http://localhost/",
    ],
)
async def test_an_undeclared_lan_destination_is_refused_before_any_lookup(monkeypatch, url):
    dns = FakeDns(monkeypatch, ["192.168.1.30"])
    with pytest.raises(EgressBlocked) as info:
        await _check(url)
    assert info.value.code in ("egress_host_not_allowed", "egress_scheme")
    assert dns.lookups == []


async def test_plain_http_stays_refused_for_a_public_host(monkeypatch):
    with pytest.raises(EgressBlocked) as info:
        await _check("http://api.example.org/")
    assert info.value.code == "egress_scheme"


async def test_without_lan_hosts_nothing_changes(monkeypatch):
    FakeDns(monkeypatch, ["192.168.1.30"])
    with pytest.raises(EgressBlocked) as info:
        await validate_destination(f"http://{HOST}/", schemes=HTTPS, allowed_hosts=LAN)
    assert info.value.code == "egress_scheme"
    with pytest.raises(EgressBlocked) as info:
        await validate_destination(f"https://{HOST}/", schemes=HTTPS, allowed_hosts=LAN)
    assert info.value.code == "egress_private_address"


def test_the_new_code_has_a_message():
    assert "egress_lan_address" in EGRESS_MESSAGES
