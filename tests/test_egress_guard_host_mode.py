"""Host-mode detection of the egress guard (`_host_networking`, `_gateway_is_host`, MSHL-340).

Inside a container the default gateway is the engine's host (radio proxy on 5051,
no authentication) unless the container shares the host's network namespace, where
it is the LAN router. Every case below is built from fake `/proc` and `/sys` files;
whatever cannot be read or does not match keeps the gateway refused (fail closed).
"""

import ipaddress
from pathlib import Path

import pytest

from app.net import egress_guard

DEV_HEADER = (
    "Inter-|   Receive                            |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes ...\n"
)
ROUTE_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
ROUTER = ipaddress.ip_address("192.168.1.1")
BRIDGE_GATEWAY = ipaddress.ip_address("172.17.0.1")
HASSIO_GATEWAY = ipaddress.ip_address("172.30.32.1")


def _hex_le(ip) -> str:
    return ip.packed[::-1].hex().upper()


class Box:
    """A fake container: `/proc/net/dev`, `/proc/net/route`, `/proc/net/ipv6_route`, `/sys/class/net`."""

    def __init__(self, root: Path):
        self.dev = root / "dev"
        self.route = root / "route"
        self.route6 = root / "ipv6_route"
        self.sys_net = root / "sys"
        self.sys_net.mkdir()
        self.route6.write_text("")

    def interfaces(self, *names: str) -> "Box":
        lines = "".join(f"{n:>6}: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n" for n in names)
        self.dev.write_text(DEV_HEADER + lines)
        return self

    def link(self, name: str, ifindex: int, iflink: int) -> "Box":
        (self.sys_net / name).mkdir()
        (self.sys_net / name / "ifindex").write_text(f"{ifindex}\n")
        (self.sys_net / name / "iflink").write_text(f"{iflink}\n")
        return self

    def default_via(self, iface: str, gateway) -> "Box":
        self.route.write_text(
            ROUTE_HEADER + f"{iface}\t00000000\t{_hex_le(gateway)}\t0003\t0\t0\t0\t00000000\n"
        )
        return self

    def host_networking(self) -> bool:
        return egress_guard._host_networking(
            str(self.dev), str(self.sys_net), str(self.route), str(self.route6)
        )

    def patch(self, monkeypatch) -> None:
        """Make the guard read this box (its callers use the default paths)."""
        host_networking = self.host_networking()
        routes = egress_guard._default_routes(str(self.route), str(self.route6))
        monkeypatch.setattr(egress_guard, "_default_routes", lambda *a, **k: routes)
        monkeypatch.setattr(egress_guard, "_host_networking", lambda: host_networking)


@pytest.fixture
def box(tmp_path) -> Box:
    return Box(tmp_path)


# --- _host_networking --------------------------------------------------------------


def test_host_network_namespace_is_recognised(box):
    box.interfaces("lo", "enp1s0", "docker0", "veth1a2b3c").link("enp1s0", 2, 2)
    box.default_via("enp1s0", ROUTER)
    assert box.host_networking()


@pytest.mark.parametrize("bridge", ["hassio", "br-0123abcd", "cni-podman0", "podman0"])
def test_every_engine_bridge_counts(box, bridge):
    box.interfaces("lo", "eth0", bridge).link("eth0", 2, 2).default_via("eth0", ROUTER)
    assert box.host_networking()


def test_a_bridged_container_is_not_host_networking(box):
    box.interfaces("lo", "eth0").link("eth0", 15, 16).default_via("eth0", BRIDGE_GATEWAY)
    assert not box.host_networking()


def test_docker_in_docker_is_not_host_networking(box):
    # A bridged container running its own engine lists docker0 and veth ends, but its
    # default route leaves through its veth eth0, whose peer is on the real host.
    box.interfaces("lo", "eth0", "docker0", "veth9f8e7d").link("eth0", 23, 24)
    box.default_via("eth0", BRIDGE_GATEWAY)
    assert not box.host_networking()


def test_unreadable_sysfs_fails_closed(box):
    box.interfaces("lo", "enp1s0", "docker0").default_via("enp1s0", ROUTER)
    assert not box.host_networking()


def test_unreadable_dev_fails_closed(box):
    box.link("enp1s0", 2, 2).default_via("enp1s0", ROUTER)
    assert not box.host_networking()


def test_no_default_route_is_not_host_networking(box):
    box.interfaces("lo", "enp1s0", "docker0").link("enp1s0", 2, 2)
    assert not box.host_networking()


def test_ipv6_default_route_interface_is_checked_too(box):
    box.interfaces("lo", "enp1s0", "docker0", "eth1").link("enp1s0", 2, 2).link("eth1", 7, 8)
    box.default_via("enp1s0", ROUTER)
    box.route6.write_text(
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
        "fe800000000000000000000000000001 00000400 00000001 00000000 00000003     eth1\n"
    )
    assert not box.host_networking()


@pytest.mark.parametrize("iface", ["", "..", "a/b"])
def test_odd_interface_names_are_not_own_links(box, iface):
    assert not egress_guard._own_link(iface, str(box.sys_net))


# --- _gateway_is_host --------------------------------------------------------------


def test_bridged_gateway_is_the_host(box, monkeypatch):
    box.interfaces("lo", "eth0").link("eth0", 15, 16).default_via("eth0", BRIDGE_GATEWAY)
    box.patch(monkeypatch)
    assert egress_guard._gateway_is_host(BRIDGE_GATEWAY)
    assert not egress_guard._gateway_is_host(ipaddress.ip_address("172.17.0.5"))


def test_docker_in_docker_gateway_stays_the_host(box, monkeypatch):
    box.interfaces("lo", "eth0", "docker0").link("eth0", 23, 24)
    box.default_via("eth0", BRIDGE_GATEWAY)
    box.patch(monkeypatch)
    assert egress_guard._gateway_is_host(BRIDGE_GATEWAY)


def test_host_networking_gateway_is_the_lan_router(box, monkeypatch):
    box.interfaces("lo", "enp1s0", "docker0").link("enp1s0", 2, 2).default_via("enp1s0", ROUTER)
    box.patch(monkeypatch)
    assert not egress_guard._gateway_is_host(ROUTER)


def test_home_assistant_supervisor_gateway_stays_refused_with_host_networking(box, monkeypatch):
    box.interfaces("lo", "hassio", "enp1s0").link("enp1s0", 2, 2)
    box.default_via("enp1s0", HASSIO_GATEWAY)
    box.patch(monkeypatch)
    assert egress_guard._gateway_is_host(HASSIO_GATEWAY)


def test_is_self_address_refuses_the_docker_in_docker_gateway(box, monkeypatch):
    box.interfaces("lo", "eth0", "docker0").link("eth0", 23, 24)
    box.default_via("eth0", BRIDGE_GATEWAY)
    box.patch(monkeypatch)
    monkeypatch.setattr(egress_guard, "_in_container", lambda: True)
    monkeypatch.setattr(egress_guard, "_binds_locally", lambda ip: False)
    assert egress_guard.is_self_address(BRIDGE_GATEWAY)
