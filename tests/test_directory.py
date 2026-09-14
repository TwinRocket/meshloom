"""CoreScope directory hop resolver: 1-byte reject, opt-in no-op, cache, SSRF."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.models import DirectoryResolveHopsRequest
from app.repository import AppSettingsRepository
from app.repository.directory import DirectoryHopCacheRepository
from app.routers.directory import post_reset_directory_cache, post_resolve_hops
from app.routers.settings import AppSettingsUpdate, update_settings
from app.services.directory import (
    get_directory_node_reach,
    list_directory_map_nodes,
    normalize_directory_origin,
    parse_corescope_map_nodes,
    parse_corescope_reach,
    parse_corescope_resolved,
    reset_directory_nodes_cache,
    resolve_directory_hops,
    validate_hop_prefixes,
)


class TestNormalizeDirectoryOrigin:
    def test_empty_stays_empty(self):
        assert normalize_directory_origin("  ") == ""

    def test_strips_path_to_origin(self):
        assert normalize_directory_origin("https://analyzer.example/api/docs") == (
            "https://analyzer.example"
        )

    def test_rejects_file_scheme(self):
        with pytest.raises(ValueError, match="http or https"):
            normalize_directory_origin("file:///etc/passwd")

    def test_rejects_credentials(self):
        with pytest.raises(ValueError, match="credentials"):
            normalize_directory_origin("https://user:pass@evil.example")

    def test_rejects_whitespace(self):
        with pytest.raises(ValueError, match="whitespace"):
            normalize_directory_origin("https://evil.example /path")


class TestValidateHopPrefixes:
    def test_accepts_2_and_3_byte(self):
        assert validate_hop_prefixes(["a1b2", "C3D4E5"]) == ["A1B2", "C3D4E5"]

    def test_rejects_1_byte(self):
        with pytest.raises(HTTPException) as exc:
            validate_hop_prefixes(["1A"])
        assert exc.value.status_code == 400
        assert "1-byte" in str(exc.value.detail)

    def test_rejects_mixed_1_byte_without_forwarding(self):
        with pytest.raises(HTTPException) as exc:
            validate_hop_prefixes(["A1B2", "1A"])
        assert exc.value.status_code == 400

    def test_rejects_odd_length(self):
        with pytest.raises(HTTPException) as exc:
            validate_hop_prefixes(["ABC"])
        assert exc.value.status_code == 400


class TestParseCorescopeResolved:
    def test_unique_name(self):
        parsed = parse_corescope_resolved(
            {
                "resolved": {
                    "A1B2": {
                        "name": "HillTop",
                        "candidates": [],
                        "conflicts": [],
                        "confidence": "unique",
                    }
                }
            }
        )
        assert parsed == {"A1B2": "HillTop"}

    def test_keeps_candidate_gps(self):
        from app.services.directory import parse_corescope_resolved_hits

        parsed = parse_corescope_resolved_hits(
            {
                "resolved": {
                    "A1B2": {
                        "name": "HillTop",
                        "pubkey": "ab" * 32,
                        "candidates": [
                            {"name": "HillTop", "pubkey": "ab" * 32, "lat": 48.1, "lon": 2.2}
                        ],
                        "conflicts": [],
                        "confidence": "unique",
                    }
                }
            }
        )
        hit = parsed["A1B2"]
        assert hit is not None
        assert hit.name == "HillTop"
        assert hit.public_key == "ab" * 32
        assert hit.lat == 48.1
        assert hit.lon == 2.2

    def test_no_match_and_1_byte_ignored(self):
        parsed = parse_corescope_resolved(
            {
                "resolved": {
                    "A1": {
                        "name": "Nope",
                        "candidates": [],
                        "conflicts": [],
                        "confidence": "unique",
                    },
                    "FFFF": {
                        "name": None,
                        "candidates": [],
                        "conflicts": [],
                        "confidence": "no_match",
                    },
                }
            }
        )
        assert "A1" not in parsed
        assert parsed["FFFF"] is None


class TestDirectoryAvailable:
    @pytest.mark.asyncio
    async def test_off_without_url_is_false(self, test_db):
        from app.services.directory import directory_is_available

        assert await directory_is_available() is False

    @pytest.mark.asyncio
    async def test_manual_corescope_is_true(self, test_db):
        from app.services.directory import directory_is_available

        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        assert await directory_is_available() is True

    @pytest.mark.asyncio
    async def test_community_on_without_manual_url_is_true(self, test_db):
        from app.services.directory import directory_is_available
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        assert await directory_is_available() is True


class TestResolveDirectoryHops:
    @pytest.mark.asyncio
    async def test_disabled_is_noop(self, test_db):
        result = await resolve_directory_hops(["A1B2"])
        assert result.resolved == {}

    @pytest.mark.asyncio
    async def test_enabled_without_url_is_noop(self, test_db):
        await AppSettingsRepository.update(directory_enabled=True, directory_url="")
        result = await resolve_directory_hops(["A1B2"])
        assert result.resolved == {}

    @pytest.mark.asyncio
    async def test_rejects_1_byte_even_when_enabled(self, test_db):
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_directory_hops(["1A"])
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_proxies_only_saved_origin_and_caches(self, test_db):
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "resolved": {
                "A1B2": {
                    "name": "HillTop",
                    "candidates": [],
                    "conflicts": [],
                    "confidence": "unique",
                }
            }
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            first = await resolve_directory_hops(["A1B2"])
            second = await resolve_directory_hops(["A1B2"])

        assert first.resolved["A1B2"].name == "HillTop"
        assert first.resolved["A1B2"].source == "corescope"
        assert first.resolved["A1B2"].hash_width == 2
        assert second.resolved["A1B2"].name == "HillTop"
        assert mock_client.get.call_count == 1
        called_url = mock_client.get.call_args.args[0]
        assert called_url == "https://corescope.test/api/resolve-hops"
        assert mock_client.get.call_args.kwargs["params"]["hops"] == "A1B2"

    @pytest.mark.asyncio
    async def test_community_hops_use_sqlite_cache(self, test_db):
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        calls = {"n": 0}

        async def fake_data(*_args: object, **_kwargs: object) -> object:
            calls["n"] += 1
            return {
                "resolved": {
                    "A1B2": {
                        "name": "HillTop",
                        "confidence": "unique",
                        "conflicts": [],
                    }
                }
            }

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            first = await resolve_directory_hops(["A1B2"])
            second = await resolve_directory_hops(["A1B2"])
        assert first.resolved["A1B2"].name == "HillTop"
        assert second.resolved["A1B2"].name == "HillTop"
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_reset_wipes_directory_cache_only(self, test_db):
        await DirectoryHopCacheRepository.upsert("A1B2", 2, "HillTop", "corescope", 9_999_999_999)
        result = await post_reset_directory_cache()
        assert result.deleted == 1
        cached = await DirectoryHopCacheRepository.get_many([("A1B2", 2)])
        assert cached == {}


class TestDirectorySettingsValidation:
    @pytest.mark.asyncio
    async def test_empty_url_persists_without_spec_check(self, test_db):
        result = await update_settings(AppSettingsUpdate(directory_url=""))
        assert result.directory_url == ""
        assert result.directory_enabled is False

    @pytest.mark.asyncio
    async def test_url_requires_successful_spec(self, test_db):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"openapi": "3.0.3", "info": {"title": "CoreScope API"}}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            result = await update_settings(
                AppSettingsUpdate(directory_url="https://analyzer.example/extra")
            )

        assert result.directory_url == "https://analyzer.example"
        assert mock_client.get.call_args.args[0] == "https://analyzer.example/api/spec"

    @pytest.mark.asyncio
    async def test_file_url_rejected(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await update_settings(AppSettingsUpdate(directory_url="file:///tmp/x"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_spec_failure_does_not_persist(self, test_db):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPException) as exc:
                await update_settings(AppSettingsUpdate(directory_url="https://nope.example"))
        assert exc.value.status_code == 400
        fresh = await AppSettingsRepository.get()
        assert fresh.directory_url == ""


class TestDirectoryRouter:
    @pytest.mark.asyncio
    async def test_post_resolve_rejects_1_byte(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await post_resolve_hops(DirectoryResolveHopsRequest(hops=["AB"]))
        assert exc.value.status_code == 400


class TestParseCorescopeMapNodes:
    def test_keeps_repeater_gps_and_drops_sentinel(self):
        nodes, total = parse_corescope_map_nodes(
            {
                "total": 3,
                "nodes": [
                    {
                        "public_key": "ab" * 32,
                        "name": "HillTop",
                        "role": "repeater",
                        "lat": 48.1,
                        "lon": 2.2,
                    },
                    {
                        "public_key": "cd" * 32,
                        "name": "ZeroIsland",
                        "role": "repeater",
                        "lat": 0,
                        "lon": 0,
                    },
                    {
                        "public_key": "ef" * 32,
                        "name": "Companion",
                        "role": "companion",
                        "lat": 48.2,
                        "lon": 2.3,
                    },
                ],
            }
        )
        assert total == 3
        assert [(n.public_key, n.name, n.role, n.source) for n in nodes] == [
            ("ab" * 32, "HillTop", "repeater", "corescope"),
            ("ef" * 32, "Companion", "unknown", "corescope"),
        ]

    def test_rejects_non_object_payload(self):
        assert parse_corescope_map_nodes(["nope"]) == ([], None)


class TestListDirectoryMapNodes:
    @pytest.mark.asyncio
    async def test_disabled_is_noop(self, test_db):
        reset_directory_nodes_cache()
        result = await list_directory_map_nodes()
        assert result.nodes == []

    @pytest.mark.asyncio
    async def test_proxies_repeaters_and_caches(self, test_db):
        reset_directory_nodes_cache()
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "total": 1,
            "nodes": [
                {
                    "public_key": "11" * 32,
                    "name": "NetRelay",
                    "role": "repeater",
                    "lat": 45.0,
                    "lon": 5.0,
                }
            ],
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            first = await list_directory_map_nodes()
            second = await list_directory_map_nodes()

        assert first.nodes[0].name == "NetRelay"
        assert first.nodes[0].lat == 45.0
        assert first.nodes[0].source == "corescope"
        assert second.nodes[0].name == "NetRelay"
        assert mock_client.get.call_count == 1
        assert mock_client.get.call_args.args[0] == "https://corescope.test/api/nodes"
        assert "role" not in mock_client.get.call_args.kwargs["params"]

    @pytest.mark.asyncio
    async def test_community_nodes_cache_skips_second_stats_call(self, test_db):
        from app.services.meshloom_community import update_community

        reset_directory_nodes_cache()
        await update_community(enabled=True, iata="LYS")
        calls = {"n": 0}

        async def fake_data(*_args: object, **_kwargs: object) -> object:
            calls["n"] += 1
            return {
                "nodes": [
                    {
                        "public_key": "11" * 32,
                        "name": "NetRelay",
                        "role": "repeater",
                        "lat": 45.0,
                        "lon": 5.0,
                    }
                ]
            }

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            first = await list_directory_map_nodes()
            second = await list_directory_map_nodes()
        assert first.nodes[0].name == "NetRelay"
        assert second.nodes[0].name == "NetRelay"
        assert calls["n"] == 1


class TestDirectoryReach:
    def test_parse_keeps_gps_observers_only(self):
        parsed = parse_corescope_reach(
            {
                "node": {"pubkey": "aa" * 32, "name": "Ghost", "lat": 0, "lon": 0},
                "direct_observers": [
                    {
                        "pubkey": "bb" * 32,
                        "name": "Obs",
                        "count": 3,
                        "avg_snr": 4.5,
                        "lat": 48.1,
                        "lon": 2.2,
                    },
                    {
                        "pubkey": "cc" * 32,
                        "name": "NoGps",
                        "count": 1,
                        "avg_snr": 1.0,
                        "lat": None,
                        "lon": None,
                    },
                ],
            },
            "aa" * 32,
        )
        assert parsed.node is not None
        assert parsed.node.lat is None
        assert [obs.public_key for obs in parsed.observers] == ["bb" * 32]
        assert parsed.observers[0].avg_snr == 4.5

    @pytest.mark.asyncio
    async def test_reach_500_raises(self, test_db):
        reset_directory_nodes_cache()
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPException) as exc:
                await get_directory_node_reach("aa" * 32)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_reach_disabled_is_empty_not_500(self, test_db):
        reset_directory_nodes_cache()
        result = await get_directory_node_reach("aa" * 32)
        assert result.directory_enabled is False
        assert result.observers == []


class TestDirectoryTtlLru:
    def test_purge_expired_before_evicting_live_entries(self):
        from app.services.ttl_lru import TtlLruCache

        cache: TtlLruCache[str, str] = TtlLruCache(2)
        cache.set("stale", "old", expires_at=10, now=0)
        cache.set("live", "keep", expires_at=100, now=0)
        cache.set("fresh", "new", expires_at=100, now=20)

        assert cache.get("stale", now=20) is None
        assert cache.get("live", now=20) == "keep"
        assert cache.get("fresh", now=20) == "new"
        assert len(cache) == 2

    def test_evicts_least_recently_used_when_full(self):
        from app.services.ttl_lru import TtlLruCache

        cache: TtlLruCache[str, str] = TtlLruCache(2)
        cache.set("a", "A", expires_at=100, now=0)
        cache.set("b", "B", expires_at=100, now=0)
        cache.set("c", "C", expires_at=100, now=0)

        assert cache.get("a", now=1) is None
        assert cache.get("b", now=1) == "B"
        assert cache.get("c", now=1) == "C"

    @pytest.mark.asyncio
    async def test_reach_cache_drops_lru_entry(self, test_db):
        from app.models import DirectoryReachResponse
        from app.services import directory
        from app.services.ttl_lru import TtlLruCache

        reset_directory_nodes_cache()
        await AppSettingsRepository.update(
            directory_enabled=True, directory_url="https://corescope.test"
        )
        original = directory._reach_cache
        directory._reach_cache = TtlLruCache[tuple[str, str], DirectoryReachResponse](2)

        def _ok(pubkey: str) -> MagicMock:
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "node": {"pubkey": pubkey, "name": pubkey[:4], "lat": 1.0, "lon": 2.0},
                "direct_observers": [],
            }
            return response

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[_ok("aa" * 32), _ok("bb" * 32), _ok("cc" * 32), _ok("aa" * 32)]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        try:
            with patch("app.services.directory.httpx.AsyncClient", return_value=mock_client):
                await get_directory_node_reach("aa" * 32)
                await get_directory_node_reach("bb" * 32)
                await get_directory_node_reach("cc" * 32)
                await get_directory_node_reach("aa" * 32)
            assert mock_client.get.call_count == 4
        finally:
            directory._reach_cache = original
            reset_directory_nodes_cache()
