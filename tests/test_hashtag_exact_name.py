"""Hashtag keys hash the exact name (issue #71).

The official app normalizes what the user types (trim, lowercase,
``^#[a-z0-9-]+$``); that rule lives in the UI. Everything server-side hashes
the name it is given, like meshcore_py, meshcore-cli, the cracker and Community.
"""

from __future__ import annotations

from hashlib import sha256

import pytest

from app.data.meshcore_channels import hashtag_key_from_name, hashtag_room_name
from app.repository import ChannelRepository
from app.services.hashtag_catalogue import _display_name, _publish_name
from app.services.meshloom_community import hashtag_publish_name


def _key(raw: str) -> bytes:
    return sha256(raw.encode("utf-8")).digest()[:16]


class TestExactKey:
    def test_case_is_significant(self) -> None:
        assert hashtag_key_from_name("#Fr") == _key("#Fr")
        assert hashtag_key_from_name("#Fr") != hashtag_key_from_name("#fr")

    def test_whitespace_is_kept(self) -> None:
        assert hashtag_key_from_name("fr ") == _key("#fr ")
        assert hashtag_key_from_name(" fr") == _key("# fr")
        assert hashtag_key_from_name("fr ") != hashtag_key_from_name("fr")

    def test_one_leading_hash_is_optional(self) -> None:
        assert hashtag_key_from_name("#fr") == hashtag_key_from_name("fr") == _key("#fr")
        assert hashtag_key_from_name("##fr") == _key("##fr")

    def test_room_name_drops_one_hash_only(self) -> None:
        assert hashtag_room_name("#Fr ") == "Fr "
        assert hashtag_room_name("##x") == "#x"
        assert hashtag_room_name("") == ""


class TestNetworkNamesAreNotNormalized:
    def test_catalogue_keeps_the_exact_name(self) -> None:
        assert _publish_name("#Fr") == "Fr"
        assert _publish_name("fr ") == "fr "
        assert _display_name("Fr") == "#Fr"

    def test_community_publish_keeps_the_exact_name(self) -> None:
        assert hashtag_publish_name("#Fr") == "Fr"
        assert hashtag_publish_name(" fr") == " fr"


class TestRoutes:
    @pytest.mark.asyncio
    async def test_create_hashes_the_exact_name(self, test_db, client) -> None:
        upper = await client.post("/api/channels", json={"name": "#Fr"})
        lower = await client.post("/api/channels", json={"name": "#fr"})

        assert upper.status_code == 200
        assert lower.status_code == 200
        assert upper.json()["key"] == _key("#Fr").hex().upper()
        assert lower.json()["key"] == _key("#fr").hex().upper()

    @pytest.mark.asyncio
    async def test_bulk_keeps_case_spaces_and_inner_hash(self, test_db, client) -> None:
        response = await client.post(
            "/api/channels/bulk-hashtag",
            json={"channel_names": ["#Fr", "fr ", "##x", "#", "   "], "try_historical": False},
        )

        assert response.status_code == 200
        data = response.json()
        assert [c["name"] for c in data["created_channels"]] == ["#Fr", "#fr ", "##x"]
        assert data["invalid_names"] == ["#", "   "]
        for name in ("#Fr", "#fr ", "##x"):
            stored = await ChannelRepository.get_by_key(_key(name).hex().upper())
            assert stored is not None and stored.name == name
