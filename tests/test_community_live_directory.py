"""Community directory map nodes: all roles and full pagination."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import ContactUpsert
from app.repository import ContactRepository
from app.routers.directory import get_directory_map_nodes, get_live_directory_map_nodes
from app.services.directory import (
    NODES_PAGE_SIZE,
    list_directory_map_nodes,
    parse_directory_map_nodes,
    reset_directory_nodes_cache,
)


def _node(key: str, *, role: str = "repeater", name: str = "N", lat: float = 45.0) -> dict:
    return {
        "public_key": key,
        "name": name,
        "role": role,
        "lat": lat,
        "lon": 5.0,
    }


class TestParseMapNodesRoles:
    def test_keeps_every_role_and_normalizes_unknown(self):
        nodes, total = parse_directory_map_nodes(
            {
                "total": 5,
                "nodes": [
                    _node("aa" * 32, role="repeater", name="R"),
                    _node("bb" * 32, role="room", name="Room", lat=46.0),
                    _node("cc" * 32, role="client", name="C", lat=47.0),
                    _node("dd" * 32, role="sensor", name="S", lat=48.0),
                    _node("ee" * 32, role="companion", name="X", lat=49.0),
                    _node("ff" * 32, role="", name="Empty", lat=50.0),
                ],
            }
        )
        assert total == 5
        assert [(n.role, n.name) for n in nodes] == [
            ("repeater", "R"),
            ("room", "Room"),
            ("client", "C"),
            ("sensor", "S"),
            ("companion", "X"),
            ("unknown", "Empty"),
        ]


@pytest.mark.asyncio
class TestListDirectoryMapNodesPaging:
    async def test_community_pages_until_exhausted(self, test_db):
        reset_directory_nodes_cache()
        pages = {
            0: {
                "total": NODES_PAGE_SIZE + 1,
                "nodes": [
                    _node(f"{i:064x}", role="client", name=f"n{i}", lat=40.0 + (i % 10))
                    for i in range(NODES_PAGE_SIZE)
                ],
            },
            NODES_PAGE_SIZE: {
                "total": NODES_PAGE_SIZE + 1,
                "nodes": [_node("ab" * 32, role="room", name="last", lat=12.0)],
            },
        }
        calls: list[int] = []

        async def fake_data(_path: str, params: dict | None = None, **_kwargs: object) -> object:
            offset = 0 if params is None else int(params.get("offset", 0))
            calls.append(offset)
            assert "role" not in (params or {})
            return pages[offset]

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await list_directory_map_nodes()
            cached = await list_directory_map_nodes()
        assert len(result.nodes) == NODES_PAGE_SIZE + 1
        assert {n.role for n in result.nodes} == {"client", "room"}
        assert result.total == NODES_PAGE_SIZE + 1
        assert cached.nodes[0].name == result.nodes[0].name
        assert calls == [0, NODES_PAGE_SIZE]

    async def test_gps_less_rows_do_not_look_like_the_end_of_the_directory(self, test_db):
        """A page shrinks because nodes lack GPS, not because the directory ran out."""
        reset_directory_nodes_cache()
        total = NODES_PAGE_SIZE * 3
        kept_per_page = 5

        def page(index: int) -> dict:
            base = index * NODES_PAGE_SIZE
            nodes = [
                _node(f"{base + i:064x}", role="client", name=f"n{base + i}")
                for i in range(kept_per_page)
            ]
            nodes += [
                {
                    "public_key": f"{base + kept_per_page + i:064x}",
                    "name": "nogps",
                    "role": "client",
                }
                for i in range(NODES_PAGE_SIZE - kept_per_page)
            ]
            return {"total": total, "nodes": nodes}

        calls: list[int] = []

        async def fake_data(_path: str, params: dict | None = None, **_kwargs: object) -> object:
            offset = 0 if params is None else int(params.get("offset", 0))
            calls.append(offset)
            return page(offset // NODES_PAGE_SIZE)

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            result = await list_directory_map_nodes()

        assert calls == [0, NODES_PAGE_SIZE, NODES_PAGE_SIZE * 2]
        assert len(result.nodes) == kept_per_page * 3
        assert result.total == total


@pytest.mark.asyncio
class TestLiveDirectoryObservers:
    async def test_live_endpoint_keeps_observers_for_origin_geometry(self, test_db):
        """Live pins keep observer GPS; the client must not paint a dedicated icon."""
        reset_directory_nodes_cache()
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")

        async def fake_data(*_args: object, **_kwargs: object) -> object:
            return {
                "total": 2,
                "nodes": [
                    _node("aa" * 32, role="repeater", name="R"),
                    _node("bb" * 32, role="observer", name="Ear", lat=46.0),
                ],
            }

        with patch(
            "app.services.directory._community_directory_data",
            side_effect=fake_data,
        ):
            map_nodes = await get_directory_map_nodes()
            live_nodes = await get_live_directory_map_nodes()

        assert [(n.role, n.name) for n in map_nodes.nodes] == [
            ("repeater", "R"),
            ("observer", "Ear"),
        ]
        assert [(n.role, n.name) for n in live_nodes.nodes] == [
            ("repeater", "R"),
            ("observer", "Ear"),
        ]
        assert live_nodes.total == 2


@pytest.mark.asyncio
class TestLiveDirectoryLocalMerge:
    async def test_live_endpoint_includes_local_map_excludes(self, test_db):
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
        map_nodes = await get_directory_map_nodes()
        live_nodes = await get_live_directory_map_nodes()
        assert map_nodes.nodes == []
        assert [(n.role, n.source, n.name) for n in live_nodes.nodes] == [
            ("companion", "local", "LocalBuddy")
        ]
