"""Clock helpers: every stored date is ISO-8601 UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from mhl_sentinel.clock import from_iso, to_iso, utcnow, utcnow_iso


def test_utcnow_is_aware_utc() -> None:
    now = utcnow()
    assert now.tzinfo is UTC
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


def test_to_iso_normalises_offsets_to_z() -> None:
    madrid = timezone(timedelta(hours=2))
    dt = datetime(2026, 10, 1, 14, 30, 5, 123, tzinfo=madrid)
    assert to_iso(dt) == "2026-10-01T12:30:05.000123Z"


def test_to_iso_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_iso(datetime(2026, 10, 1, 12, 0))


def test_from_iso_round_trip() -> None:
    dt = datetime(2026, 10, 1, 12, 0, 0, 42, tzinfo=UTC)
    assert from_iso(to_iso(dt)) == dt


def test_from_iso_converts_offsets_to_utc() -> None:
    parsed = from_iso("2026-10-01T14:00:00+02:00")
    assert parsed == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert parsed.utcoffset() == timedelta(0)


def test_from_iso_assumes_utc_for_naive_strings() -> None:
    assert from_iso("2026-10-01T12:00:00") == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_utcnow_iso_is_parseable_and_current() -> None:
    value = utcnow_iso()
    assert value.endswith("Z")
    assert abs(datetime.now(UTC) - from_iso(value)) < timedelta(seconds=5)
