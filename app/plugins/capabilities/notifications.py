"""`notifications.send {title, body?, level?, event_id?}` (permission `notifications.send`).

A plugin notifies the user through the host's notification service (MSHL-213): the
dispatcher of `app/notifications/`, as the catalogue event `plugin.notification`. The
plugin never names a destination and never sees a URL or a channel id: the user picks
the channels in Settings > Notifications > Default notifications ("Extension notifications"), like for
any other event; none is ticked until the user does. The pauses, quiet hours, the
channel limits and the delivery log apply as for any event, and the event is never
urgent, whatever its `level`.

Checks, in order, each before any side effect: the parameters (`invalid_params` with
`params.fields`), then the instance's quota (`quota_exceeded` with `params.scope`
`plugin`, `params.period` `hour` or `day`, `params.limit`, `params.retry_after`).

- `title`: 1 to `TITLE_MAX_CHARS` (100) characters; control characters become spaces
  and runs of whitespace one space. `body`: up to `BODY_MAX_CHARS` (1000), line breaks
  kept, other control characters dropped. `level`: `info` (default), `warning` or
  `critical` (a prefix of the title, never a priority).
- Quota: `per_hour` (default `DEFAULT_PER_HOUR`, 10) per clock hour and `per_day`
  (default `DEFAULT_PER_DAY`, 50) per UTC day, the manifest's request capped by the
  registry (30 and 100) and narrowed by the grant; fixed windows in memory: a restart of
  Meshloom resets them, a restart of the child does not. Every accepted call counts,
  whether or not a channel is ticked.
- Answers `{"queued": n}`: the channels the notification was queued for (0 when none is
  ticked, enabled or free of a pause). A test event (`test: true`) stops after the
  parameters: `{"queued": 0, "test": true}`, nothing sent, no quota used.

Rules: docs/dev/plugins.md, "Capabilities".
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.plugins.capabilities.base import Call, CapabilityError, capability, invalid_params
from app.plugins.permissions import (
    NOTIFICATIONS_SEND_MAX_PER_DAY,
    NOTIFICATIONS_SEND_MAX_PER_HOUR,
)

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 100
BODY_MAX_CHARS = 1000
DEFAULT_PER_HOUR = 10
DEFAULT_PER_DAY = 50

Period = Literal["hour", "day"]
_PERIOD_SECONDS: dict[Period, int] = {"hour": 3600, "day": 86400}

# C0 and C1 controls, DEL and the Unicode line/paragraph separators; the body keeps "\n".
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_BODY_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f  ]")


class SendParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=TITLE_MAX_CHARS)
    body: str = Field(default="", max_length=BODY_MAX_CHARS)
    level: Literal["info", "warning", "critical"] = "info"
    event_id: int | None = None


def clean_title(text: str) -> str:
    return " ".join(_CONTROL_RE.sub(" ", text).split())


def clean_body(text: str) -> str:
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = _BODY_CONTROL_RE.sub("", unified).split("\n")
    return "\n".join(" ".join(line.split()) for line in lines).strip()


class NotifyLimits:
    def __init__(self, per_hour: int, per_day: int):
        self.per_hour = per_hour
        self.per_day = per_day

    def of(self, period: Period) -> int:
        return self.per_hour if period == "hour" else self.per_day


def _bounded(value: Any, cap: int) -> int | None:
    return min(value, cap) if type(value) is int and value >= 1 else None


def effective_limits(permission: Mapping[str, Any]) -> NotifyLimits:
    """The instance's limits: its (already narrowed) request, or the default, capped."""
    return NotifyLimits(
        per_hour=_bounded(permission.get("per_hour"), NOTIFICATIONS_SEND_MAX_PER_HOUR)
        or DEFAULT_PER_HOUR,
        per_day=_bounded(permission.get("per_day"), NOTIFICATIONS_SEND_MAX_PER_DAY)
        or DEFAULT_PER_DAY,
    )


class NotifyQuota:
    """Fixed windows (clock hour, UTC day) per instance, in memory. `clock` is an
    attribute so tests control the windows."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._counts: dict[tuple[str, Period], tuple[int, int]] = {}

    def take(self, instance_id: str, limits: NotifyLimits) -> tuple[Period, int, int] | None:
        """Count one notification; None when it fits, else `(period, limit, retry_after)`
        of the first full window (nothing is counted then)."""
        now = int(self.clock())
        current: dict[Period, tuple[int, int]] = {}
        for period, length in _PERIOD_SECONDS.items():
            start = now - now % length
            stored_start, count = self._counts.get((instance_id, period), (start, 0))
            count = count if stored_start == start else 0
            limit = limits.of(period)
            if count >= limit:
                return period, limit, max(1, start + length - now)
            current[period] = (start, count)
        for period, (start, count) in current.items():
            self._counts[(instance_id, period)] = (start, count + 1)
        return None

    def forget(self, instance_id: str) -> None:
        for period in _PERIOD_SECONDS:
            self._counts.pop((instance_id, period), None)


QUOTA = NotifyQuota()


def parse(params: dict[str, Any]) -> SendParams:
    """The params, validated then cleaned (`invalid_params` with `params.fields`)."""
    try:
        parsed = SendParams.model_validate(params)
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors() if e.get("loc")})
        raise invalid_params("invalid notifications.send parameters", fields=fields) from None
    title = clean_title(parsed.title)
    if not title:
        raise invalid_params("title is empty once cleaned", fields=["title"])
    return parsed.model_copy(update={"title": title, "body": clean_body(parsed.body)})


def quota_refusal(period: Period, limit: int, retry_after: int) -> CapabilityError:
    return CapabilityError(
        "quota_exceeded",
        f"at most {limit} notifications per {period}",
        {"scope": "plugin", "period": period, "limit": limit, "retry_after": retry_after},
    )


@capability("notifications.send", "notifications.send")
async def send(call: Call) -> dict[str, Any]:
    from app.notifications.producers import emit_plugin_notification

    params = parse(call.params)
    if call.event is not None and call.event.test:
        return {"queued": 0, "test": True}
    instance = call.instance
    limits = effective_limits(instance.permissions["notifications.send"])
    full = QUOTA.take(instance.instance_id, limits)
    if full is not None:
        raise quota_refusal(*full)
    queued = await emit_plugin_notification(
        instance_id=instance.instance_id,
        plugin_id=instance.plugin_id,
        label=instance.label,
        title=params.title,
        body=params.body,
        level=params.level,
    )
    # Never the text: it is the plugin's, and may quote a message.
    logger.info(
        "Plugin %s notifications.send (%s): queued for %d channel(s)",
        instance.instance_id,
        params.level,
        queued,
    )
    return {"queued": queued}
