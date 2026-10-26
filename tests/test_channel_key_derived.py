"""``Channel.key_derived`` (MSHL-246, MSHL-385): the key is derivable from the name.

A hashtag channel renamed locally without its ``#`` keeps a key anyone can derive.
"""

from __future__ import annotations

import hashlib

import pytest

from app.channel_constants import PUBLIC_CHANNEL_KEY, PUBLIC_CHANNEL_NAME
from app.data.meshcore_channels import (
    channel_key_is_name_derived,
    hashtag_key_from_name,
    is_public_hashtag_channel,
)
from app.models import Channel
from app.repository import ChannelRepository

DERIVED = hashtag_key_from_name("#meshcore").hex().upper()
OWN_SECRET = "00112233445566778899AABBCCDDEEFF"
# The key of the hashtag "##double" (a name that itself starts with '#').
DOUBLE = hashlib.sha256(b"##double").digest()[:16].hex().upper()


@pytest.mark.parametrize(
    ("name", "key", "derived"),
    [
        ("#meshcore", DERIVED, True),
        ("#meshcore", DERIVED.lower(), True),
        ("#meshcore", OWN_SECRET, False),  # a '#' name on a channel with its own secret
        ("Team", OWN_SECRET, False),
        ("meshcore", DERIVED, True),  # '#meshcore' renamed locally without its '#'
        ("meshcore", DERIVED.lower(), True),
        ("Meshcore", DERIVED, False),  # derivation hashes the exact name
        ("other", DERIVED, False),
        ("#double", DOUBLE, True),  # '#' + name, when the name starts with '#'
        ("#double", hashtag_key_from_name("#double").hex(), True),
        ("double", DOUBLE, False),
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


async def test_a_renamed_hashtag_channel_stays_derived_in_the_api(test_db, client):
    await ChannelRepository.upsert(DERIVED, "meshcore")
    response = await client.get("/api/channels")
    assert response.status_code == 200
    by_key = {row["key"]: row["key_derived"] for row in response.json()}
    assert by_key[DERIVED] is True


@pytest.mark.parametrize("key_hex", ["", "zz", "ABCD", DERIVED + "00"])
def test_a_malformed_key_is_not_derived(key_hex):
    assert channel_key_is_name_derived("meshcore", key_hex) is False


def test_hashtag_publishing_still_needs_the_hash_name():
    """``is_public_hashtag_channel`` decides what reaches Community: unchanged."""
    assert is_public_hashtag_channel("#meshcore", DERIVED) is True
    assert is_public_hashtag_channel("meshcore", DERIVED) is False


def test_key_derived_is_never_an_input():
    channel = Channel.model_validate({"key": OWN_SECRET, "name": "#x", "key_derived": True})
    assert channel.key_derived is False
