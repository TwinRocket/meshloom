import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.decoder import outgoing_group_text_packet_hash
from app.models import Message
from app.path_utils import calculate_packet_hash, canonical_packet_hash
from app.repository import (
    AmbiguousPublicKeyPrefixError,
    AppSettingsRepository,
    MessageRepository,
    RawPacketRepository,
)
from app.services.observer_reach import (
    ParsedReach,
    get_packet_observer_reach,
    get_packet_observer_reach_counts,
    hops_from_path_json,
    parse_batch_observations,
    parse_batch_reach,
    parse_corescope_observers,
    parse_packet_observations,
    parse_packet_reach,
    path_from_path_json,
    payload_sealed,
    reset_observer_reach_cache,
    resolve_origin_coords,
)


def _group_text_packet(channel_key: bytes, timestamp: int, text: str) -> bytes:
    from app.decoder import encrypt_group_text

    payload = encrypt_group_text(channel_key, timestamp, text, 0)
    return bytes([0x15, 0x00]) + payload


class TestOutgoingGroupTextHash:
    def test_matches_incoming_flood_packet(self):
        channel_key = hashlib.sha256(b"#testchannel").digest()[:16]
        timestamp = 1_700_000_000
        text = "Alice: Hello world"
        raw = _group_text_packet(channel_key, timestamp, text)
        incoming = calculate_packet_hash(raw)
        outgoing = outgoing_group_text_packet_hash(channel_key.hex(), timestamp, text)
        assert outgoing == incoming
        assert canonical_packet_hash(outgoing) == incoming

    def test_utf8_over_max_waits_for_echo(self):
        channel_key = hashlib.sha256(b"#testchannel").digest()[:16]
        text = "A" * 200
        assert outgoing_group_text_packet_hash(channel_key.hex(), 1_700_000_000, text) is None


class TestPathJsonHops:
    def test_list_and_json_string(self):
        assert hops_from_path_json(["aa", "bb", "cc"]) == 3
        assert hops_from_path_json('["aa","bb"]') == 2
        assert hops_from_path_json("[]") == 0
        assert hops_from_path_json("{") is None
        assert path_from_path_json(["aa", "bb", "cc"]) == ["aa", "bb", "cc"]
        assert path_from_path_json('["AA","bb"]') == ["aa", "bb"]
        assert path_from_path_json([{"prefix": "1A2B"}]) == ["1a2b"]
        assert path_from_path_json("{") is None


class TestObservationParsers:
    def test_packet_detail_observations(self):
        parsed = parse_packet_observations(
            {
                "observations": [
                    {
                        "observer_id": "obs-1",
                        "observer_name": "Lyon",
                        "snr": -4.5,
                        "path_json": ["ab", "cd"],
                    }
                ]
            }
        )
        assert len(parsed) == 1
        assert parsed[0].observer_id == "obs-1"
        assert parsed[0].hops == 2
        assert parsed[0].path == ("ab", "cd")
        assert parsed[0].snr == -4.5

    def test_stats_shaped_observers_use_path_field(self):
        parsed = parse_packet_observations(
            {
                "observers": [
                    {
                        "name": "Lyon",
                        "public_key": "aa" * 32,
                        "path": ["ab", "cd"],
                        "hops": 2,
                    }
                ]
            }
        )
        assert len(parsed) == 1
        assert parsed[0].observer_name == "Lyon"
        assert parsed[0].hops == 2
        assert parsed[0].path == ("ab", "cd")

    def test_batch_results(self):
        parsed = parse_batch_observations(
            {
                "results": {
                    "aabbccddeeff0011": [
                        {"observer_id": "a", "path_json": []},
                        {"observer_id": "b", "path_json": ["11"]},
                    ]
                }
            }
        )
        assert parsed is not None
        assert len(parsed["AABBCCDDEEFF0011"]) == 2

    def test_batch_rejects_ingest_shaped_body(self):
        assert parse_batch_observations({"accepted": 2}) is None

    def test_batch_count_only_payload(self):
        parsed = parse_batch_observations(
            {"results": {"aabbccddeeff0011": {"observation_count": 4}}}
        )
        assert parsed is not None
        assert len(parsed["AABBCCDDEEFF0011"]) == 4

    def test_missing_sealed_is_not_final(self):
        assert payload_sealed({"observers": []}) is False
        assert parse_packet_reach({"observers": [{"observer_id": "a"}]}).sealed is False
        parsed = parse_batch_reach(
            {"results": {"aabbccddeeff0011": {"observers": [{"observer_id": "a"}]}}}
        )
        assert parsed is not None
        assert parsed["AABBCCDDEEFF0011"].sealed is False

    def test_per_hash_sealed_true(self):
        reach = parse_packet_reach(
            {
                "observers": [{"observer_id": "a"}],
                "sealed": True,
            }
        )
        assert len(reach.observations) == 1
        assert reach.sealed is True

    def test_batch_entry_sealed_true(self):
        parsed = parse_batch_reach(
            {
                "results": {
                    "aabbccddeeff0011": {
                        "observers": [{"observer_id": "a"}],
                        "sealed": True,
                    }
                }
            }
        )
        assert parsed is not None
        assert parsed["AABBCCDDEEFF0011"].sealed is True
        assert len(parsed["AABBCCDDEEFF0011"].observations) == 1

    def test_observers_join(self):
        geos = parse_corescope_observers(
            {
                "observers": [
                    {
                        "id": "obs-1",
                        "name": "Lyon",
                        "lat": 45.75,
                        "lon": 4.85,
                        "public_key": "a" * 64,
                    },
                    {"id": "obs-2", "name": "Null Island", "lat": 0.0, "lon": 0.0},
                ]
            }
        )
        assert geos["obs-1"].lat == 45.75
        assert geos["obs-2"].lat is None


class TestObserverReachPersistence:
    @pytest.mark.asyncio
    async def test_channel_echo_writes_hash_on_outgoing_row(self, test_db):
        from app.services.messages import create_outgoing_channel_message, handle_duplicate_message

        channel_key = hashlib.sha256(b"#echo").digest()[:16].hex().upper()
        timestamp = 1_700_000_111
        text = "Bob: ping"

        broadcasts: list[object] = []
        message = await create_outgoing_channel_message(
            conversation_key=channel_key,
            text=text,
            sender_timestamp=timestamp,
            received_at=timestamp,
            sender_name="Bob",
            sender_key=None,
            channel_name="#echo",
            broadcast_fn=lambda *args, **kwargs: broadcasts.append((args, kwargs)),
        )
        assert message is not None
        assert message.packet_hash
        assert message.observer_reach_eligible is True

        await handle_duplicate_message(
            packet_id=None,
            msg_type="CHAN",
            conversation_key=channel_key,
            text=text,
            sender_timestamp=timestamp,
            outgoing=True,
            path="aa",
            received_at=timestamp + 1,
            path_len=1,
            broadcast_fn=lambda *args, **kwargs: None,
            packet_hash="AABBCCDDEEFF0011",
            observer_reach_eligible=True,
        )
        stored = await MessageRepository.get_by_id(message.id)
        assert stored is not None
        assert stored.packet_hash == "AABBCCDDEEFF0011"

    @pytest.mark.asyncio
    async def test_priv_flood_stays_eligible_after_raw_purge(self, test_db):
        msg_id = await MessageRepository.create(
            msg_type="PRIV",
            text="hello",
            conversation_key="ab" * 32,
            sender_timestamp=1_700_000_000,
            received_at=1_700_000_000,
            outgoing=False,
            packet_hash="AABBCCDDEEFF0011",
            observer_reach_eligible=True,
        )
        assert msg_id is not None
        packet_id, _is_new = await RawPacketRepository.create(
            b"\x15\x00payload", timestamp=1_700_000_000
        )
        await RawPacketRepository.mark_decrypted(packet_id, msg_id)
        await RawPacketRepository.purge_linked_to_messages()
        stored = await MessageRepository.get_by_id(msg_id)
        assert stored is not None
        assert stored.observer_reach_eligible is True
        assert stored.packet_hash == "AABBCCDDEEFF0011"
        assert stored.packet_id is None


class TestObserverReachGate:
    @pytest.mark.asyncio
    async def test_directory_off_makes_no_http_call(self, test_db):
        reset_observer_reach_cache()
        with patch("app.services.directory.httpx.AsyncClient") as mock_client:
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert result.directory_enabled is False
        assert result.counts == {}
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_falls_back_when_instance_is_old(self, test_db):
        reset_observer_reach_cache()
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        spec = MagicMock()
        spec.status_code = 200
        spec.json.return_value = {"openapi": "3.0.3", "paths": {}}
        spec.content = b"{}"
        packet = MagicMock()
        packet.status_code = 200
        packet.json.return_value = {
            "observations": [{"observer_id": "obs-1", "observer_name": "Lyon", "path_json": []}]
        }
        packet.content = b"{}"
        observers = MagicMock()
        observers.status_code = 200
        observers.json.return_value = {"observers": []}
        observers.content = b"{}"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[spec, packet, observers])
        mock_client.post = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])

        assert result.directory_enabled is True
        assert result.counts["AABBCCDDEEFF0011"] == 1
        assert result.sealed["AABBCCDDEEFF0011"] is False
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_batch_falls_back_to_packet_detail(self, test_db):
        reset_observer_reach_cache()
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        spec = MagicMock()
        spec.status_code = 200
        spec.json.return_value = {
            "openapi": "3.0.3",
            "paths": {"/api/packets/observations": {"post": {}}},
        }
        spec.content = b"{}"
        batch = MagicMock()
        batch.status_code = 200
        batch.json.return_value = {"results": {"aabbccddeeff0011": []}}
        batch.content = b"{}"
        packet = MagicMock()
        packet.status_code = 200
        packet.json.return_value = {
            "observations": [{"observer_id": "obs-1", "observer_name": "Lyon", "path_json": []}]
        }
        packet.content = b"{}"
        observers = MagicMock()
        observers.status_code = 200
        observers.json.return_value = {"observers": []}
        observers.content = b"{}"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[spec, packet, observers])
        mock_client.post = AsyncMock(return_value=batch)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])

        assert result.counts["AABBCCDDEEFF0011"] == 1
        mock_client.post.assert_called()

    @pytest.mark.asyncio
    async def test_corescope_5xx_is_not_zero(self, test_db):
        reset_observer_reach_cache()
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        failed = MagicMock()
        failed.status_code = 503
        failed.content = b"down"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=failed)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPException) as exc:
                await get_packet_observer_reach("AABBCCDDEEFF0011")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_detail_includes_hop_path_and_origin(self, test_db):
        reset_observer_reach_cache()
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        packet = MagicMock()
        packet.status_code = 200
        packet.json.return_value = {
            "observations": [
                {
                    "observer_id": "obs-1",
                    "observer_name": "Lyon",
                    "snr": -4.5,
                    "path_json": ["ab", "cd"],
                }
            ]
        }
        packet.content = b"{}"
        observers = MagicMock()
        observers.status_code = 200
        observers.json.return_value = {
            "observers": [{"id": "obs-1", "name": "Lyon", "lat": 45.75, "lon": 4.85}]
        }
        observers.content = b"{}"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[packet, observers])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.services.directory.httpx.AsyncClient", return_value=mock_client),
            patch(
                "app.services.observer_reach.resolve_origin_coords",
                new=AsyncMock(return_value=(45.76, 4.83)),
            ),
        ):
            result = await get_packet_observer_reach("AABBCCDDEEFF0011")

        assert result.observers[0].path == ["ab", "cd"]
        assert result.observers[0].hops == 2
        assert result.origin_available is True
        assert result.origin_lat == 45.76
        assert result.origin_lon == 4.83

    @pytest.mark.asyncio
    async def test_ambiguous_origin_prefix_is_skipped(self, test_db):
        message = Message(
            id=1,
            type="CHAN",
            conversation_key="cc" * 16,
            text="hi",
            received_at=1,
            sender_key="aabbccddeeff",
            outgoing=False,
        )
        with (
            patch(
                "app.services.observer_reach.ContactRepository.get_by_key",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.observer_reach.ContactRepository.get_by_key_or_prefix",
                new=AsyncMock(
                    side_effect=AmbiguousPublicKeyPrefixError(
                        "aabbccddeeff", ["aa" * 32, "ab" * 32]
                    )
                ),
            ),
        ):
            assert await resolve_origin_coords(message) is None


class TestCommunityObserverReach:
    @pytest.mark.asyncio
    async def test_batch_unavailable_falls_back_to_packet_detail(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if path.endswith("/observations"):
                raise HTTPException(status_code=500, detail="Stats directory unavailable")
            if "/packets/" in path:
                return {
                    "observers": [
                        {
                            "name": "Lyon",
                            "public_key": "aa" * 32,
                            "path": ["ab", "cd"],
                            "hops": 2,
                        }
                    ]
                }
            if path.endswith("/observers"):
                return {"observers": []}
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])

        assert result.directory_enabled is True
        assert result.counts["AABBCCDDEEFF0011"] == 1

    @pytest.mark.asyncio
    async def test_detail_uses_batch_path_when_packet_get_fails(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if path.endswith("/observations"):
                return {
                    "results": {
                        "AABBCCDDEEFF0011": [
                            {
                                "observer_id": "obs-1",
                                "observer_name": "Lyon",
                                "path_json": ["ab"],
                            }
                        ]
                    }
                }
            if "/packets/" in path:
                raise HTTPException(status_code=500, detail="packet get unavailable")
            if path.endswith("/observers"):
                return {"observers": []}
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await get_packet_observer_reach("AABBCCDDEEFF0011")

        assert result.directory_enabled is True
        assert result.observer_count == 1
        assert result.observers[0].name == "Lyon"

    @pytest.mark.asyncio
    async def test_ingest_shaped_batch_falls_back_to_packet_detail(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if path.endswith("/observations"):
                return {"accepted": 0}
            if "/packets/" in path:
                return {
                    "observations": [
                        {"observer_id": "obs-1", "observer_name": "Lyon", "path_json": []}
                    ]
                }
            if path.endswith("/observers"):
                return {"observers": []}
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])

        assert result.counts["AABBCCDDEEFF0011"] == 1

    @pytest.mark.asyncio
    async def test_community_all_5xx_is_not_zero(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(*_args: object, **_kwargs: object) -> object:
            raise HTTPException(status_code=500, detail="Stats directory unavailable")

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unsealed_result_uses_live_ttl(self, test_db):
        reset_observer_reach_cache()
        from app.services import observer_reach
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        calls: list[str] = []
        clock = {"now": 1_000.0}

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            calls.append(path)
            if path.endswith("/observations"):
                return {
                    "results": {
                        "AABBCCDDEEFF0011": {
                            "observers": [
                                {
                                    "observer_id": "obs-1",
                                    "observer_name": "Lyon",
                                    "path_json": ["ab"],
                                }
                            ]
                        }
                    }
                }
            if path.endswith("/observers"):
                return {"observers": []}
            return {}

        with (
            patch(
                "app.services.directory._community_directory_data",
                side_effect=fake_data,
            ),
            patch.object(observer_reach.time, "time", side_effect=lambda: clock["now"]),
        ):
            first = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
            second = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
            clock["now"] = 1_000.0 + observer_reach.LIVE_REACH_TTL_SECONDS + 0.1
            third = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert first.counts["AABBCCDDEEFF0011"] == 1
        assert first.sealed["AABBCCDDEEFF0011"] is False
        assert second.counts["AABBCCDDEEFF0011"] == 1
        assert third.counts["AABBCCDDEEFF0011"] == 1
        assert sum(1 for path in calls if path.endswith("/observations")) == 2

    @pytest.mark.asyncio
    async def test_community_live_empty_batch_skips_per_hash_get(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        calls: list[str] = []

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            calls.append(f"{method} {path}")
            if path.endswith("/observations"):
                return {"results": {"AABBCCDDEEFF0011": []}}
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert result.counts["AABBCCDDEEFF0011"] == 0
        assert any(item.endswith("/observations") for item in calls)
        assert not any("/packets/aabbccddeeff0011" in item.lower() for item in calls)

    @pytest.mark.asyncio
    async def test_unsealed_empty_uses_live_ttl(self, test_db):
        reset_observer_reach_cache()
        from app.services import observer_reach
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        calls: list[str] = []
        clock = {"now": 1_000.0}

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            calls.append(path)
            if path.endswith("/observations"):
                return {"results": {"AABBCCDDEEFF0011": []}}
            return {}

        with (
            patch(
                "app.services.directory._community_directory_data",
                side_effect=fake_data,
            ),
            patch.object(observer_reach.time, "time", side_effect=lambda: clock["now"]),
        ):
            first = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
            second = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
            clock["now"] = 1_000.0 + observer_reach.LIVE_REACH_TTL_SECONDS + 0.1
            third = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert first.counts["AABBCCDDEEFF0011"] == 0
        assert first.sealed["AABBCCDDEEFF0011"] is False
        assert second.counts["AABBCCDDEEFF0011"] == 0
        assert third.counts["AABBCCDDEEFF0011"] == 0
        assert sum(1 for path in calls if path.endswith("/observations")) == 2

    @pytest.mark.asyncio
    async def test_per_hash_sealed_is_surfaced(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if path.endswith("/observations"):
                raise HTTPException(status_code=500, detail="Stats directory unavailable")
            if "/packets/" in path:
                return {
                    "observers": [
                        {
                            "name": "Lyon",
                            "public_key": "aa" * 32,
                            "path": ["ab"],
                            "hops": 1,
                        }
                    ],
                    "sealed": True,
                }
            if path.endswith("/observers"):
                return {"observers": []}
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await get_packet_observer_reach("AABBCCDDEEFF0011")
        assert result.observer_count == 1
        assert result.sealed is True

    @pytest.mark.asyncio
    async def test_batch_sealed_is_surfaced_per_hash(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if path.endswith("/observations"):
                return {
                    "results": {
                        "AABBCCDDEEFF0011": {
                            "observers": [
                                {
                                    "observer_id": "obs-1",
                                    "observer_name": "Lyon",
                                    "path_json": ["ab"],
                                }
                            ],
                            "sealed": True,
                        }
                    }
                }
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert result.counts["AABBCCDDEEFF0011"] == 1
        assert result.sealed["AABBCCDDEEFF0011"] is True

    @pytest.mark.asyncio
    async def test_missing_sealed_field_is_not_final(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if path.endswith("/observations"):
                return {
                    "results": {
                        "AABBCCDDEEFF0011": {
                            "observers": [
                                {
                                    "observer_id": "obs-1",
                                    "observer_name": "Lyon",
                                    "path_json": ["ab"],
                                }
                            ]
                        }
                    }
                }
            if "/packets/" in path:
                return {
                    "observers": [
                        {"observer_id": "obs-1", "observer_name": "Lyon", "path_json": []}
                    ]
                }
            if path.endswith("/observers"):
                return {"observers": []}
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            counts = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
            detail = await get_packet_observer_reach("AABBCCDDEEFF0011")
        assert counts.sealed["AABBCCDDEEFF0011"] is False
        assert detail.sealed is False

    @pytest.mark.asyncio
    async def test_sealed_result_is_served_from_cache(self, test_db):
        reset_observer_reach_cache()
        from app.services import observer_reach
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        calls: list[str] = []
        clock = {"now": 1_000.0}

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            calls.append(path)
            if path.endswith("/observations"):
                return {
                    "results": {
                        "AABBCCDDEEFF0011": {
                            "observers": [
                                {
                                    "observer_id": "obs-1",
                                    "observer_name": "Lyon",
                                    "path_json": ["ab"],
                                }
                            ],
                            "sealed": True,
                        }
                    }
                }
            return {}

        with (
            patch(
                "app.services.directory._community_directory_data",
                side_effect=fake_data,
            ),
            patch.object(observer_reach.time, "time", side_effect=lambda: clock["now"]),
        ):
            first = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
            clock["now"] = 1_000.0 + observer_reach.LIVE_REACH_TTL_SECONDS + 60.0
            second = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert first.sealed["AABBCCDDEEFF0011"] is True
        assert second.counts["AABBCCDDEEFF0011"] == 1
        assert second.sealed["AABBCCDDEEFF0011"] is True
        assert sum(1 for path in calls if path.endswith("/observations")) == 1

    @pytest.mark.asyncio
    async def test_upstream_failure_is_not_cached_as_empty(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        fail = True

        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if fail:
                raise HTTPException(status_code=500, detail="Stats directory unavailable")
            if path.endswith("/observations"):
                return {
                    "results": {
                        "AABBCCDDEEFF0011": {
                            "observers": [
                                {
                                    "observer_id": "obs-1",
                                    "observer_name": "Lyon",
                                    "path_json": ["ab"],
                                }
                            ],
                            "sealed": True,
                        }
                    }
                }
            return {}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            with pytest.raises(HTTPException) as exc:
                await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
            assert exc.value.status_code == 500
            fail = False
            result = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])
        assert result.counts["AABBCCDDEEFF0011"] == 1
        assert result.sealed["AABBCCDDEEFF0011"] is True


class TestObserverReachTtlLru:
    def test_observers_cache_purges_expired_before_eviction(self):
        from app.services.ttl_lru import TtlLruCache

        cache: TtlLruCache[str, dict[str, str]] = TtlLruCache(2)
        cache.set("old-origin", {"obs": "gone"}, expires_at=5, now=0)
        cache.set("keep-origin", {"obs": "stay"}, expires_at=50, now=0)
        cache.set("new-origin", {"obs": "fresh"}, expires_at=50, now=10)

        assert cache.get("old-origin", now=10) is None
        assert cache.get("keep-origin", now=10) == {"obs": "stay"}
        assert cache.get("new-origin", now=10) == {"obs": "fresh"}

    def test_packet_reach_cache_evicts_lru(self):
        from app.services import observer_reach
        from app.services.ttl_lru import TtlLruCache

        original = observer_reach._reach_cache
        observer_reach._reach_cache = TtlLruCache(2)
        try:
            now = 1_000.0
            empty = ParsedReach(observations=[])
            observer_reach._reach_cache.set(("o", "aa"), empty, now + 90, now)
            observer_reach._reach_cache.set(("o", "bb"), empty, now + 90, now)
            observer_reach._reach_cache.set(("o", "cc"), empty, now + 90, now)
            assert observer_reach._reach_cache.get(("o", "aa"), now) is None
            assert observer_reach._reach_cache.get(("o", "bb"), now) == empty
            assert observer_reach._reach_cache.get(("o", "cc"), now) == empty
        finally:
            observer_reach._reach_cache = original
            reset_observer_reach_cache()


class TestLocalObserverFilter:
    LOCAL_KEY = "aa" * 32
    PEER_KEY = "bb" * 32

    @staticmethod
    def _fake_data(observers: list[dict[str, str]]):
        async def fake_data(path: str, method: str = "GET", **_kwargs: object) -> object:
            if path.endswith("/observations"):
                return {"results": {"AABBCCDDEEFF0011": {"observers": observers, "sealed": True}}}
            if "/packets/" in path:
                return {"observers": observers, "sealed": True}
            if path.endswith("/observers"):
                return {"observers": []}
            return {}

        return fake_data

    def _observers(self) -> list[dict[str, str]]:
        return [
            {
                "observer_id": self.LOCAL_KEY,
                "public_key": self.LOCAL_KEY,
                "observer_name": "Me",
            },
            {
                "observer_id": self.PEER_KEY,
                "public_key": self.PEER_KEY,
                "observer_name": "Peer",
            },
        ]

    @pytest.mark.asyncio
    async def test_own_radio_is_dropped_and_counts_agree(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        with (
            patch("app.keystore.get_public_key", return_value=bytes.fromhex(self.LOCAL_KEY)),
            patch(
                "app.services.directory._community_directory_data",
                side_effect=self._fake_data(self._observers()),
            ),
        ):
            detail = await get_packet_observer_reach("AABBCCDDEEFF0011")
            counts = await get_packet_observer_reach_counts(["AABBCCDDEEFF0011"])

        assert [entry.public_key for entry in detail.observers] == [self.PEER_KEY]
        assert detail.observer_count == 1
        # The ear badge reads the counts endpoint, so it must not disagree.
        assert counts.counts["AABBCCDDEEFF0011"] == 1

    @pytest.mark.asyncio
    async def test_no_keystore_key_filters_nothing(self, test_db):
        reset_observer_reach_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        with (
            patch("app.keystore.get_public_key", return_value=None),
            patch(
                "app.services.directory._community_directory_data",
                side_effect=self._fake_data(self._observers()),
            ),
        ):
            detail = await get_packet_observer_reach("AABBCCDDEEFF0011")

        assert detail.observer_count == 2
