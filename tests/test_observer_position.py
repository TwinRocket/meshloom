"""Position this radio publishes to Community (observer_position)."""

import asyncio
import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import observer_position
from app.services.meshloom_community import update_community
from app.services.observer_position import (
    advert_location_broadcast,
    note_radio_self_info,
    publishable_position,
    put_iata_bind,
)


class TestAdvertLocationBroadcast:
    @pytest.mark.parametrize("policy", [1, 2, 7])
    def test_non_zero_integer_broadcasts(self, policy):
        assert advert_location_broadcast({"adv_loc_policy": policy}) is True

    @pytest.mark.parametrize("policy", [0, None, "x", "1", True, False, 1.0])
    def test_anything_else_is_off(self, policy):
        assert advert_location_broadcast({"adv_loc_policy": policy}) is False

    def test_absent_is_off(self):
        assert advert_location_broadcast({}) is False

    @pytest.mark.parametrize("info", [None, [], "x"])
    def test_no_self_info_is_off(self, info):
        assert advert_location_broadcast(info) is False


def _info(lat=45.764043, lon=4.835659, policy=0, **extra):
    return {"adv_lat": lat, "adv_lon": lon, "adv_loc_policy": policy, **extra}


class TestPublishablePosition:
    def test_broadcast_position_is_exact_to_six_decimals(self):
        assert publishable_position(_info(45.76404321, 4.83565987, policy=1)) == (
            45.764043,
            4.83566,
            "exact",
        )

    def test_position_not_broadcast_is_rounded_to_two_decimals(self):
        position = publishable_position(_info(policy=0))
        assert position == (45.76, 4.84, "rounded")
        assert position is not None and position.precision == "rounded"

    @pytest.mark.parametrize("policy", [None, "x", True])
    def test_untrusted_policy_rounds(self, policy):
        assert publishable_position(_info(policy=policy)) == (45.76, 4.84, "rounded")

    def test_missing_policy_rounds(self):
        info = _info()
        del info["adv_loc_policy"]
        assert publishable_position(info) == (45.76, 4.84, "rounded")

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [
            (None, 4.8),
            (45.7, None),
            (0.0, 0.0),
            (0.001, 0.002),  # rounds onto (0, 0)
            (91.0, 4.8),
            (45.7, -181.0),
            (math.nan, 4.8),
            (45.7, math.inf),
            ("45.7", "4.8"),
            (True, 4.8),
        ],
    )
    def test_no_usable_position(self, lat, lon):
        assert publishable_position(_info(lat, lon)) is None

    def test_tiny_broadcast_position_near_null_island_is_kept_exact(self):
        assert publishable_position(_info(0.001, 0.002, policy=1)) == (0.001, 0.002, "exact")

    def test_missing_fields_or_no_info(self):
        assert publishable_position({}) is None
        assert publishable_position(None) is None


INFO_EXACT = {
    "public_key": "AB" * 32,
    "adv_lat": 45.76404321,
    "adv_lon": 4.83565912,
    "adv_loc_policy": 1,
}
BIND_OK = {"iata": "LYS", "concordance": "ok", "honored_for_buckets": True}


@pytest.fixture
def radio(monkeypatch):
    """The radio's self_info, as the re-bind reads it; mutate ``radio['info']``."""
    state: dict[str, Any] = {"info": dict(INFO_EXACT)}
    monkeypatch.setattr(observer_position, "radio_self_info", lambda: state["info"])
    monkeypatch.setattr(observer_position, "REBIND_DEBOUNCE_S", 0.01)
    monkeypatch.setattr(observer_position, "REBIND_RETRY_DELAYS_S", (0.01, 0.01))
    return state


async def _settle() -> None:
    task = observer_position._rebind_task
    if task is not None:
        await asyncio.wait_for(task, 3)


def _bodies(stats: AsyncMock) -> list[dict[str, Any]]:
    return [call.kwargs["json_body"] for call in stats.await_args_list]


class TestAutomaticRebind:
    @pytest.mark.asyncio
    async def test_policy_off_rebinds_with_the_rounded_position(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            await put_iata_bind("LYS")  # the operator's bind: exact
            radio["info"] = {**INFO_EXACT, "adv_loc_policy": 0}
            note_radio_self_info(radio["info"])
            await _settle()

        assert _bodies(stats) == [
            {"iata": "LYS", "lat": 45.764043, "lon": 4.835659, "position_precision": "exact"},
            {"iata": "LYS", "lat": 45.76, "lon": 4.84, "position_precision": "rounded"},
        ]
        # Same IATA as stored, token minted for it.
        assert stats.await_args_list[1].kwargs["iata"] == "LYS"
        assert stats.await_args_list[1].args == ("PUT", "/v1/me/iata")

    @pytest.mark.asyncio
    async def test_position_change_rebinds(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            await put_iata_bind("LYS")
            radio["info"] = {**INFO_EXACT, "adv_lat": 45.8}
            note_radio_self_info(radio["info"])
            await _settle()

        assert [body["lat"] for body in _bodies(stats)] == [45.764043, 45.8]

    @pytest.mark.asyncio
    async def test_nothing_changed_no_rebind(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            await put_iata_bind("LYS")
            note_radio_self_info(dict(INFO_EXACT))
            await _settle()

        assert stats.await_count == 1
        assert observer_position._rebind_task is None

    @pytest.mark.asyncio
    async def test_small_move_of_a_rounded_position_does_not_rebind(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        radio["info"] = {**INFO_EXACT, "adv_loc_policy": 0}
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            await put_iata_bind("LYS")
            note_radio_self_info({**radio["info"], "adv_lat": 45.7612})
            await _settle()

        assert stats.await_count == 1

    @pytest.mark.asyncio
    async def test_community_off_sends_nothing(self, test_db, radio):
        await update_community(enabled=False, iata="LYS")
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            note_radio_self_info(radio["info"])
            await _settle()

        stats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_iata_sends_nothing(self, test_db, radio):
        await update_community(enabled=True)
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            note_radio_self_info(radio["info"])
            await _settle()

        stats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_self_info_after_start_rebinds_once(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            # Debounce: three SELF_INFO in a row make one bind.
            for _ in range(3):
                note_radio_self_info(radio["info"])
            await _settle()
            note_radio_self_info(radio["info"])
            await _settle()

        assert stats.await_count == 1

    @pytest.mark.asyncio
    async def test_identity_not_accepted_waits(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        with (
            patch(
                "app.services.meshloom_community.stats_json",
                new=AsyncMock(return_value=BIND_OK),
            ) as stats,
            patch("app.services.radio_ingest_gate.ingest_allowed", return_value=False),
        ):
            note_radio_self_info(radio["info"])
            await _settle()

        stats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_retries_then_gives_up(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        failing = AsyncMock(side_effect=HTTPException(status_code=503, detail="down"))
        with patch("app.services.meshloom_community.stats_json", new=failing):
            note_radio_self_info(radio["info"])
            await _settle()

        # The first try plus the two retries, then nothing.
        assert failing.await_count == 3

    @pytest.mark.asyncio
    async def test_failure_then_success(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        flaky = AsyncMock(side_effect=[HTTPException(status_code=503, detail="down"), BIND_OK])
        with patch("app.services.meshloom_community.stats_json", new=flaky):
            note_radio_self_info(radio["info"])
            await _settle()

        assert flaky.await_count == 2
        assert observer_position._last_bound == ("ab" * 32, publishable_position(INFO_EXACT))

    @pytest.mark.asyncio
    async def test_unexpected_error_counts_as_a_failure_and_the_worker_lives(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        # A body missing a required field fails validation (not an
        # HTTPException): retried like any failure, never fatal.
        odd = {"iata": "LYS"}
        flaky = AsyncMock(side_effect=[odd, BIND_OK])
        with patch("app.services.meshloom_community.stats_json", new=flaky):
            note_radio_self_info(radio["info"])
            await _settle()

        assert flaky.await_count == 2
        assert observer_position._last_bound == ("ab" * 32, publishable_position(INFO_EXACT))

    @pytest.mark.asyncio
    async def test_bind_sent_while_the_worker_waited_for_the_lock_is_not_sent_twice(
        self, test_db, radio
    ):
        from app.services import meshloom_community

        await update_community(enabled=True, iata="LYS")
        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(return_value=BIND_OK)
        ) as stats:
            async with meshloom_community._update_lock:
                # A join or an IATA change holds the lock; the worker, past its
                # debounce, waits for it.
                note_radio_self_info(radio["info"])
                await asyncio.sleep(0.1)
                assert stats.await_count == 0
                await put_iata_bind("LYS")  # sends this very position
            await _settle()

        assert stats.await_count == 1

    @pytest.mark.asyncio
    async def test_self_info_event_handler_notes_the_payload(self, radio):
        from app.event_handlers import on_self_info

        with patch.object(observer_position, "note_radio_self_info") as note:
            await on_self_info(SimpleNamespace(payload=INFO_EXACT))
        note.assert_called_once_with(INFO_EXACT)


class TestUnknownPositionPrecision:
    """A precision Community adds later must not turn a successful bind into a 500."""

    @pytest.mark.asyncio
    async def test_bind_with_an_unknown_precision_succeeds(self, test_db, radio):
        await update_community(enabled=True, iata="LYS")
        odd = {**BIND_OK, "position_precision": "approximate"}
        with patch("app.services.meshloom_community.stats_json", new=AsyncMock(return_value=odd)):
            result = await put_iata_bind("LYS")
        assert result.iata == "LYS"
        assert result.position_precision is None

    @pytest.mark.asyncio
    async def test_join_with_an_unknown_precision_turns_community_on(self, test_db, radio):
        from app.models import CommunityIataBindRequest
        from app.routers.community import put_me_iata
        from app.services.meshloom_community import get_community_effective

        odd = {**BIND_OK, "position_precision": "approximate"}
        with (
            patch("app.services.meshloom_community.stats_json", new=AsyncMock(return_value=odd)),
            patch("app.services.meshloom_community._reload_system_publisher", new=AsyncMock()),
            patch("app.services.community_live.sync_community_live", new=AsyncMock()),
        ):
            result = await put_me_iata(CommunityIataBindRequest(iata="LYS", enable=True))
        assert result.position_precision is None
        state = await get_community_effective()
        assert state.enabled is True and state.iata == "LYS"

    def test_me_stats_tolerates_an_unknown_precision(self):
        from app.models import CommunityMeStats

        stats = CommunityMeStats.model_validate(
            {
                "unique_hashes_24h": 1,
                "unique_hashes_7d": 2,
                "iata": "LYS",
                "concordance": "ok",
                "position_precision": "approximate",
            }
        )
        assert stats.position_precision is None

    def test_known_precisions_are_kept(self):
        from app.models import CommunityIataBindResult

        for value in ("exact", "rounded", None):
            parsed = CommunityIataBindResult.model_validate(
                {**BIND_OK, "position_precision": value}
            )
            assert parsed.position_precision == value
