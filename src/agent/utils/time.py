"""Time utilities for the Forge agent framework.

Provides ISO 8601 timestamp generation, date parsing, and sliding-window
calculation helpers used across memory layers and loops.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence


def utc_now() -> datetime:
    """Return the current UTC datetime as a timezone-aware object."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string.

    Example: ``2026-07-03T10:00:00Z``
    """
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_format(dt: datetime) -> str:
    """Format a datetime as ISO 8601 (``Z`` suffix for UTC).

    If *dt* is naive (no timezone), it is assumed to be UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string into a timezone-aware datetime.

    Handles trailing ``Z`` and optional fractional seconds.

    Raises:
        ValueError: If *ts* is not a valid ISO 8601 timestamp.
    """
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def today_str() -> str:
    """Return today's UTC date as ``YYYY-MM-DD``."""
    return utc_now().strftime("%Y-%m-%d")


def date_str(dt: datetime) -> str:
    """Return a datetime's UTC date as ``YYYY-MM-DD``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def days_ago(n: int) -> datetime:
    """Return the UTC datetime *n* days ago from now."""
    return utc_now() - timedelta(days=n)


def sliding_window(
    items: Sequence,
    window_size: int,
) -> list[list]:
    """Split a sequence into sliding windows of *window_size*.

    Example::

        sliding_window([1, 2, 3, 4, 5], 3)
        → [[1, 2, 3], [2, 3, 4], [3, 4, 5]]

    If ``len(items) < window_size``, returns a single window with all items.
    """
    if len(items) <= window_size:
        return [list(items)] if items else []
    return [list(items[i : i + window_size]) for i in range(len(items) - window_size + 1)]


def last_n(items: Sequence, n: int) -> list:
    """Return the last *n* items from a sequence (as a list).

    If the sequence has fewer than *n* items, returns all of them.
    """
    if n <= 0:
        return []
    return list(items[-n:])


def time_range(start: str, end: str) -> timedelta:
    """Return the timedelta between two ISO 8601 timestamps."""
    return parse_iso(end) - parse_iso(start)


def epoch_seconds(ts: str) -> float:
    """Return the epoch seconds for an ISO 8601 timestamp."""
    return parse_iso(ts).timestamp()