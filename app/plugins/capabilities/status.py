"""`status.set {summary}` (no permission): one line shown next to the instance.

Control characters are dropped and the line is cut at `SUMMARY_MAX_CHARS`
(140); `""` clears it. At most one change per `MIN_INTERVAL_SECONDS` (2 s),
else `quota_exceeded` with `params.retry_after`: every change is broadcast.
"""

import re

from app.plugins.capabilities.base import Call, CapabilityError, capability, invalid_params

SUMMARY_MAX_CHARS = 140
MIN_INTERVAL_SECONDS = 2.0
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")


def clean_summary(text: str) -> str | None:
    line = " ".join(_CONTROL_RE.sub(" ", text).split())
    return line[:SUMMARY_MAX_CHARS] or None


@capability("status.set", None)
async def set_status(call: Call) -> None:
    summary = call.params.get("summary")
    if not isinstance(summary, str):
        raise invalid_params("summary must be a string", fields=["summary"])
    wait = call.instance.summary_wait(MIN_INTERVAL_SECONDS)
    if wait > 0:
        raise CapabilityError(
            "quota_exceeded", "status changes too often", {"retry_after": round(wait, 1)}
        )
    await call.instance.set_summary(clean_summary(summary))
