"""Unit tests for Phase 0.3 utilities (ids, time, serialization, logging)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# ids.py
# ---------------------------------------------------------------------------

class TestIds:
    def test_episode_id_format(self):
        from agent.utils.ids import generate_episode_id

        eid = generate_episode_id()
        assert re.match(r"^ep_\d{8}_\d{6}_[0-9a-f]{8}$", eid), f"Bad format: {eid}"

    def test_episode_id_unique(self):
        from agent.utils.ids import generate_episode_id

        ids = {generate_episode_id() for _ in range(100)}
        assert len(ids) == 100

    def test_skill_id_format(self):
        from agent.utils.ids import generate_skill_id

        sid = generate_skill_id("Web Search Utility")
        assert sid.startswith("web_search_utility_")
        assert re.match(r"^web_search_utility_[0-9a-f]{8}$", sid)

    def test_skill_id_strips_special_chars(self):
        from agent.utils.ids import generate_skill_id

        sid = generate_skill_id("PDF-Reader v2.0!")
        assert sid.startswith("pdf_reader_v2_0_")
        assert all(c.isalnum() or c == "_" for c in sid)

    def test_reflection_id_links_episode(self):
        from agent.utils.ids import generate_reflection_id

        eid = "ep_20260703_143052_a1b2c3d4"
        rid = generate_reflection_id(eid)
        assert rid == f"refl_{eid}"

    def test_plan_id_format(self):
        from agent.utils.ids import generate_plan_id

        pid = generate_plan_id()
        assert pid.startswith("plan_")

    def test_eval_id_format(self):
        from agent.utils.ids import generate_eval_id

        eid = generate_eval_id()
        assert eid.startswith("eval_")

    def test_session_id_format(self):
        from agent.utils.ids import generate_session_id

        sid = generate_session_id()
        assert sid.startswith("sess_")

    def test_record_id_custom_prefix(self):
        from agent.utils.ids import generate_record_id

        rid = generate_record_id("custom")
        assert rid.startswith("custom_")

    def test_run_id_cached(self):
        from agent.utils.ids import generate_run_id

        r1 = generate_run_id()
        r2 = generate_run_id()
        assert r1 == r2  # cached per process


# ---------------------------------------------------------------------------
# time.py
# ---------------------------------------------------------------------------

class TestTime:
    def test_iso_now_format(self):
        from agent.utils.time import iso_now

        ts = iso_now()
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts)

    def test_parse_iso_roundtrip(self):
        from agent.utils.time import iso_now, parse_iso, iso_format

        ts = iso_now()
        dt = parse_iso(ts)
        assert iso_format(dt) == ts

    def test_parse_iso_with_z(self):
        from agent.utils.time import parse_iso

        dt = parse_iso("2026-07-03T10:00:00Z")
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 3
        assert dt.tzinfo is not None

    def test_parse_iso_naive_assumed_utc(self):
        from agent.utils.time import parse_iso

        dt = parse_iso("2026-07-03T10:00:00")
        assert dt.tzinfo is not None

    def test_today_str_format(self):
        from agent.utils.time import today_str

        ts = today_str()
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", ts)

    def test_days_ago(self):
        from agent.utils.time import days_ago, utc_now

        past = days_ago(7)
        now = utc_now()
        diff = now - past
        assert 6.9 < diff.total_seconds() / 86400 < 7.1

    def test_sliding_window(self):
        from agent.utils.time import sliding_window

        result = sliding_window([1, 2, 3, 4, 5], 3)
        assert result == [[1, 2, 3], [2, 3, 4], [3, 4, 5]]

    def test_sliding_window_small_input(self):
        from agent.utils.time import sliding_window

        result = sliding_window([1, 2], 5)
        assert result == [[1, 2]]

    def test_sliding_window_empty(self):
        from agent.utils.time import sliding_window

        assert sliding_window([], 3) == []

    def test_last_n(self):
        from agent.utils.time import last_n

        assert last_n([1, 2, 3, 4, 5], 3) == [3, 4, 5]

    def test_last_n_more_than_available(self):
        from agent.utils.time import last_n

        assert last_n([1, 2], 5) == [1, 2]

    def test_last_n_zero(self):
        from agent.utils.time import last_n

        assert last_n([1, 2, 3], 0) == []

    def test_time_range(self):
        from agent.utils.time import time_range

        delta = time_range("2026-07-03T10:00:00Z", "2026-07-03T12:00:00Z")
        assert delta.total_seconds() == 7200

    def test_epoch_seconds(self):
        from agent.utils.time import epoch_seconds

        ts = "1970-01-01T00:00:00Z"
        assert epoch_seconds(ts) == 0.0


# ---------------------------------------------------------------------------
# serialization.py
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_write_and_read_json(self, tmp_path):
        from agent.utils.serialization import write_json, read_json

        path = tmp_path / "test.json"
        data = {"name": "테스트", "value": 42, "list": [1, 2, 3]}
        write_json(path, data)
        result = read_json(path)
        assert result == data

    def test_write_json_creates_parent_dirs(self, tmp_path):
        from agent.utils.serialization import write_json, read_json

        path = tmp_path / "sub" / "dir" / "test.json"
        write_json(path, {"a": 1})
        assert path.exists()
        assert read_json(path) == {"a": 1}

    def test_write_and_read_jsonl(self, tmp_path):
        from agent.utils.serialization import write_jsonl, read_jsonl_all

        path = tmp_path / "test.jsonl"
        write_jsonl(path, {"id": 1, "msg": "first"})
        write_jsonl(path, {"id": 2, "msg": "second"})
        records = read_jsonl_all(path)
        assert len(records) == 2
        assert records[0]["id"] == 1
        assert records[1]["msg"] == "second"

    def test_read_jsonl_iterator(self, tmp_path):
        from agent.utils.serialization import write_jsonl, read_jsonl

        path = tmp_path / "test.jsonl"
        for i in range(5):
            write_jsonl(path, {"i": i})
        records = list(read_jsonl(path))
        assert len(records) == 5
        assert records[3]["i"] == 3

    def test_jsonl_skips_empty_lines(self, tmp_path):
        from agent.utils.serialization import write_jsonl, read_jsonl_all

        path = tmp_path / "test.jsonl"
        write_jsonl(path, {"a": 1})
        # Manually add an empty line
        with path.open("a") as f:
            f.write("\n")
        write_jsonl(path, {"b": 2})
        records = read_jsonl_all(path)
        assert len(records) == 2

    def test_write_and_read_yaml(self, tmp_path):
        from agent.utils.serialization import write_yaml, read_yaml

        path = tmp_path / "test.yml"
        data = {"version": 1, "items": ["a", "b"], "nested": {"key": "값"}}
        write_yaml(path, data)
        result = read_yaml(path)
        assert result == data

    def test_write_yaml_preserves_unicode(self, tmp_path):
        from agent.utils.serialization import write_yaml, read_yaml

        path = tmp_path / "test.yml"
        data = {"name": "한글테스트", "desc": "유니코드 보존"}
        write_yaml(path, data)
        result = read_yaml(path)
        assert result["name"] == "한글테스트"

    def test_write_and_read_pickle(self, tmp_path):
        from agent.utils.serialization import write_pickle, read_pickle

        path = tmp_path / "test.pkl"
        data = {"complex": [1, 2, {"a": "b"}], "set": {1, 2, 3}}
        write_pickle(path, data)
        result = read_pickle(path)
        assert result["complex"] == [1, 2, {"a": "b"}]
        assert result["set"] == {1, 2, 3}

    def test_ensure_file_creates_if_missing(self, tmp_path):
        from agent.utils.serialization import ensure_file

        path = tmp_path / "sub" / "new.txt"
        ensure_file(path, "default")
        assert path.read_text() == "default"

    def test_ensure_file_no_overwrite(self, tmp_path):
        from agent.utils.serialization import ensure_file

        path = tmp_path / "existing.txt"
        path.write_text("original")
        ensure_file(path, "default")
        assert path.read_text() == "original"


# ---------------------------------------------------------------------------
# logging.py
# ---------------------------------------------------------------------------

class TestLogging:
    def test_setup_logging_from_config(self, tmp_path):
        from agent.utils.logging import setup_logging, reset_logging, get_logger

        # Create a minimal logging config
        log_file = tmp_path / "test.log"
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "simple": {
                    "format": "%(levelname)s %(name)s: %(message)s",
                }
            },
            "handlers": {
                "file": {
                    "class": "logging.FileHandler",
                    "level": "DEBUG",
                    "formatter": "simple",
                    "filename": str(log_file),
                }
            },
            "loggers": {
                "test_agent": {
                    "level": "DEBUG",
                    "handlers": ["file"],
                    "propagate": False,
                }
            },
            "root": {"level": "WARNING", "handlers": []},
        }
        config_path = tmp_path / "logging.yml"
        with config_path.open("w") as f:
            yaml.dump(config, f)

        reset_logging()
        setup_logging(config_path)
        logger = get_logger("test_agent")
        logger.info("Test message 12345")

        assert log_file.exists()
        content = log_file.read_text()
        assert "Test message 12345" in content

    def test_setup_logging_fallback_no_config(self, tmp_path):
        from agent.utils.logging import setup_logging, reset_logging, get_logger

        reset_logging()
        setup_logging(str(tmp_path / "nonexistent.yml"))
        logger = get_logger("test_fallback")
        logger.info("Fallback works")
        # Should not raise

    def test_setup_logging_idempotent(self):
        from agent.utils.logging import setup_logging, reset_logging

        reset_logging()
        setup_logging()
        setup_logging()  # Should not raise or re-init
        # If we get here, it's idempotent

    def test_get_logger_auto_init(self):
        from agent.utils.logging import reset_logging, get_logger

        reset_logging()
        logger = get_logger("test_auto")
        assert logger is not None
        assert logger.name == "test_auto"