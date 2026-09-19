"""Pending/adopted channel membership and the refused-channel denylist."""

from hashlib import sha256
from unittest.mock import patch

import pytest

from app.channel_constants import PUBLIC_CHANNEL_KEY
from app.data.meshcore_channels import hashtag_key_from_name
from app.repository import AppSettingsRepository, ChannelRepository
from app.services.channel_membership import MEMBERSHIP_PENDING
from app.services.hashtag_catalogue import run_catalogue_pass
from tests.test_hashtag_catalogue import _group_text_packet, _store_unknown


def _hashtag_key(name: str) -> str:
    return sha256(f"#{name}".encode()).digest()[:16].hex().upper()


class TestCataloguePendingAndReject:
    @pytest.mark.asyncio
    async def test_catalogue_skips_rejected_key(self, test_db):
        key = hashtag_key_from_name("fr")
        raw = _group_text_packet(key, 1_700_000_000, "Alice: hello")
        await _store_unknown(raw, timestamp=1_700_000_000)
        await AppSettingsRepository.add_rejected_channel(key.hex().upper(), "#fr")

        with (
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_event"),
            patch("app.push.manager.push_manager.dispatch_event"),
        ):
            opened = await run_catalogue_pass()

        assert opened == []
        assert await ChannelRepository.get_by_key(key.hex().upper()) is None

    @pytest.mark.asyncio
    async def test_catalogue_does_not_downgrade_adopted(self, test_db):
        key = hashtag_key_from_name("fr")
        key_hex = key.hex().upper()
        raw = _group_text_packet(key, 1_700_000_000, "Alice: hello")
        await _store_unknown(raw, timestamp=1_700_000_000)
        await ChannelRepository.upsert(key_hex, "#fr", is_hashtag=True)

        with (
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_event"),
            patch("app.push.manager.push_manager.dispatch_event"),
        ):
            opened = await run_catalogue_pass()

        assert opened == []
        stored = await ChannelRepository.get_by_key(key_hex)
        assert stored is not None
        assert stored.membership == "adopted"


class TestAdoptRefuseApi:
    @pytest.mark.asyncio
    async def test_create_unrejects_and_adopts(self, test_db, client):
        key = _hashtag_key("fr")
        await AppSettingsRepository.add_rejected_channel(key, "#fr")

        response = await client.post("/api/channels", json={"name": "#fr"})
        assert response.status_code == 200
        assert response.json()["membership"] == "adopted"
        assert await AppSettingsRepository.find_rejected_channel(key) is None

    @pytest.mark.asyncio
    async def test_bulk_adopts_existing_pending(self, test_db, client):
        key = _hashtag_key("flightless")
        await ChannelRepository.upsert(
            key, "#flightless", is_hashtag=True, membership=MEMBERSHIP_PENDING
        )

        response = await client.post(
            "/api/channels/bulk-hashtag",
            json={"channel_names": ["flightless"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["existing_count"] == 0
        assert len(data["created_channels"]) == 1
        assert data["created_channels"][0]["membership"] == "adopted"
        stored = await ChannelRepository.get_by_key(key)
        assert stored is not None
        assert stored.membership == "adopted"

    @pytest.mark.asyncio
    async def test_adopt_pending_and_rejected(self, test_db, client):
        pending_key = "AA" * 16
        await ChannelRepository.upsert(
            pending_key, "#pending", is_hashtag=True, membership=MEMBERSHIP_PENDING
        )
        adopted = await client.post(f"/api/channels/{pending_key}/adopt")
        assert adopted.status_code == 200
        assert adopted.json()["membership"] == "adopted"

        refused_key = "BB" * 16
        await AppSettingsRepository.add_rejected_channel(refused_key, "#refused")
        restored = await client.post(f"/api/channels/{refused_key}/adopt")
        assert restored.status_code == 200
        assert restored.json()["name"] == "#refused"
        assert restored.json()["membership"] == "adopted"
        assert await AppSettingsRepository.find_rejected_channel(refused_key) is None

    @pytest.mark.asyncio
    async def test_refuse_lists_and_blocks_public(self, test_db, client):
        key = "CC" * 16
        await ChannelRepository.upsert(key, "#nope", is_hashtag=True, membership=MEMBERSHIP_PENDING)

        refused = await client.post(f"/api/channels/{key}/refuse")
        assert refused.status_code == 200
        assert await ChannelRepository.get_by_key(key) is None

        listed = await client.get("/api/channels/rejected")
        assert listed.status_code == 200
        assert listed.json() == [{"key": key, "name": "#nope"}]

        public = await client.post(f"/api/channels/{PUBLIC_CHANNEL_KEY}/refuse")
        assert public.status_code == 400

        adopted_key = "99" * 16
        await ChannelRepository.upsert(adopted_key, "#kept", is_hashtag=True)
        adopted_refuse = await client.post(f"/api/channels/{adopted_key}/refuse")
        assert adopted_refuse.status_code == 400
        assert await ChannelRepository.get_by_key(adopted_key) is not None

    @pytest.mark.asyncio
    async def test_send_pending_is_conflict(self, test_db, client):
        key = "DD" * 16
        await ChannelRepository.upsert(
            key, "#pending-send", is_hashtag=True, membership=MEMBERSHIP_PENDING
        )
        response = await client.post(
            "/api/messages/channel",
            json={"channel_key": key, "text": "hello"},
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_mark_all_read_skips_pending(self, test_db, client):
        pending_key = "EE" * 16
        adopted_key = "FF" * 16
        await ChannelRepository.upsert(
            pending_key, "#pending-read", is_hashtag=True, membership=MEMBERSHIP_PENDING
        )
        await ChannelRepository.upsert(adopted_key, "#adopted-read", is_hashtag=True)

        response = await client.post("/api/read-state/mark-all-read")
        assert response.status_code == 200

        pending = await ChannelRepository.get_by_key(pending_key)
        adopted = await ChannelRepository.get_by_key(adopted_key)
        assert pending is not None
        assert adopted is not None
        assert pending.last_read_at is None
        assert adopted.last_read_at is not None
