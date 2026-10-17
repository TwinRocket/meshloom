"""Reconnection: the radio link drops and recovers, clients drop and come back."""

import asyncio
import time

import httpx
import pytest
from meshcore import EventType, MeshCore

from tests.radio_bench import bench

pytestmark = pytest.mark.radio


async def _health(aapi: httpx.AsyncClient) -> dict:
    response = await aapi.get("/health")
    response.raise_for_status()
    return response.json()


async def _drop_and_recover(aapi: httpx.AsyncClient, ws: bench.WsRecorder) -> None:
    """POST /radio/disconnect, then /radio/reconnect until the radio is ready again."""
    started = time.time()
    response = await aapi.post("/radio/disconnect")
    response.raise_for_status()
    assert response.json()["connected"] is False
    down = await bench.async_wait_until(
        lambda: _health_is(aapi, connected=False), bench.HEALTH_TIMEOUT
    )
    assert down, "health still says connected after the disconnect"
    assert await ws.wait_for(
        lambda e: e.get("type") == "health" and e["data"].get("radio_connected") is False, 10
    ), "no disconnected health event on the WebSocket"

    async def reconnect() -> bool:
        reply = await aapi.post("/radio/reconnect")
        # 423 while the radio still holds the previous TCP session: try again.
        return reply.status_code == 200 and bench.radio_ready(await _health(aapi))

    assert await bench.async_wait_until(reconnect, bench.HEALTH_TIMEOUT, interval=5.0), (
        "the radio did not come back"
    )
    assert await ws.wait_for(
        lambda e: e.get("type") == "health" and e["data"].get("radio_connected") is True, 30
    ), "no connected health event on the WebSocket"
    print(f"radio link recovered in {time.time() - started:.1f} s")


async def _health_is(aapi: httpx.AsyncClient, *, connected: bool) -> bool:
    return bool((await _health(aapi)).get("radio_connected")) is connected


async def test_radio_link_drop_and_recovery(
    bench_config: bench.BenchConfig,
    aapi: httpx.AsyncClient,
    radio_link: httpx.Client,
    airtime: bench.AirTime,
    channel_key: str,
    run_id: str,
) -> None:
    async with bench.ws_recorder(bench_config) as ws:
        await _drop_and_recover(aapi, ws)

    # The instance sends again once setup is complete.
    token = f"bench {run_id} relink"
    await airtime.wait_turn()
    response = await aapi.post(
        "/messages/channel", json={"channel_key": channel_key, "text": token}
    )
    response.raise_for_status()
    assert [r["id"] for r in await bench.channel_rows(aapi, channel_key, token)] == [
        response.json()["id"]
    ]


async def test_proxy_session_survives_radio_link_drop(
    bench_config: bench.BenchConfig,
    aapi: httpx.AsyncClient,
    radio_link: httpx.Client,
    proxy_endpoint: tuple[str, int],
) -> None:
    mc = await MeshCore.create_tcp(*proxy_endpoint)
    assert mc is not None, "could not connect to the radio proxy"
    try:
        async with bench.ws_recorder(bench_config) as ws:
            await _drop_and_recover(aapi, ws)
        # Same TCP session to the proxy, still answered after the radio came back.
        info = await mc.commands.send_device_query()
        assert info.type == EventType.DEVICE_INFO
        assert str(info.payload.get("model", "")).startswith("MeshloomProxy/")
    finally:
        await mc.disconnect()


async def test_websocket_client_reconnect(bench_config: bench.BenchConfig) -> None:
    for _ in range(2):
        async with bench.ws_recorder(bench_config) as ws:
            first = await ws.wait_for(lambda e: True, 10)
            # On connect the server sends health first, before anything else.
            assert first and first.get("type") == "health"
            assert first["data"]["radio_connected"] is True


async def test_proxy_client_reconnect(
    aapi: httpx.AsyncClient, proxy_endpoint: tuple[str, int]
) -> None:
    async def client_count() -> int:
        response = await aapi.get("/radio/proxy")
        response.raise_for_status()
        return response.json()["client_count"]

    baseline = await client_count()
    for _ in range(2):
        mc = await MeshCore.create_tcp(*proxy_endpoint)
        assert mc is not None, "could not connect to the radio proxy"
        try:
            assert mc.self_info.get("name"), "no self info through the proxy"
            assert await bench.async_wait_until(
                lambda: _count_is(client_count, baseline + 1), 10
            ), "the proxy does not count the new client"
        finally:
            await mc.disconnect()
        assert await bench.async_wait_until(lambda: _count_is(client_count, baseline), 10), (
            "the proxy still counts the closed client"
        )
        await asyncio.sleep(1)


async def _count_is(client_count, expected: int) -> bool:
    return await client_count() == expected
