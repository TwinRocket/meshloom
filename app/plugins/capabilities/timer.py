"""`timer.set {seconds}` (permission `timer`): one `event.timer` later.

`seconds` is `TIMER_MIN_SECONDS` (10) to `TIMER_MAX_SECONDS` (one day), else
`invalid_params` with `params.min`/`params.max`. An instance holds at most
`MAX_PENDING_TIMERS` (16) pending timers (`quota_exceeded`). Answers
`{"timer_id"}`; the event carries `timer_id` and `seconds`. Timers belong to the
running process: a restart drops them (re-arm them in `on_start`).
"""

from typing import Any

from app.plugins.capabilities.base import Call, CapabilityError, capability, invalid_params

TIMER_MIN_SECONDS = 10
TIMER_MAX_SECONDS = 86400
MAX_PENDING_TIMERS = 16


@capability("timer.set", "timer")
async def set_timer(call: Call) -> dict[str, Any]:
    value = call.params.get("seconds")
    # bool is an int: refused, like NaN.
    if type(value) not in (int, float) or value != value:
        raise invalid_params("seconds must be a number", fields=["seconds"])
    seconds = float(value)  # type: ignore[arg-type]
    if not TIMER_MIN_SECONDS <= seconds <= TIMER_MAX_SECONDS:
        raise invalid_params(
            f"seconds is {TIMER_MIN_SECONDS} to {TIMER_MAX_SECONDS}",
            fields=["seconds"],
            min=TIMER_MIN_SECONDS,
            max=TIMER_MAX_SECONDS,
        )
    timer_id = call.instance.add_timer(seconds, MAX_PENDING_TIMERS)
    if timer_id is None:
        raise CapabilityError(
            "quota_exceeded", "too many pending timers", {"limit": MAX_PENDING_TIMERS}
        )
    return {"timer_id": timer_id}
