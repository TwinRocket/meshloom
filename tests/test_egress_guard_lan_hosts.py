"""Declared LAN hosts in the egress guard (`lan_hosts`, MSHL-282).

A declared device may be reached over plain HTTP, but only if every address its
name resolves to is a plain LAN address (10/8, 172.16/12, 192.168/16, 100.64/10,
fc00::/7) that is not the Meshloom host itself (MSHL-330): a name re-pointed at the
Meshloom machine, a link-local or metadata address, a transition form or the
Internet is refused (DNS rebinding).
"""

import errno
import ipaddress
import socket

import pytest

from app.net import egress_guard
from app.net.egress_guard import (
    EGRESS_MESSAGES,
    EgressBlocked,
    HostAllowlist,
    is_lan_address,
    is_self_address,
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
    # 100.64.0.0/10: a VPN such as Tailscale numbers the user's devices there (MSHL-331).
    "100.64.0.1",
    "100.127.255.254",
    "fd7a:115c:a1e0::1",  # Tailscale's IPv6 range
]
NOT_LAN = [
    "127.0.0.1",
    "127.8.9.10",
    "0.0.0.0",
    "169.254.10.20",
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


@pytest.fixture(autouse=True)
def not_this_machine(monkeypatch):
    """The LAN addresses of these tests are devices, not this machine (a CI runner may
    well own 10.x): the self-address probe is stubbed, except where a test says so."""
    monkeypatch.setattr(egress_guard, "_binds_locally", lambda ip: False)
    monkeypatch.setattr(egress_guard, "_in_container", lambda: False)


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
    assert "egress_self_address" in EGRESS_MESSAGES


# --- the Meshloom host itself (MSHL-330) ---------------------------------------------


@pytest.mark.parametrize("answers", [["192.168.1.10"], ["192.168.1.30", "192.168.1.10"]])
async def test_a_name_resolving_to_this_machine_is_refused(monkeypatch, answers):
    monkeypatch.setattr(egress_guard, "_binds_locally", lambda ip: str(ip) == "192.168.1.10")
    FakeDns(monkeypatch, answers)
    with pytest.raises(EgressBlocked) as info:
        await _check(f"http://{HOST}/")
    assert info.value.code == "egress_self_address"


async def test_a_declared_literal_of_this_machine_is_refused(monkeypatch):
    monkeypatch.setattr(egress_guard, "_binds_locally", lambda ip: str(ip) == "192.168.1.20")
    with pytest.raises(EgressBlocked) as info:
        await _check("http://192.168.1.20/")
    assert info.value.code == "egress_self_address"


async def test_in_a_container_the_default_gateway_is_the_host(monkeypatch, tmp_path):
    # Docker's bridge gateway, 172.17.0.1, as /proc/net/route writes it (little-endian).
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\n"
        "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\n"
    )
    route6 = tmp_path / "ipv6_route"
    route6.write_text(
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
        "fd000000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n"
    )
    gateways = egress_guard._default_gateways(str(route), str(route6))
    assert gateways == {ipaddress.ip_address("172.17.0.1"), ipaddress.ip_address("fd00::1")}
    monkeypatch.setattr(egress_guard, "_default_gateways", lambda: gateways)
    assert not is_self_address(ipaddress.ip_address("172.17.0.1"))  # not in a container
    monkeypatch.setattr(egress_guard, "_in_container", lambda: True)
    assert is_self_address(ipaddress.ip_address("172.17.0.1"))
    assert is_self_address(ipaddress.ip_address("fd00::1"))
    assert not is_self_address(ipaddress.ip_address("172.17.0.5"))
    FakeDns(monkeypatch, ["172.17.0.1"])
    with pytest.raises(EgressBlocked) as info:
        await _check(f"http://{HOST}/")
    assert info.value.code == "egress_self_address"


def test_unreadable_route_tables_give_no_gateway(tmp_path):
    assert (
        egress_guard._default_gateways(str(tmp_path / "missing"), str(tmp_path / "missing6"))
        == frozenset()
    )


def test_the_bind_probe_finds_this_machine(monkeypatch):
    monkeypatch.undo()  # the real probe
    assert egress_guard._binds_locally(ipaddress.ip_address("127.0.0.1"))
    # A documentation address is never assigned to an interface.
    assert not egress_guard._binds_locally(ipaddress.ip_address("192.0.2.1"))


def test_a_failing_bind_probe_refuses(monkeypatch):
    monkeypatch.undo()  # the real probe, on a socket that cannot bind

    class Sock:
        def __init__(self, *args):
            pass

        def bind(self, address):
            raise OSError(errno.EACCES, "denied")

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", Sock)
    assert egress_guard._binds_locally(ipaddress.ip_address("192.168.1.30"))
