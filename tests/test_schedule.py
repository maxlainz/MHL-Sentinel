"""Working hours (D33): weekly slots, midnight crossing and DST in Europe/Madrid."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from mhl_sentinel.config import Settings, WorkingHoursConfig
from mhl_sentinel.schedule import WorkingHours

MAD = ZoneInfo("Europe/Madrid")


def wh(days: list[str], start: str, end: str, tz: str = "Europe/Madrid") -> WorkingHours:
    settings = Settings(
        working_hours=WorkingHoursConfig(days=days, start=start, end=end), timezone=tz
    )
    return WorkingHours.from_settings(settings)


def test_from_settings_defaults() -> None:
    w = WorkingHours.from_settings(Settings())
    assert w.days == frozenset({0, 1, 2, 3, 4})
    assert (w.start, w.end) == (time(9), time(19))
    assert w.tz == ZoneInfo("UTC")


def test_default_weekday_slot() -> None:
    w = wh(["mon", "tue", "wed", "thu", "fri"], "09:00", "19:00")
    # 2026-10-05 is a Monday.
    assert not w.is_working(datetime(2026, 10, 5, 8, 59, tzinfo=MAD))
    assert w.is_working(datetime(2026, 10, 5, 9, 0, tzinfo=MAD))
    assert w.is_working(datetime(2026, 10, 5, 18, 59, tzinfo=MAD))
    assert not w.is_working(datetime(2026, 10, 5, 19, 0, tzinfo=MAD))
    assert not w.is_working(datetime(2026, 10, 10, 12, 0, tzinfo=MAD))  # Saturday
    # Aware datetimes in another zone are converted: 07:30 UTC == 09:30 CEST.
    assert w.is_working(datetime(2026, 10, 5, 7, 30, tzinfo=UTC))


def test_naive_datetime_rejected() -> None:
    w = wh(["mon"], "09:00", "19:00")
    with pytest.raises(ValueError):
        w.is_working(datetime(2026, 10, 5, 10, 0))
    with pytest.raises(ValueError):
        w.next_change(datetime(2026, 10, 5, 10, 0))


def test_next_change_within_week_and_over_weekend() -> None:
    w = wh(["mon", "tue", "wed", "thu", "fri"], "09:00", "19:00")
    assert w.next_change(datetime(2026, 10, 5, 12, 0, tzinfo=MAD)) == datetime(
        2026, 10, 5, 19, 0, tzinfo=MAD
    )
    # Friday evening → next Monday 09:00.
    assert w.next_change(datetime(2026, 10, 9, 20, 0, tzinfo=MAD)) == datetime(
        2026, 10, 12, 9, 0, tzinfo=MAD
    )


def test_slot_crossing_midnight_belongs_to_start_day() -> None:
    w = wh(["fri"], "22:00", "06:00")
    # 2026-10-09 Friday, 2026-10-10 Saturday, 2026-10-08 Thursday.
    assert not w.is_working(datetime(2026, 10, 9, 21, 59, tzinfo=MAD))
    assert w.is_working(datetime(2026, 10, 9, 23, 0, tzinfo=MAD))
    assert w.is_working(datetime(2026, 10, 10, 5, 59, tzinfo=MAD))
    assert not w.is_working(datetime(2026, 10, 10, 6, 0, tzinfo=MAD))
    assert not w.is_working(datetime(2026, 10, 10, 23, 0, tzinfo=MAD))  # Saturday night: no
    assert not w.is_working(datetime(2026, 10, 9, 3, 0, tzinfo=MAD))  # Thursday's night: no
    assert w.next_change(datetime(2026, 10, 9, 23, 0, tzinfo=MAD)) == datetime(
        2026, 10, 10, 6, 0, tzinfo=MAD
    )
    assert w.next_change(datetime(2026, 10, 10, 6, 0, tzinfo=MAD)) == datetime(
        2026, 10, 16, 22, 0, tzinfo=MAD
    )


def test_dst_spring_forward_madrid() -> None:
    # 2026-03-29 (Sunday): 02:00 CET → 03:00 CEST. A slot starting inside the gap.
    w = wh(["sun"], "02:30", "05:00")
    before = datetime(2026, 3, 29, 0, 30, tzinfo=UTC)  # 01:30 CET
    assert not w.is_working(before)
    change = w.next_change(before)
    # The wall clock jumps from 01:59:59 to 03:00, which is already past 02:30.
    assert change == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)
    assert change.astimezone(MAD).hour == 3
    assert w.is_working(change)
    assert w.next_change(change) == datetime(2026, 3, 29, 5, 0, tzinfo=MAD)


def test_dst_fall_back_madrid_uses_wall_clock() -> None:
    # 2026-10-25 (Sunday): 03:00 CEST → 02:00 CET. 09:00 is 07:00 UTC before, 08:00 UTC after.
    w = wh(["mon"], "09:00", "19:00")
    sat = datetime(2026, 10, 24, 12, 0, tzinfo=MAD)
    assert w.next_change(sat) == datetime(2026, 10, 26, 9, 0, tzinfo=MAD)
    assert w.next_change(sat).astimezone(UTC) == datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    # A slot spanning the repeated hour: the duration differs but the wall times hold.
    night = wh(["sat"], "23:00", "04:00")
    inside = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)  # 02:30 CET (second pass)
    assert night.is_working(inside)
    assert night.next_change(inside) == datetime(2026, 10, 25, 3, 0, tzinfo=UTC)


def test_never_working_raises() -> None:
    w = wh([], "09:00", "19:00")
    assert not w.is_working(datetime(2026, 10, 5, 10, 0, tzinfo=MAD))
    with pytest.raises(ValueError):
        w.next_change(datetime(2026, 10, 5, 10, 0, tzinfo=MAD))


def test_equal_start_and_end_is_never_working() -> None:
    w = WorkingHours(
        days=frozenset(range(7)), start=time(9), end=time(9), tz=ZoneInfo("Europe/Madrid")
    )
    assert not w.crosses_midnight
    assert not w.is_working(datetime(2026, 10, 5, 9, 0, tzinfo=MAD))
    assert not w.is_working(datetime(2026, 10, 5, 15, 0, tzinfo=MAD))


def test_never_working_late_in_the_day_exhausts_the_candidates() -> None:
    # Late in the day every candidate falls inside the horizon: the loop ends without a flip.
    w = wh([], "09:00", "19:00")
    with pytest.raises(ValueError, match="never change"):
        w.next_change(datetime(2026, 10, 5, 23, 59, tzinfo=MAD))


def test_first_flip_stops_when_the_midpoint_cannot_advance() -> None:
    w = wh(["mon"], "09:00", "19:00", tz="UTC")
    lo = datetime(2026, 10, 5, 8, 0, 0, tzinfo=UTC)
    hi = lo + timedelta(seconds=1, microseconds=1)
    # mid truncates back to ``lo`` (sub-second): the search gives up and returns ``hi``.
    assert w._first_flip(lo, hi, True) == hi
    # A regular bisection narrows to the first second that differs.
    start = datetime(2026, 10, 5, 8, 59, 0, tzinfo=UTC)
    assert w._first_flip(start, start + timedelta(seconds=120), False) == datetime(
        2026, 10, 5, 9, 0, 0, tzinfo=UTC
    )
