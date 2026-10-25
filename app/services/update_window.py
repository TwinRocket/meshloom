"""Host-local auto-update window math.

Weekdays use ISO Monday=0 .. Sunday=6, matching ``datetime.weekday()``.
Equal start/end means the whole selected calendar day. An empty weekday
list never opens a window.

All wall-clock arithmetic goes through naive local datetimes localized with
``astimezone()``, so days that are 23 or 25 hours long (DST switches) keep the
configured ``HH:MM`` (``tests/test_update_window.py``, ``TestDstSwitch``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, time, timedelta

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DEFAULT_AUTO_UPDATE_WEEKDAYS: list[int] = [0, 1, 2, 3, 4, 5, 6]


def parse_hhmm(value: str) -> tuple[int, int]:
    """Parse a strict ``HH:MM`` clock time into ``(hour, minute)``."""
    text = value.strip() if isinstance(value, str) else ""
    match = _HHMM.fullmatch(text)
    if match is None:
        raise ValueError(f"invalid HH:MM: {value!r}")
    return int(match.group(1)), int(match.group(2))


def format_hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def normalize_weekdays(weekdays: Iterable[int]) -> list[int]:
    """Return a sorted unique list of ISO weekdays (Monday=0 .. Sunday=6)."""
    out: list[int] = []
    for day in weekdays:
        if isinstance(day, bool) or not isinstance(day, int) or day < 0 or day > 6:
            raise ValueError(f"invalid weekday: {day!r}")
        if day not in out:
            out.append(day)
    return sorted(out)


def host_tz_name() -> str:
    """Best-effort display name for the host local timezone."""
    aware = datetime.now().astimezone()
    tz = aware.tzinfo
    if tz is None:
        return "UTC"
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    name = tz.tzname(aware)
    if name:
        return name
    return str(tz)


def _as_local(now: datetime) -> datetime:
    """Return ``now`` as an aware datetime in the host timezone.

    A naive value is read as host wall-clock time. ``astimezone()`` on a naive
    datetime resolves the UTC offset in force at *that* date, so a value on the
    other side of a DST switch gets the right offset (``replace(tzinfo=...)`` with
    today's offset would not).
    """
    return now.astimezone()


def _local_wall_clock(day: datetime, hour: int, minute: int) -> datetime:
    """Host wall-clock ``hour:minute`` on the calendar date of ``day``, DST-correct.

    Built naive then localized, so the offset is the one in force at that moment
    rather than the offset of ``day``. A time skipped by a spring-forward gap lands
    after the gap; an ambiguous autumn time resolves to its first occurrence.
    """
    naive = datetime.combine(day.date(), time(hour, minute))
    # ``fold`` picks the offset in a gap or an overlap, and CPython versions disagree on
    # which fold is the "pre-transition" one in a gap (3.11 vs 3.14): try both.
    first, second = naive.replace(fold=0).astimezone(), naive.replace(fold=1).astimezone()
    if first.replace(tzinfo=None) == naive == second.replace(tzinfo=None):
        return min(first, second)  # autumn overlap: the first occurrence
    return max(first, second)  # spring gap: the first instant after the gap


def _clock_minutes(hour: int, minute: int) -> int:
    return hour * 60 + minute


def in_window(
    now: datetime,
    start: str,
    end: str,
    weekdays: Iterable[int],
) -> bool:
    """True when ``now`` (host local) falls inside the configured window."""
    local = _as_local(now)
    days = normalize_weekdays(weekdays)
    if not days:
        return False

    start_min = _clock_minutes(*parse_hhmm(start))
    end_min = _clock_minutes(*parse_hhmm(end))
    now_min = _clock_minutes(local.hour, local.minute)
    dow = local.weekday()

    if start_min == end_min:
        return dow in days

    if start_min < end_min:
        return dow in days and start_min <= now_min < end_min

    # Overnight wrap: 22:00–06:00 belongs to the weekday the window opened on.
    if now_min >= start_min:
        return dow in days
    if now_min < end_min:
        return (dow - 1) % 7 in days
    return False


def next_window_start(
    now: datetime,
    start: str,
    end: str,
    weekdays: Iterable[int],
) -> datetime | None:
    """Next local datetime when the window opens, or None if it never does."""
    local = _as_local(now)
    days = normalize_weekdays(weekdays)
    if not days:
        return None

    start_h, start_m = parse_hhmm(start)
    end_h, end_m = parse_hhmm(end)
    start_min = _clock_minutes(start_h, start_m)
    end_min = _clock_minutes(end_h, end_m)

    # Equal start/end is a full selected calendar day, opening at midnight.
    open_h, open_m = (0, 0) if start_min == end_min else (start_h, start_m)

    for offset in range(0, 8):
        # Step calendar dates, not 24 h: a DST switch day is 23 or 25 hours long.
        day = datetime.combine(local.date() + timedelta(days=offset), time())
        if day.weekday() not in days:
            continue
        candidate = _local_wall_clock(day, open_h, open_m)
        if candidate >= local:
            return candidate
    return None
