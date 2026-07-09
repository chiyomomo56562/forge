"""CIB Guard for L4 Constitution.

Implements the Constitutional Invariant Block (CIB) gate: a hard gate that
blocks learning or action execution when the minimum K-Scenario score
falls below the threshold (default 0.95).

Also provides HITL (Human-in-the-Loop) approval checks for constitution
modifications across all three layers (absolute, principle, strategy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..schemas import Constitution, ConstitutionLayer
from .validator import ConstitutionValidator, ValidationResult
from ...utils.logging import get_logger

logger = get_logger("agent.memory.constitution.guard")


# ---------------------------------------------------------------------------
# CIB Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CIBResult:
    """Result of a CIB gate evaluation."""
    scores: list[float] = field(default_factory=list)
    min_score: float = 1.0
    passed: bool = True
    blocked: bool = False
    threshold: float = 0.95
    validation: ValidationResult | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# HITL Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class HITLResult:
    """Result of a HITL approval check."""
    layer: ConstitutionLayer
    requires_approval: bool = True
    approved: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# CIB Guard
# ---------------------------------------------------------------------------

class CIBGuard:
    """Constitutional Invariant Block guard.

    Evaluates plans/results against all K-Scenarios and blocks execution
    if the minimum score is below the threshold.

    Args:
        validator: A :class:`ConstitutionValidator` instance.
        threshold: CIB threshold (default 0.95). If ``None``, uses the
            constitution's threshold at evaluation time.
    """

    def __init__(
        self,
        validator: ConstitutionValidator | None = None,
        threshold: float = 0.95,
    ):
        self.validator = validator or ConstitutionValidator()
        self.threshold = threshold

    # ------------------------------------------------------------------
    # CIB Gate
    # ------------------------------------------------------------------

    def evaluate(
        self,
        plan_or_result: str,
        constitution: Constitution,
        threshold: float | None = None,
    ) -> CIBResult:
        """Evaluate a plan or result against the CIB gate.

        This is the main entry point.  Runs all K-Scenarios, computes the
        minimum score, and blocks if below threshold.

        Args:
            plan_or_result: The text to evaluate.
            constitution: The :class:`Constitution` with K-Scenarios.
            threshold: Override threshold. If ``None``, uses
                ``self.threshold`` or ``constitution.cib_threshold``.

        Returns:
            :class:`CIBResult` with scores, pass/fail, and block status.
        """
        effective_threshold = (
            threshold if threshold is not None
            else self.threshold
        )

        validation = self.validator.validate(plan_or_result, constitution, effective_threshold)

        min_score = validation.min_score
        passed = min_score >= effective_threshold

        result = CIBResult(
            scores=validation.scores,
            min_score=min_score,
            passed=passed,
            blocked=not passed,
            threshold=effective_threshold,
            validation=validation,
        )

        if result.blocked:
            failed_scenarios = [
                r for r in validation.scenario_results if not r.passed
            ]
            failed_ids = [r.scenario_id for r in failed_scenarios]
            result.reason = (
                f"CIB BLOCKED: min_score={min_score:.4f} < threshold={effective_threshold}. "
                f"Failed scenarios: {failed_ids}"
            )
            logger.warning(result.reason)
        else:
            result.reason = (
                f"CIB PASSED: min_score={min_score:.4f} >= threshold={effective_threshold}"
            )
            logger.info(result.reason)

        return result

    def check(
        self,
        plan_or_result: str,
        constitution: Constitution,
    ) -> bool:
        """Convenience: return ``True`` if the CIB gate passes.

        Args:
            plan_or_result: The text to evaluate.
            constitution: The :class:`Constitution`.

        Returns:
            ``True`` if the gate passes (action allowed), ``False`` if blocked.
        """
        result = self.evaluate(plan_or_result, constitution)
        return result.passed

    # ------------------------------------------------------------------
    # Emergency threshold
    # ------------------------------------------------------------------

    def evaluate_emergency(
        self,
        plan_or_result: str,
        constitution: Constitution,
    ) -> CIBResult:
        """Evaluate using the emergency (stricter) threshold.

        Used when a mathematical assumption violation is detected.

        Args:
            plan_or_result: The text to evaluate.
            constitution: The :class:`Constitution`.

        Returns:
            :class:`CIBResult` evaluated at ``cib_emergency_threshold``.
        """
        return self.evaluate(
            plan_or_result,
            constitution,
            threshold=constitution.cib_emergency_threshold,
        )

    # ------------------------------------------------------------------
    # HITL Gate
    # ------------------------------------------------------------------

    @staticmethod
    def require_hitl_approval(
        layer: ConstitutionLayer | str,
        approved: bool = False,
    ) -> HITLResult:
        """Check if a constitution modification requires HITL approval.

        All three layers (absolute, principle, strategy) require HITL
        approval for modifications via the meta loop.

        Args:
            layer: The constitution layer being modified.
            approved: Whether human approval has been granted.

        Returns:
            :class:`HITLResult` indicating whether the modification is allowed.
        """
        if isinstance(layer, str):
            try:
                layer = ConstitutionLayer(layer)
            except ValueError:
                layer = ConstitutionLayer.PRINCIPLE

        result = HITLResult(
            layer=layer,
            requires_approval=True,
            approved=approved,
        )

        if not approved:
            result.reason = (
                f"HITL BLOCKED: Constitution layer '{layer.value}' requires "
                f"human approval for modification. No approval granted."
            )
            logger.warning(result.reason)
        else:
            result.reason = (
                f"HITL PASSED: Constitution layer '{layer.value}' "
                f"modification approved by human."
            )
            logger.info(result.reason)

        return result

    def check_hitl(
        self,
        layer: ConstitutionLayer | str,
        approved: bool = False,
    ) -> bool:
        """Convenience: return ``True`` if HITL approval is granted.

        Args:
            layer: The constitution layer being modified.
            approved: Whether human approval has been granted.

        Returns:
            ``True`` if the modification is allowed, ``False`` if blocked.
        """
        result = self.require_hitl_approval(layer, approved=approved)
        return result.approved

    # ------------------------------------------------------------------
    # Combined check
    # ------------------------------------------------------------------

    def full_check(
        self,
        plan_or_result: str,
        constitution: Constitution,
        modification_layer: ConstitutionLayer | str | None = None,
        hitl_approved: bool = False,
    ) -> dict[str, Any]:
        """Run both CIB and HITL checks.

        Args:
            plan_or_result: The text to evaluate against CIB.
            constitution: The :class:`Constitution`.
            modification_layer: If provided, also checks HITL for this layer.
            hitl_approved: Whether HITL approval has been granted.

        Returns:
            Dict with ``cib_result``, ``hitl_result``, and ``allowed``.
        """
        cib_result = self.evaluate(plan_or_result, constitution)

        hitl_result: HITLResult | None = None
        if modification_layer is not None:
            hitl_result = self.require_hitl_approval(
                modification_layer, approved=hitl_approved,
            )

        allowed = cib_result.passed and (hitl_result is None or hitl_result.approved)

        return {
            "cib_result": cib_result,
            "hitl_result": hitl_result,
            "allowed": allowed,
        }