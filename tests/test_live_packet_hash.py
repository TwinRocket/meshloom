"""#live hash8 must follow firmware SHA-256, not the frontend djb2 decoder hash."""

from unittest.mock import patch

import pytest

from app.path_utils import calculate_packet_hash


@pytest.mark.asyncio
async def test_raw_packet_broadcast_includes_firmware_packet_hash(
    test_db, captured_broadcasts
):
    from app.packet_processor import process_raw_packet

    raw = bytes([0x11, 0x00, 0xDE, 0xAD])
    expected = calculate_packet_hash(raw)
    assert expected == "19D68FE91E75C7DE"

    broadcasts, mock_broadcast = captured_broadcasts
    with patch("app.packet_processor.broadcast_event", mock_broadcast):
        await process_raw_packet(raw, timestamp=1_700_000_000)

    raw_events = [item for item in broadcasts if item["type"] == "raw_packet"]
    assert raw_events
    payload = raw_events[0]["data"]
    assert payload["packet_hash"] == expected
    assert payload["packet_hash"][:8].lower() == "19d68fe9"
