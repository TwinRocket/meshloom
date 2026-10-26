"""Opaque conversation id of a channel for encrypted share uploads (MSHL-378).

The browser names the channel an encrypted file is sealed for by this id, never
by its key: the backend resolves it to the channel (``share_secrets``). It is
the first 96 bits of SHA-256 over a domain separator and the key bytes: stable,
local, and of no use to recover or use the key. 24 hex characters, so a 32-hex
channel key sent in its place never matches. Pure: no I/O, no app imports
(``models.Channel`` exposes it as ``share_conversation_id``).
"""

from __future__ import annotations

import hashlib
import re

CONVERSATION_ID_HEX = 24
CONVERSATION_ID_RE = re.compile(rf"[0-9a-f]{{{CONVERSATION_ID_HEX}}}")
_CHANNEL_KEY_RE = re.compile(r"[0-9a-fA-F]{32}")
_DOMAIN = b"meshloom-share-conversation-v1\x00"


def channel_conversation_id(channel_key_hex: str) -> str | None:
    """The conversation id of the channel with this key, None for a malformed key."""
    if not isinstance(channel_key_hex, str) or not _CHANNEL_KEY_RE.fullmatch(channel_key_hex):
        return None
    digest = hashlib.sha256(_DOMAIN + bytes.fromhex(channel_key_hex)).hexdigest()
    return digest[:CONVERSATION_ID_HEX]
