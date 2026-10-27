"""What counts as a local client address (MSHL-431): ``app/net/local_address.py``."""

from __future__ import annotations

import ipaddress

import pytest

from app.net import local_address

LOCAL = [
    # IPv4
    ("127.0.0.1", "loopback"),
    ("127.255.255.254", "loopback, top of the /8"),
    ("10.0.0.1", "RFC 1918 10/8"),
    ("10.255.255.255", "RFC 1918 10/8, top"),
    ("172.16.0.1", "RFC 1918 172.16/12"),
    ("172.31.255.254", "RFC 1918 172.16/12, top"),
    ("192.168.1.10", "RFC 1918 192.168/16"),
    ("169.254.1.1", "link-local"),
    ("100.64.0.1", "CGNAT, bottom (MSHL-434)"),
    ("100.127.255.254", "CGNAT, top"),
    # IPv6
    ("::1", "loopback"),
    ("fd00::5", "ULA fd00::/8"),
    ("fc00::1", "ULA fc00::/8"),
    ("fd7a:115c:a1e0::1", "Tailscale IPv6 (MSHL-434)"),
    ("fe80::1", "link-local"),
    ("febf::1", "link-local, top of fe80::/10"),
    # Spellings
    ("::ffff:192.168.1.30", "IPv4-mapped RFC 1918"),
    ("::ffff:127.0.0.1", "IPv4-mapped loopback"),
    ("fe80::1%eth0", "zone id dropped on plain IPv6"),
]

PUBLIC = [
    # IPv4
    ("8.8.8.8", "global"),
    ("172.15.255.255", "just below 172.16/12"),
    ("172.32.0.0", "just above 172.16/12"),
    ("100.63.255.255", "just below CGNAT"),
    ("100.128.0.0", "just above CGNAT"),
    ("192.0.2.10", "documentation TEST-NET-1"),
    ("198.51.100.20", "documentation TEST-NET-2"),
    ("203.0.113.7", "documentation TEST-NET-3"),
    ("198.18.0.1", "benchmarking"),
    ("192.0.0.8", "IETF protocol assignments"),
    ("0.0.0.0", "unspecified"),
    ("0.1.2.3", "this network 0/8"),
    ("240.0.0.1", "reserved 240/4"),
    ("255.255.255.255", "broadcast"),
    ("224.0.0.1", "multicast"),
    # IPv6
    ("2606:4700::1111", "global"),
    ("2001:db8::1", "documentation"),
    ("3fff::1", "documentation 3fff::/20"),
    ("2001:2::1", "benchmarking"),
    ("::", "unspecified"),
    ("ff02::1", "multicast"),
    ("64:ff9b::a00:1", "NAT64 of 10.0.0.1"),
    ("2002:c0a8:101::1", "6to4 of 192.168.1.1"),
    ("2001::1", "Teredo"),
    ("fec0::1", "deprecated site-local"),
    ("fec0::1%eth0", "deprecated site-local with a zone id"),
    ("::192.168.1.1", "IPv4-compatible"),
    ("::ffff:8.8.8.8", "IPv4-mapped global"),
    ("::ffff:192.0.2.1", "IPv4-mapped documentation"),
    # Not an address: fail closed.
    (None, "absent"),
    ("", "empty"),
    ("localhost", "a name"),
    ("192.168.1", "truncated"),
    ("192.168.1.10/24", "a network"),
    ("%eth0", "a zone id alone"),
    # Never local by trimming (a trusted proxy's X-Forwarded-For is verbatim).
    ("127.0.0.1%x", "zone id on IPv4"),
    ("10.0.0.1%eth0", "zone id on IPv4"),
    (" 10.0.0.1", "leading space"),
    ("10.0.0.1\n", "trailing newline"),
    ("::ffff:127.0.0.1%x", "zone id on IPv4-mapped"),
    ("fe80::1%", "empty zone id"),
]


@pytest.mark.parametrize(("raw", "label"), LOCAL)
def test_local_addresses(raw, label):
    assert local_address.is_local(raw), label
    assert not local_address.is_public(raw), label


@pytest.mark.parametrize(("raw", "label"), PUBLIC)
def test_public_addresses(raw, label):
    assert local_address.is_public(raw), label
    assert not local_address.is_local(raw), label


@pytest.mark.parametrize("raw", ["192.168.1.10", "fd00::5", "::ffff:10.0.0.1", "fe80::1%eth0"])
def test_address_objects_are_judged_like_text(raw):
    parsed = local_address.parse_address(raw)
    assert parsed is not None
    assert local_address.is_local(parsed)
    assert local_address.is_local(ipaddress.ip_address(str(parsed)))


def test_mapped_address_objects_are_unmapped():
    assert local_address.is_local(ipaddress.IPv6Address("::ffff:192.168.1.30"))
    assert local_address.is_public(ipaddress.IPv6Address("::ffff:8.8.8.8"))


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [
        ("::ffff:192.168.1.30", "192.168.1.30"),
        ("fe80::1%eth0", "fe80::1"),
        ("fe80::1%x\ny", "fe80::1"),
        ("127.0.0.1%x", None),
        ("::ffff:127.0.0.1%x", None),
        (" 10.0.0.1", None),
        ("2001:DB8::1", "2001:db8::1"),
        (None, None),
        ("", None),
        ("nope", None),
    ],
)
def test_parse_address(raw, parsed):
    result = local_address.parse_address(raw)
    assert (str(result) if result is not None else None) == parsed
    if result is not None:
        assert result.version == ipaddress.ip_address(parsed).version


@pytest.mark.parametrize(
    ("cidr", "local"),
    [
        ("192.168.1.0/24", True),
        ("10.0.0.0/8", True),
        ("172.16.0.0/12", True),
        ("172.0.0.0/8", False),
        ("100.64.0.0/10", True),
        ("100.0.0.0/8", False),
        ("127.0.0.1/32", True),
        ("169.254.0.0/16", True),
        ("192.0.2.0/24", False),
        ("0.0.0.0/0", False),
        ("fd00:1234::/48", True),
        ("fc00::/7", True),
        ("fe80::/64", True),
        ("::1/128", True),
        ("2001:db8::/32", False),
        ("::/0", False),
        ("::ffff:0:0/96", False),
    ],
)
def test_network_is_local(cidr, local):
    assert local_address.network_is_local(ipaddress.ip_network(cidr)) is local


def test_every_range_class_is_listed_once():
    assert [str(network) for network in local_address.LOCAL_NETWORKS] == [
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    ]
