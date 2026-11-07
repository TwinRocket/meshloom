"""A channel that keeps failing (MSHL-130): the ``notify.channel_failed`` notice and the
auto-pause with its backoff. The dispatcher calls these; ``app/push/manager.py`` calls
``announce_push_removed`` when it removes a browser's subscription (unsubscribed by its push
service, refused for good by the egress guard, or ``MAX_CONSECUTIVE_FAILURES`` failures).

- After ``FAILED_EVENT_AFTER`` consecutive failures, ``notify.channel_failed`` goes to
  ``builtin:push`` (administrators' devices only, MSHL-399) and every other enabled channel,
  never to the failing one. At most once
  per channel per day: the ``channel_failed:<id>`` row of ``notification_state`` (it expires
  after a day) survives a restart.
- After ``PAUSE_AFTER`` consecutive failures the channel's ``paused_until`` is set, and
  ``autopause:<id>`` (no expiry) keeps the backoff step: 5 min, 15 min, 1 h, then 6 h. When
  the pause runs out the dispatcher's tick sends a test notification: a success resumes the
  channel, a failure pauses it for the next step. A real success (or the Resume button)
  resumes it too.
- The error a notice carries is a short code (``safe_error_code``): never an exception
  text, a URL, a host or a secret.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from app.notifications.channels import BUILTIN_PUSH_ID

logger = logging.getLogger(__name__)

FAILED_EVENT_AFTER = 3
PAUSE_AFTER = 20
BACKOFF_S: tuple[int, ...] = (300, 900, 3600, 21600)
ANNOUNCE_TTL_S = 86400
ANNOUNCE_PREFIX = "channel_failed:"
AUTOPAUSE_PREFIX = "autopause:"
# The errors of the notice sent when a browser's subscription is removed: its push service
# dropped it, it failed ``MAX_CONSECUTIVE_FAILURES`` times in a row, or the egress guard
# refuses its endpoint for good (MSHL-399).
PUSH_UNSUBSCRIBED = "push_unsubscribed"
PUSH_EVICTED = "push_evicted"
PUSH_REFUSED = "push_refused"
MAX_NAME_LEN = 80

_CODE = re.compile(r"[a-z0-9_]{1,64}")
# An Apprise or legacy URL's own failure: "line 2: http_401", "line 2 (redacted): http_401".
_LINE_CODE = re.compile(r"line (?P<line>\d{1,4})(?: \([^)]*\))?: (?P<code>[a-z0-9_]{1,64})")


def safe_error_code(error: str | None) -> str:
    """The code a notice may carry: a known shape (``auth_failed``, ``http_500``,
    ``line 2: http_401``) or ``send_failed``. The host an Apprise error names is dropped."""
    text = (error or "").strip()
    if _CODE.fullmatch(text):
        return text
    match = _LINE_CODE.fullmatch(text)
    if match:
        return f"line {match.group('line')}: {match.group('code')}"
    return "send_failed"


def backoff_s(step: int) -> int:
    """The pause of backoff step ``step`` (0 = the first pause), capped at the last one."""
    return BACKOFF_S[min(max(step, 0), len(BACKOFF_S) - 1)]


def announce_key(source: str) -> str:
    return f"{ANNOUNCE_PREFIX}{source}"


def autopause_key(channel_id: str) -> str:
    return f"{AUTOPAUSE_PREFIX}{channel_id}"


async def notice_targets(exclude: Iterable[str]) -> list[str]:
    """``builtin:push`` and every enabled channel, minus ``exclude`` (the failing one)."""
    from app.repository.notification_channels import NotificationChannelRepository

    skip = set(exclude)
    channels = await NotificationChannelRepository.list_all()
    ids = [BUILTIN_PUSH_ID, *(c.id for c in channels if c.enabled)]
    return [c for c in dict.fromkeys(ids) if c not in skip]


async def announce(
    source: str,
    *,
    channel_id: str,
    name: str,
    error: str | None,
    exclude: Iterable[str],
    now: int,
    emit_fn=None,
) -> bool:
    """Emit ``notify.channel_failed`` once a day for ``source`` (a channel id, or
    ``push:<subscription id>``). Returns whether it was emitted. Never raises."""
    from app.notifications.render import CHANNEL_FAILED_EVENT
    from app.repository.notification_channels import NotificationJournalRepository

    try:
        if not await NotificationJournalRepository.state_claim(
            announce_key(source), now + ANNOUNCE_TTL_S, now
        ):
            return False
        targets = await notice_targets(exclude)
        if not targets:
            return True
        if emit_fn is None:
            from app.notifications.dispatcher import emit as emit_fn
        await emit_fn(
            None,
            payload={
                "event": CHANNEL_FAILED_EVENT,
                "urgent": True,  # the e-mail subject's [URGENT] reads it
                "channel_id": channel_id,
                "name": (name or "")[:MAX_NAME_LEN],
                "error": safe_error_code(error),
            },
            targets=targets,
        )
        return True
    except Exception:
        logger.debug("notify.channel_failed not emitted", exc_info=True)
        return False


async def announce_push_removed(subscriptions: Iterable[tuple[str, str, str]], now: int) -> None:
    """One notice per removed browser (``(id, label, error)``, ``error`` one of the ``PUSH_*``
    codes). Never raises. Web Push carries it to administrators' devices only
    (``user_rules.ADMIN_ONLY_EVENTS``), so the device label stays with them."""
    for sub_id, label, error in subscriptions:
        await announce(
            f"push:{sub_id}",
            channel_id=BUILTIN_PUSH_ID,
            name=(label or "").strip() or sub_id[:8],
            error=error,
            exclude=(),
            now=now,
        )


async def announce_push_unsubscribed(subscriptions: Iterable[tuple[str, str]], now: int) -> None:
    """One notice per browser a push service unsubscribed (``(id, label)``). Never raises."""
    await announce_push_removed(
        [(sub_id, label, PUSH_UNSUBSCRIBED) for sub_id, label in subscriptions], now
    )
