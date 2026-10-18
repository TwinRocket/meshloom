"""`channels.list` (permission `channels.read`): the configured channels as
`{key, name, is_public}`.

A channel key is its secret. Public channels (the Public channel and the `#`
channels whose key derives from their name) are always listed; a private
channel only when the instance may read every channel (`messages.read` with
`channels: all`). Pending channels are left out.
"""

from typing import Any

from app.plugins.capabilities.base import Call, capability
from app.plugins.capabilities.grants import is_public_channel


@capability("channels.list", "channels.read")
async def list_(call: Call) -> list[dict[str, Any]]:
    from app.repository import ChannelRepository
    from app.services.channel_membership import is_pending_channel

    read = call.instance.permissions.get("messages.read") or {}
    private_ok = read.get("channels") == "all"
    result = []
    for channel in await ChannelRepository.get_all():
        if is_pending_channel(channel):
            continue
        public = is_public_channel(channel.key, channel.name)
        if public or private_ok:
            result.append({"key": channel.key, "name": channel.name, "is_public": public})
    return result
