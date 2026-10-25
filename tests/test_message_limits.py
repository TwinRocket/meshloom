"""Server-side message byte limit: helper, every send path, and composer parity."""

import re
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app import message_limits
from app.message_limits import (
    CHANNEL_HARD_LIMIT,
    DM_HARD_LIMIT,
    channel_text_allowance,
    ensure_channel_text_fits,
    ensure_direct_text_fits,
)
from app.models import SendChannelMessageRequest, SendDirectMessageRequest
from app.radio import radio_manager
from app.repository import ChannelRepository, ContactRepository, MessageRepository
from app.routers.messages import (
    resend_channel_message,
    send_channel_message,
    send_direct_message,
)

COMPOSER = Path(__file__).resolve().parent.parent / "frontend/src/components/MessageInput.tsx"


def _mc(name: str | None = "Node") -> MagicMock:
    ok = MagicMock(type=EventType.MSG_SENT, payload={})
    mc = MagicMock()
    mc.self_info = {"name": name} if name is not None else {}
    mc.commands = MagicMock()
    for attr in ("set_flood_scope", "send", "send_msg", "send_chan_msg", "add_contact"):
        setattr(mc.commands, attr, AsyncMock(return_value=ok))
    mc.commands.set_channel = AsyncMock(return_value=ok)
    mc.commands.reset_path = AsyncMock(return_value=MagicMock(type=EventType.OK, payload={}))
    mc.get_contact_by_key_prefix = MagicMock(return_value=None)
    return mc


def _assert_too_long(exc: HTTPException, *, limit: int, size: int) -> None:
    assert exc.status_code == 400
    assert exc.detail["code"] == "message_too_long"
    assert exc.detail["params"] == {"limit": limit, "bytes": size}


class TestHelpers:
    def test_channel_allowance_formula(self):
        assert channel_text_allowance("Node") == CHANNEL_HARD_LIMIT - 4 - 2
        assert channel_text_allowance("é") == CHANNEL_HARD_LIMIT - 2 - 2  # bytes, not chars

    def test_channel_allowance_unknown_name_uses_ten_byte_fallback(self):
        expected = CHANNEL_HARD_LIMIT - 10 - 2
        assert channel_text_allowance(None) == expected
        assert channel_text_allowance("") == expected

    def test_channel_allowance_never_below_one(self):
        assert channel_text_allowance("x" * 400) == 1

    def test_dm_boundary(self):
        ensure_direct_text_fits("a" * 156)
        with pytest.raises(HTTPException) as exc:
            ensure_direct_text_fits("a" * 157)
        _assert_too_long(exc.value, limit=156, size=157)

    def test_multibyte_counts_bytes(self):
        ensure_direct_text_fits("é" * 78)  # 156 bytes
        with pytest.raises(HTTPException) as exc:
            ensure_direct_text_fits("é" * 79)  # 79 chars, 158 bytes
        _assert_too_long(exc.value, limit=156, size=158)

    def test_channel_boundary(self):
        limit = channel_text_allowance("Node")
        ensure_channel_text_fits("a" * limit, "Node")
        with pytest.raises(HTTPException) as exc:
            ensure_channel_text_fits("a" * (limit + 1), "Node")
        _assert_too_long(exc.value, limit=limit, size=limit + 1)


class TestSendPaths:
    @pytest.mark.asyncio
    async def test_dm_157_refused_156_accepted(self, test_db):
        pub_key = "ab" * 32
        await ContactRepository.upsert(
            {"public_key": pub_key, "name": "Alice", "type": 0, "flags": 0}
        )
        mc = _mc()
        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_direct_message(
                    SendDirectMessageRequest(destination=pub_key, text="a" * 157)
                )
            _assert_too_long(exc.value, limit=156, size=157)
            mc.commands.send_msg.assert_not_awaited()

            await send_direct_message(SendDirectMessageRequest(destination=pub_key, text="a" * 156))
            mc.commands.send_msg.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_over_limit_refused_and_nothing_stored(self, test_db):
        chan_key = "aa" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        mc = _mc("Node")
        limit = channel_text_allowance("Node")
        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key, text="a" * (limit + 1))
                )
            _assert_too_long(exc.value, limit=limit, size=limit + 1)
            mc.commands.send_chan_msg.assert_not_awaited()
            assert await MessageRepository.get_all(conversation_key=chan_key.upper()) == []

            await send_channel_message(
                SendChannelMessageRequest(channel_key=chan_key, text="a" * limit)
            )
            mc.commands.send_chan_msg.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_channel_unknown_name_uses_fallback(self, test_db):
        chan_key = "ab" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        mc = _mc(None)
        limit = channel_text_allowance(None)
        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
        ):
            with pytest.raises(HTTPException) as exc:
                await send_channel_message(
                    SendChannelMessageRequest(channel_key=chan_key, text="a" * (limit + 1))
                )
            _assert_too_long(exc.value, limit=limit, size=limit + 1)

    @pytest.mark.asyncio
    async def test_channel_multibyte_counts_bytes(self, test_db):
        chan_key = "ac" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        mc = _mc("Node")
        limit = channel_text_allowance("Node")  # 150
        text = "é" * (limit // 2 + 1)  # fewer characters than the limit, more bytes
        assert len(text) < limit < len(text.encode())
        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            pytest.raises(HTTPException) as exc,
        ):
            await send_channel_message(SendChannelMessageRequest(channel_key=chan_key, text=text))
        _assert_too_long(exc.value, limit=limit, size=len(text.encode()))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("new_timestamp", [False, True])
    async def test_resend_enforces_same_rule(self, test_db, new_timestamp):
        chan_key = "ad" * 16
        await ChannelRepository.upsert(key=chan_key, name="#general")
        mc = _mc("Node")
        limit = channel_text_allowance("Node")
        now = int(time.time()) - 5
        msg_id = await MessageRepository.create(
            msg_type="CHAN",
            text="Node: " + "a" * (limit + 1),
            conversation_key=chan_key.upper(),
            sender_timestamp=now,
            received_at=now,
            outgoing=True,
        )
        assert msg_id is not None
        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=mc),
            patch.object(radio_manager, "_meshcore", mc),
            pytest.raises(HTTPException) as exc,
        ):
            await resend_channel_message(msg_id, new_timestamp=new_timestamp)
        _assert_too_long(exc.value, limit=limit, size=limit + 1)
        mc.commands.send_chan_msg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_http_response_shape(self, client):
        pub_key = "cd" * 32
        with (
            patch("app.routers.messages.radio_manager.require_connected", return_value=MagicMock()),
            patch(
                "app.repository.ContactRepository.get_by_key_or_prefix",
                new=AsyncMock(return_value=MagicMock(public_key=pub_key)),
            ),
        ):
            response = await client.post(
                "/api/messages/direct", json={"destination": pub_key, "text": "a" * 157}
            )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "message_too_long"
        assert response.json()["detail"]["params"] == {"limit": 156, "bytes": 157}


class TestComposerParity:
    """The composer and the server must share the same constants."""

    @staticmethod
    def _tsx_constant(source: str, name: str) -> int:
        match = re.search(rf"\bconst\s+{name}\s*=\s*(\d+)\s*;", source)
        assert match, f"{name} not found in {COMPOSER}: the parity check cannot run"
        return int(match.group(1))

    def test_constants_match_composer(self):
        source = COMPOSER.read_text(encoding="utf-8")
        assert self._tsx_constant(source, "DM_HARD_LIMIT") == DM_HARD_LIMIT
        assert self._tsx_constant(source, "CHANNEL_HARD_LIMIT") == CHANNEL_HARD_LIMIT

    def test_unknown_name_fallback_matches_composer(self):
        source = COMPOSER.read_text(encoding="utf-8")
        assert (
            self._tsx_constant(source, "UNKNOWN_SENDER_NAME_BYTES")
            == message_limits.UNKNOWN_SENDER_NAME_BYTES
        )
        assert (
            self._tsx_constant(source, "CHANNEL_SENDER_SEPARATOR_BYTES")
            == message_limits.CHANNEL_SENDER_SEPARATOR_BYTES
        )
        assert "CHANNEL_HARD_LIMIT - nameByteLen - CHANNEL_SENDER_SEPARATOR_BYTES" in source

    def test_parity_fails_when_a_constant_is_missing(self):
        with pytest.raises(AssertionError, match="not found"):
            self._tsx_constant("const OTHER = 1;", "DM_HARD_LIMIT")


class TestRefusalLogging:
    @pytest.mark.asyncio
    async def test_bot_reply_too_long_logs_warning_without_traceback(self, caplog):
        from app.fanout import bot_exec

        with (
            patch("app.routers.messages.send_direct_message", new=AsyncMock()) as send,
            caplog.at_level("WARNING", logger=bot_exec.logger.name),
        ):
            send.side_effect = HTTPException(
                status_code=400,
                detail=message_limits.message_too_long_detail(limit=156, size=200),
            )
            await bot_exec._send_single_bot_message("a" * 200, True, "cd" * 32, None)
        records = [r for r in caplog.records if "too long" in r.getMessage()]
        assert records and records[0].levelname == "WARNING"
        assert records[0].exc_info is None
