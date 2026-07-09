"""Raw event logger for L1 Episodic Memory.

Logs original event data to JSONL files (one per day) for audit and
debugging purposes.  Path: ``data/memory/episodic/raw_events/YYYY-MM-DD.jsonl``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...utils.logging import get_logger
from ...utils.serialization import write_jsonl
from ...utils.time import today_str

logger = get_logger("agent.memory.episodic.event_logger")


class EventLogger:
    """Append raw events to daily JSONL files.

    Args:
        raw_events_dir: Directory for JSONL files.
    """

    def __init__(self, raw_events_dir: str = "data/memory/episodic/raw_events"):
        self.raw_events_dir = Path(raw_events_dir)
        self.raw_events_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: dict[str, Any]) -> None:
        """Append a single event to today's JSONL file.

        Args:
            event: Event data dict. Must include at least ``episode_id`` and ``timestamp``.
        """
        date = today_str()
        path = self.raw_events_dir / f"{date}.jsonl"
        write_jsonl(path, event)
        logger.debug(f"Logged event {event.get('episode_id', '?')} to {path}")

    def log_episode(self, episode_data: dict[str, Any]) -> None:
        """Log an episode as a raw event (convenience wrapper)."""
        self.log(episode_data)

    def read_day(self, date: str) -> list[dict[str, Any]]:
        """Read all events from a specific date.

        Args:
            date: Date string in ``YYYY-MM-DD`` format.

        Returns:
            List of event dicts.
        """
        from ...utils.serialization import read_jsonl_all
        path = self.raw_events_dir / f"{date}.jsonl"
        if not path.exists():
            return []
        return read_jsonl_all(path)

    def read_today(self) -> list[dict[str, Any]]:
        """Read all events from today."""
        return self.read_day(today_str())

    def list_days(self) -> list[str]:
        """List all dates that have event logs.

        Returns:
            List of date strings (``YYYY-MM-DD``), sorted.
        """
        files = sorted(self.raw_events_dir.glob("*.jsonl"))
        return [f.stem for f in files]