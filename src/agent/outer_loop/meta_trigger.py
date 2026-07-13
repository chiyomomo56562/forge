"""Step 7: Meta Loop Trigger — 정기 진화(1,000 에피소드) OR 긴급 점검(루프 100회).

Determines whether the meta loop should be triggered based on two conditions
(OR relationship):

    1. **Regular Evolution (episode-based):** ~1,000 episodes accumulated →
       trigger for periodic structural evolution.
    2. **Emergency Inspection (loop-based):** Outer loop has run 100 times →
       trigger for emergency structural inspection due to risk-level changes.

The trigger that fires first depends on the current N value (risk level):
    - Low risk (N=100):  100 loops × 100 episodes = 10,000 episodes →
      regular evolution (1,000 episodes) fires first.
    - Critical risk (N=10): 100 loops × 10 episodes = 1,000 episodes →
      both fire at roughly the same time.
    - High risk (N=20):  100 loops × 20 episodes = 2,000 episodes →
      regular evolution fires first but emergency is close.

Also supports a **stagnation trigger** from M16 (coherence index stagnation).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..utils.logging import get_logger

logger = get_logger("agent.outer_loop.meta_trigger")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TriggerType(str, Enum):
    """Meta loop trigger types."""
    NONE = "none"
    REGULAR_EVOLUTION = "regular_evolution"    # 정기 진화 (1,000 episodes)
    EMERGENCY_INSPECTION = "emergency_inspection"  # 긴급 점검 (100 loops)
    STAGNATION_RESPONSE = "stagnation_response"    # 정체 대응 (M16)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class TriggerResult:
    """Result of meta loop trigger evaluation.

    Attributes:
        trigger_type: The :class:`TriggerType` that fired.
        triggered: Whether any trigger fired.
        reason: Human-readable reason.
        episode_count: Total episodes accumulated.
        outer_loop_count: Total outer loop cycles executed.
        details: Additional trigger-specific details.
    """
    trigger_type: TriggerType = TriggerType.NONE
    triggered: bool = False
    reason: str = ""
    episode_count: int = 0
    outer_loop_count: int = 0
    details: dict[str, Any] = None  # type: ignore

    def __post_init__(self):
        if self.details is None:
            self.details = {}


# ---------------------------------------------------------------------------
# Meta Trigger
# ---------------------------------------------------------------------------

class MetaTrigger:
    """Evaluate meta loop trigger conditions.

    Args:
        episode_threshold: Episode count for regular evolution (default 1000).
        outer_loop_threshold: Outer loop count for emergency inspection (default 100).
    """

    def __init__(
        self,
        episode_threshold: int = 1000,
        outer_loop_threshold: int = 100,
    ):
        self.episode_threshold = episode_threshold
        self.outer_loop_threshold = outer_loop_threshold

    def evaluate(
        self,
        episode_count: int,
        outer_loop_count: int,
        stagnation_detected: bool = False,
    ) -> TriggerResult:
        """Evaluate all trigger conditions.

        Args:
            episode_count: Total episodes accumulated so far.
            outer_loop_count: Total outer loop cycles executed so far.
            stagnation_detected: Whether M16 detected stagnation.

        Returns:
            :class:`TriggerResult` with the fired trigger (if any).
        """
        # Check stagnation first (highest priority — immediate response needed)
        if stagnation_detected:
            logger.warning(
                "Meta loop trigger: STAGNATION_RESPONSE "
                f"(coherence stagnation detected by M16)"
            )
            return TriggerResult(
                trigger_type=TriggerType.STAGNATION_RESPONSE,
                triggered=True,
                reason="Coherence index stagnation detected by M16 growth regulator",
                episode_count=episode_count,
                outer_loop_count=outer_loop_count,
                details={"source": "M16_stagnation"},
            )

        # Check regular evolution (episode-based)
        if episode_count >= self.episode_threshold:
            logger.info(
                f"Meta loop trigger: REGULAR_EVOLUTION "
                f"({episode_count} >= {self.episode_threshold} episodes)"
            )
            return TriggerResult(
                trigger_type=TriggerType.REGULAR_EVOLUTION,
                triggered=True,
                reason=f"Regular evolution: {episode_count} episodes accumulated "
                       f"(threshold: {self.episode_threshold})",
                episode_count=episode_count,
                outer_loop_count=outer_loop_count,
                details={
                    "threshold": self.episode_threshold,
                    "excess": episode_count - self.episode_threshold,
                },
            )

        # Check emergency inspection (loop-based)
        if outer_loop_count >= self.outer_loop_threshold:
            logger.info(
                f"Meta loop trigger: EMERGENCY_INSPECTION "
                f"({outer_loop_count} >= {self.outer_loop_threshold} loops)"
            )
            return TriggerResult(
                trigger_type=TriggerType.EMERGENCY_INSPECTION,
                triggered=True,
                reason=f"Emergency inspection: {outer_loop_count} outer loop cycles "
                       f"(threshold: {self.outer_loop_threshold})",
                episode_count=episode_count,
                outer_loop_count=outer_loop_count,
                details={
                    "threshold": self.outer_loop_threshold,
                    "excess": outer_loop_count - self.outer_loop_threshold,
                },
            )

        # No trigger
        return TriggerResult(
            trigger_type=TriggerType.NONE,
            triggered=False,
            reason="No trigger conditions met",
            episode_count=episode_count,
            outer_loop_count=outer_loop_count,
        )