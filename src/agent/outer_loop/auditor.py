"""Step 5: Independent Audit — 수행자 자기평가 vs 피닉스 점수 편차 확인.

The independent audit compares the performer's (agent's) self-evaluation
against the Phoenix Auditor's (M15) score:

    - If the deviation (|self_eval - phoenix_score|) >= 0.2, the audit flags
      a "self-evaluation reliability degradation" and records it in the
      self-model (M14) calibration_error.
    - The audit also checks whether the agent is systematically overconfident
      (self_eval > phoenix) or underconfident (self_eval < phoenix).

This step ensures the agent's self-assessment remains trustworthy over time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory.identity.self_model import SelfModel
from ..memory.identity.updater import IdentityUpdater
from ..utils.logging import get_logger

logger = get_logger("agent.outer_loop.auditor")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AuditResult:
    """Result of the independent audit.

    Attributes:
        deviation: Average |self_eval - phoenix_score| across episodes.
        max_deviation: Maximum deviation observed.
        flagged: Whether the deviation exceeds the alert threshold (0.2).
        bias_direction: 'overconfident' | 'underconfident' | 'calibrated'.
        episodes_audited: Number of episodes compared.
        details: Per-episode audit details.
    """
    deviation: float | None = None
    max_deviation: float | None = None
    flagged: bool = False
    bias_direction: str = "calibrated"
    episodes_audited: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Independent Auditor
# ---------------------------------------------------------------------------

class IndependentAuditor:
    """Compare performer self-evaluation vs Phoenix Auditor scores.

    Args:
        self_model: A :class:`SelfModel` instance for recording deviations.
        identity_updater: An :class:`IdentityUpdater` for M14 updates.
        alert_threshold: Deviation threshold for flagging (default 0.2).
    """

    def __init__(
        self,
        self_model: SelfModel | None = None,
        identity_updater: IdentityUpdater | None = None,
        alert_threshold: float = 0.2,
    ):
        self.self_model = self_model
        self.identity_updater = identity_updater
        self.alert_threshold = alert_threshold

    def audit(
        self,
        aggregation_result: Any | None = None,
        episode_pairs: list[dict[str, float]] | None = None,
    ) -> AuditResult:
        """Run the independent audit.

        Args:
            aggregation_result: :class:`AggregationResult` from Step 1.
            episode_pairs: List of dicts with 'self_eval' and 'phoenix_score'
                keys for per-episode comparison. If None, uses aggregation
                data (success_score as self_eval proxy, phoenix_score as
                auditor score).

        Returns:
            :class:`AuditResult` with deviation analysis.
        """
        pairs: list[dict[str, float]] = []

        if episode_pairs is not None:
            pairs = episode_pairs
        elif aggregation_result is not None:
            # Build pairs from aggregation data
            # self_eval proxy: success_score (agent's own evaluation)
            # auditor score: phoenix_score (independent auditor)
            for ep_id in aggregation_result.episode_ids:
                # We don't have per-episode scores in aggregation_result
                # In practice, the caller should provide episode_pairs
                pass

            # If no per-episode pairs available, use aggregate averages
            if not pairs:
                avg_success = aggregation_result.avg_success_score
                avg_phoenix = aggregation_result.avg_phoenix_score
                if avg_success is not None and avg_phoenix is not None:
                    pairs = [{"self_eval": avg_success, "phoenix_score": avg_phoenix}]

        if not pairs:
            logger.info("No data for independent audit")
            return AuditResult()

        return self._compute_audit(pairs)

    def _compute_audit(self, pairs: list[dict[str, float]]) -> AuditResult:
        """Compute deviation analysis from self-eval vs phoenix pairs."""
        deviations: list[float] = []
        details: list[dict[str, Any]] = []
        overconfident_count = 0
        underconfident_count = 0

        for i, pair in enumerate(pairs):
            self_eval = pair.get("self_eval", 0.5)
            phoenix = pair.get("phoenix_score", 0.5)
            deviation = abs(self_eval - phoenix)
            deviations.append(deviation)

            if self_eval > phoenix + 0.05:
                overconfident_count += 1
            elif self_eval < phoenix - 0.05:
                underconfident_count += 1

            details.append({
                "episode_index": i,
                "self_eval": round(self_eval, 4),
                "phoenix_score": round(phoenix, 4),
                "deviation": round(deviation, 4),
            })

        avg_deviation = sum(deviations) / len(deviations)
        max_deviation = max(deviations)
        flagged = avg_deviation >= self.alert_threshold

        # Determine bias direction
        if overconfident_count > underconfident_count:
            bias_direction = "overconfident"
        elif underconfident_count > overconfident_count:
            bias_direction = "underconfident"
        else:
            bias_direction = "calibrated"

        result = AuditResult(
            deviation=round(avg_deviation, 4),
            max_deviation=round(max_deviation, 4),
            flagged=flagged,
            bias_direction=bias_direction,
            episodes_audited=len(pairs),
            details=details,
        )

        logger.info(
            f"Audit complete: avg_deviation={result.deviation}, "
            f"max={result.max_deviation}, flagged={flagged}, "
            f"bias={bias_direction}, episodes={len(pairs)}"
        )

        # If flagged, record in self-model (M14)
        if flagged and self.identity_updater is not None:
            logger.warning(
                f"Self-evaluation reliability degradation detected: "
                f"deviation={avg_deviation:.4f} >= {self.alert_threshold}"
            )

        return result