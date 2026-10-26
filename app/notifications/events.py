"""The notification event catalogue (contract version 1, ``app/notifications/AGENTS.md``).

One frozen dataclass per event. They share ``event`` (``domain.name``),
``event_id``, ``occurred_at``, ``dedup_key``, ``urgent``, ``conversation_key``
and a typed ``data``; the renderer (``render.py``) and the future dispatcher,
the Feed and the webhook v2 consume them without redefining anything.

Producers still hand over the legacy payload dicts (``{"event": "first_seen",
...}``): ``event_from_payload()`` converts one into its catalogue event and
```NotificationEvent.to_payload()`` converts back, so the two shapes stay in step.
These ids are notification ids, not WebSocket event types (``app/auth/ws_events.py``).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

CONTRACT_VERSION = 1

# Every id of the catalogue: contract version 1 is additive only.
EVENT_IDS: tuple[str, ...] = (
    "message.dm",
    "message.channel",
    "message.mention",
    "message.keyword",
    "contact.first_seen",
    "channel.found",
    "telemetry.alert",
    "radio.offline",
    "radio.online",
    "app.started",
    "app.update",
    "notify.destination_changed",
    "notify.channel_changed",
    "notify.test",
    "notify.channel_failed",
    "notify.heartbeat",
)

_DEDUP_LENGTH = 16


def dedup_hash(*parts: object) -> str:
    """A short hash of the parts: a dedup key never carries a full public key."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return digest[:_DEDUP_LENGTH]


# --- typed ``data`` -----------------------------------------------------------


@dataclass(frozen=True)
class MessageData:
    type: str  # "PRIV" or "CHAN"
    text: str = ""
    sender_name: str = ""
    channel_name: str = ""
    mention: bool = False
    keyword: str = ""  # the pattern of the rule that fired (message.keyword)
    keyword_id: str = ""


@dataclass(frozen=True)
class FirstSeenData:
    public_key: str = ""
    name: str = ""
    contact_type: int = 0


@dataclass(frozen=True)
class ChannelFoundData:
    name: str = ""
    channel_key: str = ""


@dataclass(frozen=True)
class TelemetryAlertData:
    rule_id: str = ""
    public_key: str = ""
    name: str = ""
    value: Any = None
    threshold: Any = None
    op: str = ""


@dataclass(frozen=True)
class RadioOfflineData:
    offline_s: Any = None
    connection: str = ""


@dataclass(frozen=True)
class RadioOnlineData:
    downtime_s: Any = None
    connection: str = ""


@dataclass(frozen=True)
class AppStartedData:
    version: str = ""
    connect_s: Any = None
    radio_connected: bool = False


@dataclass(frozen=True)
class AppUpdateData:
    current: str = ""
    latest: str = ""


@dataclass(frozen=True)
class DestinationChangedData:
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelChangedData:
    action: str = ""  # created | changed | enabled | disabled | deleted
    channel_type: str = ""
    name: str = ""


@dataclass(frozen=True)
class ChannelFailedData:
    channel_id: str = ""
    name: str = ""
    error: str = ""  # a short code (``failures.safe_error_code``), never a secret


@dataclass(frozen=True)
class HeartbeatData:
    instance: str = ""
    uptime_s: int = 0
    version: str = ""


@dataclass(frozen=True)
class TestData:
    pass


# --- events ---------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class NotificationEvent:
    """Common fields of every event. ``data`` is narrowed by each subclass."""

    event: str
    dedup_key: str
    data: Any
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: int = field(default_factory=lambda: int(time.time()))
    urgent: bool = False
    conversation_key: str | None = None

    version: ClassVar[int] = CONTRACT_VERSION

    def to_payload(self) -> dict[str, Any]:
        """The legacy payload dict the renderers and the producers share."""
        raise NotImplementedError


@dataclass(frozen=True, kw_only=True)
class MessageEvent(NotificationEvent):
    """``message.dm`` (``data.type == "PRIV"``) or ``message.channel``."""

    data: MessageData

    def to_payload(self) -> dict[str, Any]:
        d = self.data
        payload: dict[str, Any] = {
            "event": "message",
            "type": d.type,
            "conversation_key": self.conversation_key or "",
            "text": d.text,
            "sender_name": d.sender_name,
            "channel_name": d.channel_name,
        }
        if d.mention:
            payload["mention"] = True
        if d.keyword:
            payload["keyword"] = d.keyword
            payload["keyword_id"] = d.keyword_id
        return payload


@dataclass(frozen=True, kw_only=True)
class MentionEvent(NotificationEvent):
    """``message.mention``: a channel message that names the radio (``mention.py``); never urgent."""

    data: MessageData

    def to_payload(self) -> dict[str, Any]:
        return MessageEvent(
            event=self.event,
            dedup_key=self.dedup_key,
            data=self.data,
            conversation_key=self.conversation_key,
        ).to_payload() | {"mention": True}


@dataclass(frozen=True, kw_only=True)
class KeywordEvent(NotificationEvent):
    """``message.keyword``: a message that holds a keyword rule's pattern (``keywords.py``).
    Never urgent: anyone in range can forge it."""

    data: MessageData

    def to_payload(self) -> dict[str, Any]:
        return MessageEvent(
            event=self.event,
            dedup_key=self.dedup_key,
            data=self.data,
            conversation_key=self.conversation_key,
        ).to_payload()


@dataclass(frozen=True, kw_only=True)
class FirstSeenEvent(NotificationEvent):
    data: FirstSeenData

    def to_payload(self) -> dict[str, Any]:
        d = self.data
        return {
            "event": "first_seen",
            "public_key": d.public_key,
            "name": d.name,
            "contact_type": d.contact_type,
        }


@dataclass(frozen=True, kw_only=True)
class ChannelFoundEvent(NotificationEvent):
    data: ChannelFoundData

    def to_payload(self) -> dict[str, Any]:
        return {
            "event": "channel_found",
            "name": self.data.name,
            "channel_key": self.data.channel_key,
        }


@dataclass(frozen=True, kw_only=True)
class TelemetryAlertEvent(NotificationEvent):
    data: TelemetryAlertData

    def to_payload(self) -> dict[str, Any]:
        d = self.data
        return {
            "event": "telemetry_alert",
            "rule_id": d.rule_id,
            "public_key": d.public_key,
            "name": d.name,
            "value": d.value,
            "threshold": d.threshold,
            "op": d.op,
            "urgent": self.urgent,
        }


@dataclass(frozen=True, kw_only=True)
class RadioOfflineEvent(NotificationEvent):
    data: RadioOfflineData

    def to_payload(self) -> dict[str, Any]:
        return {
            "event": "radio_offline",
            "offline_s": self.data.offline_s,
            "connection": self.data.connection,
            "urgent": self.urgent,
        }


@dataclass(frozen=True, kw_only=True)
class RadioOnlineEvent(NotificationEvent):
    data: RadioOnlineData

    def to_payload(self) -> dict[str, Any]:
        return {
            "event": "radio_online",
            "downtime_s": self.data.downtime_s,
            "connection": self.data.connection,
        }


@dataclass(frozen=True, kw_only=True)
class AppStartedEvent(NotificationEvent):
    data: AppStartedData

    def to_payload(self) -> dict[str, Any]:
        d = self.data
        return {
            "event": "app_started",
            "version": d.version,
            "connect_s": d.connect_s,
            "radio_connected": d.radio_connected,
        }


@dataclass(frozen=True, kw_only=True)
class AppUpdateEvent(NotificationEvent):
    data: AppUpdateData

    def to_payload(self) -> dict[str, Any]:
        return {"event": "oss_update", "current": self.data.current, "latest": self.data.latest}


@dataclass(frozen=True, kw_only=True)
class DestinationChangedEvent(NotificationEvent):
    data: DestinationChangedData

    def to_payload(self) -> dict[str, Any]:
        from app.notifications.render import DESTINATION_CHANGED_EVENT

        return {"event": DESTINATION_CHANGED_EVENT, "kinds": list(self.data.kinds)}


@dataclass(frozen=True, kw_only=True)
class ChannelChangedEvent(NotificationEvent):
    """``notify.channel_changed``: proof of modification of a channel (never its secrets)."""

    data: ChannelChangedData

    def to_payload(self) -> dict[str, Any]:
        from app.notifications.render import CHANNEL_CHANGED_EVENT

        return {
            "event": CHANNEL_CHANGED_EVENT,
            "action": self.data.action,
            "channel_type": self.data.channel_type,
            "name": self.data.name,
        }


@dataclass(frozen=True, kw_only=True)
class ChannelFailedEvent(NotificationEvent):
    """``notify.channel_failed``: a channel keeps failing, or a push service unsubscribed a
    browser (``app/notifications/failures.py``). Never sent to the failing channel."""

    data: ChannelFailedData

    def to_payload(self) -> dict[str, Any]:
        from app.notifications.render import CHANNEL_FAILED_EVENT

        return {
            "event": CHANNEL_FAILED_EVENT,
            "channel_id": self.data.channel_id,
            "name": self.data.name,
            "error": self.data.error,
        }


@dataclass(frozen=True, kw_only=True)
class HeartbeatEvent(NotificationEvent):
    """``notify.heartbeat``: the periodic "still running" message of a channel with a heartbeat
    (MSHL-131, ``app/notifications/heartbeat.py``). Sent to that channel only, outside the
    rules, the pauses, quiet hours and the buckets."""

    data: HeartbeatData

    def to_payload(self) -> dict[str, Any]:
        from app.notifications.render import HEARTBEAT_EVENT

        return {
            "event": HEARTBEAT_EVENT,
            "instance": self.data.instance,
            "uptime_s": self.data.uptime_s,
            "version": self.data.version,
        }


@dataclass(frozen=True, kw_only=True)
class TestEvent(NotificationEvent):
    """``notify.test``: the "send a test" button of a destination."""

    data: TestData

    __test__ = False  # not a pytest class

    def to_payload(self) -> dict[str, Any]:
        from app.notify import TEST_ALERT_PAYLOAD

        return dict(TEST_ALERT_PAYLOAD)


# --- legacy payload -> catalogue event ---------------------------------------------


def _s(value: object) -> str:
    return "" if value is None else str(value)


def event_from_payload(
    payload: Mapping[str, Any],
    *,
    event_id: str | None = None,
    occurred_at: int | None = None,
) -> NotificationEvent | None:
    """The catalogue event for a legacy payload dict, or None when it has no id.

    The grouped summaries (``notifications_grouped``, ``first_seen_grouped``) are
    produced by the dispatcher, not events of the catalogue.
    """
    common: dict[str, Any] = {}
    if event_id is not None:
        common["event_id"] = event_id
    if occurred_at is not None:
        common["occurred_at"] = occurred_at
    kind = payload.get("event")

    if kind in (None, "message") and payload.get("type") in ("PRIV", "CHAN"):
        msg_type = str(payload["type"])
        key = _s(payload.get("conversation_key"))
        data = MessageData(
            type=msg_type,
            text=_s(payload.get("text")),
            sender_name=_s(payload.get("sender_name")),
            channel_name=_s(payload.get("channel_name")),
            mention=bool(payload.get("mention")),
            keyword=_s(payload.get("keyword")),
            keyword_id=_s(payload.get("keyword_id")),
        )
        identity = (
            payload.get("id"),
            key,
            payload.get("sender_timestamp"),
            data.text,
        )
        if data.keyword and not data.mention:
            return KeywordEvent(
                event="message.keyword",
                # One event per (rule, message): the dispatcher dedups it per channel.
                dedup_key=dedup_hash("message.keyword", data.keyword_id, *identity),
                conversation_key=key,
                data=data,
                **common,
            )
        if data.mention:
            return MentionEvent(
                event="message.mention",
                dedup_key=dedup_hash("message.mention", *identity),
                conversation_key=key,
                data=data,
                **common,
            )
        name = "message.dm" if msg_type == "PRIV" else "message.channel"
        return MessageEvent(
            event=name,
            dedup_key=dedup_hash(name, *identity),
            conversation_key=key,
            data=data,
            **common,
        )
    if kind == "first_seen":
        pubkey = _s(payload.get("public_key"))
        return FirstSeenEvent(
            event="contact.first_seen",
            dedup_key=dedup_hash("contact.first_seen", pubkey),
            data=FirstSeenData(
                public_key=pubkey,
                name=_s(payload.get("name")),
                contact_type=int(payload.get("contact_type") or payload.get("type") or 0),
            ),
            **common,
        )
    if kind == "channel_found":
        channel_key = _s(payload.get("channel_key"))
        return ChannelFoundEvent(
            event="channel.found",
            dedup_key=dedup_hash("channel.found", channel_key),
            conversation_key=channel_key or None,
            data=ChannelFoundData(name=_s(payload.get("name")), channel_key=channel_key),
            **common,
        )
    if kind == "telemetry_alert":
        pubkey = _s(payload.get("public_key"))
        rule_id = _s(payload.get("rule_id"))
        return TelemetryAlertEvent(
            event="telemetry.alert",
            dedup_key=dedup_hash("telemetry.alert", rule_id, pubkey),
            urgent=True,
            data=TelemetryAlertData(
                rule_id=rule_id,
                public_key=pubkey,
                name=_s(payload.get("name")),
                value=payload.get("value"),
                threshold=payload.get("threshold"),
                op=_s(payload.get("op")),
            ),
            **common,
        )
    if kind == "radio_offline":
        return RadioOfflineEvent(
            event="radio.offline",
            dedup_key=dedup_hash("radio.offline", payload.get("outage_started_at")),
            urgent=True,
            data=RadioOfflineData(
                offline_s=payload.get("offline_s"), connection=_s(payload.get("connection"))
            ),
            **common,
        )
    if kind == "radio_online":
        return RadioOnlineEvent(
            event="radio.online",
            dedup_key=dedup_hash("radio.online", payload.get("outage_started_at")),
            data=RadioOnlineData(
                downtime_s=payload.get("downtime_s"), connection=_s(payload.get("connection"))
            ),
            **common,
        )
    if kind == "app_started":
        return AppStartedEvent(
            event="app.started",
            dedup_key=dedup_hash("app.started", payload.get("started_at")),
            data=AppStartedData(
                version=_s(payload.get("version")),
                connect_s=payload.get("connect_s"),
                radio_connected=bool(payload.get("radio_connected")),
            ),
            **common,
        )
    if kind == "oss_update":
        latest = _s(payload.get("latest"))
        return AppUpdateEvent(
            event="app.update",
            dedup_key=dedup_hash("app.update", latest),
            data=AppUpdateData(current=_s(payload.get("current")), latest=latest),
            **common,
        )
    from app.notifications.render import (
        CHANNEL_CHANGED_EVENT,
        CHANNEL_FAILED_EVENT,
        DESTINATION_CHANGED_EVENT,
        HEARTBEAT_EVENT,
    )

    if kind == HEARTBEAT_EVENT:
        try:
            uptime = max(0, int(payload.get("uptime_s") or 0))
        except (TypeError, ValueError):
            uptime = 0
        return HeartbeatEvent(
            event="notify.heartbeat",
            dedup_key=dedup_hash("notify.heartbeat", uuid.uuid4().hex),
            data=HeartbeatData(
                instance=_s(payload.get("instance")),
                uptime_s=uptime,
                version=_s(payload.get("version")),
            ),
            **common,
        )

    if kind == CHANNEL_FAILED_EVENT:
        channel_id = _s(payload.get("channel_id"))
        return ChannelFailedEvent(
            event="notify.channel_failed",
            dedup_key=dedup_hash("notify.channel_failed", channel_id, uuid.uuid4().hex),
            data=ChannelFailedData(
                channel_id=channel_id,
                name=_s(payload.get("name")),
                error=_s(payload.get("error")),
            ),
            **common,
        )

    if kind == CHANNEL_CHANGED_EVENT:
        return ChannelChangedEvent(
            event="notify.channel_changed",
            dedup_key=dedup_hash(
                "notify.channel_changed",
                payload.get("action"),
                payload.get("name"),
                uuid.uuid4().hex,
            ),
            urgent=True,
            data=ChannelChangedData(
                action=_s(payload.get("action")),
                channel_type=_s(payload.get("channel_type")),
                name=_s(payload.get("name")),
            ),
            **common,
        )
    if kind == DESTINATION_CHANGED_EVENT:
        kinds = tuple(str(k) for k in payload.get("kinds") or ())
        return DestinationChangedEvent(
            event="notify.destination_changed",
            dedup_key=dedup_hash("notify.destination_changed", *kinds, payload.get("at")),
            data=DestinationChangedData(kinds=kinds),
            **common,
        )
    return None
