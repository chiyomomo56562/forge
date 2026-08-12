import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from forge.domain.procedural import (
    ProceduralSkill,
    SkillExecution,
    SkillStatus,
    SkillStep,
    SkillStepDraft,
)


class SqliteProceduralRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    source_l2_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
                    procedure TEXT NOT NULL, hints TEXT NOT NULL, status TEXT NOT NULL,
                    success_rate REAL NOT NULL, total_executions INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, executable_steps TEXT NOT NULL DEFAULT '[]',
                    step_drafts TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS skill_executions (
                    skill_id TEXT NOT NULL, episode_id TEXT NOT NULL,
                    success_score REAL NOT NULL, cib_score REAL NOT NULL,
                    executed_at TEXT NOT NULL, PRIMARY KEY(skill_id, episode_id)
                );
                CREATE TABLE IF NOT EXISTS pending_hints (
                    source_id TEXT PRIMARY KEY, hint TEXT NOT NULL, tool_names TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(skills)")}
            if "executable_steps" not in columns:
                db.execute(
                    "ALTER TABLE skills ADD COLUMN executable_steps TEXT NOT NULL DEFAULT '[]'"
                )
            if "step_drafts" not in columns:
                db.execute("ALTER TABLE skills ADD COLUMN step_drafts TEXT NOT NULL DEFAULT '[]'")

    def get_by_source_l2(self, knowledge_id: str) -> ProceduralSkill | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skills WHERE source_l2_id=?", (knowledge_id,)
            ).fetchone()
        return self._skill(row) if row else None

    def get(self, skill_id: str) -> ProceduralSkill | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM skills WHERE skill_id=?", (skill_id,)).fetchone()
        return self._skill(row) if row else None

    def list_active(self) -> list[ProceduralSkill]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM skills WHERE status='active'").fetchall()
        return [self._skill(row) for row in rows]

    def upsert(self, skill: ProceduralSkill) -> None:
        import json

        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO skills
                (source_l2_id, skill_id, procedure, hints, status, success_rate,
                 total_executions, updated_at, executable_steps, step_drafts)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    skill.source_l2_id,
                    skill.skill_id,
                    json.dumps(skill.procedure),
                    json.dumps(skill.reflection_hints),
                    skill.status.value,
                    skill.success_rate,
                    skill.total_executions,
                    skill.updated_at.isoformat(),
                    json.dumps(
                        [
                            {
                                "step_id": step.step_id,
                                "tool_name": step.tool_name,
                                "tool_arguments": dict(step.tool_arguments),
                            }
                            for step in skill.executable_steps
                        ]
                    ),
                    json.dumps(
                        [
                            {
                                "draft_id": draft.draft_id,
                                "source_episode_id": draft.source_episode_id,
                                "hint": draft.hint,
                                "tool_name": draft.tool_name,
                            }
                            for draft in skill.step_drafts
                        ]
                    ),
                ),
            )

    def record_execution(self, execution: SkillExecution) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO skill_executions VALUES (?,?,?,?,?)",
                (
                    execution.skill_id,
                    execution.episode_id,
                    execution.success_score,
                    execution.cib_score,
                    execution.executed_at.isoformat(),
                ),
            )

    def executions_for(self, skill_id: str) -> list[SkillExecution]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM skill_executions WHERE skill_id=?", (skill_id,)
            ).fetchall()
        return [
            SkillExecution(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]))
            for row in rows
        ]

    def store_pending_hint(self, source_id: str, hint: str, tool_names: tuple[str, ...]) -> None:
        import json

        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO pending_hints VALUES (?,?,?)",
                (source_id, hint, json.dumps(tool_names)),
            )

    def pending_hints_for(self, source_ids: tuple[str, ...]) -> list[str]:
        return [item[1] for item in self.pending_hint_records_for(source_ids)]

    def pending_hint_records_for(
        self, source_ids: tuple[str, ...]
    ) -> list[tuple[str, str, tuple[str, ...]]]:
        import json

        if not source_ids:
            return []
        placeholders = ",".join("?" for _ in source_ids)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT source_id, hint, tool_names FROM pending_hints "
                f"WHERE source_id IN ({placeholders})",
                source_ids,
            ).fetchall()
        return [
            (str(row[0]), str(row[1]), tuple(cast(list[str], json.loads(str(row[2])))))
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        return db

    def _skill(self, row: sqlite3.Row) -> ProceduralSkill:
        import json

        executable_steps = cast(list[Mapping[str, Any]], json.loads(str(row["executable_steps"])))
        step_drafts = cast(list[Mapping[str, str]], json.loads(str(row["step_drafts"])))
        return ProceduralSkill(
            str(row["skill_id"]),
            str(row["source_l2_id"]),
            tuple(cast(list[str], json.loads(str(row["procedure"])))),
            tuple(cast(list[str], json.loads(str(row["hints"])))),
            SkillStatus(str(row["status"])),
            float(row["success_rate"]),
            int(row["total_executions"]),
            datetime.fromisoformat(str(row["updated_at"])),
            tuple(
                SkillStep(
                    str(item["step_id"]),
                    str(item["tool_name"]),
                    cast(Mapping[str, Any], item.get("tool_arguments", {})),
                )
                for item in executable_steps
            ),
            tuple(
                SkillStepDraft(
                    str(item["draft_id"]),
                    str(item["source_episode_id"]),
                    str(item["hint"]),
                    str(item["tool_name"]),
                )
                for item in step_drafts
            ),
        )
