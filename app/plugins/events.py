"""The parameters of `event.message`, built from the fanout message dict.

The SDK (`meshloom_plugin.sdk.Message.from_params`) reads the fields of the
4.18.x bots' call, plus `conversation_key`, `is_public_channel` and
`received_at` (docs/dev/plugins.md, "SDK"). The derivation is the bots' one
(the in-app bot runtime was removed by the 5.0.0 switch, MSHL-192).
"""

from typing import Any

from app.channel_constants import is_public_channel_key
from app.data.meshcore_channels import is_public_hashtag_channel


def _derive_path_bytes_per_hop(paths: object, path_value: str | None) -> int | None:
    """Derive hop width from the first serialized message path when possible."""
    if not isinstance(path_value, str) or not path_value:
        return None
    if not isinstance(paths, list) or not paths:
        return None
    first_path = paths[0]
    if not isinstance(first_path, dict):
        return None
    path_hops = first_path.get("path_len")
    if not isinstance(path_hops, int) or path_hops <= 0:
        return None
    path_hex_chars = len(path_value)
    if path_hex_chars % 2 != 0:
        return None
    path_bytes = path_hex_chars // 2
    if path_bytes % path_hops != 0:
        return None
    hop_width = path_bytes // path_hops
    if hop_width not in (1, 2, 3):
        return None
    return hop_width


async def message_event_params(data: dict[str, Any]) -> dict[str, Any]:
    is_dm = data.get("type") == "PRIV"
    conversation_key = data.get("conversation_key") or ""
    is_outgoing = bool(data.get("outgoing", False))
    text = data.get("text") or ""
    channel_key: str | None = None
    channel_name: str | None = None
    if is_dm:
        sender_key = data.get("sender_key") or conversation_key
        sender_name = None if is_outgoing else data.get("sender_name")
        if sender_name is None and not is_outgoing:
            from app.repository import ContactRepository

            contact = await ContactRepository.get_by_key(conversation_key)
            sender_name = contact.name if contact else None
        message_text = text
    else:
        sender_key = None
        sender_name = data.get("sender_name")
        channel_key = conversation_key
        channel_name = data.get("channel_name")
        if channel_name is None:
            from app.repository import ChannelRepository

            channel = await ChannelRepository.get_by_key(conversation_key)
            channel_name = channel.name if channel else None
        prefix = f"{sender_name}: "
        message_text = text[len(prefix) :] if sender_name and text.startswith(prefix) else text

    path_value = data.get("path")
    paths = data.get("paths")
    if path_value is None and isinstance(paths, list) and paths and isinstance(paths[0], dict):
        path_value = paths[0].get("path")
    is_public_channel = bool(
        channel_key
        and (
            is_public_channel_key(channel_key)
            or is_public_hashtag_channel(channel_name or "", channel_key)
        )
    )
    return {
        "sender_name": sender_name,
        "sender_key": sender_key,
        "message_text": message_text,
        "is_dm": is_dm,
        "channel_key": channel_key,
        "channel_name": channel_name,
        "sender_timestamp": data.get("sender_timestamp"),
        "path": path_value,
        "path_bytes_per_hop": _derive_path_bytes_per_hop(paths, path_value),
        "packet_hash": data.get("packet_hash"),
        "region": data.get("region"),
        "scoped": data.get("transport_code") is not None,
        "is_outgoing": is_outgoing,
        "conversation_key": conversation_key,
        "is_public_channel": is_public_channel,
        "received_at": data.get("received_at"),
    }
