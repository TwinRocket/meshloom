"""Every WebSocket event type, with the permission a client needs to receive it.

This enumeration is the only list of event types (cadrage §4.2, MSHL-64):

- ``broadcast_event()``, ``ws_manager.broadcast*()``, ``send_personal()`` and
  the injected ``broadcast_fn`` take a ``WsEvent`` member, never a string
  (``tests/test_auth_route_coverage.py`` fails on a call site in ``app/`` that
  passes anything else);
- ``app/events.py`` validates each member's payload;
- ``frontend/src/wsEventTypes.ts`` is generated from it
  (``scripts/build/export_ws_events.py``; a test runs it with ``--check``).

A member declares its permission where it is defined, so no type can be added
without one. A type that is not a member reaches no client (fail closed,
``app/websocket.py``). On top of the type's permission, a ``message`` of type
``PRIV`` and a ``raw_packet`` decrypted from a direct message also need
``read_private`` (per-event content filter, ``app/websocket.py``).
"""

from __future__ import annotations

from enum import StrEnum

from app.auth.permissions import Permission


class WsEvent(StrEnum):
    permission: Permission

    def __new__(cls, value: str, permission: Permission) -> WsEvent:
        member = str.__new__(cls, value)
        member._value_ = value
        member.permission = permission
        return member

    HEALTH = "health", "read"
    # Channel messages for ``read``; direct messages also need ``read_private``.
    MESSAGE = "message", "read"
    CONTACT = "contact", "read"
    CONTACT_RESOLVED = "contact_resolved", "read"
    CHANNEL = "channel", "read"
    CONTACT_DELETED = "contact_deleted", "read"
    CHANNEL_DELETED = "channel_deleted", "read"
    # Decrypted direct messages are sent undecrypted without ``read_private``.
    RAW_PACKET = "raw_packet", "read"
    COMMUNITY_PACKET = "community_packet", "read"
    COMMUNITY_LIVE = "community_live", "read"
    MESSAGE_ACKED = "message_acked", "read"
    MESSAGE_DELETED = "message_deleted", "read"
    RADIO_WIFI = "radio_wifi", "admin"
    # Carries `last_error`: admins only. `health` keeps the statuses for
    # everyone but drops their `last_error` below admin
    # (`app/routers/health.py`, `HEALTH_DETAIL_PERMISSION`).
    PLUGIN_STATUS = "plugin_status", "admin"
    # Toasts: admin-only unless the caller names a lower ``audience``
    # (``app/websocket.py``, ``broadcast_error``/``broadcast_success``).
    ERROR = "error", "read"
    SUCCESS = "success", "read"


def ws_event(event_type: str) -> WsEvent | None:
    """The member for an event type, or None for a type that is not one."""
    if isinstance(event_type, WsEvent):
        return event_type
    try:
        return WsEvent(event_type)
    except ValueError:
        return None
