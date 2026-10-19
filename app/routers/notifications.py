"""REST API of the notification engine (``/api/notifications``; ``app/notifications/AGENTS.md``).

Every route is ``admin`` (channel configuration, rules and the journal reach destinations
and secrets). Secrets are write-only: a read shows ``********`` for a secret that holds a
value and ``""`` for one that does not, a PATCH keeps a secret that is omitted, ``null`` or
``********`` and clears it with ``""``. Errors are ``{code, params}`` (``errors.<code>`` in
the UI), never an exception text, a host or an address. A test send goes through the
dispatcher and so through the egress guard; it is limited to one per five seconds per
channel. The deprecated 4.18.x routes (``/api/push/preferences*``, the settings
destinations) are shims over the same data (``app/notifications/legacy_sync.py``).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.permissions import require
from app.notifications import api_support
from app.notifications.channels import (
    BUILTIN_PUSH_ID,
    Channel,
    ChannelCreate,
    ChannelError,
    ChannelUpdate,
    get_type,
    types_schema,
)
from app.notifications.events import EVENT_IDS
from app.notifications.legacy_sync import MEDIUM_IDS, sync_columns
from app.repository.notification_channels import (
    DELIVERIES_PER_CHANNEL,
    NotificationChannelRepository,
    NotificationJournalRepository,
)
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(require("admin"))],
)

# Events whose proof of modification reaches ``builtin:push`` whatever the rules say.
LOCKED_PUSH_EVENTS = frozenset({"notify.channel_changed", "notify.destination_changed"})
# Not rule targets: the test button and the proof events have no user matrix row of
# their own except the two above.
NOT_CONFIGURABLE = frozenset({"notify.test"})
URGENT_EVENTS = frozenset({"radio.offline", "telemetry.alert", "notify.channel_changed"})
MAX_PAUSE_S = 366 * 86400
LEGACY_CHANNELS = frozenset(MEDIUM_IDS - {BUILTIN_PUSH_ID})
CONVERSATION_KEY = re.compile(r"^(contact|channel)-[0-9A-Za-z]{1,128}$")


def fail(
    code: str, status: int = 400, *, headers: dict[str, str] | None = None, **params: Any
) -> HTTPException:
    """A coded refusal: ``{code, params}``, nothing else."""
    return HTTPException(
        status_code=status, detail={"code": code, "params": params}, headers=headers
    )


def legacy_managed() -> HTTPException:
    """409: the ``legacy:*`` destinations are still the 4.18.x ``notification_destinations``."""
    return fail("legacy_managed", 409)


def _as_http(exc: ChannelError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail())


# ── Models ───────────────────────────────────────────────────────────────


class ChannelStatusOut(BaseModel):
    status: str
    last_error: str | None = None
    last_ok_at: int | None = None


class ChannelLimits(BaseModel):
    per_min: int | None
    per_hour: int | None
    per_day: int | None


class ChannelOut(BaseModel):
    """A channel as it is shown: secrets are ``********``, never their value."""

    id: str
    type: str
    preset: str | None
    name: str
    enabled: bool
    language: str
    config: dict[str, Any]
    secret_fields: list[str]
    secret_unreadable: bool = False
    allow_private_network: bool
    include_message_text: str
    verify_tls: bool
    quiet_hours_exempt: bool
    rate_per_min: int | None
    rate_per_hour: int | None
    rate_per_day: int | None
    limits: ChannelLimits
    sort_order: int
    created_at: int
    paused_until: int | None
    failure_count: int
    deletable: bool
    status: ChannelStatusOut


class TestResult(BaseModel):
    __test__ = False  # not a pytest class

    status: str = "sent"


class ConversationRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: dict[str, bool] | None


class EventRulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[str] | None = None
    always_deliver: bool | None = None
    contact_types: dict[str, list[str]] | None = None


class RulesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: dict[str, EventRulePatch] | None = None
    paused_until: int | None = None
    radio_offline_grace_s: int | None = Field(None, ge=0, le=3600)
    mention_whole_word: bool | None = None
    push_allow_private_endpoints: bool | None = None


# ── Presentation ─────────────────────────────────────────────────────────


async def _statuses() -> dict[str, dict[str, Any]]:
    from app.notifications.dispatcher import notification_statuses

    return await notification_statuses()


def _present(channel: Channel, statuses: dict[str, dict[str, Any]]) -> ChannelOut:
    try:
        entry = get_type(channel.type, channel.preset)
    except ChannelError:
        entry = None
    defaults = entry.limits_for(channel.config) if entry else None
    state = statuses.get(channel.id) or {}
    return ChannelOut(
        id=channel.id,
        type=channel.type,
        preset=channel.preset,
        name=channel.name,
        enabled=channel.enabled,
        language=channel.language,
        config=api_support.redacted_config(channel),
        secret_fields=sorted(channel.secret_fields_set),
        allow_private_network=channel.allow_private_network,
        include_message_text=channel.include_message_text,
        verify_tls=channel.verify_tls,
        quiet_hours_exempt=channel.quiet_hours_exempt,
        rate_per_min=channel.rate_per_min,
        rate_per_hour=channel.rate_per_hour,
        rate_per_day=channel.rate_per_day,
        limits=ChannelLimits(
            per_min=channel.rate_per_min
            if channel.rate_per_min is not None
            else (defaults.per_min if defaults else None),
            per_hour=channel.rate_per_hour
            if channel.rate_per_hour is not None
            else (defaults.per_hour if defaults else None),
            per_day=channel.rate_per_day
            if channel.rate_per_day is not None
            else (defaults.per_day if defaults else None),
        ),
        sort_order=channel.sort_order,
        created_at=channel.created_at,
        paused_until=channel.paused_until,
        failure_count=channel.failure_count,
        deletable=channel.deletable,
        status=ChannelStatusOut(
            status=str(state.get("status") or ("connected" if channel.enabled else "disconnected")),
            last_error=state.get("last_error"),
            last_ok_at=state.get("last_ok_at"),
        ),
    )


async def _present_one(channel: Channel) -> ChannelOut:
    return _present(channel, await _statuses())


# ── Types and events ─────────────────────────────────────────────────────


@router.get("/types")
async def list_types() -> list[dict[str, Any]]:
    """The channel types: their JSON Schema (secret fields are ``writeOnly``; labels and help
    are i18n keys), default limits, ``cloud``, ``quiet_hours_exempt_default`` and ``preset``."""
    return types_schema()


@router.get("/events")
async def list_events() -> dict[str, Any]:
    """The event catalogue (contract version 1) for the UI and the Feed."""
    from app.notifications.events import CONTRACT_VERSION

    return {
        "contract_version": CONTRACT_VERSION,
        "events": [
            {
                "id": event_id,
                "urgent": event_id in URGENT_EVENTS,
                "configurable": event_id not in NOT_CONFIGURABLE,
                "locked_channels": [BUILTIN_PUSH_ID] if event_id in LOCKED_PUSH_EVENTS else [],
            }
            for event_id in EVENT_IDS
        ],
    }


# ── Channels ─────────────────────────────────────────────────────────────


@router.get("/channels")
async def list_channels() -> list[ChannelOut]:
    """Every channel, with its status. Secrets read ``********``."""
    statuses = await _statuses()
    return [_present(c, statuses) for c in await NotificationChannelRepository.list_all()]


@router.post("/channels", status_code=201)
async def create_channel(body: ChannelCreate, request: Request) -> ChannelOut:
    """Create a channel. The destination goes through the egress guard first (400, coded)."""
    try:
        channel = await NotificationChannelRepository.create(body)
    except ChannelError as exc:
        raise _as_http(exc) from None
    await api_support.announce_channel_change(request, "created", channel)
    return await _present_one(channel)


async def _run_test(channel: Channel, secrets: dict[str, str], *, record: bool) -> TestResult:
    from app.notifications.dispatcher import notification_dispatcher

    result = await notification_dispatcher().send_test(channel, secrets, record=record)
    if result.ok:
        return TestResult()
    status, code = api_support.test_failure(result.error_code)
    logger.info("Notification test on a %s channel failed: %s", channel.type, code)
    raise fail(code, status)


def _draft_channel(body: ChannelCreate, public: dict[str, Any]) -> Channel:
    entry = get_type(body.type, body.preset)
    return Channel(
        id=entry.singleton_id or "draft",
        type=entry.row_type,
        preset=body.preset,
        name=body.name,
        enabled=True,
        language=body.language,
        config=public,
        allow_private_network=body.allow_private_network,
        include_message_text=body.include_message_text or entry.include_message_text_for(public),
        verify_tls=body.verify_tls,
        quiet_hours_exempt=bool(body.quiet_hours_exempt),
    )


@router.post("/channels/test")
async def test_unsaved_channel(body: ChannelCreate) -> TestResult:
    """Test a configuration before saving it: the same validations and codes as a save and
    as ``POST /channels/{id}/test``; nothing is persisted. One test every two seconds, and
    the limit is taken before any validation, so the DNS lookups of the egress checks
    cannot be driven faster (no resolution oracle)."""
    if not await api_support.take_test_slot(api_support.DRAFT_KEY):
        raise fail(
            "rate_limited",
            429,
            headers={"Retry-After": str(int(api_support.DRAFT_PERIOD_S))},
        )
    try:
        _entry, public, secrets = await NotificationChannelRepository.prepare(body)
    except ChannelError as exc:
        raise _as_http(exc) from None
    return await _run_test(_draft_channel(body, public), secrets, record=False)


@router.get("/channels/{channel_id}")
async def get_channel(channel_id: str) -> ChannelOut:
    channel = await NotificationChannelRepository.get(channel_id)
    if channel is None:
        raise fail("channel_missing", 404)
    return await _present_one(channel)


def _destination_of(channel: Channel, secrets: dict[str, str]) -> tuple[Any, ...]:
    """What decides where a channel's notifications go: its public settings and the secrets
    that are destinations themselves (a webhook URL, Apprise URLs), not the credentials."""
    entry = get_type(channel.type, channel.preset)
    follows = set(entry.secret_follows)
    destination_secrets = {k: v for k, v in secrets.items() if k not in follows}
    return (
        sorted(
            (k, repr(v))
            for k, v in channel.config.items()
            if k != "private_network_grandfathered"  # a banner flag, not a destination
        ),
        sorted(destination_secrets.items()),
        channel.allow_private_network,
    )


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: str, body: ChannelUpdate, request: Request) -> ChannelOut:
    """Modify a channel. Its type is immutable; a secret that is omitted, ``null`` or
    ``********`` is kept and ``""`` clears it."""
    before = await NotificationChannelRepository.get_for_send(channel_id)
    if before is None:
        raise fail("channel_missing", 404)
    if channel_id in LEGACY_CHANNELS and (
        body.config is not None
        or body.allow_private_network is not None
        or body.verify_tls is not None
    ):
        # The legacy transports read notification_destinations at send time.
        raise legacy_managed()
    try:
        channel = await NotificationChannelRepository.update(channel_id, body)
    except ChannelError as exc:
        raise _as_http(exc) from None
    after = await NotificationChannelRepository.get_for_send(channel_id)
    if channel.enabled != before[0].enabled:
        await api_support.announce_channel_change(
            request, "enabled" if channel.enabled else "disabled", channel
        )
    elif after is not None and _destination_of(*after) != _destination_of(*before):
        await api_support.announce_channel_change(request, "changed", channel)
    return await _present_one(channel)


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, request: Request) -> dict[str, bool]:
    """Delete a channel and every reference to it. ``builtin:push`` is refused (403)."""
    if channel_id in LEGACY_CHANNELS:
        raise legacy_managed()
    channel = await NotificationChannelRepository.get(channel_id)
    try:
        await NotificationChannelRepository.delete(channel_id)
    except ChannelError as exc:
        raise _as_http(exc) from None
    if channel is not None:
        await api_support.announce_channel_change(request, "deleted", channel)
    return {"deleted": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: str) -> TestResult:
    """Send a test notification on a saved channel (one per 5 s per channel, 429 after)."""
    found = await NotificationChannelRepository.get_for_send(channel_id)
    if found is None:
        raise fail("channel_missing", 404)
    channel, secrets = found
    if not await api_support.take_test_slot(channel_id):
        raise fail(
            "rate_limited", 429, headers={"Retry-After": str(int(api_support.TEST_PERIOD_S))}
        )
    return await _run_test(channel, secrets, record=True)


# ── Rules ────────────────────────────────────────────────────────────────


def host_timezone() -> str:
    """The host's time zone name (read-only; the quiet hours use it)."""
    zone = os.environ.get("TZ", "").strip().lstrip(":")
    if zone:
        return zone
    try:
        target = os.path.realpath("/etc/localtime")
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    try:
        with open("/etc/timezone", encoding="utf-8") as handle:
            name = handle.read().strip()
            if name:
                return name
    except OSError:
        pass
    return datetime.now().astimezone().tzname() or "UTC"


async def _rules_view() -> dict[str, Any]:
    rules = await NotificationChannelRepository.get_rules()
    settings = await AppSettingsRepository.get()
    paused = rules.get("paused_until")
    return {
        "version": rules.get("version", 1),
        "paused_until": paused if isinstance(paused, int) and paused > time.time() else None,
        "events": rules.get("events") or {},
        "conversations": rules.get("conversations") or {},
        "keywords": rules.get("keywords") or [],
        "watched_nodes": rules.get("watched_nodes") or [],
        "radio_offline_grace_s": settings.radio_offline_grace_s,
        "mention_whole_word": bool(rules.get("mention_whole_word", False)),
        "push_allow_private_endpoints": settings.push_allow_private_endpoints,
        "host_timezone": host_timezone(),
    }


@router.get("/rules")
async def get_rules() -> dict[str, Any]:
    """The matrix (event to channels), the conversation exceptions, the global pause and the
    instance settings that shape delivery. ``host_timezone`` is read-only."""
    return await _rules_view()


async def _known_channel_ids() -> set[str]:
    return {c.id for c in await NotificationChannelRepository.list_all()}


def _check_ids(ids: list[str], known: set[str]) -> None:
    missing = [i for i in ids if i not in known]
    if missing:
        raise fail("channel_missing", 400, channels=missing)


@router.patch("/rules")
async def patch_rules(body: RulesPatch, request: Request) -> dict[str, Any]:
    """Change event rules (per event: channels, ``always_deliver``, contact types), the global
    pause (``paused_until``: epoch seconds, ``null`` resumes), the radio offline grace, the
    mention rule and the private push endpoints."""
    rules = await NotificationChannelRepository.get_rules()
    known = await _known_channel_ids()
    fields = body.model_fields_set

    if body.events:
        events = dict(rules.get("events") or {})
        for event_id, patch in body.events.items():
            if event_id not in EVENT_IDS or event_id in NOT_CONFIGURABLE:
                raise fail("invalid_config", 400, fields=[f"events.{event_id}"])
            entry = dict(events.get(event_id) or {})
            if patch.channels is not None:
                _check_ids(patch.channels, known)
                if event_id in LOCKED_PUSH_EVENTS and BUILTIN_PUSH_ID not in patch.channels:
                    raise fail("builtin_channel", 403, event=event_id)
                entry["channels"] = list(dict.fromkeys(patch.channels))
            if patch.always_deliver is not None:
                entry["always_deliver"] = patch.always_deliver
            if patch.contact_types is not None:
                if event_id != "contact.first_seen" or set(patch.contact_types) - {
                    "companion",
                    "repeater",
                    "sensor",
                }:
                    raise fail("invalid_config", 400, fields=[f"events.{event_id}.contact_types"])
                for ids in patch.contact_types.values():
                    _check_ids(ids, known)
                entry["contact_types"] = {
                    kind: list(dict.fromkeys(ids)) for kind, ids in patch.contact_types.items()
                }
            events[event_id] = entry
        rules["events"] = events

    if "paused_until" in fields:
        until = body.paused_until
        now = int(time.time())
        if until is not None and until > now + MAX_PAUSE_S:
            raise fail("invalid_config", 400, fields=["paused_until"])
        rules["paused_until"] = until if until is not None and until > now else None
    if "mention_whole_word" in fields and body.mention_whole_word is not None:
        rules["mention_whole_word"] = body.mention_whole_word
    rules.setdefault("version", 1)
    await NotificationChannelRepository.set_rules(rules)
    if body.events:
        await sync_columns()

    if body.radio_offline_grace_s is not None or body.push_allow_private_endpoints is not None:
        await AppSettingsRepository.update(
            radio_offline_grace_s=body.radio_offline_grace_s,
            push_allow_private_endpoints=body.push_allow_private_endpoints,
        )
    await _audit_rules(request, sorted(fields), body)
    return await _rules_view()


async def _audit_rules(request: Request, fields: list[str], body: RulesPatch | None) -> None:
    """One audit row for a change of the rules (``notify.rules_changed``): which parts changed,
    and the global pause's end, never a channel setting."""
    from app.auth.audit import record_request

    details: dict[str, Any] = {"fields": fields}
    if body is not None and "paused_until" in body.model_fields_set:
        details["paused_until"] = body.paused_until
    if body is not None and body.events:
        details["events"] = sorted(body.events)
    await record_request(
        request, "notify.rules_changed", "success", target="notification_rules", details=details
    )


@router.put("/rules/conversations/{key}")
async def put_conversation_rule(
    key: str, body: ConversationRuleBody, request: Request
) -> dict[str, Any]:
    """Set the channels of one conversation (``{id: bool}``: on or off for that channel,
    replacing what was stored) or clear it with ``null``."""
    if not CONVERSATION_KEY.match(key):
        raise fail("invalid_config", 400, fields=["key"])
    rules = await NotificationChannelRepository.get_rules()
    conversations = dict(rules.get("conversations") or {})

    if body.channels is None or not body.channels:
        conversations.pop(key, None)
    else:
        _check_ids(list(body.channels), await _known_channel_ids())
        conversations[key] = dict(body.channels)
    rules["conversations"] = conversations
    rules.setdefault("version", 1)
    await NotificationChannelRepository.set_rules(rules)
    await sync_columns()
    await _audit_rules(request, ["conversations"], None)
    return await _rules_view()


# ── Journal ──────────────────────────────────────────────────────────────


@router.get("/deliveries")
async def list_deliveries(
    channel: str | None = Query(None, max_length=120),
    limit: int = Query(50, ge=1, le=DELIVERIES_PER_CHANNEL),
) -> dict[str, Any]:
    """The journal (failures, suppressions, groupings: never the text) and the hourly send
    counters of the last 24 hours, for one channel or all."""
    if channel is not None and await NotificationChannelRepository.get(channel) is None:
        raise fail("channel_missing", 404)
    rows = await NotificationJournalRepository.list_deliveries(channel, limit)
    since_hour = int(time.time()) // 3600 - 23
    counters = await NotificationJournalRepository.hourly_counts(channel, since_hour)
    return {"deliveries": rows, "counters": counters}
