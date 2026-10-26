"""``Channel.key_derived`` (MSHL-246): the key is the one derived from the ``#`` name."""

from __future__ import annotations

import pytest

from app.channel_constants import PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME
from app.data.meshcore_channels import hashtag_key_from_name
from app.models import Channel
from app.repository import ChannelRepository

DERIVED = hashtag_key_from_name("#meshcore").hex().upper()
OWN_SECRET = "00112233445566778899AABBCCDDEEFF"


@pytest.mark.parametrize(
    ("name", "key", "derived"),
    [
        ("#meshcore", DERIVED, True),
        ("#meshcore", DERIVED.lower(), True),
        ("#meshcore", OWN_SECRET, False),  # a '#' name on a channel with its own secret
        ("Team", OWN_SECRET, False),
        (PUBLIC_CHANNEL_NAME, PUBLIC_CHANNEL_KEY, False),  # well known, but not name-derived
    ],
)
def test_key_derived_is_computed(name, key, derived):
    channel = Channel(key=key, name=name)
    assert channel.key_derived is derived
    assert channel.model_dump()["key_derived"] is derived


async def test_channels_api_carries_key_derived(test_db, client):
    await ChannelRepository.upsert(DERIVED, "#meshcore")
    await ChannelRepository.upsert(OWN_SECRET, "#hidden")
    response = await client.get("/api/channels")
    assert response.status_code == 200
    by_key = {row["key"]: row["key_derived"] for row in response.json()}
    assert by_key[DERIVED] is True
    assert by_key[OWN_SECRET] is False


def test_key_derived_is_never_an_input():
    channel = Channel.model_validate({"key": OWN_SECRET, "name": "#x", "key_derived": True})
    assert channel.key_derived is False
