import sqlite3
from datetime import datetime
from pathlib import Path

from forge.domain.procedural import ProceduralSkill, SkillExecution, SkillStatus


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
                    updated_at TEXT NOT NULL
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
                "INSERT OR REPLACE INTO skills VALUES (?,?,?,?,?,?,?,?)",
                (
                    skill.source_l2_id,
                    skill.skill_id,
                    json.dumps(skill.procedure),
                    json.dumps(skill.reflection_hints),
                    skill.status.value,
                    skill.success_rate,
                    skill.total_executions,
                    skill.updated_at.isoformat(),
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
        if not source_ids:
            return []
        placeholders = ",".join("?" for _ in source_ids)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT hint FROM pending_hints WHERE source_id IN ({placeholders})", source_ids
            ).fetchall()
        return [str(row[0]) for row in rows]

    def _connect(self):
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        return db

    def _skill(self, row):
        import json

        return ProceduralSkill(
            row["skill_id"],
            row["source_l2_id"],
            tuple(json.loads(row["procedure"])),
            tuple(json.loads(row["hints"])),
            SkillStatus(row["status"]),
            row["success_rate"],
            row["total_executions"],
            datetime.fromisoformat(row["updated_at"]),
        )
