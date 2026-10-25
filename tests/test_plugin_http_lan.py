"""`http.fetch` to declared LAN hosts (`http.lan_hosts`, MSHL-282): bypass attempts.

DNS is a table (IPv4 and IPv6 answers) and httpx a recording `MockTransport`, as in
`tests/test_plugin_http.py`; "nothing is sent" is checked on the transport.
"""

import ipaddress
import socket
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.plugins import capabilities
from app.plugins.capabilities.base import CapabilityError
from app.plugins.capabilities.grants import effective_permissions
from app.plugins.installer import consent_missing, grant_problems

# The recording transport and the per-test quota clock of the capability tests.
from tests.test_plugin_http import Transport, clock  # noqa: F401

DNS = {
    "api.example.org": ["93.184.215.14"],
    "printer.local": ["192.168.1.20"],
    "nas": ["fd00::30"],
    "rebind-loopback.local": ["127.0.0.1"],
    "rebind-link-local.local": ["169.254.10.1"],
    "rebind-metadata.local": ["169.254.169.254"],
    "rebind-v6-link-local.local": ["fe80::1"],
    "rebind-mapped.local": ["::ffff:127.0.0.1"],
    "rebind-mapped-lan.local": ["::ffff:192.168.1.20"],
    "rebind-cgnat.local": ["100.64.0.1"],
    "rebind-public.local": ["93.184.215.14"],
    "rebind-mixed.local": ["192.168.1.21", "127.0.0.1"],
    "other.local": ["192.168.1.21"],
}
LAN_HOSTS = [
    "printer.local",
    "nas",
    "192.168.1.40:8080",
    "[fd00::40]:8443",
    *(h for h in DNS if h.startswith("rebind-")),
]


class Dns:
    def __init__(self, monkeypatch) -> None:
        self.lookups: list[str] = []
        monkeypatch.setattr(socket, "getaddrinfo", self)

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.lookups.append(host)
        if host not in DNS:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        out = []
        for ip in DNS[host]:
            if ipaddress.ip_address(ip).version == 4:
                out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
            else:
                out.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0)))
        return out


@pytest.fixture
def dns(monkeypatch):
    return Dns(monkeypatch)


@pytest.fixture
def net(monkeypatch, dns):
    return Transport(monkeypatch)


def instance(
    lan_hosts: list[str] | None = None, hosts: list[str] | None = None, plugin_id="acme.lan"
):
    http: dict[str, Any] = {"lan_hosts": LAN_HOSTS if lan_hosts is None else lan_hosts}
    http["hosts"] = ["api.example.org"] if hosts is None else hosts
    return SimpleNamespace(
        instance_id=f"{plugin_id}#1", plugin_id=plugin_id, permissions={"http": http}
    )


async def fetch(params: dict[str, Any], inst=None) -> dict[str, Any]:
    return await capabilities.dispatch(inst or instance(), "http.fetch", params, None)


async def refused(params: dict[str, Any], inst=None) -> CapabilityError:
    with pytest.raises(CapabilityError) as info:
        await fetch(params, inst)
    return info.value


async def test_plain_http_reaches_a_declared_lan_device_on_its_pinned_ip(net, dns):
    answer = await fetch({"url": "http://printer.local/status"})
    assert answer["status"] == 200
    [request] = net.requests
    assert request.url.host == "192.168.1.20" and request.url.scheme == "http"
    assert request.headers["host"] == "printer.local"
    assert (await fetch({"url": "https://printer.local/status"}))["status"] == 200


async def test_ipv6_lan_devices_by_name_and_literal_with_port(net, dns):
    assert (await fetch({"url": "http://nas/"}))["status"] == 200
    assert (await fetch({"url": "https://[fd00::40]:8443/"}))["status"] == 200
    assert (await fetch({"url": "http://192.168.1.40:8080/"}))["status"] == 200
    assert [r.url.host for r in net.requests] == ["fd00::30", "fd00::40", "192.168.1.40"]


@pytest.mark.parametrize("host", [h for h in DNS if h.startswith("rebind-")])
async def test_a_lan_name_resolving_outside_the_lan_is_refused(net, dns, host):
    err = await refused({"url": f"http://{host}/"})
    assert err.refusal == "host_not_allowed"
    assert net.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://other.local/",  # an undeclared device of the same network
        "http://192.168.1.21/",
        "http://192.168.1.40/",  # declared with :8080 only
        "http://[fd00::40]/",
        "http://[::ffff:192.168.1.40]:8080/",
        "http://printer.local:8080/",  # declared without a port
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_an_undeclared_lan_destination_is_refused_before_any_lookup(net, dns, url):
    err = await refused({"url": url})
    assert err.refusal == "host_not_allowed"
    assert dns.lookups == [] and net.requests == []


async def test_plain_http_to_a_public_host_is_still_refused(net, dns):
    err = await refused({"url": "http://api.example.org/"})
    assert err.refusal == "https_required"
    assert dns.lookups == [] and net.requests == []


@pytest.mark.parametrize(
    "location",
    [
        "http://other.local/",
        "http://192.168.1.21/",
        "http://127.0.0.1/",
        "http://[fd00::41]:8443/",
        "http://printer.local:8080/",
    ],
)
async def test_a_redirect_to_an_undeclared_lan_host_is_refused(net, dns, location):
    net.handler = lambda r: httpx.Response(302, headers={"location": location})
    err = await refused({"url": "http://printer.local/"})
    assert err.refusal == "host_not_allowed"
    assert len(net.requests) == 1


async def test_a_redirect_from_a_lan_device_to_plain_http_on_the_internet_is_refused(net, dns):
    net.handler = lambda r: httpx.Response(302, headers={"location": "http://api.example.org/"})
    err = await refused({"url": "http://printer.local/"})
    assert err.refusal == "https_required"
    assert len(net.requests) == 1


async def test_a_redirect_to_a_lan_name_that_rebinds_is_refused(net, dns):
    net.handler = lambda r: httpx.Response(
        302, headers={"location": "http://rebind-loopback.local/"}
    )
    err = await refused({"url": "http://printer.local/"})
    assert err.refusal == "host_not_allowed"
    assert len(net.requests) == 1


async def test_a_redirect_between_declared_hosts_is_followed(net, dns):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "printer.local":
            return httpx.Response(302, headers={"location": "https://api.example.org/done"})
        return httpx.Response(200, text="done")

    net.handler = handler
    assert (await fetch({"url": "http://printer.local/"}))["body"] == "done"


async def test_meshloom_domains_in_lan_hosts_are_ignored_for_a_third_party(net, dns):
    inst = instance(lan_hosts=["api.meshloom.app"], hosts=[])
    err = await refused({"url": "http://api.meshloom.app/"}, inst)
    assert err.refusal == "host_not_allowed"
    assert dns.lookups == []


# --- consent ------------------------------------------------------------------------

MANIFEST = {
    "permissions": {
        "http": {"hosts": ["api.example.org"], "lan_hosts": ["printer.local", "nas:8080"]}
    }
}


def test_a_lan_host_is_reached_only_once_granted():
    granted = {"http": {"hosts": ["api.example.org"]}}
    assert effective_permissions(MANIFEST, granted) == {"http": {"hosts": ["api.example.org"]}}
    assert consent_missing(MANIFEST, granted) == ["http"]

    granted = {"http": {"hosts": ["api.example.org"], "lan_hosts": ["printer.local", "nas:8080"]}}
    assert effective_permissions(MANIFEST, granted) == MANIFEST["permissions"]
    assert consent_missing(MANIFEST, granted) == []
    assert grant_problems(MANIFEST, granted) == []


def test_a_grant_cannot_add_an_undeclared_lan_host():
    granted = {"http": {"lan_hosts": ["printer.local", "router.lan"]}}
    assert effective_permissions(MANIFEST, granted) == {"http": {"lan_hosts": ["printer.local"]}}
    assert grant_problems(MANIFEST, granted) == ["http"]


def test_a_grant_cannot_move_a_lan_host_to_the_public_list():
    granted = {"http": {"hosts": ["printer.local"]}}
    assert effective_permissions(MANIFEST, granted) == {}


def test_a_grant_written_before_lan_hosts_still_matches():
    manifest = {"permissions": {"http": {"hosts": ["api.example.org"]}}}
    granted = {"http": {"hosts": ["api.example.org"]}}
    assert consent_missing(manifest, granted) == []
    assert grant_problems(manifest, granted) == []
