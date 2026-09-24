"""Naming a region from traffic already heard, and failing usefully when asking."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.models import Contact, RadioRegionVerifyRequest
from app.region_resolver import RegionSample, compute_transport_code, count_region_matches
from app.routers.repeaters import (
    AnonRegionResult,
    _anon_region_timeout,
    request_anon_region_names_detailed,
)
from app.services.region_candidates import build_candidates, is_testable_name, match_candidates

PAYLOAD_TYPE = 7
PAYLOAD = b"a stored packet"


def _sample(region: str, payload: bytes = PAYLOAD) -> RegionSample:
    code = compute_transport_code(region, PAYLOAD_TYPE, payload)
    assert code is not None
    return RegionSample(
        payload_type=PAYLOAD_TYPE, payload=payload, transport_code=code, timestamp=0
    )


class TestCheckingANameAgainstStoredTraffic:
    def test_a_name_that_produced_a_packet_is_confirmed(self) -> None:
        assert count_region_matches(["fr-06"], [_sample("fr-06")]) == {"fr-06": 1}

    def test_a_name_that_did_not_is_reported_as_zero_rather_than_omitted(self) -> None:
        """Nothing heard is an answer. It is not the same as a wrong name."""
        assert count_region_matches(["fr-paca"], [_sample("fr-06")]) == {"fr-paca": 0}

    def test_the_wildcard_is_not_a_region(self) -> None:
        # "*" means unscoped flood. Merged into known_regions it would name a
        # region that cannot exist, and the resolver would never match it.
        assert count_region_matches(["*", "", "  "], [_sample("fr-06")]) == {}

    def test_each_packet_is_counted_once_per_name(self) -> None:
        samples = [_sample("fr-06", b"one"), _sample("fr-06", b"two"), _sample("fr-13", b"three")]
        assert count_region_matches(["fr-06", "fr-13"], samples) == {"fr-06": 2, "fr-13": 1}

    def test_matches_come_back_best_first(self) -> None:
        samples = [_sample("fr-06", b"one"), _sample("fr-06", b"two"), _sample("fr-13", b"three")]
        assert match_candidates(["fr-13", "fr-06", "nowhere"], samples) == [
            ("fr-06", 2),
            ("fr-13", 1),
            ("nowhere", 0),
        ]


class TestProposingNames:
    def test_refuses_what_the_firmware_would_refuse(self) -> None:
        assert is_testable_name("fr-06")
        assert not is_testable_name("deux mots")
        assert not is_testable_name("*")
        assert not is_testable_name("")

    def test_asks_about_what_the_operator_typed_first(self) -> None:
        candidates = build_candidates(
            known_regions=["configured"],
            flood_scope="scope",
            channel_scopes=[],
            repeater_names=[],
            extra=["typed"],
        )
        assert candidates[0] == "typed"

    def test_guesses_a_region_from_the_place_a_repeater_is_named_after(self) -> None:
        candidates = build_candidates(
            known_regions=[],
            flood_scope=None,
            channel_scopes=[],
            repeater_names=["Coursegoules Relay"],
        )
        assert "Coursegoules" in candidates
        assert "Coursegoules-Relay" in candidates

    def test_leaves_out_words_that_describe_the_box(self) -> None:
        """A word like Relay could only ever match by accident, and costs a MAC per packet."""
        candidates = build_candidates(
            known_regions=[],
            flood_scope=None,
            channel_scopes=[],
            repeater_names=["Mont Vial Repeater", "Pic de l Ours"],
        )
        assert "Relay" not in candidates
        assert "Repeater" not in candidates
        assert "de" not in candidates
        assert "Vial" in candidates

    def test_says_a_name_once_however_many_sources_offer_it(self) -> None:
        candidates = build_candidates(
            known_regions=["fr-06"],
            flood_scope="fr-06",
            channel_scopes=["FR-06"],
            repeater_names=[],
        )
        assert candidates == ["fr-06"]


class TestAskingARepeater:
    def _contact(self, hops: int = -1) -> Contact:
        return Contact(public_key="a" * 64, name="Repeater", type=2, direct_path_len=hops)

    def test_waits_longer_for_a_repeater_further_away(self) -> None:
        assert _anon_region_timeout(self._contact(-1)) == 10
        assert _anon_region_timeout(self._contact(3)) > _anon_region_timeout(self._contact(1))

    def test_one_unreachable_repeater_cannot_hold_a_sweep_open(self) -> None:
        assert _anon_region_timeout(self._contact(50)) == 40

    @pytest.mark.asyncio
    async def test_a_full_contact_list_says_so_instead_of_looking_like_silence(self) -> None:
        """This one fails every repeater at once, for a reason none of them caused."""
        mc = AsyncMock()
        with patch(
            "app.routers.repeaters.ensure_on_radio",
            side_effect=HTTPException(status_code=422, detail="Failed to add contact to radio"),
        ):
            result = await request_anon_region_names_detailed(mc, self._contact())

        assert result.outcome == "contact_add_failed"
        assert result.detail is not None
        assert result.attempts == 0
        mc.commands.req_regions_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_lost_packet_is_asked_again(self) -> None:
        mc = AsyncMock()
        mc.commands.req_regions_sync = AsyncMock(side_effect=[None, "fr-06,fr-13"])
        with (
            patch("app.routers.repeaters.ensure_on_radio", new=AsyncMock()),
            patch("app.routers.repeaters.asyncio.sleep", new=AsyncMock()),
        ):
            result = await request_anon_region_names_detailed(mc, self._contact())

        assert result == AnonRegionResult(names=["fr-06", "fr-13"], outcome="answered", attempts=2)

    @pytest.mark.asyncio
    async def test_silence_after_every_attempt_is_reported_as_silence(self) -> None:
        mc = AsyncMock()
        mc.commands.req_regions_sync = AsyncMock(return_value=None)
        with (
            patch("app.routers.repeaters.ensure_on_radio", new=AsyncMock()),
            patch("app.routers.repeaters.asyncio.sleep", new=AsyncMock()),
        ):
            result = await request_anon_region_names_detailed(mc, self._contact())

        assert result.outcome == "no_reply"
        assert result.names is None
        assert mc.commands.req_regions_sync.await_count == 2


class TestVerifyEndpoint:
    @pytest.mark.asyncio
    async def test_answers_without_the_radio(self) -> None:
        """Discovery needs a repeater to answer. This needs only the disk."""
        from app.routers.radio import verify_regions

        with (
            patch(
                "app.routers.radio.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings(["fr-06"])),
            ),
            patch("app.routers.radio.ChannelRepository.get_all", new=AsyncMock(return_value=[])),
            patch(
                "app.routers.radio.ContactRepository.get_repeaters_by_recent",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.routers.radio.RawPacketRepository.recent_data",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = await verify_regions(RadioRegionVerifyRequest(names=["fr-06"]))

        assert response.regional_packets == 0
        assert response.proposed is False
        assert [match.name for match in response.matches] == ["fr-06"]


def _settings(known_regions: list[str]):
    from app.models import AppSettings

    return AppSettings(known_regions=known_regions, flood_scope="")
