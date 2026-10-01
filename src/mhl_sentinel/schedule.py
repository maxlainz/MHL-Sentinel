"""Studio working hours (D33): inside them the app does not scan, hash or verify.

A slot with ``start > end`` crosses midnight and belongs to the entry in ``days`` of the day it
starts on (``fri 22:00-06:00`` covers Friday night and Saturday until 06:00). Times are wall-clock
times in ``Settings.timezone``; DST changes are handled by comparing local wall-clock times.
See the vault note `Ventana de inactividad (quiet hours)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mhl_sentinel.config import DAYS, Settings

_HORIZON_DAYS = 8


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive datetime: pass an aware datetime")


@dataclass(frozen=True, slots=True)
class WorkingHours:
    days: frozenset[int]  # weekday() numbers, Monday == 0
    start: time
    end: time
    tz: ZoneInfo

    @classmethod
    def from_settings(cls, settings: Settings) -> WorkingHours:
        wh = settings.working_hours
        return cls(
            days=frozenset(DAYS.index(d) for d in wh.days),
            start=_parse_hhmm(wh.start),
            end=_parse_hhmm(wh.end),
            tz=ZoneInfo(settings.timezone),
        )

    @property
    def crosses_midnight(self) -> bool:
        return self.start > self.end

    def is_working(self, now: datetime) -> bool:
        """True if ``now`` (aware) falls inside the studio working hours."""
        _require_aware(now)
        local = now.astimezone(self.tz)
        wall = local.time().replace(tzinfo=None)
        weekday = local.weekday()
        if self.start == self.end:  # rejected by config; defensive: no working hours
            return False
        if not self.crosses_midnight:
            return weekday in self.days and self.start <= wall < self.end
        if weekday in self.days and wall >= self.start:
            return True
        return (weekday - 1) % 7 in self.days and wall < self.end

    def next_change(self, now: datetime) -> datetime:
        """Next instant (aware, in ``tz``) at which :meth:`is_working` flips.

        Searches up to 8 days ahead; raises ``ValueError`` if the predicate never flips (no
        working days).
        """
        _require_aware(now)
        current = self.is_working(now)
        local_now = now.astimezone(self.tz)
        candidates: set[datetime] = set()
        for offset in range(-1, _HORIZON_DAYS + 1):
            day = local_now.date() + timedelta(days=offset)
            for wall in (self.start, self.end):
                for fold in (0, 1):
                    local = datetime.combine(day, wall, tzinfo=self.tz).replace(fold=fold)
                    candidates.add(local.astimezone(UTC))
        ordered = sorted(candidates)
        now_utc = now.astimezone(UTC)
        limit = now_utc + timedelta(days=_HORIZON_DAYS)
        previous = now_utc
        for candidate in ordered:
            if candidate <= now_utc:
                continue
            if candidate > limit:
                break
            if self.is_working(candidate) != current:
                # A wall time inside a DST gap maps after the real transition: refine.
                return self._first_flip(previous, candidate, current).astimezone(self.tz)
            previous = candidate
        raise ValueError("working hours never change within the search horizon")

    def _first_flip(self, lo: datetime, hi: datetime, current: bool) -> datetime:
        """Earliest second in ``(lo, hi]`` where the predicate differs from ``current``."""
        while hi - lo > timedelta(seconds=1):
            mid = lo + (hi - lo) / 2
            mid = mid.replace(microsecond=0) if mid.microsecond else mid
            if mid <= lo:
                break
            if self.is_working(mid) != current:
                hi = mid
            else:
                lo = mid
        return hi
