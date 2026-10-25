"""What the notification routes and the legacy shims share (``docs/dev``, "Notifications API").

- the coded test answer (``test_failure``): a transport's ``SendResult.error_code`` becomes
  one stable code and an HTTP status, never the exception text, a host or an address;
- the test limiter: one ``AsyncLimiter(1, 5)`` per channel id (``POST .../test``);
- ``announce_channel_change``: the admin-only WebSocket event, the audit row and the
  ``notify.channel_changed`` notification (to ``builtin:push``, which cannot be unchecked,
  and by default to every other enabled channel);
- ``redacted_config``: a channel's config as every response shows it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aiolimiter import AsyncLimiter

from app.notifications.channels import BUILTIN_PUSH_ID, Channel, get_type
from app.secret_fields import REDACTED

logger = logging.getLogger(__name__)

TEST_PERIOD_S = 5.0
# A test with the configuration in the body has no channel id: one shared budget.
DRAFT_KEY = "*draft*"
DRAFT_PERIOD_S = 2.0

# Every test send of the instance shares one small budget on top of the per-key limits.
GLOBAL_KEY = "*all*"
GLOBAL_TESTS = 10
GLOBAL_PERIOD_S = 60.0

_limiters: dict[str, AsyncLimiter] = {}


def reset_test_limiters() -> None:
    _limiters.clear()


async def take_test_slot(key: str) -> bool:
    """Whether a test may run now for ``key`` (a channel id); takes the slot when it may.

    ``has_capacity()`` then ``acquire()`` with no suspension between: with capacity,
    ``acquire()`` returns at once, so a refusal never queues and two concurrent
    requests cannot both take the one slot."""
    period = DRAFT_PERIOD_S if key == DRAFT_KEY else TEST_PERIOD_S
    limiter = _limiters.get(key)
    if limiter is None:
        limiter = _limiters[key] = AsyncLimiter(1, period)
    shared = _limiters.get(GLOBAL_KEY)
    if shared is None:
        shared = _limiters[GLOBAL_KEY] = AsyncLimiter(GLOBAL_TESTS, GLOBAL_PERIOD_S)
    if not limiter.has_capacity() or not shared.has_capacity():
        return False
    await limiter.acquire()
    await shared.acquire()
    return True


# transport / egress code -> (HTTP status, public code). Anything else is a service_error.
_PASS_THROUGH: dict[str, int] = {
    "egress_scheme": 400,
    "egress_private_address": 400,
    "egress_unresolvable": 400,
    "egress_host_not_allowed": 400,
    "verify_tls_requires_private_destination": 400,
    "smtp_plaintext_auth_refused": 400,
    "apprise_url_invalid": 400,
    "apprise_chat_id_required": 400,
    "invalid_config": 400,
    "auth_failed": 502,
    "service_error": 502,
    "tls_error": 502,
    "timeout": 504,
    "rate_limited": 429,
    "quota_exceeded": 429,
}
_LINE_CODE = re.compile(r"^line \d+(?: \([^)]*\))?: (?P<code>[a-z0-9_]+)$")


def test_failure(error_code: str | None) -> tuple[int, str]:
    """The HTTP status and the public code of a failed test send."""
    code = (error_code or "").strip()
    match = _LINE_CODE.match(code)
    if match:  # an Apprise URL's own failure: "line 2 (redacted): http_401"
        code = match.group("code")
    if code in _PASS_THROUGH:
        return _PASS_THROUGH[code], code
    if code == "not_configured":
        return 400, "invalid_config"
    if code.startswith("http_"):
        status = code.removeprefix("http_")
        if status in ("401", "403"):
            return 502, "auth_failed"
        if status == "429":
            return 429, "rate_limited"
    return 502, "service_error"


test_failure.__test__ = False  # type: ignore[attr-defined]  # not a pytest function


def redacted_config(channel: Channel) -> dict[str, Any]:
    """The config of ``channel`` with every secret field shown as ``********`` when it
    holds a value and ``""`` when it does not."""
    config = dict(channel.config)
    try:
        secret_names = get_type(channel.type, channel.preset).secret_fields
    except Exception:
        secret_names = frozenset(channel.secret_fields_set)
    for name in secret_names:
        config[name] = REDACTED if name in channel.secret_fields_set else ""
    return config


async def channel_changed_targets(exclude: str | None = None) -> list[str]:
    """``builtin:push`` first, then the channels of the ``notify.channel_changed`` rule
    (every enabled channel when the rule names none)."""
    from app.notifications import rules as rules_mod
    from app.repository.notification_channels import NotificationChannelRepository

    rules = await rules_mod.load_rules()
    events = rules.get("events")
    entry = events.get("notify.channel_changed") if isinstance(events, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("channels"), list):
        wanted = [str(c) for c in entry["channels"]]
    else:
        wanted = [c.id for c in await NotificationChannelRepository.list_all() if c.enabled]
    known = {c.id for c in await NotificationChannelRepository.list_all()}
    ids = [BUILTIN_PUSH_ID, *(c for c in wanted if c != BUILTIN_PUSH_ID and c in known)]
    return [c for c in dict.fromkeys(ids) if c != exclude]


async def announce_channel_change(request: Any, action: str, channel: Channel) -> None:
    """The three traces of a channel change. Never raises, never carries a setting.

    The notification goes to every other channel, never to the one that changed: its new
    destination would learn that the change worked, and the old one is gone."""
    payload = {"id": channel.id, "action": action, "type": channel.type, "name": channel.name}
    try:
        from app.auth.ws_events import WsEvent
        from app.websocket import broadcast_event

        broadcast_event(WsEvent.NOTIFICATION_CHANNEL_CHANGED, payload, realtime=False)
    except Exception:
        logger.debug("notification_channel_changed broadcast failed", exc_info=True)
    try:
        from app.auth.audit import record_request

        await record_request(
            request,
            "notify.channel_changed",
            "success",
            target=f"notification_channel:{channel.id}",
            details={"action": action, "type": channel.type, "name": channel.name[:80]},
        )
    except Exception:
        logger.debug("notify.channel_changed audit failed", exc_info=True)
    try:
        from app.notifications.dispatcher import emit
        from app.notifications.render import CHANNEL_CHANGED_EVENT

        targets = await channel_changed_targets(exclude=channel.id)
        await emit(
            None,
            payload={
                "event": CHANNEL_CHANGED_EVENT,
                "action": action,
                "channel_type": channel.type,
                "name": channel.name,
            },
            targets=targets,
        )
    except Exception:
        logger.debug("notify.channel_changed notification failed", exc_info=True)
