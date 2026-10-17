"""Offline checks of the radio bench helpers (the bench itself is opt-in)."""

import httpx
import pytest

from tests.radio_bench import bench


def test_load_config_needs_the_url(monkeypatch):
    monkeypatch.delenv(bench.ENV_URL, raising=False)
    monkeypatch.delenv(bench.ENV_PROXY, raising=False)
    assert bench.load_config() is None

    monkeypatch.setenv(bench.ENV_URL, "http://bench.test:8010/api/")
    config = bench.load_config()
    assert config == bench.BenchConfig(
        base_url="http://bench.test:8010", channel="#meshloom-next-test"
    )
    assert config.ws_url == "ws://bench.test:8010/api/ws"

    monkeypatch.setenv(bench.ENV_PROXY, "bench.test:5011")
    config = bench.load_config()
    assert config is not None
    assert (config.proxy_host, config.proxy_port) == ("bench.test", 5011)

    monkeypatch.setenv(bench.ENV_PROXY, "bench.test")
    with pytest.raises(ValueError):
        bench.load_config()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/radio/private-key"),
        ("PUT", "/api/radio/private-key"),
        ("POST", "/api/radio/factory-reset"),
        ("POST", "/api/radio/identity/adopt"),
        ("POST", "/api/settings/backup/export"),
        ("POST", "/api/radio/reboot"),
        ("PUT", "/api/radio/transport"),
        ("PATCH", "/api/radio/proxy"),
        ("PUT", "/api/radio/custom-vars/gps"),
    ],
)
def test_client_refuses_identity_and_destructive_routes(method, path):
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))
    config = bench.BenchConfig(base_url="http://bench.test", channel="#t")
    with bench.api_client(config, transport) as client:
        with pytest.raises(RuntimeError, match="refuses"):
            client.request(method, path.removeprefix("/api"))
    assert calls == []


def test_client_allows_reads_of_transport_and_proxy():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    config = bench.BenchConfig(base_url="http://bench.test", channel="#t")
    with bench.api_client(config, transport) as client:
        assert client.get("/radio/transport").status_code == 200
        assert client.get("/radio/proxy").status_code == 200


def test_snapshot_diff_names_paths_and_tolerates_gps_jitter():
    before = {
        "radio": {"lat": 43.758743, "lon": 7.180228, "tx_power": 22},
        "app": {"flood_scope": ""},
        "channels": [{"key": "A"}],
    }
    jitter = {
        "radio": {"lat": 43.758767, "lon": 7.18023, "tx_power": 22},
        "app": {"flood_scope": ""},
        "channels": [{"key": "A"}],
    }
    assert bench.snapshot_diff(before, jitter) == []

    moved = {
        "radio": {"lat": 44.0, "lon": 7.180228, "tx_power": 20},
        "app": {"flood_scope": "#x"},
        "channels": [],
    }
    assert bench.snapshot_diff(before, moved) == [
        "app.flood_scope",
        "channels",
        "radio.lat",
        "radio.tx_power",
    ]


def test_restore_radio_fields_patches_only_what_differs():
    original = {"multi_acks_enabled": True, "telemetry_mode_env": 0, "tx_power": 22}
    current = {"multi_acks_enabled": False, "telemetry_mode_env": 0, "tx_power": 10}
    patches = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            patches.append(request.read())
            return httpx.Response(200, json={})
        return httpx.Response(200, json={**current, "public_key": "ab" * 32})

    config = bench.BenchConfig(base_url="http://bench.test", channel="#t")
    with bench.api_client(config, httpx.MockTransport(handler)) as client:
        assert "public_key" not in bench.radio_config(client)
        bench.restore_radio_fields(client, original)
    # tx_power is not a field the bench changes: it is reported, never written.
    assert patches == [b'{"multi_acks_enabled":true}']
