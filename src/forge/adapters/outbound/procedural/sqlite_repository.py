import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import yaml

from forge.domain.procedural import (
    ProceduralSkill,
    SkillExecution,
    SkillStatus,
    SkillStep,
    SkillStepDraft,
)


class SqliteProceduralRepository:
    def __init__(
        self,
        path: str | Path,
        *,
        skills_dir: str | Path | None = None,
        registry_path: str | Path | None = None,
    ) -> None:
        self._path = Path(path)
        self._skills_dir = Path(skills_dir) if skills_dir else self._path.parent / "skills"
        self._registry_path = (
            Path(registry_path) if registry_path else self._path.parent / "skill_registry.json"
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS skills (
                    source_l2_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL,
                    procedure TEXT NOT NULL, hints TEXT NOT NULL, status TEXT NOT NULL,
                    success_rate REAL NOT NULL, total_executions INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, executable_steps TEXT NOT NULL DEFAULT '[]',
                    step_drafts TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
                    avg_pain_index REAL, last_executed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS skill_executions (
                    skill_id TEXT NOT NULL, episode_id TEXT NOT NULL,
                    success_score REAL NOT NULL, cib_score REAL NOT NULL,
                    executed_at TEXT NOT NULL, pain_index REAL, tool_error_ratio REAL,
                    PRIMARY KEY(skill_id, episode_id)
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
            if "version" not in columns:
                db.execute("ALTER TABLE skills ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            if "avg_pain_index" not in columns:
                db.execute("ALTER TABLE skills ADD COLUMN avg_pain_index REAL")
            if "last_executed_at" not in columns:
                db.execute("ALTER TABLE skills ADD COLUMN last_executed_at TEXT")
            execution_columns = {
                row[1] for row in db.execute("PRAGMA table_info(skill_executions)")
            }
            if "pain_index" not in execution_columns:
                db.execute("ALTER TABLE skill_executions ADD COLUMN pain_index REAL")
            if "tool_error_ratio" not in execution_columns:
                db.execute("ALTER TABLE skill_executions ADD COLUMN tool_error_ratio REAL")

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

    def list_all(self) -> list[ProceduralSkill]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM skills ORDER BY updated_at ASC").fetchall()
        return [self._skill(row) for row in rows]

    def upsert(self, skill: ProceduralSkill) -> None:
        previous = self.get_by_source_l2(skill.source_l2_id)
        version = max(skill.version, previous.version + 1 if previous else 1)
        skill = ProceduralSkill(
            skill.skill_id, skill.source_l2_id, skill.procedure, skill.reflection_hints,
            skill.status, skill.success_rate, skill.total_executions, skill.updated_at,
            skill.executable_steps, skill.step_drafts, version,
            skill.avg_pain_index, skill.last_executed_at,
        )
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO skills
                (source_l2_id, skill_id, procedure, hints, status, success_rate,
                 total_executions, updated_at, executable_steps, step_drafts, version,
                 avg_pain_index, last_executed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    skill.version,
                    skill.avg_pain_index,
                    skill.last_executed_at.isoformat() if skill.last_executed_at else None,
                ),
            )
        self._write_projection(skill)
        self._write_registry()

    def record_execution(self, execution: SkillExecution) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO skill_executions VALUES (?,?,?,?,?,?,?)",
                (
                    execution.skill_id,
                    execution.episode_id,
                    execution.success_score,
                    execution.cib_score,
                    execution.executed_at.isoformat(),
                    execution.pain_index,
                    execution.tool_error_ratio,
                ),
            )

    def executions_for(self, skill_id: str) -> list[SkillExecution]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM skill_executions WHERE skill_id=?", (skill_id,)
            ).fetchall()
        return [
            SkillExecution(
                row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5], row[6]
            )
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
            int(row["version"]),
            float(row["avg_pain_index"]) if row["avg_pain_index"] is not None else None,
            datetime.fromisoformat(str(row["last_executed_at"]))
            if row["last_executed_at"] is not None
            else None,
        )

    def _write_registry(self) -> None:
        payload = [
            {
                "skill_id": skill.skill_id,
                "version": skill.version,
                "status": skill.status.value,
                "success_rate": skill.success_rate,
            }
            for skill in self.list_all()
            if skill.status is not SkillStatus.ARCHIVED
        ]
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._registry_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self._registry_path)

    def _write_projection(self, skill: ProceduralSkill) -> None:
        """Publish a review-only YAML projection; SQLite remains the execution source."""
        projection = self._skills_dir / f"{skill.skill_id}.yml"
        payload = {
            "skill_id": skill.skill_id,
            "version": skill.version,
            "source_l2_id": skill.source_l2_id,
            "status": skill.status.value,
            "procedure": list(skill.procedure),
            "reflection_hints": list(skill.reflection_hints),
            "executable_steps": [
                {
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "tool_arguments": dict(step.tool_arguments),
                }
                for step in skill.executable_steps
            ],
            "updated_at": skill.updated_at.isoformat(),
        }
        temporary = projection.with_suffix(".yml.tmp")
        temporary.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        temporary.replace(projection)
