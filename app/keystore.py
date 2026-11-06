"""
Ephemeral keystore for storing sensitive keys in memory, plus the Ed25519
signing primitive used by fanout modules that need to sign requests with the
radio's own key.

The private key is stored in memory only and is never persisted to disk.
It's exported from the radio on startup and reconnect, then used for
server-side decryption of direct messages. The export (0x0E) and import (0x18)
frames that carry it are redacted from every log line by ``app.log_redaction``.
"""

import hashlib
import logging
from typing import TYPE_CHECKING, Literal

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
# True when the radio link answered the last export attempt with DISABLED: firmware
# built without ENABLE_PRIVATE_KEY_EXPORT, or a proxy (a Meshloom radio proxy always
# does). The full backup then says "export_disabled".
_last_export_disabled: bool = False
# Why it refused, from the DEVICE_INFO asked right after the refusal; None when unknown.
_export_refused_by: Literal["meshloom_proxy", "radio_link"] | None = None

# Local API errors of a feature that needs the key (503, ``detail = {code, message}``),
# translated by the UI as ``errors.<code>``. Behind a Meshloom radio proxy the peer says
# so itself (its DEVICE_INFO model): no host name or address is ever read for this.
# ``radio_key_export_disabled`` is any other refusal, so it names both possible causes:
# a third-party proxy answers with the model of the radio behind it.
RADIO_KEY_ERRORS: dict[str, str] = {
    "radio_key_behind_proxy": (
        "This instance reaches the radio through a Meshloom radio proxy, which never gives "
        "the radio's key: connect the radio directly or use the instance that holds it"
    ),
    "radio_key_export_disabled": (
        "The radio's firmware or a proxy in between refused to give the radio's key"
    ),
    "radio_key_unavailable": "The radio's key is not available on this instance",
}


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
    global _private_key, _public_key, _last_export_disabled, _export_refused_by
    had_key = _private_key is not None or _public_key is not None
    _private_key = None
    _public_key = None
    _last_export_disabled = False
    _export_refused_by = None
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


def last_export_disabled() -> bool:
    """True when the radio link refused the last private key export (DISABLED)."""
    return _last_export_disabled


def radio_key_unavailable_code() -> str:
    """The ``RADIO_KEY_ERRORS`` code for a missing key: only what the radio link told us.

    ``radio_key_unavailable`` when no refusal is known (not connected yet, the export
    got no answer) or its cause could not be read.
    """
    if _last_export_disabled and _export_refused_by == "meshloom_proxy":
        return "radio_key_behind_proxy"
    if _last_export_disabled and _export_refused_by == "radio_link":
        return "radio_key_export_disabled"
    return "radio_key_unavailable"


async def _who_refused_the_export(mc: "MeshCore") -> Literal["meshloom_proxy", "radio_link"] | None:
    """Ask the peer what it is: a Meshloom radio proxy names itself in DEVICE_INFO."""
    from app.radio_proxy.protocol import parse_proxy_instance_id

    try:
        event = await mc.commands.send_device_query()
    except Exception as e:
        logger.debug("Device query after a refused key export failed: %s", e)
        return None
    if event.type != EventType.DEVICE_INFO or not isinstance(event.payload, dict):
        return None
    model = event.payload.get("model")
    return "meshloom_proxy" if parse_proxy_instance_id(model) else "radio_link"


def has_private_key() -> bool:
    """Check if a private key is stored.

    Returns:
        True if a private key is available
    """
    return _private_key is not None


async def export_and_store_private_key(mc: "MeshCore") -> bool:
    """Export private key from the radio and store it in the keystore.

    This should be called on startup and after each reconnect.

    Args:
        mc: Connected MeshCore instance

    Returns:
        True if the private key was successfully exported and stored
    """
    global _last_export_disabled, _export_refused_by
    logger.info("Exporting private key from radio...")
    try:
        result = await mc.commands.export_private_key()

        if result.type == EventType.PRIVATE_KEY:
            private_key_bytes = result.payload["private_key"]
            set_private_key(private_key_bytes)
            _last_export_disabled = False
            _export_refused_by = None
            return True
        elif result.type == EventType.DISABLED:
            _last_export_disabled = True
            _export_refused_by = await _who_refused_the_export(mc)
            if _export_refused_by == "meshloom_proxy":
                logger.warning(
                    "No private key from this radio: it is reached through a Meshloom radio "
                    "proxy, which never gives it. Server-side DM decryption, Community and the "
                    "features that need it stay off here. Connect the radio directly, or use "
                    "the Meshloom instance that holds the key."
                )
            else:
                logger.warning(
                    "No private key from this radio: the export was refused, by firmware built "
                    "without ENABLE_PRIVATE_KEY_EXPORT=1 or by a proxy in between. Server-side "
                    "DM decryption, Community and the features that need it stay off here."
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
