"""Tests for message pagination using cursor parameters."""

import pytest

from app.repository import MessageRepository

CHAN_KEY = "ABC123DEF456ABC123DEF456ABC12345"
DM_KEY = "aa" * 32


@pytest.mark.asyncio
async def test_cursor_pagination_avoids_overlap(test_db):
    ids = []
    for received_at, text in [(200, "m1"), (200, "m2"), (150, "m3"), (100, "m4")]:
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text=text,
            conversation_key=CHAN_KEY,
            sender_timestamp=received_at,
            received_at=received_at,
        )
        assert msg_id is not None
        ids.append(msg_id)

    page1 = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=2,
        offset=0,
    )
    assert len(page1) == 2

    oldest = page1[-1]
    page2 = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=2,
        offset=0,
        before=oldest.received_at,
        before_id=oldest.id,
    )
    assert len(page2) == 2

    ids_page1 = {m.id for m in page1}
    ids_page2 = {m.id for m in page2}
    assert ids_page1.isdisjoint(ids_page2)


@pytest.mark.asyncio
async def test_empty_page_when_no_messages(test_db):
    """Pagination on a conversation with no messages returns empty list."""
    result = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=50,
    )
    assert result == []


@pytest.mark.asyncio
async def test_empty_page_after_oldest_message(test_db):
    """Requesting a page before the oldest message returns empty list."""
    msg_id = await MessageRepository.create(
        msg_type="CHAN",
        text="only message",
        conversation_key=CHAN_KEY,
        sender_timestamp=100,
        received_at=100,
    )
    assert msg_id is not None

    # Use before cursor pointing at the only message — should get nothing
    result = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=50,
        before=100,
        before_id=msg_id,
    )
    assert result == []


@pytest.mark.asyncio
async def test_timestamp_tie_uses_id_tiebreaker(test_db):
    """Multiple messages with the same received_at are ordered by id DESC."""
    ids = []
    for text in ["first", "second", "third"]:
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text=text,
            conversation_key=CHAN_KEY,
            sender_timestamp=500,
            received_at=500,
        )
        assert msg_id is not None
        ids.append(msg_id)

    # All three at same timestamp; page of 2 should get the two highest IDs
    page1 = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=2,
    )
    assert len(page1) == 2
    assert page1[0].id == ids[2]  # "third" (highest id)
    assert page1[1].id == ids[1]  # "second"

    # Cursor from page1's last entry should get the remaining one
    page2 = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=2,
        before=page1[-1].received_at,
        before_id=page1[-1].id,
    )
    assert len(page2) == 1
    assert page2[0].id == ids[0]  # "first" (lowest id)


@pytest.mark.asyncio
async def test_conversation_key_isolates_messages(test_db):
    """Messages from different conversations don't leak into each other's pages."""
    other_key = "FF" * 16

    await MessageRepository.create(
        msg_type="CHAN",
        text="chan1",
        conversation_key=CHAN_KEY,
        sender_timestamp=100,
        received_at=100,
    )
    await MessageRepository.create(
        msg_type="CHAN",
        text="chan2",
        conversation_key=other_key,
        sender_timestamp=100,
        received_at=100,
    )

    result = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=50,
    )
    assert len(result) == 1
    assert result[0].text == "chan1"


@pytest.mark.asyncio
async def test_limit_respected(test_db):
    """Returned page never exceeds the requested limit."""
    for i in range(10):
        await MessageRepository.create(
            msg_type="CHAN",
            text=f"msg{i}",
            conversation_key=CHAN_KEY,
            sender_timestamp=100 + i,
            received_at=100 + i,
        )

    result = await MessageRepository.get_all(
        msg_type="CHAN",
        conversation_key=CHAN_KEY,
        limit=3,
    )
    assert len(result) == 3


@pytest.mark.asyncio
async def test_full_walk_collects_all_messages(test_db):
    """Walking through all pages collects every message exactly once."""
    total = 7
    for i in range(total):
        await MessageRepository.create(
            msg_type="CHAN",
            text=f"msg{i}",
            conversation_key=CHAN_KEY,
            sender_timestamp=100 + i,
            received_at=100 + i,
        )

    collected_ids: list[int] = []
    before = None
    before_id = None

    for _ in range(total):  # safety bound
        kwargs: dict = {
            "msg_type": "CHAN",
            "conversation_key": CHAN_KEY,
            "limit": 3,
        }
        if before is not None:
            kwargs["before"] = before
            kwargs["before_id"] = before_id
        else:
            kwargs["offset"] = 0

        page = await MessageRepository.get_all(**kwargs)
        if not page:
            break
        collected_ids.extend(m.id for m in page)
        before = page[-1].received_at
        before_id = page[-1].id

    assert len(collected_ids) == total
    assert len(set(collected_ids)) == total  # no duplicates


@pytest.mark.asyncio
async def test_filtered_cursor_pages_cover_exactly_the_kept_rows(test_db):
    """Walking `before`/`before_id` with muted/membership/keys never skips or repeats a kept
    row, whatever the page size: the cursor cuts after the filter, over equal timestamps."""
    now = 1_000
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO channels (key, name, muted, muted_until) VALUES (?, 'm', 1, NULL)",
            (CHAN_KEY,),
        )
        await conn.execute(
            "INSERT INTO channels (key, name, muted, muted_until) VALUES (?, 'e', 1, ?)",
            ("EE" * 16, now - 1),
        )
    kept = set()
    for i in range(30):
        key, msg_type = [(CHAN_KEY, "CHAN"), ("EE" * 16, "CHAN"), (DM_KEY, "PRIV")][i % 3]
        msg_id = await MessageRepository.create(
            msg_type=msg_type,
            text=f"m{i}",
            conversation_key=key,
            sender_timestamp=i // 4,
            received_at=100 + i // 4,  # four rows share each second
        )
        if key != CHAN_KEY:
            kept.add(msg_id)
    for size in (1, 2, 3, 7, 50):
        seen: list[int] = []
        before = before_id = None
        while True:
            page = await MessageRepository.get_all(
                limit=size,
                muted="exclude",
                membership="adopted",
                now=now,
                before=before,
                before_id=before_id,
            )
            if not page:
                break
            seen.extend(m.id for m in page)
            before, before_id = page[-1].received_at, page[-1].id
        assert len(seen) == len(set(seen)), size
        assert set(seen) == kept, size
