"""Step 1: Data Aggregation — 최근 N개 에피소드 성공률, 피닉스 점수 평균 산출.

Aggregates metrics from the most recent *N* episodes stored in L1:
    - Success rate (from evaluation.status or success_score)
    - Average Phoenix score
    - Average CIB score
    - Average pain index
    - Episode count and status distribution

The result feeds into Step 2 (metrics recording) and Step 6 (growth regulator).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from collections import Counter

from ..memory.episodic.store import EpisodicStore
from ..memory.schemas import EpisodeStatus
from ..utils.logging import get_logger

logger = get_logger("agent.outer_loop.aggregator")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AggregationResult:
    """Aggregated metrics from recent N episodes.

    Attributes:
        episode_count: Number of episodes in the window.
        success_rate: Fraction of episodes with status Success (0–1).
        avg_phoenix_score: Average Phoenix score across episodes (0–1).
        avg_cib_score: Average CIB score across episodes (0–1).
        avg_success_score: Average success_score across episodes (0–1).
        avg_pain_index: Average pain index across episodes (0–1).
        status_distribution: Count of each EpisodeStatus.
        episode_ids: IDs of the episodes in this window.
        phoenix_scores: List of individual Phoenix scores (for volatility).
        cib_scores: List of individual CIB scores (for volatility).
    """
    episode_count: int = 0
    success_rate: float = 0.0
    avg_phoenix_score: float | None = None
    avg_cib_score: float | None = None
    avg_success_score: float | None = None
    avg_pain_index: float | None = None
    status_distribution: dict[str, int] = field(default_factory=dict)
    episode_ids: list[str] = field(default_factory=list)
    phoenix_scores: list[float] = field(default_factory=list)
    cib_scores: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Data Aggregator
# ---------------------------------------------------------------------------

class DataAggregator:
    """Aggregate metrics from recent N episodes in L1.

    Args:
        episodic_store: An :class:`EpisodicStore` instance.
        window_size: Number of recent episodes to aggregate (default 50).
    """

    def __init__(
        self,
        episodic_store: EpisodicStore | None = None,
        window_size: int = 50,
    ):
        self.episodic_store = episodic_store
        self.window_size = window_size

    def aggregate(self, window_size: int | None = None) -> AggregationResult:
        """Aggregate metrics from the most recent episodes.

        Args:
            window_size: Override the default window size.

        Returns:
            :class:`AggregationResult` with aggregated metrics.
        """
        n = window_size or self.window_size

        if self.episodic_store is None:
            logger.warning("No episodic store available, returning empty aggregation")
            return AggregationResult()

        # Get recent episodes from L1
        episodes = self.episodic_store.list_recent(n=n)

        if not episodes:
            logger.info("No episodes found in L1 for aggregation")
            return AggregationResult()

        return self._compute_aggregation(episodes)

    def _compute_aggregation(
        self,
        episodes: list[dict[str, Any]],
    ) -> AggregationResult:
        """Compute aggregation from a list of episode dicts."""
        count = len(episodes)
        episode_ids: list[str] = []
        status_counts: Counter = Counter()
        phoenix_scores: list[float] = []
        cib_scores: list[float] = []
        success_scores: list[float] = []
        pain_indices: list[float] = []
        success_count = 0

        for ep in episodes:
            meta = ep.get("metadata", {})
            ep_id = meta.get("episode_id", ep.get("id", ""))
            episode_ids.append(ep_id)

            # Status distribution
            status = meta.get("status", "Pending")
            status_counts[status] += 1
            if status == EpisodeStatus.SUCCESS.value:
                success_count += 1

            # Collect scores
            phoenix = meta.get("phoenix_score")
            if phoenix is not None:
                phoenix_scores.append(float(phoenix))

            cib = meta.get("cib_score")
            if cib is not None:
                cib_scores.append(float(cib))

            success = meta.get("success_score")
            if success is not None:
                success_scores.append(float(success))

            pain = meta.get("pain_index")
            if pain is not None:
                pain_indices.append(float(pain))

        success_rate = success_count / count if count > 0 else 0.0

        avg_phoenix = _safe_mean(phoenix_scores)
        avg_cib = _safe_mean(cib_scores)
        avg_success = _safe_mean(success_scores)
        avg_pain = _safe_mean(pain_indices)

        result = AggregationResult(
            episode_count=count,
            success_rate=round(success_rate, 4),
            avg_phoenix_score=avg_phoenix,
            avg_cib_score=avg_cib,
            avg_success_score=avg_success,
            avg_pain_index=avg_pain,
            status_distribution=dict(status_counts),
            episode_ids=episode_ids,
            phoenix_scores=phoenix_scores,
            cib_scores=cib_scores,
        )

        logger.info(
            f"Aggregated {count} episodes: success_rate={result.success_rate:.2%}, "
            f"avg_phoenix={avg_phoenix}, avg_cib={avg_cib}"
        )
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_mean(values: list[float]) -> float | None:
    """Compute mean, returning None for empty lists."""
    if not values:
        return None
    return round(sum(values) / len(values), 4)