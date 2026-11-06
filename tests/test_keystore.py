"""Tests for the ephemeral keystore module.

Verifies private key storage, validation, public key derivation,
and the export_and_store_private_key flow with various radio responses.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from meshcore import EventType

from app.keystore import (
    clear_keys,
    export_and_store_private_key,
    get_private_key,
    get_public_key,
    has_private_key,
    set_private_key,
)


@pytest.fixture(autouse=True)
def reset_keystore():
    """Reset keystore state before each test."""
    import app.keystore as ks

    ks.clear_keys()
    yield
    ks.clear_keys()


def _make_valid_private_key() -> bytes:
    """Create a valid 64-byte MeshCore private key for testing.

    The first 32 bytes are a clamped Ed25519 scalar,
    the last 32 bytes are the signing prefix.
    """
    # A clamped scalar: clear bottom 3 bits, set bit 254, clear bit 255
    scalar = bytearray(b"\x01" * 32)
    scalar[0] &= 0xF8  # Clear bottom 3 bits
    scalar[31] &= 0x7F  # Clear top bit
    scalar[31] |= 0x40  # Set bit 254
    prefix = b"\x02" * 32
    return bytes(scalar) + prefix


VALID_KEY = _make_valid_private_key()


class TestSetPrivateKey:
    """Test set_private_key validation and storage."""

    def test_stores_key_and_derives_public_key(self):
        """Valid 64-byte key is stored and public key is derived."""
        set_private_key(VALID_KEY)

        assert get_private_key() == VALID_KEY
        pub = get_public_key()
        assert pub is not None
        assert len(pub) == 32
        assert has_private_key() is True

    def test_rejects_wrong_length(self):
        """Keys that aren't 64 bytes are rejected."""
        with pytest.raises(ValueError, match="64 bytes"):
            set_private_key(b"\x00" * 32)

    def test_rejects_empty_key(self):
        """Empty key is rejected."""
        with pytest.raises(ValueError, match="64 bytes"):
            set_private_key(b"")

    def test_overwrites_previous_key(self):
        """Setting a new key replaces the old one."""
        set_private_key(VALID_KEY)
        pub1 = get_public_key()

        # Create a different valid key
        other_key = bytearray(VALID_KEY)
        other_key[1] = 0x42  # Change a byte in the scalar
        other_key = bytes(other_key)

        set_private_key(other_key)
        pub2 = get_public_key()

        assert get_private_key() == other_key
        assert pub1 != pub2


class TestClearKeys:
    """Test clearing in-memory key material."""

    def test_clear_keys_removes_private_and_public_key(self):
        set_private_key(VALID_KEY)

        clear_keys()

        assert get_private_key() is None
        assert get_public_key() is None
        assert has_private_key() is False


class TestExportAndStorePrivateKey:
    """Test the export_and_store_private_key flow with various radio responses."""

    @pytest.mark.asyncio
    async def test_success_stores_key(self):
        """Successful export stores the key in the keystore."""
        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.PRIVATE_KEY
        mock_result.payload = {"private_key": VALID_KEY}
        mock_mc.commands.export_private_key = AsyncMock(return_value=mock_result)

        result = await export_and_store_private_key(mock_mc)

        assert result is True
        assert has_private_key()
        assert get_private_key() == VALID_KEY

    @pytest.mark.asyncio
    async def test_disabled_returns_false(self):
        """DISABLED response returns False without storing."""
        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.DISABLED
        mock_result.payload = {}
        mock_mc.commands.export_private_key = AsyncMock(return_value=mock_result)

        result = await export_and_store_private_key(mock_mc)

        assert result is False
        assert not has_private_key()

    @pytest.mark.asyncio
    async def test_error_returns_false(self):
        """ERROR response returns False without storing."""
        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.ERROR
        mock_result.payload = {"error": "something went wrong"}
        mock_mc.commands.export_private_key = AsyncMock(return_value=mock_result)

        result = await export_and_store_private_key(mock_mc)

        assert result is False
        assert not has_private_key()

    @pytest.mark.asyncio
    async def test_no_event_received_raises_runtime_error(self):
        """no_event_received indicates command channel failure and should fail setup."""
        mock_mc = MagicMock()
        mock_result = MagicMock()
        mock_result.type = EventType.ERROR
        mock_result.payload = {"reason": "no_event_received"}
        mock_mc.commands.export_private_key = AsyncMock(return_value=mock_result)

        with pytest.raises(RuntimeError, match="cannot proceed"):
            await export_and_store_private_key(mock_mc)

        assert not has_private_key()

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        """Exception during export returns False without storing."""
        mock_mc = MagicMock()
        mock_mc.commands.export_private_key = AsyncMock(side_effect=Exception("Connection lost"))

        result = await export_and_store_private_key(mock_mc)

        assert result is False
        assert not has_private_key()


def _device_info(model: str) -> MagicMock:
    event = MagicMock()
    event.type = EventType.DEVICE_INFO
    event.payload = {"model": model}
    return event


def _refusing_mc(device_query: AsyncMock) -> MagicMock:
    refused = MagicMock()
    refused.type = EventType.DISABLED
    refused.payload = {}
    mc = MagicMock()
    mc.commands.export_private_key = AsyncMock(return_value=refused)
    mc.commands.send_device_query = device_query
    return mc


class TestWhyTheKeyIsMissing:
    """MSHL-471: the proxy announces itself in DEVICE_INFO; nothing else is guessed."""

    @pytest.mark.asyncio
    async def test_a_meshloom_proxy_is_named_and_the_firmware_is_not_blamed(self, caplog):
        from app.keystore import radio_key_unavailable_code

        mc = _refusing_mc(AsyncMock(return_value=_device_info("MeshloomProxy/0123456789ab")))
        assert await export_and_store_private_key(mc) is False
        assert radio_key_unavailable_code() == "radio_key_behind_proxy"
        assert "radio proxy" in caplog.text
        assert "firmware" not in caplog.text.lower()

    @pytest.mark.asyncio
    async def test_a_real_model_keeps_both_possible_causes(self, caplog):
        from app.keystore import radio_key_unavailable_code

        mc = _refusing_mc(AsyncMock(return_value=_device_info("Heltec V3")))
        await export_and_store_private_key(mc)
        assert radio_key_unavailable_code() == "radio_key_export_disabled"
        assert "ENABLE_PRIVATE_KEY_EXPORT" in caplog.text
        assert "proxy" in caplog.text

    @pytest.mark.asyncio
    async def test_a_host_like_model_says_nothing(self):
        from app.keystore import radio_key_unavailable_code

        mc = _refusing_mc(AsyncMock(return_value=_device_info("proxy.example.org")))
        await export_and_store_private_key(mc)
        assert radio_key_unavailable_code() == "radio_key_export_disabled"

    @pytest.mark.asyncio
    async def test_no_device_info_claims_no_cause(self):
        from app.keystore import last_export_disabled, radio_key_unavailable_code

        mc = _refusing_mc(AsyncMock(side_effect=OSError("link lost")))
        assert await export_and_store_private_key(mc) is False
        assert last_export_disabled() is True
        assert radio_key_unavailable_code() == "radio_key_unavailable"

    def test_never_asked_is_the_generic_code(self):
        from app.keystore import radio_key_unavailable_code

        assert radio_key_unavailable_code() == "radio_key_unavailable"

    @pytest.mark.asyncio
    async def test_a_later_good_export_and_a_disconnect_forget_the_cause(self):
        from app.keystore import radio_key_unavailable_code

        mc = _refusing_mc(AsyncMock(return_value=_device_info("MeshloomProxy/0123456789ab")))
        await export_and_store_private_key(mc)
        clear_keys()
        assert radio_key_unavailable_code() == "radio_key_unavailable"

        await export_and_store_private_key(mc)
        good = MagicMock()
        good.type = EventType.PRIVATE_KEY
        good.payload = {"private_key": VALID_KEY}
        mc.commands.export_private_key = AsyncMock(return_value=good)
        await export_and_store_private_key(mc)
        assert radio_key_unavailable_code() == "radio_key_unavailable"
