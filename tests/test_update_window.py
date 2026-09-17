from datetime import UTC, datetime, timedelta

import pytest

from app.services.update_window import (
    host_tz_name,
    in_window,
    next_window_start,
    parse_hhmm,
)


def _at(weekday: int, hour: int, minute: int) -> datetime:
    """Build a timezone-aware local datetime on a specific ISO weekday."""
    local = datetime.now().astimezone()
    delta = weekday - local.weekday()
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=delta)


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
