"""What an instance may do and read: its effective permissions and the read filters.

- **Effective permissions.** A permission is effective only when the manifest
  declares it **and** the user granted it (`plugins.granted_permissions`), and its
  parameters are the narrower of the two: `messages.read` `channels: all` and
  `direct`/`outgoing` need both, `messages.send` `targets` is the lower of
  `reply` < `scope` < `any` and the quotas the smaller, `http` the hosts both list.
  Parameters that do not validate drop the permission (fail closed).
- **Reads.** `message_visible()` decides whether an `event.message` reaches an
  instance: `messages.read` first (`channels: public` = the Public channel and the
  `#` channels whose key derives from their name, `direct`, `outgoing`), then the
  user's scope (`plugins.scope`, the fanout `ScopeSelector` shape). The other hooks
  are gated by `raw_packets.read`, `contacts.read` and `telemetry.read`; a raw
  packet keeps its decryption only when the instance could read that message.

Rules: docs/dev/plugins.md, "Capabilities".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.channel_constants import is_public_channel_key
from app.data.meshcore_channels import is_public_hashtag_channel
from app.plugins.permissions import Permissions

_TARGET_RANK = {"reply": 0, "scope": 1, "any": 2}


def _parse(name: str, params: Any) -> dict[str, Any] | None:
    """One permission's parameters as the registry validates them, None when invalid."""
    try:
        parsed = Permissions.model_validate({name: params})
    except ValidationError:
        return None
    return parsed.declared().get(name)


def _narrow(name: str, declared: dict[str, Any], granted: dict[str, Any]) -> dict[str, Any]:
    if name == "messages.read":
        return {
            "channels": "all"
            if declared.get("channels") == "all" and granted.get("channels") == "all"
            else "public",
            "direct": bool(declared.get("direct") and granted.get("direct")),
            "outgoing": bool(declared.get("outgoing") and granted.get("outgoing")),
        }
    if name == "messages.send":
        targets = min(
            declared.get("targets", "reply"),
            granted.get("targets", "reply"),
            key=_TARGET_RANK.__getitem__,
        )
        result: dict[str, Any] = {"targets": targets}
        for key in ("per_hour", "per_day"):
            values = [v for v in (declared.get(key), granted.get(key)) if v is not None]
            if values:
                result[key] = min(values)
        return result
    if name == "http":
        # Public hosts and LAN devices narrow the same way; an empty list is left out,
        # like `HttpParams` serialises it.
        result = {}
        for key in ("hosts", "lan_hosts"):
            allowed = set(declared.get(key, []))
            kept = [h for h in granted.get(key, []) if h in allowed]
            if kept:
                result[key] = kept
        return result
    return {}


def effective_permissions(
    manifest: Mapping[str, Any] | None, granted: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """The permissions an instance holds: declared and granted, narrowed."""
    declared = (manifest or {}).get("permissions")
    if not isinstance(declared, Mapping) or not isinstance(granted, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, granted_params in granted.items():
        if not isinstance(name, str) or name not in declared:
            continue
        mine = _parse(name, granted_params)
        theirs = _parse(name, declared[name])
        if mine is None or theirs is None:
            continue
        narrowed = _narrow(name, theirs, mine)
        if name == "http" and not narrowed:
            continue
        result[name] = narrowed
    return result


# --- reads -----------------------------------------------------------------------


def _matches_filter(filter_value: Any, key: str) -> bool:
    """The fanout scope filter (`app/fanout/manager.py`), keys compared case-insensitively."""
    from app.fanout.manager import _matches_filter as fanout_matches

    key = key.upper()
    if isinstance(filter_value, list):
        filter_value = [k.upper() for k in filter_value if isinstance(k, str)]
    elif isinstance(filter_value, dict) and isinstance(filter_value.get("except"), list):
        filter_value = {"except": [k.upper() for k in filter_value["except"] if isinstance(k, str)]}
    return fanout_matches(filter_value, key)


def scope_allows(scope: Mapping[str, Any] | None, *, is_dm: bool, key: str) -> bool:
    """The user's restriction of the instance (`plugins.scope`). No `messages` entry
    means no restriction; otherwise the fanout shapes: `all`, `none`,
    `{"channels": ..., "contacts": ...}`."""
    messages = (scope or {}).get("messages", "all")
    if messages == "all":
        return True
    if isinstance(messages, Mapping):
        return _matches_filter(messages.get("contacts" if is_dm else "channels", "none"), key)
    return False


def is_public_channel(key: str, name: str | None) -> bool:
    return is_public_channel_key(key) or is_public_hashtag_channel(name or "", key)


def conversation_readable(
    permissions: Mapping[str, Any],
    scope: Mapping[str, Any] | None,
    *,
    is_dm: bool,
    key: str,
    is_public: bool,
) -> bool:
    """Whether `messages.read` and the scope cover a conversation (outgoing aside)."""
    read = permissions.get("messages.read")
    if read is None or not key:
        return False
    if is_dm:
        if not read.get("direct"):
            return False
    elif read.get("channels") != "all" and not is_public:
        return False
    return scope_allows(scope, is_dm=is_dm, key=key)


def message_visible(
    permissions: Mapping[str, Any], scope: Mapping[str, Any] | None, params: Mapping[str, Any]
) -> bool:
    """Whether an `event.message` (`app/plugins/events.py` params) reaches the instance."""
    read = permissions.get("messages.read")
    if read is None:
        return False
    if params.get("is_outgoing") and not read.get("outgoing"):
        return False
    return conversation_readable(
        permissions,
        scope,
        is_dm=bool(params.get("is_dm")),
        key=str(params.get("conversation_key") or ""),
        is_public=bool(params.get("is_public_channel")),
    )


# The permission each fanout hook needs (`message` is filtered further per event).
HOOK_PERMISSIONS = {
    "message": "messages.read",
    "raw_packet": "raw_packets.read",
    "contact": "contacts.read",
    "telemetry": "telemetry.read",
    "health": "telemetry.read",
}


def hook_allowed(
    permissions: Mapping[str, Any], scope: Mapping[str, Any] | None, hook: str
) -> bool:
    """The permission gate of a fanout hook (`timer` events never come from fanout)."""
    needed = HOOK_PERMISSIONS.get(hook)
    if needed is None or needed not in permissions:
        return False
    if hook == "raw_packet":
        return (scope or {}).get("raw_packets", "all") != "none"
    return True


def raw_packet_for(
    permissions: Mapping[str, Any], scope: Mapping[str, Any] | None, data: dict[str, Any]
) -> dict[str, Any]:
    """A raw packet as the instance gets it: its decryption only when the instance
    could read that message, never a direct message it may not read."""
    from app.auth.permissions import DIRECT_MESSAGE_PAYLOAD_TYPE

    info = data.get("decrypted_info")
    if not isinstance(info, dict):
        return data
    channel_key = info.get("channel_key")
    if data.get("payload_type") == DIRECT_MESSAGE_PAYLOAD_TYPE or not channel_key:
        readable = conversation_readable(
            permissions, scope, is_dm=True, key=str(info.get("contact_key") or ""), is_public=False
        )
    else:
        readable = conversation_readable(
            permissions,
            scope,
            is_dm=False,
            key=str(channel_key),
            is_public=is_public_channel(str(channel_key), info.get("channel_name")),
        )
    return data if readable else {**data, "decrypted": False, "decrypted_info": None}
