"""Send and receive on the bench channel: round trip, timestamps, no duplicates.

The bench has one radio, so the receive side is its own flood message repeated
back by the mesh: every repeat is a real RF arrival, parsed, decrypted and
deduplicated by the instance like any incoming packet. A send no repeater relayed
(RF collision) is tried again with a new text, up to `bench.ECHO_ATTEMPTS` times;
every attempt must still be stored exactly once.
"""

import time
from collections.abc import Awaitable, Callable

import httpx
import pytest
from meshcore import EventType, MeshCore

from app.path_utils import calculate_packet_hash
from tests.radio_bench import bench

pytestmark = pytest.mark.radio


def _assert_timestamps(row: dict, sent_at: float) -> None:
    assert row["sender_timestamp"] is not None
    assert abs(row["sender_timestamp"] - sent_at) <= bench.MAX_CLOCK_SKEW
    assert 0 <= row["received_at"] - row["sender_timestamp"] <= 5
    for path in row["paths"] or []:
        assert path["received_at"] >= row["sender_timestamp"]


async def _single_row(aapi: httpx.AsyncClient, key: str, token: str) -> dict:
    rows = await bench.channel_rows(aapi, key, token)
    assert len(rows) == 1, f"{len(rows)} rows for {token!r}"
    return rows[0]


async def _send_until_repeated(
    aapi: httpx.AsyncClient,
    key: str,
    airtime: bench.AirTime,
    base_token: str,
    send: Callable[[str], Awaitable[None]],
) -> tuple[str, dict, float]:
    """Send until the mesh repeats one; returns (token, echoed row, local send time)."""
    tokens = []
    for attempt in range(1, bench.ECHO_ATTEMPTS + 1):
        token = f"{base_token} {attempt}"
        tokens.append(token)
        await airtime.wait_turn()
        sent_at = time.time()
        await send(token)

        async def echoed(token: str = token) -> dict | None:
            row = await _single_row(aapi, key, token)
            return row if row["acked"] >= 1 and row["packet_hash"] else None

        row = await bench.async_wait_until(echoed, bench.ECHO_TIMEOUT, interval=2.0)
        if row:
            for earlier in tokens:
                await _single_row(aapi, key, earlier)
            return token, row, sent_at
    pytest.fail(f"no repeat heard for {bench.ECHO_ATTEMPTS} sends of {base_token!r}")


async def test_api_send_round_trip(
    bench_config: bench.BenchConfig,
    aapi: httpx.AsyncClient,
    airtime: bench.AirTime,
    channel_key: str,
    run_id: str,
) -> None:
    sent: dict[str, dict] = {}
    async with bench.ws_recorder(bench_config) as ws:

        async def send(token: str) -> None:
            response = await aapi.post(
                "/messages/channel", json={"channel_key": channel_key, "text": token}
            )
            response.raise_for_status()
            message = sent[token] = response.json()
            assert message["outgoing"] is True
            assert message["type"] == "CHAN"
            assert message["conversation_key"] == channel_key
            assert message["text"].endswith(token)
            live = await ws.wait_for(
                lambda e: e.get("type") == "message" and e["data"].get("id") == message["id"], 10
            )
            assert live, "the sent message never reached the WebSocket"

        token, row, sent_at = await _send_until_repeated(
            aapi, channel_key, airtime, f"bench {run_id} api", send
        )
        message = sent[token]
        _assert_timestamps(message, sent_at)
        acked = await ws.wait_for(
            lambda e: (
                e.get("type") == "message_acked" and e["data"].get("message_id") == message["id"]
            ),
            5,
        )
        assert acked, "no message_acked event for the repeat"
        live_ids = {e["id"] for e in ws.of_type("message") if token in e.get("text", "")}
        assert live_ids == {message["id"]}, "the repeat was broadcast as a new message"

    assert row["id"] == message["id"], "the repeat created a second row"
    assert row["sender_timestamp"] == message["sender_timestamp"]
    _assert_timestamps(row, sent_at)


async def test_proxy_send_round_trip(
    aapi: httpx.AsyncClient,
    airtime: bench.AirTime,
    channel_key: str,
    bench_config: bench.BenchConfig,
    proxy_endpoint: tuple[str, int],
    run_id: str,
) -> None:
    mc = await MeshCore.create_tcp(*proxy_endpoint)
    assert mc is not None, "could not connect to the radio proxy"
    heard: set[str] = set()

    def on_log(event) -> None:
        payload = event.payload or {}
        if payload.get("payload"):
            heard.add(calculate_packet_hash(bytes.fromhex(payload["payload"])))

    subscription = mc.subscribe(EventType.RX_LOG_DATA, on_log)
    try:
        slot = None
        for idx in range(16):
            info = await mc.commands.get_channel(idx)
            if info.type == EventType.CHANNEL_INFO and (
                info.payload.get("channel_name") == bench_config.channel
            ):
                slot = idx
                break
        assert slot is not None, f"{bench_config.channel} is not visible through the proxy"

        async def send(token: str) -> None:
            result = await mc.commands.send_chan_msg(slot, token)
            assert result.type == EventType.OK, f"proxy refused the send: {result.payload}"

        _token, row, sent_at = await _send_until_repeated(
            aapi, channel_key, airtime, f"bench {run_id} proxy", send
        )
        assert row["outgoing"] is True
        _assert_timestamps(row, sent_at)
        # The proxy client hears the repeat of its own message as raw RF.
        assert await bench.async_wait_until(lambda: row["packet_hash"] in heard, 15), (
            "the proxy client never received the repeat as RX log data"
        )
    finally:
        mc.unsubscribe(subscription)
        await mc.disconnect()
