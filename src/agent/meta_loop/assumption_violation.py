"""Mathematical Assumption Violation Detection — L3 한계 보완.

Detects when the mathematical assumptions underlying the framework's
convergence proofs may be violated in practice:

    1. **Bimodal success rate** — If recent episodes' success scores
       split into two distinct clusters (bimodal distribution), the loss
       function may have transitioned from convex to non-convex.
    2. **CIB variance threshold** — If CIB scores' variance exceeds a
       critical threshold, the system is unstable and the mathematical
       convergence guarantee may not hold.

When a violation is detected:
    - CIB threshold is automatically elevated (0.95 → 0.97) for more
      conservative learning blocking
    - An emergency meta loop inspection is requested

This module extends the Phoenix Auditor (M15) role from simple scoring
to empirical assumption verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..utils.logging import get_logger

logger = get_logger("agent.meta_loop.assumption_violation")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ViolationType(str, Enum):
    """Types of mathematical assumption violations."""
    NONE = "none"
    BIMODAL_SUCCESS = "bimodal_success"           # 성공률 바이모달
    CIB_VARIANCE_EXCEEDED = "cib_variance_exceeded"  # CIB 분산 임계 초과
    COMBINED = "combined"                           # 복합 위반


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ViolationDetectionResult:
    """Result of mathematical assumption violation detection.

    Attributes:
        violation_type: The detected :class:`ViolationType`.
        detected: Whether any violation was detected.
        bimodal_detected: Whether bimodal success distribution was found.
        cib_variance_exceeded: Whether CIB variance exceeded threshold.
        cib_variance: Computed CIB variance.
        success_rate_clusters: Centers of detected clusters (for bimodal).
        recommended_cib_threshold: Recommended new CIB threshold (0.97).
        emergency_meta_loop_required: Whether emergency meta loop is needed.
        details: Additional detection details.
    """
    violation_type: ViolationType = ViolationType.NONE
    detected: bool = False
    bimodal_detected: bool = False
    cib_variance_exceeded: bool = False
    cib_variance: float = 0.0
    success_rate_clusters: list[float] = field(default_factory=list)
    recommended_cib_threshold: float = 0.97
    emergency_meta_loop_required: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Assumption Violation Detector
# ---------------------------------------------------------------------------

class AssumptionViolationDetector:
    """Detect mathematical assumption violations empirically.

    Args:
        bimodal_gap_threshold: Minimum gap between cluster centers to
            classify as bimodal (default 0.3).
        bimodal_min_cluster_size: Minimum points per cluster (default 5).
        cib_variance_threshold: CIB score variance threshold (default 0.02).
        normal_cib_threshold: Normal CIB threshold (default 0.95).
        emergency_cib_threshold: Elevated CIB threshold (default 0.97).
        min_samples: Minimum samples needed for detection (default 20).
    """

    def __init__(
        self,
        bimodal_gap_threshold: float = 0.3,
        bimodal_min_cluster_size: int = 5,
        cib_variance_threshold: float = 0.02,
        normal_cib_threshold: float = 0.95,
        emergency_cib_threshold: float = 0.97,
        min_samples: int = 20,
    ):
        self.bimodal_gap_threshold = bimodal_gap_threshold
        self.bimodal_min_cluster_size = bimodal_min_cluster_size
        self.cib_variance_threshold = cib_variance_threshold
        self.normal_cib_threshold = normal_cib_threshold
        self.emergency_cib_threshold = emergency_cib_threshold
        self.min_samples = min_samples

    # ------------------------------------------------------------------
    # Main detection
    # ------------------------------------------------------------------

    def detect(
        self,
        success_scores: list[float] | None = None,
        cib_scores: list[float] | None = None,
    ) -> ViolationDetectionResult:
        """Run all violation detection checks.

        Args:
            success_scores: Recent episode success scores (0–1).
            cib_scores: Recent CIB scores (0–1).

        Returns:
            :class:`ViolationDetectionResult` with detection outcome.
        """
        result = ViolationDetectionResult()

        # Check bimodal success distribution
        if success_scores and len(success_scores) >= self.min_samples:
            bimodal_result = self._detect_bimodal(success_scores)
            result.bimodal_detected = bimodal_result["detected"]
            result.success_rate_clusters = bimodal_result["clusters"]
            result.details["bimodal"] = bimodal_result
        else:
            result.details["bimodal"] = {
                "detected": False,
                "reason": f"Insufficient samples ({len(success_scores) if success_scores else 0} < {self.min_samples})",
            }

        # Check CIB variance
        if cib_scores and len(cib_scores) >= self.min_samples:
            cib_var_result = self._detect_cib_variance(cib_scores)
            result.cib_variance_exceeded = cib_var_result["exceeded"]
            result.cib_variance = cib_var_result["variance"]
            result.details["cib_variance"] = cib_var_result
        else:
            result.details["cib_variance"] = {
                "exceeded": False,
                "variance": 0.0,
                "reason": f"Insufficient samples ({len(cib_scores) if cib_scores else 0} < {self.min_samples})",
            }

        # Determine overall violation
        violations = []
        if result.bimodal_detected:
            violations.append(ViolationType.BIMODAL_SUCCESS)
        if result.cib_variance_exceeded:
            violations.append(ViolationType.CIB_VARIANCE_EXCEEDED)

        if len(violations) == 0:
            result.violation_type = ViolationType.NONE
            result.detected = False
        elif len(violations) == 1:
            result.violation_type = violations[0]
            result.detected = True
        else:
            result.violation_type = ViolationType.COMBINED
            result.detected = True

        # Set recommended actions
        if result.detected:
            result.recommended_cib_threshold = self.emergency_cib_threshold
            result.emergency_meta_loop_required = True
            logger.warning(
                f"Mathematical assumption violation detected: "
                f"{result.violation_type.value} — "
                f"recommending CIB threshold elevation to {self.emergency_cib_threshold} "
                f"and emergency meta loop inspection"
            )
        else:
            result.recommended_cib_threshold = self.normal_cib_threshold
            logger.info("No mathematical assumption violations detected")

        return result

    # ------------------------------------------------------------------
    # Bimodal detection
    # ------------------------------------------------------------------

    def _detect_bimodal(self, scores: list[float]) -> dict[str, Any]:
        """Detect if a distribution is bimodal (two distinct clusters).

        Uses a simple k-means-like approach with k=2:
            1. Split scores into two clusters using the mean as threshold
            2. Compute cluster centers
            3. If the gap between centers exceeds the threshold and both
               clusters have enough members, classify as bimodal

        Args:
            scores: List of success scores (0–1).

        Returns:
            Dict with 'detected', 'clusters', 'gap', 'sizes'.
        """
        n = len(scores)
        if n < self.min_samples:
            return {
                "detected": False,
                "clusters": [],
                "gap": 0.0,
                "sizes": [],
                "reason": "Insufficient samples",
            }

        # Initial split: use median as threshold
        sorted_scores = sorted(scores)
        median = sorted_scores[n // 2]

        # Assign to clusters
        low_cluster = [s for s in scores if s <= median]
        high_cluster = [s for s in scores if s > median]

        # Iterate to refine clusters (simple k-means with k=2)
        for _ in range(5):
            if not low_cluster or not high_cluster:
                break
            low_center = sum(low_cluster) / len(low_cluster)
            high_center = sum(high_cluster) / len(high_cluster)

            new_low = []
            new_high = []
            for s in scores:
                if abs(s - low_center) <= abs(s - high_center):
                    new_low.append(s)
                else:
                    new_high.append(s)

            low_cluster = new_low
            high_cluster = new_high

        if not low_cluster or not high_cluster:
            return {
                "detected": False,
                "clusters": [],
                "gap": 0.0,
                "sizes": [n, 0],
                "reason": "Could not form two clusters",
            }

        low_center = sum(low_cluster) / len(low_cluster)
        high_center = sum(high_cluster) / len(high_cluster)
        gap = abs(high_center - low_center)

        detected = (
            gap >= self.bimodal_gap_threshold
            and len(low_cluster) >= self.bimodal_min_cluster_size
            and len(high_cluster) >= self.bimodal_min_cluster_size
        )

        return {
            "detected": detected,
            "clusters": [round(low_center, 4), round(high_center, 4)],
            "gap": round(gap, 4),
            "sizes": [len(low_cluster), len(high_cluster)],
            "threshold_gap": self.bimodal_gap_threshold,
            "threshold_min_size": self.bimodal_min_cluster_size,
        }

    # ------------------------------------------------------------------
    # CIB variance detection
    # ------------------------------------------------------------------

    def _detect_cib_variance(self, cib_scores: list[float]) -> dict[str, Any]:
        """Check if CIB score variance exceeds the critical threshold.

        High variance in CIB scores indicates instability — the system
        is not consistently satisfying its constitutional constraints.

        Args:
            cib_scores: List of CIB scores (0–1).

        Returns:
            Dict with 'exceeded', 'variance', 'threshold'.
        """
        n = len(cib_scores)
        if n < 2:
            return {
                "exceeded": False,
                "variance": 0.0,
                "threshold": self.cib_variance_threshold,
                "reason": "Insufficient samples",
            }

        mean = sum(cib_scores) / n
        variance = sum((x - mean) ** 2 for x in cib_scores) / n

        exceeded = variance > self.cib_variance_threshold

        return {
            "exceeded": exceeded,
            "variance": round(variance, 6),
            "threshold": self.cib_variance_threshold,
            "mean": round(mean, 4),
            "n": n,
        }

    # ------------------------------------------------------------------
    # Recommended actions
    # ------------------------------------------------------------------

    def get_recommended_actions(
        self,
        result: ViolationDetectionResult,
    ) -> list[dict[str, Any]]:
        """Get recommended actions for a detected violation.

        Args:
            result: The :class:`ViolationDetectionResult`.

        Returns:
            List of recommended action dicts.
        """
        actions: list[dict[str, Any]] = []

        if not result.detected:
            return actions

        # Action 1: Elevate CIB threshold
        actions.append({
            "action": "elevate_cib_threshold",
            "from": self.normal_cib_threshold,
            "to": self.emergency_cib_threshold,
            "reason": f"Assumption violation ({result.violation_type.value}) detected",
        })

        # Action 2: Request emergency meta loop
        if result.emergency_meta_loop_required:
            actions.append({
                "action": "request_emergency_meta_loop",
                "reason": "Mathematical assumption violation requires structural inspection",
                "trigger_type": "emergency_inspection",
            })

        # Action 3: Specific to violation type
        if result.bimodal_detected:
            actions.append({
                "action": "flag_non_convex_loss",
                "reason": "Bimodal success distribution suggests non-convex loss landscape",
                "clusters": result.success_rate_clusters,
            })

        if result.cib_variance_exceeded:
            actions.append({
                "action": "flag_cib_instability",
                "reason": f"CIB variance {result.cib_variance:.6f} exceeds threshold {self.cib_variance_threshold}",
            })

        return actions