"""F3 OSS catalogue consume: bundled names, then Stats resolve."""

from __future__ import annotations

import json
import time
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.data import meshcore_channels
from app.data.meshcore_channels import (
    bundled_hashtag_names,
    channel_key_hash_byte,
    hashtag_key_from_name,
    packaged_snapshot_path,
    snapshot_path,
)
from app.decoder import encrypt_group_text, extract_payload
from app.packet_processor import process_raw_packet
from app.repository.channels import ChannelRepository
from app.repository.messages import MessageRepository
from app.routers.channels import CreateChannelRequest, create_channel
from app.services.hashtag_catalogue import run_catalogue_pass
from app.services.meshloom_community import update_community


def _group_text_packet(channel_key: bytes, timestamp: int, text: str) -> bytes:
    payload = encrypt_group_text(channel_key, timestamp, text, 0)
    return bytes([0x15, 0x00]) + payload


async def _store_unknown(raw: bytes, timestamp: int | None = None) -> None:
    received_at = timestamp if timestamp is not None else int(time.time())
    with (
        patch("app.packet_processor.broadcast_event"),
        patch("app.services.hashtag_catalogue.schedule_unknown_group_text_resolve"),
    ):
        result = await process_raw_packet(raw, timestamp=received_at)
    assert result is not None
    assert result.get("decrypted") is not True


class TestBundledListShare:
    def test_repo_snapshot_exists_and_names_are_non_empty(self):
        path = snapshot_path()
        assert path.is_file(), f"bundled snapshot missing: {path}"
        names = bundled_hashtag_names()
        assert names, "bundled_hashtag_names() must not be empty in a repo checkout"

    def test_python_list_matches_frontend_snapshot(self):
        path = snapshot_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["license"] == "CC0-1.0"
        assert list(bundled_hashtag_names()) == payload["names"]
        assert "fr" in bundled_hashtag_names()

    def test_packaged_snapshot_lives_next_to_loader(self):
        assert packaged_snapshot_path() == (
            Path(meshcore_channels.__file__).resolve().parent / "meshcoreChannels.snapshot.json"
        )

    def test_packaged_fallback_when_repo_snapshot_absent(self, tmp_path, monkeypatch):
        packaged = tmp_path / "meshcoreChannels.snapshot.json"
        packaged.write_text(
            json.dumps({"license": "CC0-1.0", "names": ["from-package"]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            meshcore_channels,
            "_REPO_SNAPSHOT",
            tmp_path / "frontend" / "src" / "data" / "meshcoreChannels.snapshot.json",
        )
        monkeypatch.setattr(meshcore_channels, "_PACKAGED_SNAPSHOT", packaged)
        meshcore_channels.bundled_hashtag_names.cache_clear()
        try:
            assert meshcore_channels.snapshot_path() == packaged
            assert meshcore_channels.bundled_hashtag_names() == ("from-package",)
        finally:
            meshcore_channels.bundled_hashtag_names.cache_clear()


class TestBundledUnlock:
    @pytest.mark.asyncio
    async def test_bundled_name_opens_unknown_group_text_without_stats(self, test_db):
        key = hashtag_key_from_name("fr")
        raw = _group_text_packet(key, int(time.time()), "Alice: hello")
        await _store_unknown(raw)

        with (
            patch("app.services.meshloom_community.stats_json", new=AsyncMock()) as stats,
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_event"),
            patch("app.push.manager.push_manager.dispatch_event", new=AsyncMock()) as dispatch,
        ):
            opened = await run_catalogue_pass()

        stats.assert_not_called()
        assert opened == ["fr"]
        stored = await ChannelRepository.get_by_key(key.hex().upper())
        assert stored is not None
        assert stored.name == "#fr"
        assert stored.is_hashtag is True
        assert stored.membership == "pending"
        messages = await MessageRepository.get_all(
            msg_type="CHAN", conversation_key=key.hex().upper(), limit=10
        )
        assert len(messages) == 1
        assert dispatch.await_count == 1
        assert dispatch.await_args.args[0]["event"] == "channel_found"


class TestCommunityResolve:
    @pytest.mark.asyncio
    async def test_resolve_lys_name_unlocks_bod_and_body_is_hash_bytes_only(self, test_db):
        name = "lys-published-xyz"
        key = hashtag_key_from_name(name)
        hb = channel_key_hash_byte(key)
        raw = _group_text_packet(key, int(time.time()), "Bob: from lys")
        await _store_unknown(raw)
        await update_community(enabled=True, iata="BOD")

        calls: list[tuple[str, str, object]] = []

        async def fake_stats(method: str, path: str, **kwargs):
            body = kwargs.get("json_body")
            calls.append((method, path, body))
            if method == "POST" and path == "/v1/hashtags/resolve":
                return {"hashtags": [{"name": name, "hash_byte": hb}]}
            return {"hashtags": []}

        with (
            patch(
                "app.services.meshloom_community.stats_json", new=AsyncMock(side_effect=fake_stats)
            ),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_event"),
            patch("app.push.manager.push_manager.dispatch_event", new=AsyncMock()) as dispatch,
        ):
            opened = await run_catalogue_pass()

        assert opened == [name]
        resolve_calls = [c for c in calls if c[0] == "POST" and c[1] == "/v1/hashtags/resolve"]
        assert len(resolve_calls) == 1
        body = resolve_calls[0][2]
        assert body == {"hash_bytes": [hb]}
        assert set(body.keys()) == {"hash_bytes"}
        packet_hex = raw.hex()
        assert packet_hex not in json.dumps(body)
        assert "ciphertext" not in json.dumps(body)
        assert not any(path.startswith("/v1/iata/") for _method, path, _body in calls)
        assert not any(path == "/v1/hashtags/samples" for _method, path, _body in calls)

        stored = await ChannelRepository.get_by_key(key.hex().upper())
        assert stored is not None
        assert stored.name == f"#{name}"
        assert stored.is_hashtag is True
        assert stored.membership == "pending"
        assert dispatch.await_count == 1
        assert dispatch.await_args.args[0]["event"] == "channel_found"
        assert dispatch.await_args.args[0]["name"] == f"#{name}"

        with (
            patch(
                "app.services.meshloom_community.stats_json", new=AsyncMock(side_effect=fake_stats)
            ),
            patch("app.websocket.broadcast_success"),
            patch("app.websocket.broadcast_event"),
            patch(
                "app.push.manager.push_manager.dispatch_event", new=AsyncMock()
            ) as dispatch_again,
        ):
            opened_again = await run_catalogue_pass()

        assert opened_again == []
        assert dispatch_again.await_count == 0

    @pytest.mark.asyncio
    async def test_community_off_skips_resolve_http(self, test_db):
        name = "lys-published-xyz"
        key = hashtag_key_from_name(name)
        raw = _group_text_packet(key, int(time.time()), "Bob: still locked")
        await _store_unknown(raw)

        with patch("app.services.meshloom_community.stats_json", new=AsyncMock()) as stats:
            opened = await run_catalogue_pass()

        stats.assert_not_called()
        assert opened == []
        assert await ChannelRepository.get_by_key(key.hex().upper()) is None

    @pytest.mark.asyncio
    async def test_community_on_without_iata_skips_sample_upload(self, test_db, monkeypatch):
        monkeypatch.delenv("MESHLOOM_COMMUNITY_IATA", raising=False)
        name = "lys-published-xyz"
        key = hashtag_key_from_name(name)
        raw = _group_text_packet(key, int(time.time()), "Bob: still locked")
        await _store_unknown(raw)
        await update_community(enabled=True, iata="")

        with patch("app.services.meshloom_community.stats_json", new=AsyncMock()) as stats:
            opened = await run_catalogue_pass()

        stats.assert_not_called()
        assert opened == []


class TestSampleQueueUpload:
    @pytest.mark.asyncio
    async def test_resolve_name_skips_sample_even_when_mac_fails(self, test_db):
        name = "lys-published-xyz"
        key = hashtag_key_from_name(name)
        hb = channel_key_hash_byte(key)
        raw = _group_text_packet(key, int(time.time()), "Bob: from lys")
        await _store_unknown(raw)
        await update_community(enabled=True, iata="BOD")

        calls: list[tuple[str, str, object]] = []

        async def fake_stats(method: str, path: str, **kwargs):
            calls.append((method, path, kwargs.get("json_body")))
            if method == "POST" and path == "/v1/hashtags/resolve":
                return {"hashtags": [{"name": "wrong-name-no-mac", "hash_byte": hb}]}
            return {"stored": True}

        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(side_effect=fake_stats)
        ):
            opened = await run_catalogue_pass()

        assert opened == []
        assert not any(path == "/v1/hashtags/samples" for _method, path, _body in calls)
        assert await ChannelRepository.get_by_key(key.hex().upper()) is None

    @pytest.mark.asyncio
    async def test_unknown_after_resolve_uploads_payload_once(self, test_db):
        name = "unknown-sample-queue-xyz"
        key = hashtag_key_from_name(name)
        hb = channel_key_hash_byte(key)
        raw = _group_text_packet(key, int(time.time()), "Bob: still locked")
        await _store_unknown(raw)
        await update_community(enabled=True, iata="BOD")

        calls: list[tuple[str, str, object]] = []

        async def fake_stats(method: str, path: str, **kwargs):
            calls.append((method, path, kwargs.get("json_body")))
            if method == "POST" and path == "/v1/hashtags/resolve":
                return {"hashtags": []}
            return {"stored": True}

        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(side_effect=fake_stats)
        ):
            opened = await run_catalogue_pass()
            opened_again = await run_catalogue_pass()

        assert opened == []
        assert opened_again == []
        resolve_calls = [c for c in calls if c[0] == "POST" and c[1] == "/v1/hashtags/resolve"]
        sample_calls = [c for c in calls if c[0] == "POST" and c[1] == "/v1/hashtags/samples"]
        assert len(resolve_calls) >= 1
        assert len(sample_calls) == 1
        assert calls.index(resolve_calls[0]) < calls.index(sample_calls[0])
        body = sample_calls[0][2]
        payload = extract_payload(raw)
        assert payload is not None
        expected_hex = payload.hex()
        assert body == {"hash_byte": hb, "payload_hex": expected_hex}
        assert expected_hex[:2] == hb
        assert raw.hex() != expected_hex
        dumped = json.dumps(body)
        assert raw.hex() not in dumped
        assert key.hex() not in dumped
        assert key.hex().lower() not in dumped
        assert "still locked" not in dumped

    @pytest.mark.asyncio
    async def test_sample_stats_error_does_not_raise(self, test_db):
        name = "unknown-sample-queue-xyz"
        key = hashtag_key_from_name(name)
        raw = _group_text_packet(key, int(time.time()), "Bob: still locked")
        await _store_unknown(raw)
        await update_community(enabled=True, iata="BOD")

        calls: list[tuple[str, str]] = []

        async def fake_stats(method: str, path: str, **kwargs):
            calls.append((method, path))
            if path == "/v1/hashtags/samples":
                raise RuntimeError("stats down")
            return {"hashtags": []}

        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(side_effect=fake_stats)
        ):
            opened = await run_catalogue_pass()
            opened_again = await run_catalogue_pass()

        assert opened == []
        assert opened_again == []
        sample_calls = [c for c in calls if c == ("POST", "/v1/hashtags/samples")]
        assert len(sample_calls) == 2

    @pytest.mark.asyncio
    async def test_sample_quota_does_not_retry_same_hour(self, test_db):
        name = "unknown-sample-quota-xyz"
        key = hashtag_key_from_name(name)
        raw = _group_text_packet(key, int(time.time()), "Bob: still locked")
        await _store_unknown(raw)
        await update_community(enabled=True, iata="BOD")

        calls: list[tuple[str, str]] = []

        async def fake_stats(method: str, path: str, **kwargs):
            calls.append((method, path))
            if path == "/v1/hashtags/samples":
                raise HTTPException(status_code=429, detail="Stats hashtag sample quota reached")
            return {"hashtags": []}

        with patch(
            "app.services.meshloom_community.stats_json", new=AsyncMock(side_effect=fake_stats)
        ):
            opened = await run_catalogue_pass()
            opened_again = await run_catalogue_pass()

        assert opened == []
        assert opened_again == []
        sample_calls = [c for c in calls if c == ("POST", "/v1/hashtags/samples")]
        assert len(sample_calls) == 1


class TestCreateDoesNotNotify:
    @pytest.mark.asyncio
    async def test_create_hashtag_does_not_fire_channel_found(self, test_db):
        await update_community(enabled=True, iata="LYS")
        with (
            patch(
                "app.services.meshloom_community.stats_json",
                new=AsyncMock(return_value={"hashtags": []}),
            ),
            patch("app.push.manager.push_manager.dispatch_event", new=AsyncMock()) as dispatch,
            patch("app.routers.channels.broadcast_event"),
        ):
            await create_channel(CreateChannelRequest(name="#fr"))

        assert dispatch.await_count == 0
        key = sha256(b"#fr").digest()[:16].hex().upper()
        stored = await ChannelRepository.get_by_key(key)
        assert stored is not None
        assert stored.is_hashtag is True
        assert stored.membership == "adopted"
