"""SQLite store for L5 Identity.

Manages the ``identity.sqlite3`` database with two tables:

    - ``self_model`` — M14 calibration records (predicted vs actual)
    - ``capabilities`` — task category capability records

Path: ``identity/identity.sqlite3``
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..schemas import (
    SelfModelRecord,
    CapabilityRecord,
    CalibrationDirection,
    UpdaterSource,
)
from ...utils.logging import get_logger
from ...utils.time import iso_now

logger = get_logger("agent.memory.identity.identity_store")

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_CREATE_SELF_MODEL_SQL = """
CREATE TABLE IF NOT EXISTS self_model (
    record_id               TEXT PRIMARY KEY,
    episode_id              TEXT NOT NULL,
    task_category           TEXT NOT NULL,
    predicted_success       REAL NOT NULL,
    predicted_effort        REAL,
    actual_success          REAL NOT NULL,
    actual_effort           REAL,
    calibration_error       REAL NOT NULL,
    calibration_direction   TEXT NOT NULL,
    window_avg_calibration  REAL,
    window_success_rate     REAL,
    window_confidence_margin REAL,
    coherence_index         REAL,
    timestamp               TEXT NOT NULL,
    updated_by              TEXT NOT NULL
);
"""

_CREATE_CAPABILITIES_SQL = """
CREATE TABLE IF NOT EXISTS capabilities (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL DEFAULT '',
    success_rate    REAL NOT NULL DEFAULT 0.5,
    confidence      REAL NOT NULL DEFAULT 0.7,
    effort_estimate REAL NOT NULL DEFAULT 0.5,
    total_attempts  INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_self_model_category ON self_model(task_category);
CREATE INDEX IF NOT EXISTS idx_self_model_timestamp ON self_model(timestamp);
"""


class IdentityStore:
    """SQLite-backed store for L5 identity data.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = "identity/identity.sqlite3"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_initialized()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_CREATE_SELF_MODEL_SQL)
        self._conn.execute(_CREATE_CAPABILITIES_SQL)
        for stmt in _CREATE_INDEX_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()
        logger.info(f"Initialized identity store at {self.db_path}")

    @property
    def conn(self) -> sqlite3.Connection:
        self._ensure_initialized()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Self-model CRUD
    # ------------------------------------------------------------------

    def insert_self_model(self, record: SelfModelRecord) -> None:
        """Insert a self-model calibration record."""
        params = {
            "record_id": record.record_id,
            "episode_id": record.episode_id,
            "task_category": record.task_category,
            "predicted_success": record.predicted_success,
            "predicted_effort": record.predicted_effort,
            "actual_success": record.actual_success,
            "actual_effort": record.actual_effort,
            "calibration_error": record.calibration_error,
            "calibration_direction": record.calibration_direction.value,
            "window_avg_calibration": record.window_avg_calibration,
            "window_success_rate": record.window_success_rate,
            "window_confidence_margin": record.window_confidence_margin,
            "coherence_index": record.coherence_index,
            "timestamp": record.timestamp,
            "updated_by": record.updated_by.value,
        }
        columns = ", ".join(params.keys())
        placeholders = ", ".join(f":{k}" for k in params)
        self.conn.execute(
            f"INSERT INTO self_model ({columns}) VALUES ({placeholders})",
            params,
        )
        self.conn.commit()
        logger.debug(f"Inserted self-model record {record.record_id}")

    def get_self_model(self, record_id: str) -> SelfModelRecord | None:
        """Retrieve a self-model record by ID."""
        row = self.conn.execute(
            "SELECT * FROM self_model WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_self_model(row)

    def list_self_model(
        self,
        category: str | None = None,
        limit: int | None = None,
    ) -> list[SelfModelRecord]:
        """List self-model records, optionally filtered by category.

        Args:
            category: Filter by task category. If ``None``, returns all.
            limit: Maximum number of records to return.

        Returns:
            List of :class:`SelfModelRecord`, ordered by timestamp descending.
        """
        sql = "SELECT * FROM self_model"
        params: tuple = ()
        if category is not None:
            sql += " WHERE task_category = ?"
            params = (category,)
        sql += " ORDER BY timestamp DESC, rowid DESC"
        if limit is not None:
            sql += f" LIMIT {limit}"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_self_model(r) for r in rows]

    def get_recent_self_model(
        self,
        category: str | None = None,
        n: int = 50,
    ) -> list[SelfModelRecord]:
        """Return the most recent *n* self-model records.

        Args:
            category: Filter by task category.
            n: Number of recent records to return.

        Returns:
            List of :class:`SelfModelRecord`, ordered by timestamp ascending
            (oldest first within the window).
        """
        sql = "SELECT * FROM self_model"
        params: tuple = ()
        if category is not None:
            sql += " WHERE task_category = ?"
            params = (category,)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        rows = self.conn.execute(sql, (*params, n)).fetchall()
        return list(reversed([self._row_to_self_model(r) for r in rows]))

    def count_self_model(self, category: str | None = None) -> int:
        """Count self-model records, optionally filtered by category."""
        if category is not None:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM self_model WHERE task_category = ?",
                (category,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM self_model").fetchone()
        return row[0]

    # ------------------------------------------------------------------
    # Capability CRUD
    # ------------------------------------------------------------------

    def upsert_capability(self, cap: CapabilityRecord) -> None:
        """Insert or update a capability record."""
        params = {
            "id": cap.id,
            "label": cap.label,
            "success_rate": cap.success_rate,
            "confidence": cap.confidence,
            "effort_estimate": cap.effort_estimate,
            "total_attempts": cap.total_attempts,
        }
        columns = ", ".join(params.keys())
        placeholders = ", ".join(f":{k}" for k in params)
        update_clause = ", ".join(f"{k} = :{k}" for k in params if k != "id")
        self.conn.execute(
            f"INSERT INTO capabilities ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {update_clause}",
            params,
        )
        self.conn.commit()
        logger.debug(f"Upserted capability {cap.id}")

    def get_capability(self, cap_id: str) -> CapabilityRecord | None:
        """Retrieve a capability by ID."""
        row = self.conn.execute(
            "SELECT * FROM capabilities WHERE id = ?",
            (cap_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_capability(row)

    def list_capabilities(self) -> list[CapabilityRecord]:
        """Return all capability records."""
        rows = self.conn.execute("SELECT * FROM capabilities ORDER BY id").fetchall()
        return [self._row_to_capability(r) for r in rows]

    def delete_capability(self, cap_id: str) -> bool:
        """Delete a capability by ID."""
        cur = self.conn.execute("DELETE FROM capabilities WHERE id = ?", (cap_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_self_model(row: sqlite3.Row) -> SelfModelRecord:
        return SelfModelRecord(
            record_id=row["record_id"],
            episode_id=row["episode_id"],
            task_category=row["task_category"],
            predicted_success=row["predicted_success"],
            predicted_effort=row["predicted_effort"],
            actual_success=row["actual_success"],
            actual_effort=row["actual_effort"],
            calibration_error=row["calibration_error"],
            calibration_direction=CalibrationDirection(row["calibration_direction"]),
            window_avg_calibration=row["window_avg_calibration"],
            window_success_rate=row["window_success_rate"],
            window_confidence_margin=row["window_confidence_margin"],
            coherence_index=row["coherence_index"],
            timestamp=row["timestamp"],
            updated_by=UpdaterSource(row["updated_by"]),
        )

    @staticmethod
    def _row_to_capability(row: sqlite3.Row) -> CapabilityRecord:
        return CapabilityRecord(
            id=row["id"],
            label=row["label"],
            success_rate=row["success_rate"],
            confidence=row["confidence"],
            effort_estimate=row["effort_estimate"],
            total_attempts=row["total_attempts"],
        )