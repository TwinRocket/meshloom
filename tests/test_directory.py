"""Community-only directory: 1-byte reject, off = empty, SQLite hop cache."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models import DirectoryResolveHopsRequest
from app.repository.directory import DirectoryHopCacheRepository
from app.routers.directory import post_reset_directory_cache, post_resolve_hops
from app.services.directory import (
    DIRECTORY_NODE_MAX_AGE_SECONDS,
    drop_observer_nodes,
    drop_stale_remote_map_nodes,
    get_directory_node_neighbors,
    get_directory_node_reach,
    list_directory_map_nodes,
    parse_directory_map_nodes,
    parse_directory_reach,
    parse_directory_resolved,
    reset_directory_nodes_cache,
    resolve_directory_hops,
    search_directory_nodes,
    validate_hop_prefixes,
)


class TestNoUpstreamClient:
    def test_directory_module_has_no_http_client(self):
        """OSS talks to Meshloom Community only. No direct upstream connection."""
        import app.services.directory as directory

        assert not hasattr(directory, "httpx")
        for removed in (
            "_corescope_get_json",
            "_corescope_post_json",
            "_require_directory_origin",
            "validate_corescope_spec",
            "normalize_directory_origin",
        ):
            assert not hasattr(directory, removed), removed


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


class TestParseDirectoryResolved:
    def test_unique_name(self):
        parsed = parse_directory_resolved(
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
        from app.services.directory import parse_directory_resolved_hits

        parsed = parse_directory_resolved_hits(
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
        parsed = parse_directory_resolved(
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
    async def test_community_off_is_false(self, test_db):
        from app.services.directory import directory_is_available

        assert await directory_is_available() is False

    @pytest.mark.asyncio
    async def test_community_on_is_true(self, test_db):
        from app.services.directory import directory_is_available
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        assert await directory_is_available() is True


class TestResolveDirectoryHops:
    @pytest.mark.asyncio
    async def test_community_off_is_noop(self, test_db):
        result = await resolve_directory_hops(["A1B2"])
        assert result.resolved == {}

    @pytest.mark.asyncio
    async def test_rejects_1_byte_even_when_community_on(self, test_db):
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        with pytest.raises(HTTPException) as exc:
            await resolve_directory_hops(["1A"])
        assert exc.value.status_code == 400

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
        assert first.resolved["A1B2"].hash_width == 2
        assert second.resolved["A1B2"].name == "HillTop"
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_reset_wipes_directory_cache_only(self, test_db):
        await DirectoryHopCacheRepository.upsert("A1B2", 2, "HillTop", "corescope", 9_999_999_999)
        result = await post_reset_directory_cache()
        assert result.deleted == 1
        cached = await DirectoryHopCacheRepository.get_many([("A1B2", 2)])
        assert cached == {}


class TestDirectoryRouter:
    @pytest.mark.asyncio
    async def test_post_resolve_rejects_1_byte(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await post_resolve_hops(DirectoryResolveHopsRequest(hops=["AB"]))
        assert exc.value.status_code == 400


class TestParseDirectoryMapNodes:
    def test_keeps_repeater_gps_and_drops_sentinel(self):
        nodes, total = parse_directory_map_nodes(
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
            ("ab" * 32, "HillTop", "repeater", "community-db"),
            ("ef" * 32, "Companion", "companion", "community-db"),
        ]

    def test_keeps_observer_role_and_drop_helper(self):
        nodes, _total = parse_directory_map_nodes(
            {
                "nodes": [
                    {
                        "public_key": "ab" * 32,
                        "name": "Ear",
                        "role": "observer",
                        "lat": 48.1,
                        "lon": 2.2,
                    }
                ]
            }
        )
        assert nodes[0].role == "observer"
        assert drop_observer_nodes(nodes) == []

    def test_drops_remote_pins_older_than_24h_and_keeps_local(self):
        from app.models import DirectoryMapNode

        now = 1_800_000_000
        fresh = DirectoryMapNode(
            public_key="aa" * 32,
            name="Fresh",
            role="repeater",
            lat=45.0,
            lon=5.0,
            source="community-db",
            last_seen=now - DIRECTORY_NODE_MAX_AGE_SECONDS + 60,
        )
        stale = DirectoryMapNode(
            public_key="bb" * 32,
            name="Stale",
            role="repeater",
            lat=46.0,
            lon=6.0,
            source="community-db",
            last_seen=now - DIRECTORY_NODE_MAX_AGE_SECONDS - 60,
        )
        unknown = DirectoryMapNode(
            public_key="cc" * 32,
            name="UnknownAge",
            role="companion",
            lat=47.0,
            lon=7.0,
            source="community-db",
        )
        local = DirectoryMapNode(
            public_key="dd" * 32,
            name="LocalOld",
            role="companion",
            lat=48.0,
            lon=8.0,
            source="local",
            last_seen=now - DIRECTORY_NODE_MAX_AGE_SECONDS - 60,
        )
        kept = drop_stale_remote_map_nodes(
            [fresh, stale, unknown, local], now=now
        )
        assert [node.public_key for node in kept] == [
            fresh.public_key,
            unknown.public_key,
            local.public_key,
        ]

    def test_keeps_upstream_source_tag_and_parses_last_seen_at(self):
        nodes, total = parse_directory_map_nodes(
            {
                "total": 1,
                "nodes": [
                    {
                        "public_key": "ab" * 32,
                        "name": "HillTop",
                        "role": "repeater",
                        "lat": 48.1,
                        "lon": 2.2,
                        "source": "corescope",
                        "last_seen_at": "2026-09-15T03:00:00Z",
                    }
                ],
            }
        )
        assert total == 1
        assert nodes[0].source == "corescope"
        assert nodes[0].last_seen == int(datetime(2026, 9, 15, 3, 0, tzinfo=UTC).timestamp())

    def test_community_default_source_without_payload_field(self):
        nodes, _total = parse_directory_map_nodes(
            {
                "nodes": [
                    {
                        "public_key": "ab" * 32,
                        "name": "HillTop",
                        "role": "client",
                        "lat": 48.1,
                        "lon": 2.2,
                    }
                ],
            }
        )
        assert nodes[0].role == "client"
        assert nodes[0].source == "community-db"
        assert nodes[0].last_seen is None

    def test_rejects_non_object_payload(self):
        assert parse_directory_map_nodes(["nope"]) == ([], None)


class TestListDirectoryMapNodes:
    @pytest.mark.asyncio
    async def test_community_off_is_noop(self, test_db):
        reset_directory_nodes_cache()
        result = await list_directory_map_nodes()
        assert result.nodes == []

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
        assert first.nodes[0].source == "community-db"
        assert second.nodes[0].name == "NetRelay"
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_default_list_excludes_local_gps_contacts(self, test_db):
        from app.models import ContactUpsert
        from app.repository import ContactRepository

        reset_directory_nodes_cache()
        await ContactRepository.upsert(
            ContactUpsert(
                public_key="aa" * 32,
                name="LocalBuddy",
                type=1,
                lat=43.7,
                lon=7.3,
                last_seen=1_700_000_000,
            )
        )
        result = await list_directory_map_nodes()
        assert result.nodes == []

    @pytest.mark.asyncio
    async def test_include_local_merges_gps_and_remote_wins(self, test_db):
        from app.models import ContactUpsert
        from app.repository import ContactRepository
        from app.services.meshloom_community import update_community

        reset_directory_nodes_cache()
        await update_community(enabled=True, iata="LYS")
        shared = "11" * 32
        local_only = "aa" * 32
        await ContactRepository.upsert(
            ContactUpsert(
                public_key=shared,
                name="LocalName",
                type=1,
                lat=1.0,
                lon=1.0,
                last_seen=1_700_000_000,
            )
        )
        await ContactRepository.upsert(
            ContactUpsert(
                public_key=local_only,
                name="LocalBuddy",
                type=1,
                lat=43.7,
                lon=7.3,
                last_seen=1_700_000_100,
            )
        )
        await ContactRepository.upsert(
            ContactUpsert(
                public_key="00" * 32,
                name="NoGps",
                type=2,
                lat=0.0,
                lon=0.0,
            )
        )

        async def fake_data(*_args: object, **_kwargs: object) -> object:
            return {
                "total": 1,
                "nodes": [
                    {
                        "public_key": shared,
                        "name": "NetRelay",
                        "role": "repeater",
                        "lat": 45.0,
                        "lon": 5.0,
                        "source": "community-db",
                        "last_seen_at": 1_800_000_000,
                    }
                ],
            }

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            map_nodes = await list_directory_map_nodes()
            live_nodes = await list_directory_map_nodes(include_local=True)

        assert [(n.public_key, n.source, n.name) for n in map_nodes.nodes] == [
            (shared, "community-db", "NetRelay")
        ]
        assert [(n.public_key, n.source, n.role, n.name) for n in live_nodes.nodes] == [
            (shared, "community-db", "repeater", "NetRelay"),
            (local_only, "local", "companion", "LocalBuddy"),
        ]
        assert live_nodes.nodes[0].last_seen == 1_800_000_000
        assert live_nodes.nodes[1].last_seen == 1_700_000_100
        assert live_nodes.total == 2

    @pytest.mark.asyncio
    async def test_live_list_drops_stale_remote_and_keeps_local(self, test_db):
        from app.models import ContactUpsert
        from app.repository import ContactRepository
        from app.services.meshloom_community import update_community

        reset_directory_nodes_cache()
        await update_community(enabled=True, iata="LYS")
        local_only = "cc" * 32
        await ContactRepository.upsert(
            ContactUpsert(
                public_key=local_only,
                name="LocalBuddy",
                type=1,
                lat=43.7,
                lon=7.3,
                last_seen=1_700_000_100,
            )
        )
        now = 1_800_000_000
        stale_seen = now - DIRECTORY_NODE_MAX_AGE_SECONDS - 120

        async def fake_data(*_args: object, **_kwargs: object) -> object:
            return {
                "total": 1,
                "nodes": [
                    {
                        "public_key": "aa" * 32,
                        "name": "ColdRelay",
                        "role": "repeater",
                        "lat": 45.0,
                        "lon": 5.0,
                        "source": "community-db",
                        "last_seen": stale_seen,
                    }
                ],
            }

        with (
            patch(
                "app.services.directory._community_directory_data",
                side_effect=fake_data,
            ),
            patch("app.services.directory.time.time", return_value=now),
        ):
            live_nodes = await list_directory_map_nodes(
                include_local=True,
                include_observers=True,
                max_remote_age_seconds=DIRECTORY_NODE_MAX_AGE_SECONDS,
            )

        assert [(n.public_key, n.source) for n in live_nodes.nodes] == [
            (local_only, "local")
        ]
        assert live_nodes.total == 1


class TestDirectoryReach:
    def test_parse_keeps_gps_observers_only(self):
        parsed = parse_directory_reach(
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
    async def test_reach_community_off_is_empty_not_500(self, test_db):
        reset_directory_nodes_cache()
        result = await get_directory_node_reach("aa" * 32)
        assert result.directory_enabled is False
        assert result.observers == []

    @pytest.mark.asyncio
    async def test_neighbors_and_search_community_off_are_empty(self, test_db):
        neighbors = await get_directory_node_neighbors("aa" * 32)
        assert neighbors.directory_enabled is False
        assert neighbors.neighbors == []
        found = await search_directory_nodes("hill")
        assert found.directory_enabled is False
        assert found.nodes == []

    @pytest.mark.asyncio
    async def test_reach_uses_stats_when_community_on(self, test_db):
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        paths: list[str] = []

        async def fake_data(path: str, **_kwargs: object) -> object:
            paths.append(path)
            return {"node": {"pubkey": "aa" * 32, "name": "Ghost", "lat": 48.1, "lon": 2.2}}

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await get_directory_node_reach("aa" * 32)
        assert paths == [f"/v1/directory/nodes/{'aa' * 32}/reach"]
        assert result.directory_enabled is True
        assert result.node is not None
        assert result.node.name == "Ghost"


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
