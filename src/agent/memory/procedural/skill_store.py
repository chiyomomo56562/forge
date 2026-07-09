"""SQLite wrapper for L3 Procedural Memory.

Stores skill **metadata** in SQLite and skill **code** as Python files on
disk.  This separation keeps code version-controllable, editable without
touching the database, and avoids corruption risks from storing large
code blobs in a single DB field.

Layout::

    data/memory/procedural/skills.sqlite3   ← metadata (status, success_rate, …)
    scripts/skills/<skill_id>.py            ← actual executable code

SQLite schema stores ``code_path`` (relative path to the code file), not
the code itself.  When a :class:`Skill` is upserted, its ``code`` field is
written to the file and ``code_path`` is recorded in the DB.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..schemas import Skill, SkillMetadata, SkillStatus
from ...utils.logging import get_logger
from ...utils.time import iso_now

logger = get_logger("agent.memory.procedural.skill_store")

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id            TEXT PRIMARY KEY,
    name                TEXT NOT NULL DEFAULT '',
    code_path           TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'Seed',
    success_rate        REAL NOT NULL DEFAULT 0.0,
    version             TEXT NOT NULL DEFAULT '1.0',
    total_executions    INTEGER NOT NULL DEFAULT 0,
    last_executed_at    TEXT,
    reflection_hints     TEXT NOT NULL DEFAULT '[]',
    causal_conditions   TEXT NOT NULL DEFAULT '[]',
    protected           INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT ''
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
"""


class SkillStore:
    """SQLite-backed metadata store + file-backed code store for L3 skills.

    Args:
        db_path: Path to the SQLite database file.
        skills_dir: Directory where skill code files are stored.
    """

    def __init__(
        self,
        db_path: str = "data/memory/procedural/skills.sqlite3",
        skills_dir: str = "scripts/skills",
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_initialized()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Open the connection and create tables if needed."""
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_INDEX_SQL)
        self._conn.commit()
        logger.info(f"Initialized skill store at {self.db_path}")

    @property
    def conn(self) -> sqlite3.Connection:
        """Return the active SQLite connection (lazy init)."""
        self._ensure_initialized()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Code file I/O
    # ------------------------------------------------------------------

    def _resolve_code_path(self, skill_id: str) -> Path:
        """Return the full path for a skill's code file."""
        return self.skills_dir / f"{skill_id}.py"

    def _write_code_file(self, skill: Skill) -> str:
        """Write skill code to a file and return the relative path.

        Args:
            skill: The skill whose ``code`` field will be written.

        Returns:
            Relative path string (e.g. ``scripts/skills/skill_001.py``).
        """
        code_path = self._resolve_code_path(skill.skill_id)
        code_path.parent.mkdir(parents=True, exist_ok=True)
        code_path.write_text(skill.code, encoding="utf-8")
        rel = str(code_path)
        logger.debug(f"Wrote skill code to {rel}")
        return rel

    def _read_code_file(self, code_path: str) -> str:
        """Read skill code from a file path.

        Args:
            code_path: Relative or absolute path to the code file.

        Returns:
            Code string. Returns ``""`` if the file does not exist.
        """
        path = Path(code_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            logger.warning(f"Skill code file not found: {code_path}")
            return ""
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Row ↔ Skill conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_skill(row: sqlite3.Row, code: str = "") -> Skill:
        """Convert a SQLite row to a :class:`Skill` model.

        Args:
            row: SQLite row with metadata.
            code: Code string loaded from the file (empty if unavailable).
        """
        metadata = SkillMetadata(
            status=SkillStatus(row["status"]),
            success_rate=row["success_rate"],
            version=row["version"],
            total_executions=row["total_executions"],
            last_executed_at=row["last_executed_at"],
        )
        return Skill(
            skill_id=row["skill_id"],
            name=row["name"],
            code_path=row["code_path"],
            code=code,
            description=row["description"],
            metadata=metadata,
            reflection_hints=json.loads(row["reflection_hints"]),
            causal_conditions=json.loads(row["causal_conditions"]),
            protected=bool(row["protected"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _skill_to_params(skill: Skill, code_path: str) -> dict[str, Any]:
        """Convert a :class:`Skill` to a dict of SQL parameters.

        Args:
            skill: The skill model.
            code_path: Resolved file path to store in the DB.
        """
        now = iso_now()
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "code_path": code_path,
            "description": skill.description,
            "status": skill.metadata.status.value,
            "success_rate": skill.metadata.success_rate,
            "version": skill.metadata.version,
            "total_executions": skill.metadata.total_executions,
            "last_executed_at": skill.metadata.last_executed_at,
            "reflection_hints": json.dumps(skill.reflection_hints, ensure_ascii=False),
            "causal_conditions": json.dumps(skill.causal_conditions, ensure_ascii=False),
            "protected": int(skill.protected),
            "created_at": skill.created_at or now,
            "updated_at": now,
        }

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def upsert(self, skill: Skill) -> None:
        """Insert or update a skill.

        Writes the skill's code to ``scripts/skills/<skill_id>.py`` and
        stores metadata + ``code_path`` in SQLite.

        Args:
            skill: The :class:`Skill` to store. Must have ``code`` populated.
        """
        # Write code to file
        code_path = self._write_code_file(skill)

        # Upsert metadata in DB
        params = self._skill_to_params(skill, code_path)
        columns = ", ".join(params.keys())
        placeholders = ", ".join(f":{k}" for k in params)
        update_clause = ", ".join(
            f"{k} = :{k}" for k in params if k != "skill_id"
        )
        sql = (
            f"INSERT INTO skills ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(skill_id) DO UPDATE SET {update_clause}"
        )
        self.conn.execute(sql, params)
        self.conn.commit()
        logger.debug(f"Upserted skill {skill.skill_id} (code: {code_path})")

    def get(self, skill_id: str) -> Skill | None:
        """Retrieve a single skill by ID.

        Loads metadata from SQLite and code from the file referenced by
        ``code_path``.

        Returns:
            :class:`Skill` or ``None`` if not found.
        """
        row = self.conn.execute(
            "SELECT * FROM skills WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None:
            return None
        code = self._read_code_file(row["code_path"])
        return self._row_to_skill(row, code=code)

    def get_metadata(self, skill_id: str) -> Skill | None:
        """Retrieve skill metadata only (without loading code from file).

        Faster than :meth:`get` when you only need metadata.

        Returns:
            :class:`Skill` with empty ``code`` field, or ``None``.
        """
        row = self.conn.execute(
            "SELECT * FROM skills WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_skill(row, code="")

    def delete(self, skill_id: str, delete_code_file: bool = True) -> bool:
        """Delete a skill by ID.

        Args:
            skill_id: The skill to delete.
            delete_code_file: If ``True``, also removes the code file from disk.

        Returns:
            ``True`` if a row was deleted, ``False`` if the skill was not found.
        """
        # Optionally get code_path before deleting
        code_path = None
        if delete_code_file:
            row = self.conn.execute(
                "SELECT code_path FROM skills WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()
            if row:
                code_path = row["code_path"]

        cur = self.conn.execute(
            "DELETE FROM skills WHERE skill_id = ?",
            (skill_id,),
        )
        self.conn.commit()
        deleted = cur.rowcount > 0

        if deleted and delete_code_file and code_path:
            path = Path(code_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.exists():
                path.unlink()
                logger.debug(f"Deleted code file: {code_path}")

        if deleted:
            logger.debug(f"Deleted skill {skill_id}")
        return deleted

    def list_all(self) -> list[Skill]:
        """Return all skills (metadata only, code loaded lazily)."""
        rows = self.conn.execute("SELECT * FROM skills ORDER BY updated_at DESC").fetchall()
        return [self._row_to_skill(r, code="") for r in rows]

    def list_by_status(self, status: SkillStatus) -> list[Skill]:
        """Return all skills with the given status (metadata only)."""
        rows = self.conn.execute(
            "SELECT * FROM skills WHERE status = ? ORDER BY updated_at DESC",
            (status.value,),
        ).fetchall()
        return [self._row_to_skill(r, code="") for r in rows]

    def count(self) -> int:
        """Return the total number of skills."""
        row = self.conn.execute("SELECT COUNT(*) FROM skills").fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Field-level updates
    # ------------------------------------------------------------------

    def update_status(self, skill_id: str, status: SkillStatus) -> bool:
        """Update only the status field of a skill.

        Returns:
            ``True`` if updated, ``False`` if skill not found.
        """
        cur = self.conn.execute(
            "UPDATE skills SET status = ?, updated_at = ? WHERE skill_id = ?",
            (status.value, iso_now(), skill_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_success_rate(
        self,
        skill_id: str,
        success_rate: float,
        total_executions: int | None = None,
        last_executed_at: str | None = None,
    ) -> bool:
        """Update the success rate (and optionally execution count/timestamp).

        Returns:
            ``True`` if updated, ``False`` if skill not found.
        """
        now = iso_now()
        if total_executions is not None and last_executed_at is not None:
            sql = (
                "UPDATE skills SET success_rate = ?, total_executions = ?, "
                "last_executed_at = ?, updated_at = ? WHERE skill_id = ?"
            )
            params: tuple = (success_rate, total_executions, last_executed_at, now, skill_id)
        elif total_executions is not None:
            sql = (
                "UPDATE skills SET success_rate = ?, total_executions = ?, "
                "updated_at = ? WHERE skill_id = ?"
            )
            params = (success_rate, total_executions, now, skill_id)
        else:
            sql = "UPDATE skills SET success_rate = ?, updated_at = ? WHERE skill_id = ?"
            params = (success_rate, now, skill_id)

        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount > 0

    def update_reflection_hints(self, skill_id: str, hints: list[str]) -> bool:
        """Update the reflection_hints field (dual-storage L3 target).

        Returns:
            ``True`` if updated, ``False`` if skill not found.
        """
        cur = self.conn.execute(
            "UPDATE skills SET reflection_hints = ?, updated_at = ? WHERE skill_id = ?",
            (json.dumps(hints, ensure_ascii=False), iso_now(), skill_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_causal_conditions(self, skill_id: str, conditions: list[str]) -> bool:
        """Update the causal_conditions field.

        Returns:
            ``True`` if updated, ``False`` if skill not found.
        """
        cur = self.conn.execute(
            "UPDATE skills SET causal_conditions = ?, updated_at = ? WHERE skill_id = ?",
            (json.dumps(conditions, ensure_ascii=False), iso_now(), skill_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def set_protected(self, skill_id: str, protected: bool) -> bool:
        """Set the protected flag on a skill.

        Returns:
            ``True`` if updated, ``False`` if skill not found.
        """
        cur = self.conn.execute(
            "UPDATE skills SET protected = ?, updated_at = ? WHERE skill_id = ?",
            (int(protected), iso_now(), skill_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_code(self, skill_id: str, code: str) -> bool:
        """Update the skill's code file on disk.

        This does not change the DB metadata — only the code file.

        Returns:
            ``True`` if updated, ``False`` if skill not found in DB.
        """
        row = self.conn.execute(
            "SELECT code_path FROM skills WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None:
            return False

        code_path = Path(row["code_path"])
        if not code_path.is_absolute():
            code_path = Path.cwd() / code_path
        code_path.parent.mkdir(parents=True, exist_ok=True)
        code_path.write_text(code, encoding="utf-8")
        logger.debug(f"Updated code file for skill {skill_id}: {row['code_path']}")
        return True