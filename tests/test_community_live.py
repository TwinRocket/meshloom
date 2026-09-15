"""Meshloom OSS Stats live-packet relay."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events import dump_ws_event
from app.models import CommunityLiveStatus
from app.services.community_live import (
    CLOSE_INACTIVE,
    CLOSE_JWT_EXPIRED,
    CLOSE_RATE_LIMIT,
    CLOSE_SLOT_BUSY,
    CLOSE_SUPERSEDED,
    CommunityLiveRelay,
    map_stats_close,
    reset_community_live_for_tests,
    sanitize_community_packet,
    stats_live_ws_url,
    user_visible_close_code,
)
from app.services.meshloom_community import CommunityEffective


def _v1_packet(**overrides: object) -> dict:
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


def _v2_packet(**overrides: object) -> dict:
    data = {
        "v": 2,
        "event_id": "e1",
        "hash8": "deadbeef",
        "type": "advert",
        "path": ["ab12", "cd"],
        "hop_count": 2,
        "hops": [
            {
                "token": "ab12",
                "lat": 45.7,
                "lon": 4.8,
                "confidence": "exact",
                "pubkey": "ab" * 32,
                "name": "Hill",
            },
            {"token": "cd", "confidence": "unresolved", "reason": "ambiguous_prefix"},
        ],
        "ear": {"lat": 43.66, "lon": 7.21, "source": "advert"},
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


async def _wait_until(predicate, *, ticks: int = 80) -> None:
    for _ in range(ticks):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met")


class TestSanitizeAndCloseCodes:
    def test_stats_live_ws_url_uses_wss_and_no_query(self):
        url = stats_live_ws_url("https://api.meshloom.app")
        assert url == "wss://api.meshloom.app/v1/live/packets"
        assert "?" not in url

    def test_sanitize_v2_keeps_contract_fields_and_strips_secrets(self):
        raw = _v2_packet(
            raw="aabbcc",
            publicKey="aa" * 32,
            hash="deadbeefcafef00d",
            observer_lat=45.123456,
        )
        cleaned = sanitize_community_packet(json.dumps(raw))
        assert cleaned is not None
        assert cleaned["v"] == 2
        assert cleaned["hash8"] == "deadbeef"
        assert "packet_hash" not in cleaned
        assert cleaned["ear"] == {"lat": 43.66, "lon": 7.21, "source": "advert"}
        assert cleaned["hops"][0]["confidence"] == "exact"
        assert cleaned["hops"][0]["pubkey"] == "ab" * 32
        assert cleaned["hops"][1] == {
            "token": "cd",
            "confidence": "unresolved",
            "reason": "ambiguous_prefix",
        }
        assert "raw" not in cleaned
        assert "publicKey" not in cleaned
        assert "hash" not in cleaned
        assert "observer_lat" not in cleaned

    def test_sanitize_v2_allows_null_ear_and_probable_hop(self):
        cleaned = sanitize_community_packet(
            _v2_packet(
                path=["ab12"],
                hop_count=1,
                hops=[
                    {
                        "token": "ab12",
                        "lat": 45.7,
                        "lon": 4.8,
                        "confidence": "probable",
                        "reason": "geo_filtered",
                    }
                ],
                ear=None,
            )
        )
        assert cleaned is not None
        assert cleaned["ear"] is None
        assert cleaned["hops"][0]["confidence"] == "probable"
        assert cleaned["hops"][0]["reason"] == "geo_filtered"

    def test_sanitize_normalizes_v1_unresolved_flag(self):
        cleaned = sanitize_community_packet(
            _v1_packet(hops=[{"token": "ab12", "unresolved": True}])
        )
        assert cleaned is not None
        assert cleaned["v"] == 2
        assert cleaned["ear"] is None
        assert cleaned["hops"] == [
            {"token": "ab12", "confidence": "unresolved", "reason": "unresolved"}
        ]

    def test_sanitize_normalizes_v1_coords_to_exact(self):
        cleaned = sanitize_community_packet(_v1_packet())
        assert cleaned is not None
        assert cleaned["hops"][0]["confidence"] == "exact"
        assert cleaned["hops"][0]["lat"] == 45.7

    def test_sanitize_truncates_long_hash_and_drops_unknown_v(self):
        assert sanitize_community_packet(_v2_packet(hash8="DEADBEEFCAFE"))["hash8"] == "deadbeef"
        assert sanitize_community_packet(_v2_packet(v=3)) is None
        assert sanitize_community_packet(_v1_packet(v="1")) is None

    def test_sanitize_keeps_packet_hash_when_consistent_with_hash8(self):
        cleaned = sanitize_community_packet(
            _v2_packet(hash8="DEADBEEF", packet_hash="DEADBEEFCAFEF00D")
        )
        assert cleaned is not None
        assert cleaned["hash8"] == "deadbeef"
        assert cleaned["packet_hash"] == "deadbeefcafef00d"

    def test_sanitize_accepts_hash8_only_from_old_stats(self):
        cleaned = sanitize_community_packet(_v2_packet())
        assert cleaned is not None
        assert cleaned["hash8"] == "deadbeef"
        assert "packet_hash" not in cleaned
        assert "origin" not in cleaned

    def test_sanitize_keeps_advert_origin_pubkey(self):
        pubkey = "ab" * 32
        cleaned = sanitize_community_packet(
            _v2_packet(
                origin={
                    "token": pubkey[:8],
                    "lat": 43.70,
                    "lon": 7.25,
                    "confidence": "exact",
                    "pubkey": pubkey.upper(),
                    "name": "Mobile",
                }
            )
        )
        assert cleaned is not None
        assert cleaned["origin"]["pubkey"] == pubkey
        assert cleaned["origin"]["lat"] == 43.70

    def test_sanitize_keeps_unresolved_origin_pubkey_for_map_pin(self):
        pubkey = "cd" * 32
        cleaned = sanitize_community_packet(
            _v2_packet(
                origin={
                    "token": pubkey[:8],
                    "confidence": "unresolved",
                    "reason": "no_position",
                    "pubkey": pubkey,
                }
            )
        )
        assert cleaned is not None
        assert cleaned["origin"]["pubkey"] == pubkey
        assert "lat" not in cleaned["origin"]

    def test_sanitize_drops_bad_origin_keeps_frame(self):
        cleaned = sanitize_community_packet(_v2_packet(origin={"token": "nope"}))
        assert cleaned is not None
        assert "origin" not in cleaned

    def test_sanitize_keeps_optional_route_kind(self):
        cleaned = sanitize_community_packet(_v2_packet(route_kind="flood"))
        assert cleaned is not None
        assert cleaned["route_kind"] == "flood"
        assert "route_kind" not in sanitize_community_packet(_v2_packet())
        dropped = sanitize_community_packet(_v2_packet(route_kind="nope"))
        assert dropped is not None
        assert "route_kind" not in dropped

    def test_sanitize_drops_packet_hash_that_does_not_match_hash8(self):
        assert (
            sanitize_community_packet(_v2_packet(hash8="deadbeef", packet_hash="cafef00ddeadbeef"))
            is None
        )

    def test_sanitize_drops_invalid_packet_hash(self):
        assert sanitize_community_packet(_v2_packet(packet_hash="deadbeef")) is None
        assert sanitize_community_packet(_v2_packet(packet_hash="zz" * 8)) is None
        assert sanitize_community_packet(_v2_packet(packet_hash=12)) is None

    def test_sanitize_keeps_new_and_unknown_type_tokens(self):
        for token in ("req", "grp_txt", "path", "control", "raw_custom", "future_token"):
            cleaned = sanitize_community_packet(_v2_packet(type=token))
            assert cleaned is not None
            assert cleaned["type"] == token

    def test_sanitize_rejects_invalid_type_tokens(self):
        assert sanitize_community_packet(_v2_packet(type="ADVERT")) is None
        assert sanitize_community_packet(_v2_packet(type="")) is None
        assert sanitize_community_packet(_v2_packet(type="group-text")) is None
        assert sanitize_community_packet(_v2_packet(type=4)) is None

    def test_sanitize_rejects_malformed_v2(self):
        assert sanitize_community_packet(_v2_packet(hops=[{"token": "ab12"}])) is None
        assert (
            sanitize_community_packet(
                _v2_packet(hops=[{"token": "cd", "confidence": "unresolved", "reason": "x"}])
            )
            is None
        )
        assert (
            sanitize_community_packet(
                _v2_packet(
                    hops=[{"token": "ab12", "confidence": "unresolved", "reason": "x", "lat": 1.0}],
                    path=["ab12"],
                    hop_count=1,
                )
            )
            is None
        )
        assert sanitize_community_packet(_v2_packet(ear={"lat": 1.0, "lon": 2.0})) is None

    def test_map_handshake_and_close_codes(self):
        assert map_stats_close(code=4001) == CLOSE_JWT_EXPIRED
        assert map_stats_close(http_status=403) == CLOSE_INACTIVE
        assert map_stats_close(http_status=409) == CLOSE_SUPERSEDED
        assert map_stats_close(code=CLOSE_SLOT_BUSY) == CLOSE_SUPERSEDED
        assert map_stats_close(http_status=503) == CLOSE_RATE_LIMIT
        assert map_stats_close(code=1006) is None
        assert user_visible_close_code(CLOSE_SUPERSEDED) is None
        assert user_visible_close_code(CLOSE_SLOT_BUSY) is None
        assert user_visible_close_code(CLOSE_RATE_LIMIT) is None
        assert user_visible_close_code(CLOSE_INACTIVE) == CLOSE_INACTIVE

    def test_dump_ws_event_community_packet(self):
        packet = sanitize_community_packet(_v2_packet(packet_hash="deadbeefcafef00d"))
        assert packet is not None
        serialized = dump_ws_event("community_packet", packet)
        envelope = json.loads(serialized)
        assert envelope["type"] == "community_packet"
        assert envelope["data"]["event_id"] == "e1"
        assert envelope["data"]["packet_hash"] == "deadbeefcafef00d"
        assert envelope["data"]["hash8"] == "deadbeef"
        assert envelope["data"]["ear"]["source"] == "advert"
        assert envelope["data"]["hops"][1]["confidence"] == "unresolved"

    def test_status_model_strips_non_user_close_codes(self):
        hidden = CommunityLiveStatus.model_validate(
            {"close_code": 4005, "opted_out": False, "connected": False, "state": "reconnecting"}
        )
        assert hidden.close_code is None
        gated = CommunityLiveStatus.model_validate(
            {"close_code": 4002, "opted_out": False, "connected": False, "state": "gate"}
        )
        assert gated.close_code == CLOSE_INACTIVE


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
        assert status["state"] == "opted_out"
        connect.assert_not_called()

    async def test_subscribe_opens_one_socket_and_fans_out_sanitized_frame(self):
        relay = CommunityLiveRelay()
        socket = FakeStatsSocket([json.dumps(_v2_packet(raw="ff00"))])
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
            await _wait_until(lambda: any(event == "community_packet" for event, _ in broadcasts))
            await relay.close_stats()

        mint.assert_called_once()
        assert mint.call_args.kwargs["require_iata"] is False
        assert mint.call_args.kwargs["audience"] == "api.meshloom.app"
        frames = [data for event, data in broadcasts if event == "community_packet"]
        assert frames
        assert frames[0]["event_id"] == "e1"
        assert frames[0]["v"] == 2
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

    async def test_jwt_exp_remints_and_reconnects(self):
        relay = CommunityLiveRelay()
        sockets = [FakeStatsSocket([], close_exc=_Close(4001)), FakeStatsSocket([])]
        mint = MagicMock(side_effect=["first", "second", "third"])
        connects: list[str] = []

        async def connect(_url: str, token: str):
            connects.append(token)
            return sockets.pop(0)

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
            patch("app.services.community_live.RECONNECT_INITIAL_S", 0.01),
        ):
            await relay.subscribe()
            await _wait_until(lambda: len(connects) >= 2)
            await relay.close_stats()
        assert mint.call_count >= 2
        assert connects[0] == "first"
        assert connects[1] == "second"

    async def test_relancer_mints_a_second_jwt(self):
        relay = CommunityLiveRelay()
        sockets = [FakeStatsSocket([]), FakeStatsSocket([])]
        mint = MagicMock(side_effect=["first", "second", "third"])

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
            await asyncio.sleep(0.02)
            assert mint.call_count == 1
            await relay.relancer()
            await asyncio.sleep(0.02)
            await relay.close_stats()
        assert mint.call_count >= 2

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

    async def test_handshake_409_reconnects_without_user_close_code(self):
        relay = CommunityLiveRelay()
        calls = {"n": 0}

        async def connect(_url: str, _token: str):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _HttpFail(409)
            return FakeStatsSocket([])

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
            patch.object(relay, "_connect", side_effect=connect),
            patch("app.services.community_live.RECONNECT_INITIAL_S", 0.01),
        ):
            status = await relay.subscribe()
            await _wait_until(lambda: calls["n"] >= 2)
            await relay.close_stats()
        assert status["close_code"] not in {CLOSE_SLOT_BUSY, CLOSE_SUPERSEDED}
        assert relay.close_code not in {CLOSE_SLOT_BUSY, CLOSE_SUPERSEDED}

    async def test_bounded_fanout_drops_instead_of_blocking(self):
        relay = CommunityLiveRelay()
        for i in range(40):
            packet = sanitize_community_packet(
                _v2_packet(
                    event_id=f"e{i}",
                    path=["ab12"],
                    hops=[{"token": "ab12", "lat": 45.7, "lon": 4.8, "confidence": "exact"}],
                    hop_count=1,
                )
            )
            assert packet is not None
            try:
                relay._queue.put_nowait(packet)
            except asyncio.QueueFull:
                pass
        assert relay._queue.qsize() == 32
        assert relay._queue.full()
