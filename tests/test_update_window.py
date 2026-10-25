import time as _time
from datetime import UTC, datetime, timedelta

import pytest

from app.services.update_window import (
    host_tz_name,
    in_window,
    next_window_start,
    parse_hhmm,
)


def _at(weekday: int, hour: int, minute: int) -> datetime:
    """Build a timezone-aware local datetime on a specific ISO weekday.

    Built from the calendar date and localized as wall-clock time, so it stays correct
    when the week contains a DST switch (a fixed-offset ``+ timedelta(days=n)`` would
    shift the wall clock by an hour).
    """
    today = datetime.now().astimezone().date()
    day = today + timedelta(days=weekday - today.weekday())
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute).astimezone()


class TestParseHhmm:
    def test_accepts_valid_times(self):
        assert parse_hhmm("00:00") == (0, 0)
        assert parse_hhmm("22:00") == (22, 0)
        assert parse_hhmm(" 06:05 ") == (6, 5)

    def test_rejects_invalid_times(self):
        with pytest.raises(ValueError):
            parse_hhmm("24:00")
        with pytest.raises(ValueError):
            parse_hhmm("9:00")
        with pytest.raises(ValueError):
            parse_hhmm("")


class TestInWindow:
    def test_overnight_wrap(self):
        weekdays = [0]
        assert in_window(_at(0, 23, 0), "22:00", "06:00", weekdays) is True
        assert in_window(_at(1, 3, 0), "22:00", "06:00", weekdays) is True
        assert in_window(_at(0, 12, 0), "22:00", "06:00", weekdays) is False
        assert in_window(_at(1, 3, 0), "22:00", "06:00", [1]) is False
        assert in_window(_at(1, 22, 30), "22:00", "06:00", [1]) is True
        assert in_window(_at(1, 6, 0), "22:00", "06:00", weekdays) is False

    def test_full_day_when_start_equals_end(self):
        assert in_window(_at(0, 12, 0), "00:00", "00:00", [0]) is True
        assert in_window(_at(0, 0, 0), "08:00", "08:00", [0]) is True
        assert in_window(_at(1, 12, 0), "00:00", "00:00", [0]) is False

    def test_empty_weekdays_never_matches(self):
        assert in_window(_at(0, 12, 0), "00:00", "00:00", []) is False
        assert in_window(_at(0, 23, 0), "22:00", "06:00", []) is False

    def test_same_day_range(self):
        weekdays = [0]
        assert in_window(_at(0, 8, 0), "08:00", "18:00", weekdays) is True
        assert in_window(_at(0, 17, 59), "08:00", "18:00", weekdays) is True
        assert in_window(_at(0, 18, 0), "08:00", "18:00", weekdays) is False
        assert in_window(_at(0, 7, 59), "08:00", "18:00", weekdays) is False


class TestNextWindowStart:
    def test_empty_weekdays_is_none(self):
        assert next_window_start(_at(0, 12, 0), "22:00", "06:00", []) is None

    def test_overnight_next_open(self):
        now = _at(0, 12, 0)
        nxt = next_window_start(now, "22:00", "06:00", [0])
        assert nxt is not None
        assert nxt.weekday() == 0
        assert (nxt.hour, nxt.minute) == (22, 0)

    def test_full_day_opens_at_midnight(self):
        now = _at(1, 12, 0)
        nxt = next_window_start(now, "00:00", "00:00", [0])
        assert nxt is not None
        assert nxt.weekday() == 0
        assert (nxt.hour, nxt.minute) == (0, 0)
        assert nxt > now

    def test_naive_datetime_is_localized(self):
        naive = datetime(2026, 1, 5, 12, 0, 0)  # Monday
        assert in_window(naive, "00:00", "00:00", [0]) is True


def test_host_tz_name_is_nonempty():
    name = host_tz_name()
    assert isinstance(name, str)
    assert name


def test_in_window_accepts_utc_aware_now():
    utc_now = datetime.now(UTC)
    local = utc_now.astimezone()
    assert in_window(utc_now, "00:00", "00:00", [local.weekday()]) is True


@pytest.fixture
def paris(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Paris")
    _time.tzset()
    yield
    monkeypatch.undo()
    _time.tzset()


def _paris(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).astimezone()


@pytest.mark.skipif(not hasattr(_time, "tzset"), reason="needs time.tzset")
class TestDstSwitch:
    """Europe/Paris: 2026-03-29 (spring, 23 h day) and 2026-10-25 (autumn, 25 h day)."""

    def test_autumn_day_same_day_range(self, paris):
        # Sunday 2026-10-25, weekday 6
        assert in_window(_paris(2026, 10, 25, 8), "08:00", "18:00", [6]) is True
        assert in_window(_paris(2026, 10, 25, 17, 59), "08:00", "18:00", [6]) is True
        assert in_window(_paris(2026, 10, 25, 18), "08:00", "18:00", [6]) is False
        assert in_window(_paris(2026, 10, 25, 7, 59), "08:00", "18:00", [6]) is False

    def test_spring_day_same_day_range(self, paris):
        # Sunday 2026-03-29
        assert in_window(_paris(2026, 3, 29, 8), "08:00", "18:00", [6]) is True
        assert in_window(_paris(2026, 3, 29, 18), "08:00", "18:00", [6]) is False

    def test_overnight_across_autumn_switch(self, paris):
        # Window opened Saturday 22:00, still open Sunday 03:00 (after the 25 h night).
        assert in_window(_paris(2026, 10, 25, 3), "22:00", "06:00", [5]) is True
        assert in_window(_paris(2026, 10, 25, 6), "22:00", "06:00", [5]) is False

    def test_next_start_is_wall_clock_across_autumn(self, paris):
        now = _paris(2026, 10, 24, 12)  # Saturday, CEST
        nxt = next_window_start(now, "08:00", "18:00", [0])  # Monday, CET
        assert nxt is not None
        assert nxt.date().isoformat() == "2026-10-26"
        assert (nxt.hour, nxt.minute) == (8, 0)
        assert nxt.utcoffset() == timedelta(hours=1)

    def test_next_start_is_wall_clock_across_spring(self, paris):
        now = _paris(2026, 3, 28, 12)  # Saturday, CET
        nxt = next_window_start(now, "08:00", "18:00", [0])  # Monday, CEST
        assert nxt is not None
        assert nxt.date().isoformat() == "2026-03-30"
        assert (nxt.hour, nxt.minute) == (8, 0)
        assert nxt.utcoffset() == timedelta(hours=2)

    def test_full_day_opens_at_midnight_after_switch(self, paris):
        now = _paris(2026, 10, 24, 12)
        nxt = next_window_start(now, "00:00", "00:00", [6])  # Sunday 25th, 00:00 CEST
        assert nxt is not None
        assert nxt.date().isoformat() == "2026-10-25"
        assert (nxt.hour, nxt.minute) == (0, 0)

    def test_naive_datetime_after_switch_uses_its_own_offset(self, paris):
        naive = datetime(2026, 10, 25, 12, 0)  # CET by then
        assert in_window(naive, "08:00", "18:00", [6]) is True

    def test_spring_gap_window_start_resolves_after_the_gap(self, paris):
        # 02:30 does not exist on 2026-03-29 (02:00 -> 03:00): the start lands at 03:30 CEST.
        now = _paris(2026, 3, 28, 12)  # Saturday
        nxt = next_window_start(now, "02:30", "06:00", [6])
        assert nxt is not None
        assert nxt.date().isoformat() == "2026-03-29"
        assert (nxt.hour, nxt.minute) == (3, 30)
        assert nxt.utcoffset() == timedelta(hours=2)
