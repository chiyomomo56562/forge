"""Utility package for the Forge agent framework.

Re-exports commonly used helpers for convenience.
"""

from .ids import (
    generate_episode_id,
    generate_eval_id,
    generate_plan_id,
    generate_record_id,
    generate_reflection_id,
    generate_run_id,
    generate_session_id,
    generate_skill_id,
)
from .logging import get_logger, reset_logging, setup_logging
from .serialization import (
    ensure_file,
    read_json,
    read_jsonl,
    read_jsonl_all,
    read_pickle,
    read_yaml,
    write_json,
    write_jsonl,
    write_pickle,
    write_yaml,
)
from .time import (
    date_str,
    days_ago,
    epoch_seconds,
    iso_format,
    iso_now,
    last_n,
    parse_iso,
    sliding_window,
    time_range,
    today_str,
    utc_now,
)

__all__ = [
    # ids
    "generate_episode_id",
    "generate_eval_id",
    "generate_plan_id",
    "generate_record_id",
    "generate_reflection_id",
    "generate_run_id",
    "generate_session_id",
    "generate_skill_id",
    # logging
    "get_logger",
    "reset_logging",
    "setup_logging",
    # serialization
    "ensure_file",
    "read_json",
    "read_jsonl",
    "read_jsonl_all",
    "read_pickle",
    "read_yaml",
    "write_json",
    "write_jsonl",
    "write_pickle",
    "write_yaml",
    # time
    "date_str",
    "days_ago",
    "epoch_seconds",
    "iso_format",
    "iso_now",
    "last_n",
    "parse_iso",
    "sliding_window",
    "time_range",
    "today_str",
    "utc_now",
]