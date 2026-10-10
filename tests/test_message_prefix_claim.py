"""Tests for prefix-claiming DM messages."""

import pytest

from app.repository import ContactRepository, MessageRepository


@pytest.mark.asyncio
async def test_claim_prefix_promotes_dm_to_full_key(test_db):
    full_key = "a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"
    prefix = full_key[:6].upper()

    # Create the contact so the claim safety check (exactly 1 contact matches prefix) passes
    await ContactRepository.upsert({"public_key": full_key, "name": "Test"})

    msg_id = await MessageRepository.create(
        msg_type="PRIV",
        text="hello",
        conversation_key=prefix,
        sender_timestamp=123,
        received_at=123,
    )
    assert msg_id is not None

    updated = await MessageRepository.claim_prefix_messages(full_key)
    assert updated == 1

    messages = await MessageRepository.get_all(
        msg_type="PRIV",
        conversation_key=full_key,
        limit=10,
    )
    assert len(messages) == 1
    assert messages[0].conversation_key == full_key.lower()


@pytest.mark.asyncio
async def test_claim_prefix_only_touches_true_prefixes(test_db):
    full_key = "a1b2c3d3ba9f5fa8705b9845fe11cc6f01d1d49caaf4d122ac7121663c5beec7"
    other_full = "ffee" + full_key[4:]
    await ContactRepository.upsert({"public_key": full_key, "name": "Test"})
    await ContactRepository.upsert({"public_key": other_full, "name": "Other"})

    for i, key in enumerate([full_key[:4], full_key[:12].upper(), "a1b2ff", other_full[:8]]):
        await MessageRepository.create(
            msg_type="PRIV", text=f"m{i}", conversation_key=key, received_at=100 + i
        )

    assert await MessageRepository.claim_prefix_messages(full_key) == 2
    claimed = await MessageRepository.get_all(msg_type="PRIV", conversation_key=full_key)
    assert sorted(m.text for m in claimed) == ["m0", "m1"]
