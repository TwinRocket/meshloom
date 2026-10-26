"""`messages.send {to, text, region?, event_id?}` (permission `messages.send`).

Checks, in order, each before any side effect: the parameters (`invalid_params`),
the permission's `targets` (`target_not_allowed`), the destination exists
(`target_unknown`, only under `targets: any`), the region (`region_unknown`), the
byte limit of the server (`message_too_long` with `params.limit`), then the
instance's hourly and daily quotas (`quota_exceeded` with `params.scope` `plugin`,
`period`, `limit`, `retry_after`), then the host-side send guards (`quota_exceeded` with
`params.scope`: `sender_cooldown`, `quiet_window`, `destination`, `instance`;
`app/plugins/quotas.py`). Under `reply` and `scope` an unknown destination is
`target_not_allowed` like any other refused one, so a plugin without
`contacts.read` cannot probe which contacts exist.

- `to` is `"reply"` (the conversation of the event being handled), or
  `{"channel_key": <32 hex>}` / `{"contact_key": <64 hex>}`.
- `targets`: `reply` allows only the conversation of the message event being
  handled; `scope` the channels and contacts the instance may read
  (`messages.read` and the user's scope); `any` every known channel and contact.
- `region` (channels only): absent keeps the channel's default, `null` or `""`
  sends unscoped, a name must be a region Meshloom knows (`known_regions`, the
  radio's default scope or the channel's own override).
- A send of a `test: true` event passes the same checks, then is captured for
  the caller and never sent; it uses no quota.
- Real sends wait for the global 2 s spacing (`app/plugins/quotas.py`), shared
  with the legacy bots, and go through the same send services as the REST API
  (radio checks, pending channels, byte limit). While a send runs, the outgoing
  message it produces is marked with the instance (`SENDING_INSTANCE`), so the
  host never hands an instance its own send without `outgoing: true`, and marks
  it `own_send` when it does.
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.message_limits import DM_HARD_LIMIT, byte_length, channel_text_allowance
from app.plugins.capabilities.base import Call, CapabilityError, capability, invalid_params
from app.plugins.capabilities.grants import conversation_readable, is_public_channel
from app.plugins.quotas import (
    GuardRefused,
    QuotaExceeded,
    SendGuard,
    effective_send_limits,
    send_guard,
    send_quota,
    send_spacer,
)
from app.region_scope import is_unscoped, normalize_region_scope

logger = logging.getLogger(__name__)

# The plugin instance whose send is running in this context (set around the send,
# copied into the tasks the send spawns, so the fanout dispatch of the outgoing
# message sees it).
SENDING_INSTANCE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "meshloom_plugin_sending_instance", default=None
)

MAX_TEXT_CHARS = 1024  # far above any radio limit; the byte check decides


class _ChannelTo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel_key: str = Field(pattern=r"^[0-9A-Fa-f]{32}$")


class _ContactTo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contact_key: str = Field(pattern=r"^[0-9A-Fa-f]{64}$")


class SendParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    to: Literal["reply"] | _ChannelTo | _ContactTo
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    region: str | None = None
    event_id: int | None = None


@dataclass
class Target:
    is_dm: bool
    key: str  # channel key uppercase, contact key lowercase
    row: Any = None  # the Channel or Contact, None when unknown

    @property
    def is_public(self) -> bool:
        return not self.is_dm and is_public_channel(self.key, getattr(self.row, "name", None))


def _parse(params: dict[str, Any]) -> SendParams:
    try:
        return SendParams.model_validate(params)
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")})
        raise invalid_params("invalid messages.send parameters", fields=fields) from None


def _reply_target(call: Call) -> Target:
    event = call.event
    if event is None or event.hook != "message":
        raise CapabilityError("target_not_allowed", "a reply needs the message event being handled")
    params = event.params
    if params.get("is_dm"):
        key = str(params.get("sender_key") or params.get("conversation_key") or "")
        return Target(True, key.lower())
    key = str(params.get("channel_key") or params.get("conversation_key") or "")
    return Target(False, key.upper())


async def _resolve(call: Call, params: SendParams) -> Target:
    """The destination, its row looked up (None when unknown or pending); never refuses
    an unknown one: `send()` decides, after the `targets` check."""
    from app.repository import ChannelRepository, ContactRepository

    if params.to == "reply":
        target = _reply_target(call)
    elif isinstance(params.to, _ChannelTo):
        target = Target(False, params.to.channel_key.upper())
    else:
        assert isinstance(params.to, _ContactTo)
        target = Target(True, params.to.contact_key.lower())
    if not target.key:
        raise CapabilityError("target_not_allowed", "the event has no conversation to reply to")
    if target.is_dm:
        target.row = await ContactRepository.get_by_key(target.key)
    else:
        from app.services.channel_membership import is_pending_channel

        channel = await ChannelRepository.get_by_key(target.key)
        target.row = None if is_pending_channel(channel) else channel
    return target


def _is_test(call: Call) -> bool:
    return call.event is not None and call.event.test


def _event_conversation(call: Call) -> Target | None:
    try:
        return _reply_target(call)
    except CapabilityError:
        return None


def _check_target(call: Call, targets: str, target: Target) -> None:
    """`targets` first, then existence. Under `reply` and `scope` an unknown
    destination gets the same refusal as a forbidden one (no contact oracle)."""
    known = target.row is not None or _is_test(call)
    if targets == "any":
        if not known:
            raise CapabilityError("target_unknown", "no such channel or contact")
        return
    if known and _target_allowed(call, targets, target):
        return
    raise CapabilityError(
        "target_not_allowed",
        f"targets '{targets}' does not allow this destination",
        {"targets": targets},
    )


def _target_allowed(call: Call, targets: str, target: Target) -> bool:
    current = _event_conversation(call)
    if current is not None and (current.is_dm, current.key) == (target.is_dm, target.key):
        return True
    return targets == "scope" and conversation_readable(
        call.instance.permissions,
        call.instance.scope,
        is_dm=target.is_dm,
        key=target.key,
        is_public=target.is_public,
    )


def region_request(params: SendParams, target: Target) -> tuple[bool, str | None]:
    """`(named, value)`: `named` when the send names a region that must be checked
    against the known ones (`check_region`); otherwise `value` is the
    `flood_scope_override` (None = the channel's default, "" = unscoped)."""
    if target.is_dm or "region" not in params.model_fields_set:
        return False, None
    if params.region is None or is_unscoped(params.region):
        return False, ""
    return True, params.region


def check_region(
    region: str, target: Target, known_regions: Iterable[str], flood_scope: str
) -> None:
    """`region_unknown` unless the region is a known one, the default flood scope or
    the channel's own override."""
    known = {normalize_region_scope(r) for r in known_regions}
    known.add(normalize_region_scope(flood_scope))
    override = getattr(target.row, "flood_scope_override", None)
    if override and not is_unscoped(override):
        known.add(normalize_region_scope(override))
    known.discard("")
    if normalize_region_scope(region) not in known:
        raise CapabilityError("region_unknown", "unknown region")


async def _region(params: SendParams, target: Target) -> str | None:
    """The `flood_scope_override` of the send: None = the channel's default."""
    named, value = region_request(params, target)
    if not named:
        return value
    from app.repository import AppSettingsRepository

    settings = await AppSettingsRepository.get()
    assert params.region is not None
    check_region(params.region, target, settings.known_regions, settings.flood_scope)
    return params.region


def _radio_name() -> str | None:
    try:
        from app.services.radio_runtime import radio_runtime

        mc = radio_runtime.meshcore
        return ((mc.self_info or {}).get("name") or None) if mc is not None else None
    except Exception:
        return None


def check_size(text: str, target: Target, radio_name: str | None) -> None:
    """`message_too_long` when the text is over the server's byte rule."""
    limit = DM_HARD_LIMIT if target.is_dm else channel_text_allowance(radio_name)
    size = byte_length(text)
    if size > limit:
        raise CapabilityError(
            "message_too_long",
            f"the message is {size} bytes, the limit is {limit}",
            {"limit": limit, "bytes": size},
        )


def _check_size(text: str, target: Target) -> None:
    check_size(text, target, _radio_name())


def _capture(call: Call) -> dict[str, Any]:
    from app.plugins.host import MAX_TEST_SEND_BYTES, MAX_TEST_SENDS, HostMethodError
    from meshloom_plugin import rpc

    event = call.event
    assert event is not None
    send = {k: v for k, v in call.params.items() if k != "event_id"}
    size = len(json.dumps(send, ensure_ascii=False).encode("utf-8"))
    if len(event.sends) >= MAX_TEST_SENDS or event.sends_bytes + size > MAX_TEST_SEND_BYTES:
        event.truncated = True
        raise HostMethodError(
            rpc.OVERLOADED, "too many test sends", {"code": "test_sends_truncated"}
        )
    event.sends.append(send)
    event.sends_bytes += size
    return {"captured": True}


def quota_refusal(exc: QuotaExceeded) -> CapabilityError:
    return CapabilityError(
        "quota_exceeded",
        f"at most {exc.limit} messages per {exc.period}",
        {
            "scope": "plugin",
            "period": exc.period,
            "limit": exc.limit,
            "retry_after": exc.retry_after,
        },
    )


async def _check_quota(instance_id: str, limits: Any) -> None:
    try:
        await send_quota.check(instance_id, limits)
    except QuotaExceeded as exc:
        raise quota_refusal(exc) from None


def reply_context(
    guard: SendGuard,
    instance_id: str,
    event: Any,
    target: Target,
) -> tuple[str | None, int | None]:
    """`(sender, received_at)` of the message a send answers. The loop guards must not
    depend on the plugin tying its send to an event: with the message event being handled
    that is its sender, otherwise (several events in flight, after the handler returned,
    no or a non-message `event_id`, a timer) the send counts as a reply to the most recent
    message the instance was handed from that destination, none if it was handed none."""
    if event is not None and event.hook == "message" and not event.params.get("is_outgoing"):
        received = event.params.get("received_at")
        return guard.sender_of(event.params), received if type(received) is int else None
    last = guard.last_inbound(instance_id, is_dm=target.is_dm, key=target.key)
    return (last[0], last[1]) if last is not None else (None, None)


def _reply_context(call: Call, target: Target) -> tuple[str | None, int | None]:
    return reply_context(send_guard, call.instance.instance_id, call.event, target)


def _unavailable() -> CapabilityError:
    return CapabilityError("quota_unavailable", "the send guards could not be checked")


async def _check_guards(call: Call, target: Target) -> None:
    sender, received_at = _reply_context(call, target)
    try:
        await send_guard.check(
            call.instance.instance_id,
            is_dm=target.is_dm,
            key=target.key,
            sender=sender,
            received_at=received_at,
        )
    except GuardRefused as exc:
        send_guard.note_refusal(call.instance.instance_id, call.instance.label, exc)
        raise CapabilityError("quota_exceeded", str(exc), exc.params) from None
    except Exception:
        logger.exception("Plugin %s: send guards unavailable", call.instance.instance_id)
        raise _unavailable() from None


async def _count_guards(call: Call, target: Target) -> None:
    """Count the send before it goes out: if the counters cannot be written nothing is
    sent (fail closed). A send that then fails stays counted (conservative)."""
    try:
        await send_guard.record(
            call.instance.instance_id,
            is_dm=target.is_dm,
            key=target.key,
            sender=_reply_context(call, target)[0],
        )
    except Exception:
        logger.exception(
            "Plugin %s: could not count a send in the guards", call.instance.instance_id
        )
        raise _unavailable() from None


async def _transmit(target: Target, text: str, region: str | None) -> Any:
    from app.models import SendChannelMessageRequest, SendDirectMessageRequest
    from app.routers.messages import send_channel_message, send_direct_message

    if target.is_dm:
        return await send_direct_message(
            SendDirectMessageRequest(destination=target.key, text=text)
        )
    return await send_channel_message(
        SendChannelMessageRequest(channel_key=target.key, text=text, flood_scope_override=region)
    )


@capability("messages.send", "messages.send")
async def send(call: Call) -> dict[str, Any]:
    from fastapi import HTTPException

    from app.message_limits import is_message_too_long

    params = _parse(call.params)
    permission = call.instance.permissions["messages.send"]
    target = await _resolve(call, params)
    _check_target(call, permission.get("targets", "reply"), target)
    region = await _region(params, target)
    _check_size(params.text, target)
    if _is_test(call):
        return _capture(call)

    instance_id = call.instance.instance_id
    limits = effective_send_limits(permission, call.instance.quotas)
    # Refuse at once, without waiting for the spacing; checked again once the slot is ours.
    await _check_quota(instance_id, limits)
    await _check_guards(call, target)
    async with send_spacer.slot() as slot:
        await _check_quota(instance_id, limits)
        await _check_guards(call, target)
        await _count_guards(call, target)
        token = SENDING_INSTANCE.set(instance_id)
        try:
            message = await _transmit(target, params.text, region)
        except HTTPException as exc:
            if is_message_too_long(exc):
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                raise CapabilityError(
                    "message_too_long", "the message is too long", detail.get("params")
                ) from None
            raise CapabilityError(
                "send_failed", "the radio did not send the message", {"status": exc.status_code}
            ) from None
        except Exception as exc:
            logger.warning(
                "Plugin %s: send failed (%s)", call.instance.instance_id, type(exc).__name__
            )
            raise CapabilityError("send_failed", "the radio did not send the message") from None
        finally:
            SENDING_INSTANCE.reset(token)
        # The message went out: the spacing moves whatever happens to the counter.
        slot.sent()
        try:
            await send_quota.record(instance_id)
        except Exception:
            logger.exception("Plugin %s: could not count a send", instance_id)
    return {"sent": True, "message_id": getattr(message, "id", None)}
