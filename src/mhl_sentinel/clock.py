"""Tiny time helpers: every date stored in the DB is ISO-8601 UTC (docs/arquitectura.md)."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current instant as an aware UTC datetime."""
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    """Aware datetime → ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (UTC). Naive datetimes are rejected."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime: pass an aware datetime")
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def from_iso(value: str) -> datetime:
    """Inverse of :func:`to_iso` (also accepts any ISO-8601 string with offset)."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utcnow_iso() -> str:
    """Current instant as an ISO-8601 UTC string."""
    return to_iso(utcnow())
