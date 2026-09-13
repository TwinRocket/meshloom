"""Tests for the repeater pane cache.

The dashboard used to lose every pane on reload and could only recover by asking
the repeater again over the air. These cover the store and the radio-free read
endpoint that let it reopen on known values instead.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Contact
from app.repository import ContactRepository, RepeaterPaneCacheRepository
from app.repository.repeater_pane_cache import CACHEABLE_PANES
from app.routers.repeaters import repeater_pane_cache

KEY = "cc" * 32


async def _make_repeater(public_key: str = KEY) -> Contact:
    await ContactRepository.upsert(Contact(public_key=public_key, name="Repeater", type=2, flags=0))
    contact = await ContactRepository.get_by_key(public_key)
    assert contact is not None
    return contact


@pytest.mark.asyncio
async def test_stores_and_returns_the_last_answer(test_db):
    await _make_repeater()

    await RepeaterPaneCacheRepository.put(KEY, "node_info", {"name": "R", "lat": "43.5"})
    cached = await RepeaterPaneCacheRepository.get_all(KEY)

    assert set(cached) == {"node_info"}
    assert cached["node_info"]["data"] == {"name": "R", "lat": "43.5"}
    assert cached["node_info"]["fetched_at"] <= int(time.time())


@pytest.mark.asyncio
async def test_a_second_answer_replaces_the_first(test_db):
    """It is a cache, not a history: one row per pane."""
    await _make_repeater()

    await RepeaterPaneCacheRepository.put(KEY, "regions", {"regions": ["a"]})
    await RepeaterPaneCacheRepository.put(KEY, "regions", {"regions": ["a", "b"]})
    cached = await RepeaterPaneCacheRepository.get_all(KEY)

    assert cached["regions"]["data"] == {"regions": ["a", "b"]}


@pytest.mark.asyncio
async def test_ignores_panes_that_are_not_cacheable(test_db):
    await _make_repeater()

    await RepeaterPaneCacheRepository.put(KEY, "console", {"lines": ["secret"]})

    assert await RepeaterPaneCacheRepository.get_all(KEY) == {}
    assert "console" not in CACHEABLE_PANES


@pytest.mark.asyncio
async def test_drops_values_older_than_the_window(test_db):
    """Stale values are withheld rather than shown as if they were current."""
    await _make_repeater()
    await RepeaterPaneCacheRepository.put(KEY, "acl", {"acl": []})

    with patch(
        "app.repository.repeater_pane_cache.time.time", return_value=time.time() + 8 * 86400
    ):
        assert await RepeaterPaneCacheRepository.get_all(KEY) == {}


@pytest.mark.asyncio
async def test_endpoint_reads_the_cache_without_touching_the_radio(test_db):
    contact = await _make_repeater()
    await RepeaterPaneCacheRepository.put(KEY, "owner_info", {"owner": "someone"})

    with patch("app.routers.repeaters.radio_manager") as radio:
        radio.require_connected = AsyncMock(
            side_effect=AssertionError("the cache endpoint must not require the radio")
        )
        result = await repeater_pane_cache(contact.public_key)

    assert result["owner_info"]["data"] == {"owner": "someone"}


@pytest.mark.asyncio
async def test_clear_removes_every_pane_for_one_repeater(test_db):
    await _make_repeater()
    await RepeaterPaneCacheRepository.put(KEY, "node_info", {"name": "R"})
    await RepeaterPaneCacheRepository.put(KEY, "acl", {"acl": []})

    await RepeaterPaneCacheRepository.clear(KEY)

    assert await RepeaterPaneCacheRepository.get_all(KEY) == {}


@pytest.mark.asyncio
async def test_a_pane_handler_writes_its_answer_to_the_cache(test_db):
    """The wiring that matters: fetching a pane leaves its answer in the database.

    Without this, the endpoint and the store could both be correct while nothing
    ever populated the cache.
    """
    from meshcore import EventType

    from app.radio import radio_manager
    from app.routers.repeaters import repeater_neighbors

    contact = await _make_repeater()

    mc = MagicMock()
    mc.commands = MagicMock()
    mc.stop_auto_message_fetching = AsyncMock()
    mc.start_auto_message_fetching = AsyncMock()
    mc.commands.add_contact = AsyncMock(return_value=MagicMock(type=EventType.OK, payload={}))
    mc.commands.fetch_all_neighbours = AsyncMock(
        return_value={
            "neighbours": [{"pubkey": "ab" * 6, "snr": 7.5, "secs_ago": 30}],
            "neighbours_count": 1,
        }
    )

    with (
        patch("app.routers.repeaters.radio_manager.require_connected", return_value=mc),
        patch.object(radio_manager, "_meshcore", mc),
        patch("app.routers.repeaters.ensure_on_radio", AsyncMock()),
    ):
        response = await repeater_neighbors(contact.public_key)

    assert len(response.neighbors) == 1

    cached = await RepeaterPaneCacheRepository.get_all(contact.public_key)
    assert "neighbors" in cached, "the handler did not store its answer"
    assert cached["neighbors"]["data"]["reported_count"] == 1
    assert cached["neighbors"]["data"]["neighbors"][0]["snr"] == 7.5
