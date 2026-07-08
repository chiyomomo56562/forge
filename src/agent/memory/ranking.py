"""Memory ranking utilities for the Forge agent framework.

Computes a weighted score combining importance, recency, and relevance
for memory records retrieved across layers.  Used by the MemoryManager
to prioritise which memories to inject (Selective Injection).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .schemas import MemoryRecord


# ===========================================================================
# Weight configuration
# ===========================================================================

@dataclass
class RankingWeights:
    """Weights for the three ranking factors.  Must sum to 1.0."""
    importance: float = 0.35
    recency: float = 0.30
    relevance: float = 0.35

    def __post_init__(self):
        total = self.importance + self.recency + self.relevance
        if not 0.99 <= total <= 1.01:
            raise ValueError(
                f"Ranking weights must sum to 1.0, got {total:.2f} "
                f"(importance={self.importance}, recency={self.recency}, relevance={self.relevance})"
            )


# ===========================================================================
# Individual score computations
# ===========================================================================

def compute_recency(timestamp: str, now: datetime | None = None) -> float:
    """Compute a recency score (0.0–1.0).

    Uses exponential decay:  recent records score near 1.0, old records
    approach 0.0.  Half-life is 7 days.

    Args:
        timestamp: ISO 8601 timestamp string.
        now: Reference datetime (defaults to current UTC).
    """
    if not timestamp:
        return 0.0

    if now is None:
        now = datetime.now(timezone.utc)

    # Parse timestamp
    cleaned = timestamp.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return 0.0

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    age = now - dt
    half_life_days = 7.0
    decay = 0.5 ** (age.total_seconds() / (half_life_days * 86400))
    return round(max(0.0, min(1.0, decay)), 4)


def compute_importance(
    success_score: float | None = None,
    pain_index: float | None = None,
    cib_score: float | None = None,
    has_reflection: bool = False,
    protected: bool = False,
) -> float:
    """Compute an importance score (0.0–1.0).

    Factors:
        - High pain index → more important (we learn from failures)
        - Low CIB score → more important (near-violations need attention)
        - Has reflection → more important (compressed insight)
        - Protected → maximally important

    Args:
        success_score: Episode success score (0–1). Lower = more important.
        pain_index: Pain index (0–1). Higher = more important.
        cib_score: CIB score (0–1). Lower = more important.
        has_reflection: Whether the record has reflection data.
        protected: Whether the record is protected (e.g. constitution).
    """
    if protected:
        return 1.0

    score = 0.3  # Base importance

    if pain_index is not None:
        score += 0.3 * pain_index  # Failures are important

    if cib_score is not None:
        # Near-threshold (0.95) is most important; far above is less
        score += 0.2 * max(0.0, 1.0 - cib_score)

    if has_reflection:
        score += 0.2  # Reflections are compressed insights

    return round(max(0.0, min(1.0, score)), 4)


def compute_relevance(query_similarity: float) -> float:
    """Compute a relevance score (0.0–1.0) from a similarity value.

    Args:
        query_similarity: Cosine similarity or other similarity metric (0–1).
    """
    return round(max(0.0, min(1.0, query_similarity)), 4)


# ===========================================================================
# Combined ranking
# ===========================================================================

def rank_record(
    record: MemoryRecord,
    query_similarity: float = 0.0,
    weights: RankingWeights | None = None,
    now: datetime | None = None,
) -> float:
    """Compute the combined ranking score for a single MemoryRecord.

    The score is stored in ``record.score`` and also returned.

    Args:
        record: The memory record to score.
        query_similarity: Similarity to the current query (0–1).
        weights: Weight configuration (defaults to RankingWeights()).
        now: Reference datetime for recency calculation.

    Returns:
        Combined score (0.0–1.0).
    """
    if weights is None:
        weights = RankingWeights()

    # Extract data from record
    data = record.data

    # Importance
    importance = compute_importance(
        success_score=data.get("evaluation", {}).get("success_score") if isinstance(data.get("evaluation"), dict) else None,
        pain_index=data.get("evaluation", {}).get("pain_index") if isinstance(data.get("evaluation"), dict) else None,
        cib_score=data.get("evaluation", {}).get("cib_score") if isinstance(data.get("evaluation"), dict) else None,
        has_reflection=data.get("has_reflection", False),
        protected=data.get("protected", False),
    )

    # Recency
    recency = compute_recency(record.created_at or data.get("timestamp", ""), now)

    # Relevance
    relevance = compute_relevance(query_similarity)

    # Weighted sum
    combined = (
        weights.importance * importance
        + weights.recency * recency
        + weights.relevance * relevance
    )

    score = round(max(0.0, min(1.0, combined)), 4)
    record.score = score
    return score


def rank_records(
    records: Sequence[MemoryRecord],
    query_similarity: float = 0.0,
    weights: RankingWeights | None = None,
    now: datetime | None = None,
) -> list[MemoryRecord]:
    """Rank and sort a list of MemoryRecords by combined score (descending).

    Args:
        records: Memory records to rank.
        query_similarity: Similarity to the current query (applied to all).
        weights: Weight configuration.
        now: Reference datetime.

    Returns:
        New list sorted by score (highest first).
    """
    ranked = list(records)
    for r in ranked:
        rank_record(r, query_similarity, weights, now)
    ranked.sort(key=lambda r: r.score or 0.0, reverse=True)
    return ranked


def rank_records_with_similarities(
    records: Sequence[tuple[MemoryRecord, float]],
    weights: RankingWeights | None = None,
    now: datetime | None = None,
) -> list[MemoryRecord]:
    """Rank records where each has its own similarity score.

    Args:
        records: List of (record, similarity) tuples.
        weights: Weight configuration.
        now: Reference datetime.

    Returns:
        New list sorted by score (highest first).
    """
    result: list[MemoryRecord] = []
    for record, sim in records:
        rank_record(record, sim, weights, now)
        result.append(record)
    result.sort(key=lambda r: r.score or 0.0, reverse=True)
    return result