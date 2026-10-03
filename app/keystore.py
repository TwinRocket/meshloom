"""
Ephemeral keystore for storing sensitive keys in memory, plus the Ed25519
signing primitive used by fanout modules that need to sign requests with the
radio's own key.

The private key is stored in memory only and is never persisted to disk.
It's exported from the radio on startup and reconnect, then used for
server-side decryption of direct messages.
"""

import hashlib
import logging
from typing import TYPE_CHECKING, Any

import nacl.bindings
from meshcore import EventType

from app.decoder import derive_public_key

if TYPE_CHECKING:
    from meshcore import MeshCore

logger = logging.getLogger(__name__)

NO_EVENT_RECEIVED_GUIDANCE = (
    "Radio command channel is unresponsive (no_event_received). Ensure that your firmware is not "
    "incompatible, outdated, or wrong-mode (e.g. repeater, not client), and that "
    "serial/TCP/BLE connectivity is successful (try another app and see if that one works?). The app cannot proceed because it cannot "
    "issue commands to the radio."
)

# Ed25519 group order (L) — used in the expanded signing primitive below
_L = 2**252 + 27742317777372353535851937790883648493

# In-memory storage for the private key and derived public key
_private_key: bytes | None = None
_public_key: bytes | None = None


def ed25519_sign_expanded(message: bytes, scalar: bytes, prefix: bytes, public_key: bytes) -> bytes:
    """Sign a message using MeshCore's expanded Ed25519 key format.

    MeshCore stores 64-byte keys as scalar(32) || prefix(32).  Standard
    Ed25519 libraries expect seed format and would re-SHA-512 the key, so we
    perform the signing manually using the already-expanded key material.

    Port of meshcore-packet-capture's ed25519_sign_with_expanded_key().
    """
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    R = nacl.bindings.crypto_scalarmult_ed25519_base_noclamp(r.to_bytes(32, "little"))
    k = int.from_bytes(hashlib.sha512(R + public_key + message).digest(), "little") % _L
    s = (r + k * int.from_bytes(scalar, "little")) % _L
    return R + s.to_bytes(32, "little")


def clear_keys() -> None:
    """Clear any stored private/public key material from memory."""
    global _private_key, _public_key
    had_key = _private_key is not None or _public_key is not None
    _private_key = None
    _public_key = None
    if had_key:
        logger.info("Cleared in-memory keystore")


def set_private_key(key: bytes) -> None:
    """Store the private key in memory and derive the public key.

    Args:
        key: 64-byte Ed25519 private key in MeshCore format
    """
    global _private_key, _public_key
    if len(key) != 64:
        raise ValueError(f"Private key must be 64 bytes, got {len(key)}")
    _private_key = key
    _public_key = derive_public_key(key)
    logger.info("Private key stored in keystore (public key: %s...)", _public_key.hex()[:12])


def get_private_key() -> bytes | None:
    """Get the stored private key.

    Returns:
        The 64-byte private key, or None if not set
    """
    return _private_key


def get_public_key() -> bytes | None:
    """Get the derived public key.

    Returns:
        The 32-byte public key derived from the private key, or None if not set
    """
    return _public_key


def has_private_key() -> bool:
    """Check if a private key is stored.

    Returns:
        True if a private key is available
    """
    return _private_key is not None


async def export_and_store_private_key(mc: "MeshCore", radio_instance: Any = None) -> bool:
    """Export private key from the radio and store it in the keystore.

    This should be called on startup and after each reconnect.

    Args:
        mc: Connected MeshCore instance
        radio_instance: Optional RadioInstance to store the key into

    Returns:
        True if the private key was successfully exported and stored
    """
    logger.info("Exporting private key from radio...")
    try:
        result = await mc.commands.export_private_key()

        if result.type == EventType.PRIVATE_KEY:
            private_key_bytes = result.payload["private_key"]
            if radio_instance is not None:
                radio_instance.set_private_key(private_key_bytes)
            if radio_instance is None or getattr(radio_instance, "radio_id", None) == "default":
                set_private_key(private_key_bytes)
            return True
        elif result.type == EventType.DISABLED:
            logger.warning(
                "Private key export disabled on radio firmware. "
                "Server-side DM decryption will not be available. "
                "Enable ENABLE_PRIVATE_KEY_EXPORT=1 in firmware to enable this feature."
            )
            return False
        else:
            reason = result.payload.get("reason") if isinstance(result.payload, dict) else None
            if result.type == EventType.ERROR and reason == "no_event_received":
                logger.error("%s Raw response: %s", NO_EVENT_RECEIVED_GUIDANCE, result.payload)
                raise RuntimeError(NO_EVENT_RECEIVED_GUIDANCE)
            logger.error("Failed to export private key: %s", result.payload)
            return False
    except RuntimeError:
        raise
    except Exception as e:
        logger.error("Error exporting private key: %s", e)
        return False
