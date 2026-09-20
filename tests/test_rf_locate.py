"""RF locate: 0-hop local extraction, identity rules, CoreScope reach merge."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models import ContactUpsert
from app.repository import (
    ContactAdvertPathRepository,
    ContactRepository,
    MessageRepository,
)
from app.services.directory import reset_directory_nodes_cache
from app.services.meshloom_community import update_community
from app.services.rf_locate import locate_query


def _radio_info(lat: float = 48.85, lon: float = 2.35, name: str = "Home") -> MagicMock:
    runtime = MagicMock()
    runtime.meshcore = MagicMock(
        self_info={
            "adv_lat": lat,
            "adv_lon": lon,
            "name": name,
            "public_key": "ff" * 32,
        }
    )
    return runtime


async def _insert_contact(
    key: str,
    name: str,
    *,
    contact_type: int = 1,
    lat: float | None = None,
    lon: float | None = None,
) -> None:
    await ContactRepository.upsert(
        ContactUpsert(public_key=key, name=name, type=contact_type, lat=lat, lon=lon)
    )


class TestLocateIdentity:
    @pytest.mark.asyncio
    async def test_refuses_1_byte_hop(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await locate_query("1a")
        assert exc.value.status_code == 400
        assert "1-byte" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_ambiguous_prefix_is_409(self, test_db):
        await _insert_contact("abc123" + "11" * 29, "One")
        await _insert_contact("abc123" + "22" * 29, "Two")
        with pytest.raises(HTTPException) as exc:
            await locate_query("abc123")
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "ambiguous"
        assert len(exc.value.detail["candidates"]) == 2

    @pytest.mark.asyncio
    async def test_unique_prefix_promotes_to_pubkey(self, test_db):
        key = "abc123" + "33" * 29
        await _insert_contact(key, "Solo")
        result = await locate_query("abc123")
        assert result.identity is not None
        assert result.identity.public_key == key
        assert result.identity.inferred is False

    @pytest.mark.asyncio
    async def test_unknown_name_is_insufficient_identity(self, test_db):
        result = await locate_query("Nobody")
        assert result.identity is None
        assert result.empty_reason == "insufficient_identity"
        assert result.directory_enabled is False


class TestLocateLocalZeroHop:
    @pytest.mark.asyncio
    async def test_path_len_zero_advert_plus_radio_gps(self, test_db):
        key = "aa" * 32
        await _insert_contact(key, "Ghost")
        await ContactAdvertPathRepository.record_observation(key, "", 1_700_000_000, hop_count=0)
        with patch("app.services.rf_locate.radio_runtime", _radio_info()):
            result = await locate_query(key)
        assert result.heard_locally_0hop is True
        assert result.radio_has_gps is True
        assert result.source == "local"
        assert len(result.anchors) == 1
        assert result.anchors[0].kind == "local_0hop"
        assert result.anchors[0].lat == pytest.approx(48.85)
        assert result.anchors[0].radius_km == 20.0
        assert result.empty_reason is None

    @pytest.mark.asyncio
    async def test_zero_hop_message_path_counts(self, test_db):
        key = "bb" * 32
        await _insert_contact(key, "Talker")
        await MessageRepository.create(
            "PRIV",
            "hello",
            1_700_000_100,
            key,
            sender_timestamp=1_700_000_100,
            path="",
            path_len=0,
            snr=7.5,
        )
        with patch("app.services.rf_locate.radio_runtime", _radio_info()):
            result = await locate_query(key)
        assert result.heard_locally_0hop is True
        assert result.anchors[0].kind == "local_0hop"
        assert result.anchors[0].snr == pytest.approx(7.5)

    @pytest.mark.asyncio
    async def test_nearest_repeaters_first_hop_is_not_zero_hop(self, test_db):
        target = "cc" * 32
        repeater = "dd" * 32
        await _insert_contact(target, "Far")
        await _insert_contact(repeater, "Hill", contact_type=2, lat=45.0, lon=5.0)
        await ContactAdvertPathRepository.record_observation(
            target, repeater[:8], 1_700_000_000, hop_count=2
        )
        with patch("app.services.rf_locate.radio_runtime", _radio_info()):
            result = await locate_query(target)
        assert result.heard_locally_0hop is False
        kinds = {anchor.kind for anchor in result.anchors}
        assert "local_0hop" not in kinds
        assert "first_hop" in kinds
        assert result.anchors[0].calibratable is True

    @pytest.mark.asyncio
    async def test_ambiguous_first_hop_is_unresolved_not_silent(self, test_db):
        target = "ee" * 32
        await _insert_contact(target, "Target")
        await _insert_contact("abcd" + "11" * 30, "R1", contact_type=2, lat=1.0, lon=2.0)
        await _insert_contact("abcd" + "22" * 30, "R2", contact_type=2, lat=3.0, lon=4.0)
        await ContactAdvertPathRepository.record_observation(
            target, "abcd0000", 1_700_000_000, hop_count=2
        )
        result = await locate_query(target)
        assert result.anchors == []
        assert result.unresolved_hops[0].reason == "ambiguous"
        assert len(result.unresolved_hops[0].candidates) == 2

    @pytest.mark.asyncio
    async def test_never_heard_and_directory_off_is_cta(self, test_db):
        key = "11" * 32
        result = await locate_query(key)
        assert result.identity is not None
        assert result.identity.public_key == key
        assert result.anchors == []
        assert result.directory_enabled is False
        assert result.empty_reason == "directory_off"

    @pytest.mark.asyncio
    async def test_community_on_without_manual_url_is_not_directory_off(self, test_db):
        await update_community(enabled=True, iata="LYS")
        key = "11" * 32
        result = await locate_query(key)
        assert result.directory_enabled is True
        assert result.empty_reason == "no_anchors"


class TestLocateStaysLocal:
    @pytest.mark.asyncio
    async def test_stats_failure_does_not_500(self, test_db):
        reset_directory_nodes_cache()
        key = "44" * 32
        await update_community(enabled=True, iata="LYS")

        async def fake_data(*_args: object, **_kwargs: object) -> object:
            raise HTTPException(status_code=500, detail="Stats directory unavailable")

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await locate_query(key)
        assert result.identity is not None
        assert result.anchors == []
        assert result.empty_reason == "no_anchors"

    @pytest.mark.asyncio
    async def test_unknown_prefix_does_not_call_stats(self, test_db):
        await update_community(enabled=True, iata="LYS")
        with patch(
            "app.services.directory.search_directory_nodes",
            new=AsyncMock(side_effect=AssertionError("search should stay out of /locate")),
        ):
            result = await locate_query("abcd")
        assert result.identity is None
        assert result.empty_reason == "insufficient_identity"

    @pytest.mark.asyncio
    async def test_local_zero_hop_survives_without_directory_merge(self, test_db):
        key = "66" * 32
        await _insert_contact(key, "Both")
        await ContactAdvertPathRepository.record_observation(key, "", 1_700_000_000, hop_count=0)
        await update_community(enabled=True, iata="LYS")
        with patch("app.services.rf_locate.radio_runtime", _radio_info()):
            result = await locate_query(key)
        assert result.source == "local"
        assert {anchor.kind for anchor in result.anchors} == {"local_0hop"}
