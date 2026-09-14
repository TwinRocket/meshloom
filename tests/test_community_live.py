"""Meshloom OSS Stats live-packet relay."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events import dump_ws_event
from app.services.community_live import (
    CLOSE_INACTIVE,
    CLOSE_JWT_EXPIRED,
    CLOSE_RATE_LIMIT,
    CLOSE_SLOT_BUSY,
    CommunityLiveRelay,
    map_stats_close,
    reset_community_live_for_tests,
    sanitize_community_packet,
    stats_live_ws_url,
)
from app.services.meshloom_community import CommunityEffective


def _packet(**overrides: object) -> dict:
    data = {
        "v": 1,
        "event_id": "e1",
        "hash8": "deadbeef",
        "type": "advert",
        "path": ["ab12"],
        "hop_count": 1,
        "hops": [{"token": "ab12", "lat": 45.7, "lon": 4.8}],
        "snr": -8.5,
        "iata": "LYS",
        "t": 1710000000000,
        "ear_id": "ear-1",
    }
    data.update(overrides)
    return data


def _enabled_state() -> CommunityEffective:
    return CommunityEffective(
        enabled=True,
        locked=False,
        iata="LYS",
        broker_host="mqtt.meshloom.app",
        api_base="https://api.meshloom.app",
        api_audience="api.meshloom.app",
        mqtt_audience="mqtt.meshloom.app",
    )


class FakeStatsSocket:
    def __init__(self, messages: list[object], *, close_exc: Exception | None = None) -> None:
        self._messages = list(messages)
        self._close_exc = close_exc
        self._closed = asyncio.Event()
        self.closed = False
        self.headers: dict[str, str] | None = None

    async def __aenter__(self) -> FakeStatsSocket:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    def __aiter__(self) -> FakeStatsSocket:
        return self

    async def __anext__(self) -> object:
        if self._messages:
            return self._messages.pop(0)
        if self._close_exc is not None:
            raise self._close_exc
        await self._closed.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True
        self._closed.set()


class _Close(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _HttpFail(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _reset_relay():
    reset_community_live_for_tests()
    yield
    reset_community_live_for_tests()


class TestSanitizeAndCloseCodes:
    def test_stats_live_ws_url_uses_wss_and_no_query(self):
        url = stats_live_ws_url("https://api.meshloom.app")
        assert url == "wss://api.meshloom.app/v1/live/packets"
        assert "?" not in url

    def test_sanitize_keeps_contract_fields_and_strips_secrets(self):
        raw = _packet(
            raw="aabbcc",
            publicKey="aa" * 32,
            hash="deadbeefcafef00d",
            observer_lat=45.123456,
        )
        cleaned = sanitize_community_packet(json.dumps(raw))
        assert cleaned is not None
        assert cleaned["hash8"] == "deadbeef"
        assert "raw" not in cleaned
        assert "publicKey" not in cleaned
        assert "hash" not in cleaned
        assert "observer_lat" not in cleaned

    def test_sanitize_truncates_long_hash_and_drops_unknown_v(self):
        assert sanitize_community_packet(_packet(hash8="DEADBEEFCAFE"))["hash8"] == "deadbeef"
        assert sanitize_community_packet(_packet(v=2)) is None

    def test_map_handshake_and_close_codes(self):
        assert map_stats_close(code=4001) == CLOSE_JWT_EXPIRED
        assert map_stats_close(http_status=403) == CLOSE_INACTIVE
        assert map_stats_close(http_status=409) == CLOSE_SLOT_BUSY
        assert map_stats_close(http_status=503) == CLOSE_RATE_LIMIT
        assert map_stats_close(code=1006) is None

    def test_dump_ws_event_community_packet(self):
        serialized = dump_ws_event("community_packet", _packet())
        envelope = json.loads(serialized)
        assert envelope["type"] == "community_packet"
        assert envelope["data"]["event_id"] == "e1"


@pytest.mark.asyncio
class TestCommunityLiveRelay:
    async def test_opt_out_never_opens_stats_socket(self):
        relay = CommunityLiveRelay()
        connect = AsyncMock()
        with (
            patch(
                "app.services.meshloom_community.community_enabled",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch.object(relay, "_connect", connect),
            patch.object(relay, "_broadcast_status", AsyncMock()),
        ):
            status = await relay.subscribe()
        assert status["opted_out"] is True
        assert status["connected"] is False
        connect.assert_not_called()

    async def test_subscribe_opens_one_socket_and_fans_out_sanitized_frame(self):
        relay = CommunityLiveRelay()
        socket = FakeStatsSocket([json.dumps(_packet(raw="ff00"))])
        broadcasts: list[tuple[str, dict]] = []

        async def capture(event_type: str, data: dict) -> None:
            broadcasts.append((event_type, data))

        async def connect(url: str, token: str):
            assert url == "wss://api.meshloom.app/v1/live/packets"
            assert token == "api-jwt"
            assert "token=" not in url
            return socket

        with (
            patch(
                "app.services.meshloom_community.community_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.meshloom_community.get_community_effective",
                new_callable=AsyncMock,
                return_value=_enabled_state(),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", return_value="api-jwt") as mint,
            patch("app.websocket.ws_manager.broadcast", side_effect=capture),
            patch.object(relay, "_connect", side_effect=connect),
        ):
            status = await relay.subscribe()
            assert status["session_id"]
            for _ in range(50):
                if any(event == "community_packet" for event, _ in broadcasts):
                    break
                await asyncio.sleep(0.01)
            await relay.close_stats()

        mint.assert_called_once()
        assert mint.call_args.kwargs["require_iata"] is False
        assert mint.call_args.kwargs["audience"] == "api.meshloom.app"
        frames = [data for event, data in broadcasts if event == "community_packet"]
        assert frames
        assert frames[0]["event_id"] == "e1"
        assert "raw" not in frames[0]

    async def test_last_consumer_closes_stats_socket(self):
        relay = CommunityLiveRelay()
        socket = FakeStatsSocket([])
        with (
            patch(
                "app.services.meshloom_community.community_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.meshloom_community.get_community_effective",
                new_callable=AsyncMock,
                return_value=_enabled_state(),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", return_value="tok"),
            patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock),
            patch.object(relay, "_connect", return_value=socket),
            patch("app.services.community_live.IDLE_CLOSE_GRACE_S", 0.01),
        ):
            status = await relay.subscribe()
            await asyncio.sleep(0.02)
            assert relay.connected or relay._reader_task is not None
            await relay.unsubscribe(status["session_id"])
            await asyncio.sleep(0.05)
        assert socket.closed is True
        assert relay.connected is False

    async def test_reader_does_not_remint_after_jwt_exp(self):
        relay = CommunityLiveRelay()
        socket = FakeStatsSocket([], close_exc=_Close(4001))
        mint = MagicMock(return_value="tok")
        with (
            patch(
                "app.services.meshloom_community.community_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.meshloom_community.get_community_effective",
                new_callable=AsyncMock,
                return_value=_enabled_state(),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", mint),
            patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock),
            patch.object(relay, "_connect", return_value=socket),
        ):
            await relay.subscribe()
            for _ in range(50):
                if relay.close_code == CLOSE_JWT_EXPIRED:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.02)
        assert relay.close_code == CLOSE_JWT_EXPIRED
        assert mint.call_count == 1
        assert relay.connected is False

    async def test_relancer_mints_a_second_jwt(self):
        relay = CommunityLiveRelay()
        sockets = [FakeStatsSocket([], close_exc=_Close(4001)), FakeStatsSocket([])]
        mint = MagicMock(side_effect=["first", "second"])

        async def connect(_url: str, token: str):
            socket = sockets.pop(0)
            socket.headers = {"Authorization": f"Bearer {token}"}
            return socket

        with (
            patch(
                "app.services.meshloom_community.community_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.meshloom_community.get_community_effective",
                new_callable=AsyncMock,
                return_value=_enabled_state(),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", mint),
            patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock),
            patch.object(relay, "_connect", side_effect=connect),
        ):
            await relay.subscribe()
            for _ in range(50):
                if relay.close_code == CLOSE_JWT_EXPIRED:
                    break
                await asyncio.sleep(0.01)
            assert mint.call_count == 1
            await relay.relancer()
            await asyncio.sleep(0.02)
            await relay.close_stats()
        assert mint.call_count == 2

    async def test_heartbeat_does_not_remint(self):
        relay = CommunityLiveRelay()
        socket = FakeStatsSocket([])
        mint = MagicMock(return_value="tok")
        with (
            patch(
                "app.services.meshloom_community.community_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.meshloom_community.get_community_effective",
                new_callable=AsyncMock,
                return_value=_enabled_state(),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", mint),
            patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock),
            patch.object(relay, "_connect", return_value=socket),
        ):
            first = await relay.subscribe()
            await asyncio.sleep(0.01)
            second = await relay.subscribe(first["session_id"])
            await relay.close_stats()
        assert second["session_id"] == first["session_id"]
        assert mint.call_count == 1

    async def test_handshake_409_maps_to_slot_busy(self):
        relay = CommunityLiveRelay()

        async def boom(_url: str, _token: str):
            raise _HttpFail(409)

        with (
            patch(
                "app.services.meshloom_community.community_enabled",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.meshloom_community.get_community_effective",
                new_callable=AsyncMock,
                return_value=_enabled_state(),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", return_value="tok"),
            patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock),
            patch.object(relay, "_connect", side_effect=boom),
        ):
            await relay.subscribe()
            for _ in range(50):
                if relay.close_code == CLOSE_SLOT_BUSY:
                    break
                await asyncio.sleep(0.01)
        assert relay.close_code == CLOSE_SLOT_BUSY

    async def test_bounded_fanout_drops_instead_of_blocking(self):
        relay = CommunityLiveRelay()
        for i in range(40):
            packet = sanitize_community_packet(_packet(event_id=f"e{i}"))
            assert packet is not None
            try:
                relay._queue.put_nowait(packet)
            except asyncio.QueueFull:
                pass
        assert relay._queue.qsize() == 32
        assert relay._queue.full()
