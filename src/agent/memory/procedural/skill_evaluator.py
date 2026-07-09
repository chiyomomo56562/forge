"""Skill evaluator for L3 Procedural Memory.

Evaluates skill execution results and updates the skill's success rate
and lifecycle state in the store.

Skill Lifecycle State Machine::

    Seed ──(success_rate > 0.9, last 5)──→ Active
    Active ──(success_rate < 0.5, last 10)──→ Degrading
    Degrading ──(success_rate < 0.2 or 30일 미사용)──→ Archived
    Degrading ──(success_rate > 0.7, last 5)──→ Active (recovery)

The evaluator maintains an in-memory execution history per skill to
compute rolling-window success rates.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from ..schemas import Skill, SkillStatus
from .skill_store import SkillStore
from ...utils.logging import get_logger
from ...utils.time import iso_now, parse_iso

logger = get_logger("agent.memory.procedural.skill_evaluator")

# ---------------------------------------------------------------------------
# State machine thresholds (from Implementation Process.md Phase 1.3)
# ---------------------------------------------------------------------------

# Seed → Active
SEED_TO_ACTIVE_THRESHOLD = 0.9
SEED_TO_ACTIVE_WINDOW = 5

# Active → Degrading
ACTIVE_TO_DEGRADING_THRESHOLD = 0.5
ACTIVE_TO_DEGRADING_WINDOW = 10

# Degrading → Archived
DEGRADING_TO_ARCHIVED_THRESHOLD = 0.2
ARCHIVE_UNUSED_DAYS = 30

# Degrading → Active (recovery)
DEGRADING_RECOVERY_THRESHOLD = 0.7
DEGRADING_RECOVERY_WINDOW = 5


class SkillEvaluator:
    """Evaluate skill execution results and manage lifecycle transitions.

    Args:
        store: A :class:`SkillStore` instance.
        history_size: Maximum number of execution results to keep per skill
            in the in-memory rolling window.
    """

    def __init__(
        self,
        store: SkillStore | None = None,
        history_size: int = 100,
    ):
        self.store = store or SkillStore()
        self._history: dict[str, deque[bool]] = {}
        self._history_size = history_size

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def _get_history(self, skill_id: str) -> deque[bool]:
        """Return (or create) the execution history deque for a skill."""
        if skill_id not in self._history:
            self._history[skill_id] = deque(maxlen=self._history_size)
        return self._history[skill_id]

    def record_execution(self, skill_id: str, success: bool) -> None:
        """Record a single execution result in the rolling window.

        Args:
            skill_id: The skill that was executed.
            success: Whether the execution succeeded.
        """
        self._get_history(skill_id).append(success)

    def get_recent_success_rate(self, skill_id: str, window: int) -> float:
        """Compute the success rate over the last *window* executions.

        If there are fewer than *window* executions, uses all available.

        Returns:
            Success rate as a float in ``[0.0, 1.0]``. Returns ``0.0`` if
            there is no execution history.
        """
        history = self._get_history(skill_id)
        if not history:
            return 0.0
        recent = list(history)[-window:]
        successes = sum(1 for s in recent if s)
        return successes / len(recent) if recent else 0.0

    # ------------------------------------------------------------------
    # Evaluation & state transition
    # ------------------------------------------------------------------

    def evaluate(
        self,
        skill_id: str,
        success: bool,
    ) -> SkillStatus:
        """Record an execution, update success rate, and transition state.

        This is the main entry point: call after each skill execution.

        Args:
            skill_id: The skill that was executed.
            success: Whether the execution succeeded.

        Returns:
            The new (possibly unchanged) :class:`SkillStatus`.
        """
        # Record the execution
        self.record_execution(skill_id, success)

        # Load current skill
        skill = self.store.get(skill_id)
        if skill is None:
            logger.warning(f"Cannot evaluate unknown skill: {skill_id}")
            return SkillStatus.SEED

        # Compute overall success rate from full history
        history = self._get_history(skill_id)
        total = len(history)
        successes = sum(1 for s in history if s)
        overall_rate = successes / total if total > 0 else 0.0

        # Update execution count and timestamp
        new_total = skill.metadata.total_executions + 1
        now = iso_now()
        self.store.update_success_rate(
            skill_id=skill_id,
            success_rate=round(overall_rate, 4),
            total_executions=new_total,
            last_executed_at=now,
        )

        # Determine state transition
        new_status = self._determine_transition(skill_id, skill.metadata.status)

        if new_status != skill.metadata.status:
            self.store.update_status(skill_id, new_status)
            logger.info(
                f"Skill {skill_id} transitioned: "
                f"{skill.metadata.status.value} → {new_status.value}"
            )

        return new_status

    def _history_count(self, skill_id: str) -> int:
        """Return the number of recorded executions for a skill."""
        return len(self._get_history(skill_id))

    def _determine_transition(self, skill_id: str, current: SkillStatus) -> SkillStatus:
        """Determine the next state based on current state and recent history.

        Implements the state machine from Implementation Process.md:

            Seed → Active:        success_rate > 0.9 (last 5, min 5 execs)
            Active → Degrading:   success_rate < 0.5 (last 10, min 10 execs)
            Degrading → Archived: success_rate < 0.2 (last 10, min 10 execs) or 30일 미사용
            Degrading → Active:   success_rate > 0.7 (last 5, min 5 execs)  [recovery]

        Transitions only fire when enough execution data is available to
        fill the evaluation window, preventing premature state changes
        from small sample sizes.
        """
        if current == SkillStatus.SEED:
            if self._history_count(skill_id) >= SEED_TO_ACTIVE_WINDOW:
                rate = self.get_recent_success_rate(skill_id, SEED_TO_ACTIVE_WINDOW)
                if rate >= SEED_TO_ACTIVE_THRESHOLD:
                    return SkillStatus.ACTIVE

        elif current == SkillStatus.ACTIVE:
            if self._history_count(skill_id) >= ACTIVE_TO_DEGRADING_WINDOW:
                rate = self.get_recent_success_rate(skill_id, ACTIVE_TO_DEGRADING_WINDOW)
                if rate < ACTIVE_TO_DEGRADING_THRESHOLD:
                    return SkillStatus.DEGRADING

        elif current == SkillStatus.DEGRADING:
            # Check recovery first
            if self._history_count(skill_id) >= DEGRADING_RECOVERY_WINDOW:
                recovery_rate = self.get_recent_success_rate(skill_id, DEGRADING_RECOVERY_WINDOW)
                if recovery_rate >= DEGRADING_RECOVERY_THRESHOLD:
                    return SkillStatus.ACTIVE

            # Check archive conditions (rate-based)
            if self._history_count(skill_id) >= ACTIVE_TO_DEGRADING_WINDOW:
                archive_rate = self.get_recent_success_rate(skill_id, ACTIVE_TO_DEGRADING_WINDOW)
                if archive_rate < DEGRADING_TO_ARCHIVED_THRESHOLD:
                    return SkillStatus.ARCHIVED

            # Check 30-day unused
            skill = self.store.get(skill_id)
            if skill and skill.metadata.last_executed_at:
                try:
                    last_exec = parse_iso(skill.metadata.last_executed_at)
                    if datetime.now(timezone.utc) - last_exec > timedelta(days=ARCHIVE_UNUSED_DAYS):
                        return SkillStatus.ARCHIVED
                except ValueError:
                    pass

        elif current == SkillStatus.ARCHIVED:
            # Archived skills stay archived unless restored by meta loop
            pass

        return current

    # ------------------------------------------------------------------
    # Batch evaluation
    # ------------------------------------------------------------------

    def evaluate_batch(
        self,
        results: list[tuple[str, bool]],
    ) -> dict[str, SkillStatus]:
        """Evaluate multiple skill executions.

        Args:
            results: List of ``(skill_id, success)`` tuples.

        Returns:
            Dict mapping ``skill_id`` → new :class:`SkillStatus`.
        """
        statuses: dict[str, SkillStatus] = {}
        for skill_id, success in results:
            statuses[skill_id] = self.evaluate(skill_id, success)
        return statuses

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def reset_history(self, skill_id: str | None = None) -> None:
        """Clear execution history for a skill (or all skills if ``None``)."""
        if skill_id is not None:
            self._history.pop(skill_id, None)
        else:
            self._history.clear()