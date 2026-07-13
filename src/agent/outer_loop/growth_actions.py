"""M16 Growth Rate Regulator — Action Execution (조치 실행).

Implements the *actions* taken when M16 detects abnormal growth signals.
Phase 3.1 implemented signal *detection*; this module implements the
*response*:

    ┌───────────────────┬──────────────────────────────────────────────────┐
    │ Signal            │ Actions                                          │
    ├───────────────────┼──────────────────────────────────────────────────┤
    │ Crash (추락)      │ 1. Force CIB gate on recent episodes             │
    │                   │ 2. Suspend learning (set flag)                   │
    │                   │ 3. Root cause analysis (identify failed episodes)│
    ├───────────────────┼──────────────────────────────────────────────────┤
    │ Stagnation (정체)  │ 1. Meta-loop stagnation trigger (already in      │
    │                   │    meta_trigger.py — no additional action here)   │
    ├───────────────────┼──────────────────────────────────────────────────┤
    │ Overgrowth (과속)  │ 1. Force CIB gate on recent L2/L3 knowledge      │
    │                   │ 2. Overfitting verification (CIB re-validation)  │
    │                   │ 3. Degrade low-confidence L2/L3 memories to      │
    │                   │    ``Degrading`` status                          │
    └───────────────────┴──────────────────────────────────────────────────┘

The learning suspension flag is persisted in :class:`OuterLoopState` so the
inner loop can check it before allowing new learning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory.constitution.guard import CIBGuard, CIBResult
from ..memory.procedural.skill_store import SkillStore
from ..memory.semantic.graph_store import GraphStore
from ..memory.schemas import SkillStatus, KnowledgeNode
from ..utils.logging import get_logger

from .growth_regulator import GrowthRegulationResult, GrowthSignal

logger = get_logger("agent.outer_loop.growth_actions")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    """Result of executing a growth regulator action.

    Attributes:
        signal: The :class:`GrowthSignal` that triggered this action.
        action_taken: Human-readable description of the action.
        cib_forced: Whether CIB gate was force-invoked.
        cib_results: Per-item CIB evaluation results.
        learning_suspended: Whether learning was suspended.
        skills_degraded: Number of L3 skills degraded to ``Degrading``.
        knowledge_nodes_degraded: Number of L2 knowledge nodes degraded.
        root_cause: Root cause analysis summary (for crash).
        errors: List of error messages.
    """
    signal: GrowthSignal = GrowthSignal.NORMAL
    action_taken: str = ""
    cib_forced: bool = False
    cib_results: list[dict[str, Any]] = field(default_factory=list)
    learning_suspended: bool = False
    skills_degraded: int = 0
    knowledge_nodes_degraded: int = 0
    root_cause: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Growth Action Executor
# ---------------------------------------------------------------------------

class GrowthActionExecutor:
    """Execute M16 growth regulator actions.

    Args:
        cib_guard: A :class:`CIBGuard` instance for forced CIB evaluation.
        skill_store: A :class:`SkillStore` for L3 skill degradation.
        graph_store: A :class:`GraphStore` for L2 knowledge node operations.
        constitution: The :class:`Constitution` model for CIB evaluation.
        degradation_confidence_threshold: Skills/knowledge with confidence
            below this threshold are candidates for degradation (default 0.5).
    """

    def __init__(
        self,
        cib_guard: CIBGuard | None = None,
        skill_store: SkillStore | None = None,
        graph_store: GraphStore | None = None,
        constitution: Any | None = None,
        degradation_confidence_threshold: float = 0.5,
    ):
        self.cib_guard = cib_guard or CIBGuard()
        self.skill_store = skill_store
        self.graph_store = graph_store
        self.constitution = constitution
        self.degradation_confidence_threshold = degradation_confidence_threshold

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    def execute(
        self,
        regulation_result: GrowthRegulationResult,
        aggregation_result: Any | None = None,
        episode_texts: list[str] | None = None,
    ) -> ActionResult:
        """Execute the appropriate action based on the detected signal.

        Args:
            regulation_result: The :class:`GrowthRegulationResult` from M16.
            aggregation_result: :class:`AggregationResult` from Step 1.
            episode_texts: List of episode result texts for CIB evaluation.

        Returns:
            :class:`ActionResult` with action execution details.
        """
        signal = regulation_result.signal

        if signal == GrowthSignal.CRASH:
            return self._execute_crash(regulation_result, aggregation_result, episode_texts)
        elif signal == GrowthSignal.OVERGROWTH:
            return self._execute_overgrowth(regulation_result, aggregation_result, episode_texts)
        elif signal == GrowthSignal.STAGNATION:
            return self._execute_stagnation(regulation_result)
        else:
            return ActionResult(
                signal=GrowthSignal.NORMAL,
                action_taken="No action needed (normal signal).",
            )

    # ------------------------------------------------------------------
    # Crash action: CIB force + learning suspend + root cause analysis
    # ------------------------------------------------------------------

    def _execute_crash(
        self,
        regulation_result: GrowthRegulationResult,
        aggregation_result: Any | None,
        episode_texts: list[str] | None,
    ) -> ActionResult:
        """Execute crash response.

        1. Force CIB gate on recent episode results
        2. Suspend learning
        3. Root cause analysis (identify failed episodes, patterns)
        """
        result = ActionResult(
            signal=GrowthSignal.CRASH,
            action_taken="CIB force → learning suspend → root cause analysis",
            learning_suspended=True,
        )

        # 1. Force CIB evaluation on recent episodes
        if episode_texts and self.constitution is not None:
            result.cib_forced = True
            for i, text in enumerate(episode_texts):
                try:
                    cib_result = self.cib_guard.evaluate(text, self.constitution)
                    result.cib_results.append({
                        "episode_index": i,
                        "min_score": cib_result.min_score,
                        "passed": cib_result.passed,
                        "blocked": cib_result.blocked,
                    })
                except Exception as e:
                    result.errors.append(f"CIB eval episode {i}: {e}")
                    logger.warning(f"CIB evaluation failed for episode {i}: {e}")
        else:
            logger.info("Crash action: no episode texts or constitution, skipping CIB force")

        # 2. Learning suspension is signaled via the result flag
        logger.warning(
            f"CRASH action executed: learning suspended, "
            f"CIB forced={result.cib_forced}, "
            f"root cause analysis below"
        )

        # 3. Root cause analysis
        result.root_cause = self._analyze_crash_root_cause(
            regulation_result, aggregation_result
        )

        return result

    @staticmethod
    def _analyze_crash_root_cause(
        regulation_result: GrowthRegulationResult,
        aggregation_result: Any | None,
    ) -> dict[str, Any]:
        """Analyze the root cause of a crash signal.

        Identifies:
            - The magnitude of the success rate drop
            - The status distribution of recent episodes
            - Whether specific categories are failing
        """
        details = regulation_result.details
        root_cause: dict[str, Any] = {
            "signal": "crash",
            "success_rate_drop": details.get("drop"),
            "previous_rate": details.get("previous_rate"),
            "recent_rate": details.get("recent_rate"),
            "threshold": details.get("threshold"),
        }

        if aggregation_result is not None:
            root_cause["status_distribution"] = aggregation_result.status_distribution
            root_cause["episode_count"] = aggregation_result.episode_count
            root_cause["avg_pain_index"] = aggregation_result.avg_pain_index

        return root_cause

    # ------------------------------------------------------------------
    # Overgrowth action: CIB force + overfitting verification + L2/L3 degrade
    # ------------------------------------------------------------------

    def _execute_overgrowth(
        self,
        regulation_result: GrowthRegulationResult,
        aggregation_result: Any | None,
        episode_texts: list[str] | None,
    ) -> ActionResult:
        """Execute overgrowth response.

        1. Force CIB gate on recent L2/L3 knowledge
        2. Overfitting verification (CIB re-validation)
        3. Degrade low-confidence L2/L3 memories to Degrading status
        """
        result = ActionResult(
            signal=GrowthSignal.OVERGROWTH,
            action_taken="CIB force → overfitting verification → L2/L3 degradation",
        )

        # 1. Force CIB evaluation on recent episode results (if available)
        if episode_texts and self.constitution is not None:
            result.cib_forced = True
            for i, text in enumerate(episode_texts):
                try:
                    cib_result = self.cib_guard.evaluate(text, self.constitution)
                    result.cib_results.append({
                        "episode_index": i,
                        "min_score": cib_result.min_score,
                        "passed": cib_result.passed,
                        "blocked": cib_result.blocked,
                    })
                except Exception as e:
                    result.errors.append(f"CIB eval episode {i}: {e}")
                    logger.warning(f"CIB evaluation failed for episode {i}: {e}")

        # 2. Overfitting verification — check if CIB passed
        cib_all_passed = all(
            r.get("passed", False) for r in result.cib_results
        ) if result.cib_results else True

        # 3. Degrade low-confidence L2/L3 memories if CIB verification failed
        if not cib_all_passed:
            logger.warning(
                "Overgrowth: CIB verification FAILED — degrading low-confidence memories"
            )
            result.skills_degraded = self._degrade_low_confidence_skills()
            result.knowledge_nodes_degraded = self._degrade_low_confidence_knowledge()
        else:
            logger.info("Overgrowth: CIB verification passed — no degradation needed")

        return result

    def _degrade_low_confidence_skills(self) -> int:
        """Degrade L3 skills with low confidence to Degrading status.

        Targets Active skills whose success_rate is below the degradation
        threshold. Protected skills are skipped.

        Returns:
            Number of skills degraded.
        """
        if self.skill_store is None:
            return 0

        degraded = 0
        try:
            active_skills = self.skill_store.list_by_status(SkillStatus.ACTIVE)
            for skill in active_skills:
                if skill.protected:
                    continue
                if skill.metadata.success_rate < self.degradation_confidence_threshold:
                    success = self.skill_store.update_status(
                        skill.skill_id, SkillStatus.DEGRADING
                    )
                    if success:
                        degraded += 1
                        logger.info(
                            f"Degrading skill '{skill.skill_id}': "
                            f"success_rate={skill.metadata.success_rate:.2f} "
                            f"< {self.degradation_confidence_threshold}"
                        )
        except Exception as e:
            logger.error(f"Failed to degrade skills: {e}")

        return degraded

    def _degrade_low_confidence_knowledge(self) -> int:
        """Degrade L2 knowledge nodes with low confidence.

        Removes or lowers the confidence of knowledge nodes whose
        ``confidence`` field is below the degradation threshold.

        Returns:
            Number of knowledge nodes degraded.
        """
        if self.graph_store is None:
            return 0

        degraded = 0
        try:
            knowledge_nodes = self.graph_store.get_knowledge_nodes()
            for node in knowledge_nodes:
                if node.confidence < self.degradation_confidence_threshold:
                    # Lower confidence further (mark as degraded)
                    try:
                        # Update the node's confidence in the graph
                        if node.node_id in self.graph_store.graph:
                            self.graph_store.graph.nodes[node.node_id]["confidence"] = (
                                node.confidence * 0.5  # halve confidence
                            )
                            degraded += 1
                            logger.info(
                                f"Degrading knowledge node '{node.node_id}': "
                                f"confidence={node.confidence:.2f} → {node.confidence * 0.5:.2f}"
                            )
                    except Exception as e:
                        logger.warning(f"Failed to degrade knowledge node {node.node_id}: {e}")

            # Save graph if any nodes were degraded
            if degraded > 0:
                self.graph_store.save()
        except Exception as e:
            logger.error(f"Failed to degrade knowledge nodes: {e}")

        return degraded

    # ------------------------------------------------------------------
    # Stagnation action: meta loop trigger (handled by meta_trigger.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _execute_stagnation(
        regulation_result: GrowthRegulationResult,
    ) -> ActionResult:
        """Execute stagnation response.

        The actual meta-loop trigger is handled by :class:`MetaTrigger`
        in Step 7. This method just records the action.
        """
        return ActionResult(
            signal=GrowthSignal.STAGNATION,
            action_taken="Meta-loop stagnation trigger (handled in Step 7)",
            root_cause=regulation_result.details,
        )