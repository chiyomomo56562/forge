"""Serialization utilities for the Forge agent framework.

Provides JSONL read/write, YAML load/dump, and pickle helpers
used across memory layers, audit logs, and configuration files.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, Iterator

import yaml


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def write_json(path: str | Path, data: Any, *, indent: int = 2) -> None:
    """Write *data* to a JSON file, creating parent directories if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False, default=str)
        f.write("\n")


def read_json(path: str | Path) -> Any:
    """Read and return the contents of a JSON file."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------

def write_jsonl(path: str | Path, record: dict) -> None:
    """Append a single record to a JSONL file (one JSON object per line).

    Creates parent directories if needed. Each call appends one line.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: str | Path) -> Iterator[dict]:
    """Yield records from a JSONL file, one dict at a time.

    Skips empty lines silently.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_jsonl_all(path: str | Path) -> list[dict]:
    """Read all records from a JSONL file into a list."""
    return list(read_jsonl(path))


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

def write_yaml(path: str | Path, data: Any) -> None:
    """Write *data* to a YAML file, creating parent directories if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def read_yaml(path: str | Path) -> Any:
    """Read and return the contents of a YAML file."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Pickle
# ---------------------------------------------------------------------------

def write_pickle(path: str | Path, obj: Any) -> None:
    """Pickle *obj* to a file, creating parent directories if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)


def read_pickle(path: str | Path) -> Any:
    """Unpickle and return the contents of a file."""
    path = Path(path)
    with path.open("rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# Convenience: ensure file exists
# ---------------------------------------------------------------------------

def ensure_file(path: str | Path, default_content: str = "") -> None:
    """Create a file with *default_content* if it does not exist."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default_content, encoding="utf-8")