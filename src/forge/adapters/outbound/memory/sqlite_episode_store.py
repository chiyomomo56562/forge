"""SQLite persistence for the L1 canonical record."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from forge.adapters.outbound.memory.errors import (
    MemoryInfrastructureError,
    RetryableMemoryOperationError,
)
from forge.domain.memory import (
    CibEvaluationStatus,
    Episode,
    EpisodeStatus,
    Evaluation,
    EvaluationReason,
    ExecutionOutcome,
    ExecutionResult,
    IndexState,
    PromotionEligibility,
    Reflection,
)


class SqliteEpisodeStore:
    def __init__(self, database_path: Path) -> None:
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(database_path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate()
        except (sqlite3.Error, OSError) as exc:
            raise MemoryInfrastructureError(
                code="sqlite.initialization_failed", safe_message="Episode storage is unavailable."
            ) from exc

    def _migrate(self) -> None:
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        already = self._connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 1"
        ).fetchone()
        if already:
            return
        migration = Path(__file__).with_name("migrations") / "001_create_l1.sql"
        with self._connection:
            self._connection.executescript(migration.read_text(encoding="utf-8"))
            self._connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
                (datetime.now(UTC).isoformat(),),
            )

    def insert(self, episode: Episode) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    f"INSERT INTO episodes VALUES ({','.join('?' for _ in range(37))})",
                    (
                        episode.episode_id,
                        episode.schema_version,
                        episode.session_id,
                        episode.request_id,
                        episode.created_at.isoformat(),
                        episode.task_request,
                        episode.task_category,
                        episode.execution.summary,
                        episode.execution.outcome.value,
                        json.dumps(episode.execution.tool_names),
                        episode.evaluation.result_quality,
                        episode.evaluation.requirement_coverage,
                        episode.evaluation.verification_confidence,
                        episode.evaluation.success_score,
                        episode.evaluation.pain_index,
                        episode.evaluation.retry_ratio,
                        episode.evaluation.tool_error_ratio,
                        episode.evaluation.budget_overrun_ratio,
                        episode.evaluation.rework_ratio,
                        episode.evaluation.human_intervention_ratio,
                        episode.evaluation.cib_score,
                        episode.evaluation.cib_evaluation_status.value,
                        episode.evaluation.status.value,
                        episode.evaluation.promotion_eligibility.value,
                        int(episode.evaluation.retryable),
                        episode.reflection.what_worked,
                        episode.reflection.what_failed,
                        episode.reflection.next_hint,
                        episode.reflection.causal_condition,
                        int(episode.reflection.has_content),
                        episode.pattern_candidate_id,
                        episode.supersedes_episode_id,
                        int(episode.evidence_complete),
                        IndexState.PENDING.value,
                        None,
                        None,
                        None,
                    ),
                )
                self._connection.executemany(
                    "INSERT INTO episode_event_refs(episode_id, ordinal, event_id) "
                    "VALUES (?, ?, ?)",
                    [
                        (episode.episode_id, ordinal, event_id)
                        for ordinal, event_id in enumerate(episode.raw_event_refs)
                    ],
                )
                self._connection.executemany(
                    "INSERT INTO episode_evaluation_reasons VALUES (?, ?, ?, ?)",
                    [
                        (episode.episode_id, reason.metric, reason.reason_code, reason.detail)
                        for reason in episode.evaluation.reasons
                    ],
                )
        except sqlite3.OperationalError as exc:
            if "busy" in str(exc).lower() or "locked" in str(exc).lower():
                raise RetryableMemoryOperationError(
                    code="sqlite.temporarily_unavailable",
                    safe_message="Episode storage is temporarily unavailable.",
                ) from exc
            raise MemoryInfrastructureError(
                code="sqlite.write_failed_before_commit", safe_message="Episode storage failed."
            ) from exc
        except sqlite3.Error as exc:
            raise MemoryInfrastructureError(
                code="sqlite.write_failed_before_commit", safe_message="Episode storage failed."
            ) from exc

    def get(self, episode_id: str) -> Episode | None:
        row = self._connection.execute(
            "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        return self._to_episode(row) if row else None

    def successor_id(self, episode_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT episode_id FROM episodes WHERE supersedes_episode_id = ?", (episode_id,)
        ).fetchone()
        return str(row[0]) if row else None

    def state(self, episode_id: str) -> IndexState:
        row = self._connection.execute(
            "SELECT index_state FROM episodes WHERE episode_id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            raise KeyError(episode_id)
        return IndexState(row[0])

    def set_indexed(
        self, episode_id: str, projection_version: int, embedding_model_id: str, now: datetime
    ) -> None:
        with self._connection:
            self._connection.execute(
                """UPDATE episodes SET index_state = ?, indexed_projection_version = ?,
                indexed_embedding_model_id = ?, indexed_at = ? WHERE episode_id = ?""",
                (
                    IndexState.INDEXED.value,
                    projection_version,
                    embedding_model_id,
                    now.isoformat(),
                    episode_id,
                ),
            )

    def set_index_state(self, episode_id: str, state: IndexState) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE episodes SET index_state = ? WHERE episode_id = ?",
                (state.value, episode_id),
            )

    def reindex_candidates(self, episode_ids: tuple[str, ...] | None) -> list[Episode]:
        if episode_ids is None:
            rows = self._connection.execute("SELECT * FROM episodes").fetchall()
        else:
            placeholders = ",".join("?" for _ in episode_ids)
            rows = self._connection.execute(
                f"SELECT * FROM episodes WHERE episode_id IN ({placeholders})", episode_ids
            ).fetchall()
        return [self._to_episode(row) for row in rows]

    def list_after(self, created_after: datetime) -> list[Episode]:
        rows = self._connection.execute(
            "SELECT * FROM episodes WHERE created_at > ? ORDER BY created_at ASC",
            (created_after.isoformat(),),
        ).fetchall()
        return [self._to_episode(row) for row in rows]

    def index_is_current(
        self, episode_id: str, projection_version: int, embedding_model_id: str
    ) -> bool:
        row = self._connection.execute(
            "SELECT index_state, indexed_projection_version, "
            "indexed_embedding_model_id FROM episodes WHERE episode_id = ?",
            (episode_id,),
        ).fetchone()
        return (
            bool(row)
            and row[0] == IndexState.INDEXED.value
            and row[1] == projection_version
            and row[2] == embedding_model_id
        )

    def _to_episode(self, row: sqlite3.Row) -> Episode:
        refs = self._connection.execute(
            "SELECT event_id FROM episode_event_refs WHERE episode_id = ? ORDER BY ordinal",
            (row["episode_id"],),
        ).fetchall()
        reasons = self._connection.execute(
            "SELECT metric, reason_code, detail FROM episode_evaluation_reasons "
            "WHERE episode_id = ?",
            (row["episode_id"],),
        ).fetchall()
        evaluation = Evaluation(
            **{
                metric: row[metric]
                for metric in (
                    "result_quality",
                    "requirement_coverage",
                    "verification_confidence",
                    "success_score",
                    "pain_index",
                    "retry_ratio",
                    "tool_error_ratio",
                    "budget_overrun_ratio",
                    "rework_ratio",
                    "human_intervention_ratio",
                    "cib_score",
                )
            },
            cib_evaluation_status=CibEvaluationStatus(row["cib_evaluation_status"]),
            status=EpisodeStatus(row["status"]),
            promotion_eligibility=PromotionEligibility(row["promotion_eligibility"]),
            retryable=bool(row["retryable"]),
            reasons=tuple(EvaluationReason(**dict(reason)) for reason in reasons),
        )
        return Episode(
            episode_id=row["episode_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            task_request=row["task_request"],
            task_category=row["task_category"],
            execution=ExecutionResult(
                row["execution_summary"],
                ExecutionOutcome(row["execution_outcome"]),
                tuple(json.loads(row["tool_names_json"])),
            ),
            evaluation=evaluation,
            reflection=Reflection(
                row["what_worked"], row["what_failed"], row["next_hint"], row["causal_condition"]
            ),
            evidence_complete=bool(row["evidence_complete"]),
            session_id=row["session_id"],
            request_id=row["request_id"],
            raw_event_refs=tuple(item[0] for item in refs),
            pattern_candidate_id=row["pattern_candidate_id"],
            supersedes_episode_id=row["supersedes_episode_id"],
            schema_version=row["schema_version"],
        )
