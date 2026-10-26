"""The webhook body v2 (MSHL-137): one Pydantic model per event, and their JSON Schema.

A webhook channel chooses its body with ``config.body_type``: ``v1`` (the 4.18.x payload,
``transports/webhook.py``) or ``v2``, built here from the catalogue event (``events.py``)::

    {"version": 2, "event": "contact.first_seen", "event_id": "...", "occurred_at": 1700000000,
     "dedup_key": "...", "urgent": false,
     "instance": {"name": "...", "radio_public_key": "..."},
     "data": {...typed per event...}, "rendered": {"title": "...", "body": "..."}}

``data`` never carries more than the v1 body of the same event, and less in two places: a
message's text is ``null`` when the channel's text policy (``include_message_text``) holds
it back, and no message carries its ``conversation_key`` (for a private channel that key
is the channel's secret). The dispatcher's summaries, which are not catalogue events, are
``summary.grouped``, ``summary.first_seen`` and ``summary.digest``.

``schema_text()`` is what ``docs/user/en/deep/notifications-webhook.schema.json`` holds; a
test regenerates it and fails on drift (``scripts/dev/gen_webhook_schema.py`` rewrites it).
The signature of a v2 body covers its timestamp: ``transports/webhook.py``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel, create_model

from app.notifications import events as ev
from app.notifications.render import (
    enrich_notification_payload,
    event_notification_text,
    message_text_is_public,
)

BODY_VERSION = 2
SCHEMA_ID = (
    "https://raw.githubusercontent.com/TwinRocket/meshloom/main/"
    "docs/user/en/deep/notifications-webhook.schema.json"
)

Number = int | float
Scalar = int | float | str | None


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Instance(_Model):
    """The Meshloom that sent the call: its radio's name and public key (null while no
    radio has answered since the start)."""

    name: str | None = None
    radio_public_key: str | None = None


class RenderedText(_Model):
    """The notification as a person reads it, in the channel's language and after its
    text policy: the title and body of the e-mail and ntfy channels."""

    title: str
    body: str


# --- data -----------------------------------------------------------------------------


class MessageData(_Model):
    type: Literal["PRIV", "CHAN"] = Field(description="PRIV: direct message; CHAN: channel.")
    text: str | None = Field(
        description="The message text; null when the channel's text policy holds it back."
    )
    sender_name: str = ""
    channel_name: str = Field("", description="Empty for a direct message.")
    mention: bool = False


class FirstSeenData(_Model):
    public_key: str
    name: str = ""
    contact_type: int = Field(0, description="1 companion, 2 repeater, 3 room, 4 sensor.")


class ChannelFoundData(_Model):
    name: str
    channel_key: str = Field(description="Derived from the # name, so public.")


class TelemetryAlertData(_Model):
    rule_id: str
    public_key: str = ""
    name: str = ""
    metric: str = ""
    unit: str = ""
    value: Scalar = None
    threshold: Scalar = None
    op: str = ""


class RadioOfflineData(_Model):
    offline_s: Number | None = None
    connection: str = ""


class RadioOnlineData(_Model):
    downtime_s: Number | None = None
    connection: str = ""


class AppStartedData(_Model):
    version: str = ""
    connect_s: Number | None = None
    radio_connected: bool = False


class AppUpdateData(_Model):
    current: str = ""
    latest: str = ""


class DestinationChangedData(_Model):
    kinds: list[str] = Field(default_factory=list)


class ChannelChangedData(_Model):
    action: Literal["created", "changed", "enabled", "disabled", "deleted", ""] = ""
    channel_type: str = ""
    name: str = ""


class ChannelFailedData(_Model):
    channel_id: str = ""
    name: str = ""
    error: str = ""


class TestData(_Model):
    pass


class GroupedData(_Model):
    """What the rate limiter held back during its window, counted (no text)."""

    count: int
    dms: int = 0
    private: int = 0
    others: int = 0
    channels: dict[str, int] = Field(
        default_factory=dict, description="Public and # channels: name to count (top three)."
    )


class FirstSeenGroupedData(_Model):
    count: int


class DigestData(_Model):
    """What quiet hours or a pause held, counted (no text)."""

    scope: str = Field(description="quiet_hours or pause.")
    count: int = Field(description="Notifications held.")
    conversations: int = 0
    items: list[tuple[str, int]] = Field(
        default_factory=list, description="Named conversations: [name, count], top three."
    )
    more: int = 0
    dms: int = 0
    private: int = 0
    contacts: int = 0
    others: int = 0


# --- bodies ---------------------------------------------------------------------------


class BodyBase(_Model):
    # ``event`` and ``data`` are narrowed per event by ``_body_model``; declared here so
    # the JSON keeps the documented order.
    version: Literal[2] = BODY_VERSION
    event: str
    event_id: str
    occurred_at: int = Field(description="Epoch seconds.")
    dedup_key: str = Field(description="Same on every retry; also the X-Webhook-Id header.")
    urgent: bool = False
    instance: Instance
    data: Any
    rendered: RenderedText


_EVENTS: tuple[tuple[str, type[_Model], str], ...] = (
    ("message.dm", MessageData, "A direct message."),
    ("message.channel", MessageData, "A channel message."),
    ("message.mention", MessageData, "A channel message that names the radio."),
    ("contact.first_seen", FirstSeenData, "A node heard for the first time."),
    ("channel.found", ChannelFoundData, "A # channel found active."),
    ("telemetry.alert", TelemetryAlertData, "A telemetry rule fired."),
    ("radio.offline", RadioOfflineData, "The radio stopped answering."),
    ("radio.online", RadioOnlineData, "The radio answers again."),
    ("app.started", AppStartedData, "Meshloom started."),
    ("app.update", AppUpdateData, "A new Meshloom version is out."),
    ("notify.destination_changed", DestinationChangedData, "A notification destination changed."),
    ("notify.channel_changed", ChannelChangedData, "A notification channel changed."),
    ("notify.test", TestData, "The test button of a channel."),
    (
        "notify.channel_failed",
        ChannelFailedData,
        "A channel keeps failing, or a push service unsubscribed a browser.",
    ),
    ("summary.grouped", GroupedData, "Notifications the rate limiter held back."),
    ("summary.first_seen", FirstSeenGroupedData, "First-seen nodes over the hourly cap."),
    ("summary.digest", DigestData, "What quiet hours or a pause held."),
)


def _body_model(event_id: str, data: type[_Model], doc: str) -> type[BodyBase]:
    name = "".join(p.capitalize() for p in event_id.replace(".", "_").split("_")) + "Body"
    model = create_model(
        name,
        __base__=BodyBase,
        __module__=__name__,
        event=(Literal[event_id], ...),  # type: ignore[valid-type]
        data=(data, ...),
    )
    model.__doc__ = doc
    return model


BODIES: dict[str, type[BodyBase]] = {e: _body_model(e, d, doc) for e, d, doc in _EVENTS}

# The dispatcher's summaries (not catalogue events), by their payload ``event``.
SUMMARY_EVENTS = {
    "notifications_grouped": "summary.grouped",
    "first_seen_grouped": "summary.first_seen",
    "quiet_digest": "summary.digest",
}


class WebhookBodyV2(RootModel[Any]):
    """A v2 webhook body: one model per ``event``."""

    root: Annotated[Union[tuple(BODIES.values())], Field(discriminator="event")]  # type: ignore[valid-type]  # noqa: UP007


def json_schema() -> dict[str, Any]:
    """The published JSON Schema of the v2 body (draft 2020-12, generated by Pydantic)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        **WebhookBodyV2.model_json_schema(),
        "title": "Meshloom notification webhook body, version 2",
    }


def schema_text() -> str:
    """``json_schema()`` as the committed file holds it."""
    return json.dumps(json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# --- building -------------------------------------------------------------------------


def _number(value: Any) -> Number | None:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _scalar(value: Any) -> Scalar:
    return value if isinstance(value, int | float | str) and not isinstance(value, bool) else None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _event_data(event: ev.NotificationEvent, payload: Mapping[str, Any], policy: str) -> _Model:
    d = event.data
    if isinstance(event, ev.MessageEvent | ev.MentionEvent):
        withheld = policy == "never" or (
            policy == "public_only" and not message_text_is_public(payload)
        )
        return MessageData(
            type="PRIV" if d.type == "PRIV" else "CHAN",
            text=None if withheld else d.text,
            sender_name=d.sender_name,
            channel_name=d.channel_name,
            mention=bool(d.mention) or isinstance(event, ev.MentionEvent),
        )
    if isinstance(event, ev.FirstSeenEvent):
        return FirstSeenData(
            public_key=d.public_key, name=d.name, contact_type=_int(d.contact_type)
        )
    if isinstance(event, ev.ChannelFoundEvent):
        return ChannelFoundData(name=d.name, channel_key=d.channel_key)
    if isinstance(event, ev.TelemetryAlertEvent):
        enriched = enrich_notification_payload(event.to_payload())
        return TelemetryAlertData(
            rule_id=d.rule_id,
            public_key=d.public_key,
            name=d.name,
            metric=str(enriched.get("metric") or ""),
            unit=str(enriched.get("unit") or ""),
            value=_scalar(d.value),
            threshold=_scalar(d.threshold),
            op=d.op,
        )
    if isinstance(event, ev.RadioOfflineEvent):
        return RadioOfflineData(offline_s=_number(d.offline_s), connection=d.connection)
    if isinstance(event, ev.RadioOnlineEvent):
        return RadioOnlineData(downtime_s=_number(d.downtime_s), connection=d.connection)
    if isinstance(event, ev.AppStartedEvent):
        return AppStartedData(
            version=d.version,
            connect_s=_number(d.connect_s),
            radio_connected=bool(d.radio_connected),
        )
    if isinstance(event, ev.AppUpdateEvent):
        return AppUpdateData(current=d.current, latest=d.latest)
    if isinstance(event, ev.DestinationChangedEvent):
        return DestinationChangedData(kinds=[str(k) for k in d.kinds])
    if isinstance(event, ev.ChannelChangedEvent):
        known = ("created", "changed", "enabled", "disabled", "deleted")
        return ChannelChangedData(
            action=d.action if d.action in known else "",
            channel_type=d.channel_type,
            name=d.name,
        )
    if isinstance(event, ev.ChannelFailedEvent):
        return ChannelFailedData(channel_id=d.channel_id, name=d.name, error=d.error)
    return TestData()


def _summary_data(name: str, payload: Mapping[str, Any]) -> _Model:
    if name == "summary.grouped":
        channels = payload.get("channels")
        return GroupedData(
            count=_int(payload.get("count")),
            dms=_int(payload.get("dms")),
            private=_int(payload.get("private")),
            others=_int(payload.get("others")),
            channels={str(k): _int(v) for k, v in channels.items()}
            if isinstance(channels, Mapping)
            else {},
        )
    if name == "summary.first_seen":
        return FirstSeenGroupedData(count=_int(payload.get("count")))
    items = payload.get("items")
    return DigestData(
        scope=str(payload.get("scope") or ""),
        count=_int(payload.get("count")),
        conversations=_int(payload.get("conversations")),
        items=[
            (str(i[0]), _int(i[1]))
            for i in (items if isinstance(items, list) else [])
            if isinstance(i, list | tuple) and len(i) == 2
        ],
        more=_int(payload.get("more")),
        dms=_int(payload.get("dms")),
        private=_int(payload.get("private")),
        contacts=_int(payload.get("contacts")),
        others=_int(payload.get("others")),
    )


def radio_instance() -> Instance:
    """The connected radio's name and public key, from its SELF_INFO (nulls without one)."""
    try:
        from app.services.radio_runtime import radio_runtime

        mc = radio_runtime.meshcore
        info = getattr(mc, "self_info", None) if mc is not None else None
    except Exception:
        info = None
    if not isinstance(info, Mapping):
        return Instance()
    name = info.get("name")
    key = info.get("public_key")
    return Instance(
        name=name if isinstance(name, str) and name else None,
        radio_public_key=key.lower() if isinstance(key, str) and key else None,
    )


def v2_event_name(payload: Mapping[str, Any], event: ev.NotificationEvent | None) -> str | None:
    """The v2 ``event`` of a delivery, or None when v2 has no model for it."""
    if event is not None:
        return event.event if event.event in BODIES else None
    return SUMMARY_EVENTS.get(str(payload.get("event") or ""))


def build_body(
    payload: Mapping[str, Any],
    event: ev.NotificationEvent | None,
    *,
    lang: str,
    text_policy: str,
    instance: Instance,
    now: float | None = None,
) -> BodyBase | None:
    """The v2 body of one delivery, or None for a payload v2 has no model for."""
    name = v2_event_name(payload, event)
    if name is None:
        return None
    title, text = event_notification_text(dict(payload), lang, include_message_text=text_policy)
    rendered = RenderedText(title=title, body=text)
    model = BODIES[name]
    if event is not None:
        return model(
            event=name,
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            dedup_key=event.dedup_key,
            urgent=event.urgent,
            instance=instance,
            data=_event_data(event, payload, text_policy),
            rendered=rendered,
        )
    data = _summary_data(name, payload)
    # A summary has no catalogue id: its content names it, so a retry keeps the same id.
    key = hashlib.sha256(f"{name}\x1f{data.model_dump_json()}".encode()).hexdigest()[:32]
    return model(
        event=name,
        event_id=key,
        occurred_at=int(time.time() if now is None else now),
        dedup_key=key,
        urgent=False,
        instance=instance,
        data=data,
        rendered=rendered,
    )


def body_bytes(body: BodyBase) -> bytes:
    """The exact bytes sent and signed: compact JSON, UTF-8, fields in model order."""
    return body.model_dump_json().encode()
