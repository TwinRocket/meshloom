"""Built-in ``#meshloom-testing`` channel used by the radio self-test.

The test send stores no Channel and no Message row, so its packets stay
undecrypted forever. That is deliberate: the catalogue, the browser cracker and
the hashtag publish all have to look past them, which is what this module is
for. The channel only appears in chat when the user adds it by hand.

Every filter here verifies the full MAC. The one-byte channel hash is shared by
roughly 1/256 of the mesh, so filtering on it alone would hide other people's
traffic.
"""

from __future__ import annotations

from app.data.meshcore_channels import channel_key_hash_byte, hashtag_key_from_name
from app.decoder import try_decrypt_packet_with_channel_key

TEST_CHANNEL_NAME = "#meshloom-testing"
TEST_CHANNEL_KEY = hashtag_key_from_name(TEST_CHANNEL_NAME)
TEST_CHANNEL_KEY_HEX = TEST_CHANNEL_KEY.hex().upper()
TEST_CHANNEL_HASH_BYTE = channel_key_hash_byte(TEST_CHANNEL_KEY)

# Rows as returned by RawPacketRepository.get_undecrypted_group_text_samples
UndecryptedSampleRow = tuple[str, int, bytes, int, str]


def is_test_channel_key(key_hex: str) -> bool:
    return (key_hex or "").strip().upper() == TEST_CHANNEL_KEY_HEX


def is_test_channel_name(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    if not text.startswith("#"):
        text = f"#{text}"
    return text.casefold() == TEST_CHANNEL_NAME.casefold()


def is_test_channel_packet(raw_packet: bytes) -> bool:
    """True only when ``raw_packet`` is a GroupText that MACs against the test key."""
    if not raw_packet:
        return False
    decrypted = try_decrypt_packet_with_channel_key(raw_packet, TEST_CHANNEL_KEY)
    return decrypted is not None and decrypted.channel_hash == TEST_CHANNEL_HASH_BYTE


def drop_test_channel_samples(rows: list[UndecryptedSampleRow]) -> list[UndecryptedSampleRow]:
    """Remove test-channel packets from an undecrypted GroupText sample listing.

    The hash-byte comparison is only a cheap pre-filter; a packet is dropped
    solely on the MAC, so a foreign channel sharing the byte survives.
    """
    return [
        row
        for row in rows
        if not (row[0].lower() == TEST_CHANNEL_HASH_BYTE and is_test_channel_packet(row[2]))
    ]
