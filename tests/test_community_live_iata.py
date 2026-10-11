"""Live relay region filter (?iata=) and the active-regions proxy."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.models import CommunityLiveStatus, CommunityLiveSubscribeRequest
from app.services import community_live
from app.services.community_live import (
    UPSTREAM_IATA_MAX,
    CommunityLiveRelay,
    active_iata_codes,
    normalize_iata_selection,
    parse_active_iata,
    reset_community_live_for_tests,
    stats_live_ws_url,
    upstream_iata_filter,
)
from app.services.meshloom_community import CommunityEffective, CommunityUpstreamError
from tests.test_community_live import FakeStatsSocket, _wait_until


def _state(*, enabled: bool = True, iata: str = "LYS") -> CommunityEffective:
    return CommunityEffective(
        enabled=enabled,
        iata=iata,
        broker_host="mqtt.meshloom.app",
        api_base="https://api.meshloom.app",
        api_audience="api.meshloom.app",
        mqtt_audience="mqtt.meshloom.app",
    )


class _HttpFail(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _reset_relay():
    reset_community_live_for_tests()
    yield
    reset_community_live_for_tests()


class Upstream:
    """Records every connect URL; each socket stays open until closed."""

    def __init__(self, *, refuse_filtered: bool = False) -> None:
        self.urls: list[str] = []
        self.sockets: list[FakeStatsSocket] = []
        self.refuse_filtered = refuse_filtered

    async def connect(self, url: str, _token: str) -> FakeStatsSocket:
        self.urls.append(url)
        if self.refuse_filtered and "?iata=" in url:
            raise _HttpFail(400)
        socket = FakeStatsSocket([])
        self.sockets.append(socket)
        return socket


def _enter(
    stack: ExitStack, relay: CommunityLiveRelay, upstream: Upstream, state=None
) -> AsyncMock:
    effective = AsyncMock(return_value=state or _state())
    stack.enter_context(
        patch(
            "app.services.meshloom_community.community_enabled",
            new_callable=AsyncMock,
            return_value=True,
        )
    )
    stack.enter_context(patch("app.services.meshloom_community.get_community_effective", effective))
    stack.enter_context(
        patch("app.services.meshloom_community.mint_stats_jwt", MagicMock(return_value="tok"))
    )
    stack.enter_context(patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock))
    stack.enter_context(patch.object(relay, "_connect", side_effect=upstream.connect))
    stack.enter_context(patch.object(community_live, "FILTER_DEBOUNCE_S", 0.05))
    stack.enter_context(patch.object(community_live, "RECONNECT_INITIAL_S", 0.01))
    return effective


BASE = "wss://api.meshloom.app/v1/live/packets"


class TestFilterHelpers:
    def test_union_always_adds_the_radio_iata(self):
        assert upstream_iata_filter([frozenset({"NCE"})], "LYS") == frozenset({"NCE", "LYS"})
        assert upstream_iata_filter(
            [frozenset({"NCE"}), frozenset({"TLN", "NCE"})], "lys"
        ) == frozenset({"NCE", "TLN", "LYS"})

    def test_one_session_for_every_region_means_global(self):
        assert upstream_iata_filter([frozenset({"NCE"}), None], "LYS") is None

    def test_no_session_means_global(self):
        assert upstream_iata_filter([], "LYS") is None

    def test_too_many_codes_falls_back_to_global(self):
        codes = frozenset(
            f"A{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(UPSTREAM_IATA_MAX)
        )
        assert upstream_iata_filter([codes], "") == codes
        assert upstream_iata_filter([codes], "LYS") is None

    def test_selection_normalisation(self):
        assert normalize_iata_selection(None) is None
        assert normalize_iata_selection([]) is None
        assert normalize_iata_selection(["nce", " tln ", "b4d", "XXX", 3]) == frozenset(
            {"NCE", "TLN"}
        )
        assert normalize_iata_selection("NCE") is None

    def test_url_carries_sorted_codes_or_nothing(self):
        assert stats_live_ws_url("https://api.meshloom.app") == BASE
        assert stats_live_ws_url("https://api.meshloom.app", None) == BASE
        assert (
            stats_live_ws_url("https://api.meshloom.app", frozenset({"TLN", "NCE"}))
            == f"{BASE}?iata=NCE,TLN"
        )

    def test_request_model(self):
        assert CommunityLiveSubscribeRequest().iata is None
        assert CommunityLiveSubscribeRequest(iata=[]).iata is None
        assert CommunityLiveSubscribeRequest(iata=["nce", "NCE", "lys"]).iata == ["NCE", "LYS"]
        with pytest.raises(ValidationError):
            CommunityLiveSubscribeRequest(iata=["NICE"])
        with pytest.raises(ValidationError):
            CommunityLiveSubscribeRequest(iata=[f"A{i:02d}" for i in range(65)])

    def test_status_reports_the_upstream_filter(self):
        status = CommunityLiveStatus.model_validate(
            {"opted_out": False, "connected": True, "upstream_iata": ["LYS", "NCE"]}
        )
        assert status.upstream_iata == ["LYS", "NCE"]


@pytest.mark.asyncio
class TestRelayFilter:
    async def test_session_filter_reaches_the_url_with_the_radio_iata(self):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            status = await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            assert relay.snapshot(opted_out=False)["upstream_iata"] == ["LYS", "NCE"]
            await relay.close_stats()
        assert status["session_id"]
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE"]

    async def test_a_tab_without_filter_keeps_the_global_feed(self):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            await relay.subscribe()
            await _wait_until(lambda: relay.connected)
            assert relay.snapshot(opted_out=False)["upstream_iata"] is None
            await relay.close_stats()
        assert upstream.urls == [BASE]

    async def test_union_change_reconnects_once_after_the_debounce(self):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        instance = relay.instance_id
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            first = await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            # Three changes inside the debounce window: one reconnect.
            await relay.subscribe(first["session_id"], ["NCE", "TLN"])
            await relay.subscribe(first["session_id"], ["TLN"])
            second = await relay.subscribe(None, ["MRS"])
            await _wait_until(lambda: len(upstream.urls) >= 2 and relay.connected)
            await asyncio.sleep(0.15)
            await relay.close_stats()
        assert second["session_id"] != first["session_id"]
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE", f"{BASE}?iata=LYS,MRS,TLN"]
        assert upstream.sockets[0].closed is True
        assert relay.instance_id == instance

    async def test_heartbeat_with_the_same_filter_does_not_reconnect(self):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            first = await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            await relay.subscribe(first["session_id"], ["nce"])
            await asyncio.sleep(0.15)
            await relay.close_stats()
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE"]

    async def test_a_new_tab_for_every_region_switches_to_global(self):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            other = await relay.subscribe(None, None)
            await _wait_until(lambda: len(upstream.urls) >= 2 and relay.connected)
            # The global tab leaves: back to the filtered feed.
            await relay.unsubscribe(other["session_id"])
            await _wait_until(lambda: len(upstream.urls) >= 3 and relay.connected)
            await relay.close_stats()
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE", BASE, f"{BASE}?iata=LYS,NCE"]

    async def test_filter_refused_with_400_falls_back_to_global(self):
        relay = CommunityLiveRelay()
        upstream = Upstream(refuse_filtered=True)
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            first = await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            assert relay.snapshot(opted_out=False)["upstream_iata"] is None
            # A filter change inside the retry window stays global: no reconnect.
            await relay.subscribe(first["session_id"], ["TLN"])
            await asyncio.sleep(0.15)
            assert relay.close_code is None
            await relay.close_stats()
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE", BASE]

    async def test_relancer_tries_the_filter_again(self):
        relay = CommunityLiveRelay()
        upstream = Upstream(refuse_filtered=True)
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            upstream.refuse_filtered = False
            await relay.relancer()
            await _wait_until(lambda: len(upstream.urls) >= 3 and relay.connected)
            await relay.close_stats()
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE", BASE, f"{BASE}?iata=LYS,NCE"]

    async def test_old_community_ignoring_the_parameter_just_works(self):
        """No 400: the socket opens with ?iata= and whatever comes is relayed."""
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            await relay.close_stats()
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE"]

    async def test_opt_out_during_the_debounce_never_reopens(self):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            effective = _enter(stack, relay, upstream)
            first = await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            await relay.subscribe(first["session_id"], ["TLN"])
            effective.return_value = _state(enabled=False)
            await relay.sync_community(False)
            await asyncio.sleep(0.15)
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE"]
        assert relay.connected is False
        assert relay._filter_task is None

    async def test_opt_out_after_the_debounce_check_never_reopens(self):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            effective = _enter(stack, relay, upstream)
            first = await relay.subscribe(None, ["NCE"])
            await _wait_until(lambda: relay.connected)
            real_close = relay.close_stats

            async def close_then_opt_out() -> None:
                effective.return_value = _state(enabled=False)
                await real_close()

            stack.enter_context(patch.object(relay, "close_stats", close_then_opt_out))
            await relay.subscribe(first["session_id"], ["TLN"])
            await asyncio.sleep(0.15)
        assert upstream.urls == [f"{BASE}?iata=LYS,NCE"]
        assert relay._reader_task is None

    async def test_expired_session_leaves_the_union(self, monkeypatch):
        relay = CommunityLiveRelay()
        upstream = Upstream()
        with ExitStack() as stack:
            _enter(stack, relay, upstream)
            gone = await relay.subscribe(None, ["NCE"])
            await relay.subscribe(None, ["TLN"])
            await _wait_until(lambda: relay.connected)
            seen, selection = relay._sessions[gone["session_id"]]
            relay._sessions[gone["session_id"]] = (seen - 1000, selection)
            keep = next(sid for sid in relay._sessions if sid != gone["session_id"])
            await relay.subscribe(keep, ["TLN"])
            await _wait_until(lambda: len(upstream.urls) >= 2 and relay.connected)
            await relay.close_stats()
        assert upstream.urls[-1] == f"{BASE}?iata=LYS,TLN"


class TestActiveIataParse:
    def test_parse(self):
        assert parse_active_iata(
            [
                {"code": "nce", "unique_hashes_24h": 4},
                {"code": "LYS", "unique_hashes_24h": 9},
                {"code": "XXX", "unique_hashes_24h": 99},
                {"code": "bad!", "unique_hashes_24h": 1},
                {"code": "TLN"},
                "junk",
            ]
        ) == [
            {"code": "LYS", "unique_hashes_24h": 9},
            {"code": "NCE", "unique_hashes_24h": 4},
            {"code": "TLN", "unique_hashes_24h": 0},
        ]
        assert parse_active_iata({"iata": [{"code": "NCE", "unique_hashes_24h": 1}]}) == [
            {"code": "NCE", "unique_hashes_24h": 1}
        ]
        assert parse_active_iata(None) == []


@pytest.mark.asyncio
class TestActiveIata:
    async def test_opted_out_sends_nothing(self):
        stats = AsyncMock()
        with (
            patch(
                "app.services.meshloom_community.community_egress_state",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.services.meshloom_community.stats_json", stats),
        ):
            assert await active_iata_codes() == {"available": False, "codes": []}
        stats.assert_not_called()

    async def test_cached_for_a_minute(self):
        stats = AsyncMock(return_value=[{"code": "NCE", "unique_hashes_24h": 3}])
        with (
            patch(
                "app.services.meshloom_community.community_egress_state",
                new_callable=AsyncMock,
                return_value=_state(),
            ),
            patch("app.services.meshloom_community.stats_json", stats),
        ):
            first = await active_iata_codes()
            second = await active_iata_codes()
        assert (
            first
            == second
            == {
                "available": True,
                "codes": [{"code": "NCE", "unique_hashes_24h": 3}],
            }
        )
        stats.assert_awaited_once_with("GET", "/v1/iata/active", auth=False)

    async def test_route_missing_upstream_is_unavailable(self):
        stats = AsyncMock(side_effect=CommunityUpstreamError(502, "nope", upstream_status=404))
        with (
            patch(
                "app.services.meshloom_community.community_egress_state",
                new_callable=AsyncMock,
                return_value=_state(),
            ),
            patch("app.services.meshloom_community.stats_json", stats),
        ):
            assert await active_iata_codes() == {"available": False, "codes": []}
            assert await active_iata_codes() == {"available": False, "codes": []}
        stats.assert_awaited_once()

    async def test_other_failures_are_raised(self):
        stats = AsyncMock(side_effect=CommunityUpstreamError(502, "nope", upstream_status=418))
        with (
            patch(
                "app.services.meshloom_community.community_egress_state",
                new_callable=AsyncMock,
                return_value=_state(),
            ),
            patch("app.services.meshloom_community.stats_json", stats),
            pytest.raises(CommunityUpstreamError),
        ):
            await active_iata_codes()
