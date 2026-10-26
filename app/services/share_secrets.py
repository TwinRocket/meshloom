"""Conversation secrets of encrypted share files (MSHL-255). Local only, no egress.

The key of an encrypted file derives from the secret of the MeshCore conversation
it is shared in (meshloom-community docs/contracts/links-files.md, "Encrypted
envelope v1", "Which conversations"):

- a private channel: its 16-byte key (``Channel.key``);
- a direct message: the 32-byte X25519 shared secret of the radio private key
  and the contact's public key (``decoder.derive_shared_secret``).

The Public channel and name-derived ``#hashtag`` channels are public only (their
key can be computed by anyone, TwinRocket included): an encrypted upload there is
422 ``share_mode_not_allowed``. A room server is refused too: each member shares
a different secret with the room, so nobody else could decrypt the file. An
all-zero DM secret (low-order contact key) is refused before any key derivation.

No secret is supplied by the client either. An upload names a channel by its
``conversation_id`` (``Channel.share_conversation_id``: a truncated, domain
separated SHA-256 of the key, which cannot be turned back into it) and a contact
by its public key; ``upload_secret`` resolves the secret here (MSHL-378). On the
read side ``message_secret`` resolves it from the message row (``type``,
``conversation_key``) named by ``message_id``.

A secret returned here goes to ``share_crypto`` and nowhere else: never logged,
never returned by a route, never sent. Callers wrap its use in
``log_redaction.secret_scope`` as a last line of defence.
"""

from __future__ import annotations

import hmac
import logging
import re

from app import keystore
from app.channel_constants import PUBLIC_CHANNEL_KEY
from app.data.meshcore_channels import channel_key_is_name_derived
from app.decoder import derive_shared_secret
from app.models import CONTACT_TYPE_ROOM, Channel, Message
from app.repository import ChannelRepository, ContactRepository
from app.services.share import ShareError
from app.share_conversation import CONVERSATION_ID_RE, channel_conversation_id

logger = logging.getLogger(__name__)

CONVERSATION_TYPES = frozenset({"channel", "contact"})
CHANNEL_SECRET_BYTES = 16
DM_SECRET_BYTES = 32

_CHANNEL_KEY_RE = re.compile(r"[0-9a-fA-F]{32}")
_PUBLIC_KEY_RE = re.compile(r"[0-9a-fA-F]{64}")
_ALL_ZERO_DM = bytes(DM_SECRET_BYTES)


async def _channel_by_conversation_id(value: str) -> Channel | None:
    """The channel whose conversation id is ``value``, else None (constant-time compare)."""
    matches = [
        channel
        for channel in await ChannelRepository.get_all()
        if (candidate := channel_conversation_id(channel.key)) is not None
        and hmac.compare_digest(candidate, value)
    ]
    # Two channels sharing an id (a duplicate key row) are ambiguous: refuse
    # rather than seal under whichever came last.
    return matches[0] if len(matches) == 1 else None


def _channel_is_public(name: str, key_hex: str) -> bool:
    # A key derivable from the channel name, '#' or not (a hashtag channel renamed
    # locally to 'foo' keeps its public key): encrypting under it protects nothing.
    return key_hex.upper() == PUBLIC_CHANNEL_KEY or channel_key_is_name_derived(name, key_hex)


def _dm_secret(contact_key_hex: str) -> bytes | None:
    """The X25519 shared secret with a contact; None without the radio key.

    A key that is not a valid Ed25519 point, or a low-order one (all-zero
    secret), raises ``ValueError``.
    """
    private_key = keystore.get_private_key()
    if private_key is None:
        return None
    try:
        secret = bytes(derive_shared_secret(private_key, bytes.fromhex(contact_key_hex)))
    except Exception:  # noqa: BLE001 - libsodium refuses a bad point; no detail kept
        raise ValueError("unusable contact key") from None
    if len(secret) != DM_SECRET_BYTES or hmac.compare_digest(secret, _ALL_ZERO_DM):
        raise ValueError("unusable contact key")
    return secret


async def upload_secret(conversation_type: str, conversation: str) -> bytes:
    """The secret an encrypted upload to this conversation is sealed with.

    ``conversation_type`` and ``conversation`` come from the
    ``X-Share-Conversation-Type`` and ``X-Share-Conversation-Id`` headers: a
    channel's ``conversation_id`` (never its key) or a contact's public key.
    Malformed or unknown is 400 ``share_invalid_request`` (a raw channel key
    included); a public channel or
    hashtag, a room or an unusable contact key is 422 ``share_mode_not_allowed``;
    a DM before the radio private key was read is 503
    ``share_radio_key_unavailable``.
    """
    if conversation_type == "channel":
        if not CONVERSATION_ID_RE.fullmatch(conversation):
            raise ShareError("share_invalid_request")
        channel = await _channel_by_conversation_id(conversation)
        if channel is None:
            raise ShareError("share_invalid_request")
        if _channel_is_public(channel.name, channel.key):
            raise ShareError("share_mode_not_allowed")
        secret = bytes.fromhex(channel.key)
        if len(secret) != CHANNEL_SECRET_BYTES:
            raise ShareError("share_invalid_request")
        return secret
    if conversation_type == "contact":
        if not _PUBLIC_KEY_RE.fullmatch(conversation):
            raise ShareError("share_invalid_request")
        contact = await ContactRepository.get_by_key(conversation)
        if contact is None:
            raise ShareError("share_invalid_request")
        if contact.type == CONTACT_TYPE_ROOM:
            raise ShareError("share_mode_not_allowed")
        try:
            dm_secret = _dm_secret(contact.public_key)
        except ValueError:
            logger.warning("Encrypted share refused: unusable contact key")
            raise ShareError("share_mode_not_allowed") from None
        if dm_secret is None:
            raise ShareError("share_radio_key_unavailable")
        return dm_secret
    raise ShareError("share_invalid_request")


def message_secret(message: Message) -> bytes | None:
    """The secret of the conversation ``message`` belongs to, else None.

    A channel message: the channel key of its ``conversation_key``. A direct
    message: the shared secret with the contact of its ``conversation_key`` (the
    same for an incoming and an outgoing message). None when it cannot be
    resolved (no radio key, malformed or unusable key): the file is then
    ``share_undecryptable``.
    """
    key = message.conversation_key or ""
    if message.type == "CHAN":
        return bytes.fromhex(key) if _CHANNEL_KEY_RE.fullmatch(key) else None
    if message.type == "PRIV" and _PUBLIC_KEY_RE.fullmatch(key):
        try:
            return _dm_secret(key)
        except ValueError:
            return None
    return None
