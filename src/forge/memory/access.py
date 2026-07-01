from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from forge.contracts import EpisodeCandidate, MemoryLayer, PolicyDocument, TaskContext, TaskEvent


class MemoryAccessLayer:
    """Thin access layer over YAML-backed policies and SQLite-backed task memory."""

    def __init__(self, project_root: str | Path | None = None, db_path: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.memory_root = self.project_root / "memory"
        self.policy_root = self.memory_root / "l1_project_policy"
        self.db_path = Path(db_path) if db_path else self.memory_root / "memory.db"
        self._initialize()

    def load_constitution(self) -> PolicyDocument:
        path = self.memory_root / "l0_constitution.yaml"
        return PolicyDocument(
            layer=MemoryLayer.L0,
            name="constitution",
            path=self._relative_to_root(path),
            content=self._read_yaml_mapping(path),
        )

    def load_project_policies(self) -> list[PolicyDocument]:
        if not self.policy_root.exists():
            return []

        documents: list[PolicyDocument] = []
        for path in sorted(self.policy_root.glob("*.yaml")):
            documents.append(
                PolicyDocument(
                    layer=MemoryLayer.L1,
                    name=path.stem,
                    path=self._relative_to_root(path),
                    content=self._read_yaml_mapping(path),
                )
            )
        return documents

    def get_task_context(self, run_id: str) -> TaskContext:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, run_id, event_type, payload_json, created_at
                FROM task_events
                WHERE run_id = ?
                ORDER BY created_at ASC, event_id ASC
                """,
                (run_id,),
            ).fetchall()

        events = [
            TaskEvent(
                event_id=row["event_id"],
                run_id=row["run_id"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
        return TaskContext(run_id=run_id, events=events)

    def append_task_event(self, run_id: str, event: TaskEvent | dict[str, Any]) -> TaskEvent:
        task_event = self._coerce_task_event(run_id, event)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_events (event_id, run_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_event.event_id,
                    task_event.run_id,
                    task_event.event_type,
                    json.dumps(task_event.payload, ensure_ascii=False),
                    task_event.created_at.isoformat(),
                ),
            )
            conn.commit()

        return task_event

    def save_episode_candidate(self, episode: EpisodeCandidate) -> EpisodeCandidate:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    episode_id,
                    request_id,
                    run_id,
                    outcome,
                    summary,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    request_id = excluded.request_id,
                    run_id = excluded.run_id,
                    outcome = excluded.outcome,
                    summary = excluded.summary,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                (
                    episode.episode_id,
                    episode.request_id,
                    episode.run_id,
                    episode.outcome,
                    episode.summary,
                    episode.model_dump_json(),
                    episode.created_at.isoformat(),
                ),
            )
            conn.commit()

        return episode

    def _initialize(self) -> None:
        self.memory_root.mkdir(parents=True, exist_ok=True)
        self.policy_root.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_task_events_run_id_created_at
                ON task_events (run_id, created_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    run_id TEXT,
                    outcome TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_episodes_request_id_created_at
                ON episodes (request_id, created_at)
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _read_yaml_mapping(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}

        raw_text = path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return {}

        loaded = yaml.safe_load(raw_text)
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            return {"value": loaded}
        return loaded

    def _coerce_task_event(self, run_id: str, event: TaskEvent | dict[str, Any]) -> TaskEvent:
        if isinstance(event, TaskEvent):
            if event.run_id != run_id:
                return event.model_copy(update={"run_id": run_id})
            return event

        payload = dict(event)
        payload_run_id = payload.pop("run_id", run_id)
        payload.setdefault("payload", {})
        payload.setdefault("event_type", "generic")
        payload["run_id"] = payload_run_id
        return TaskEvent.model_validate(payload)

    def _relative_to_root(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()
