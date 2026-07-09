"""Data models (Pydantic v2) for all memory layers (L1–L5).

This module defines the canonical data structures used across the entire
Forge agent framework.  Every memory layer, loop, and audit trail references
these models.

Layers:
    L1 — Episodic   : Episode, Reflection, Evaluation
    L2 — Semantic   : Entity, Relation, KnowledgeNode
    L3 — Procedural : Skill, SkillMetadata
    L4 — Constitution: Constitution, Principle, KScenario
    L5 — Identity   : SelfModelRecord, CalibrationDirection
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ===========================================================================
# Enums
# ===========================================================================

class MemoryLayer(str, Enum):
    """The five memory layers."""
    L1 = "L1"   # Episodic
    L2 = "L2"   # Semantic
    L3 = "L3"   # Procedural
    L4 = "L4"   # Constitution
    L5 = "L5"   # Identity


class EpisodeStatus(str, Enum):
    """Execution status of an episode."""
    SUCCESS = "Success"
    FAILURE = "Failure"
    PARTIAL = "Partial"
    PENDING = "Pending"


class SkillStatus(str, Enum):
    """Skill lifecycle states (README: Seed → Active → Degrading → Archived)."""
    SEED = "Seed"
    ACTIVE = "Active"
    DEGRADING = "Degrading"
    ARCHIVED = "Archived"


class HintType(str, Enum):
    """Dual-storage strategy hint classification.

    - ``general``       → stored in L2 (semantic graph)
    - ``tool_specific`` → stored in L3 (procedural DB reflection_hints)
    """
    GENERAL = "general"
    TOOL_SPECIFIC = "tool_specific"


class CalibrationDirection(str, Enum):
    """M14 self-model calibration direction."""
    OVERCONFIDENT = "overconfident"       # predicted > actual
    UNDERCONFIDENT = "underconfident"     # predicted < actual
    CALIBRATED = "calibrated"             # |predicted - actual| < threshold


class ConstitutionLayer(str, Enum):
    """L4 constitution three-tier structure."""
    ABSOLUTE = "absolute"
    PRINCIPLE = "principle"
    STRATEGY = "strategy"


class UpdaterSource(str, Enum):
    """Who updated a self-model record."""
    OUTER_LOOP = "outer_loop"
    META_LOOP = "meta_loop"


# ===========================================================================
# L1 — Episodic Memory
# ===========================================================================

class Evaluation(BaseModel):
    """Evaluation result from the inner loop evaluation stage."""
    status: EpisodeStatus = EpisodeStatus.PENDING
    success_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Overall success score (0.0–1.0). Set during evaluation stage.",
    )
    pain_index: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Pain Index = 1 - success_score. None during execution, set after evaluation.",
    )
    cib_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="CIB (Constitutional Invariant) score. Must be >= 0.95 to pass.",
    )
    phoenix_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Phoenix Auditor score (0.6*domain + 0.4*reflection). Must be >= 0.95.",
    )
    domain_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Domain score (60% weight in Phoenix score).",
    )
    reflection_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Reflection quality score (40% weight in Phoenix score).",
    )

    def compute_pain_index(self) -> None:
        """Compute pain_index = 1 - success_score after evaluation."""
        if self.success_score is not None:
            self.pain_index = round(1.0 - self.success_score, 4)


class Reflection(BaseModel):
    """Reflection data — the 4 core fields from the inner loop reflection stage.

    Maps directly to the README L1 example:
        what_worked, what_failed, next_hint, causal_condition
    """
    what_worked: str = Field(default="", description="What worked well in this episode.")
    what_failed: str = Field(default="", description="What failed or went wrong.")
    next_hint: str = Field(default="", description="Hint for the next execution.")
    causal_condition: str = Field(default="", description="Causal conditions for success/failure.")

    @property
    def is_empty(self) -> bool:
        """True if all four fields are empty."""
        return not any([self.what_worked, self.what_failed, self.next_hint, self.causal_condition])


class GeneralizedHint(BaseModel):
    """A generalized hint extracted from reflections, routed by dual-storage strategy."""
    hint_id: str
    text: str
    hint_type: HintType
    source_episodes: list[str] = Field(
        default_factory=list,
        description="Episode IDs from which this hint was generalized.",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: str = Field(default="")

    @property
    def target_layer(self) -> MemoryLayer:
        """Where this hint should be stored (dual-storage strategy)."""
        if self.hint_type == HintType.GENERAL:
            return MemoryLayer.L2
        return MemoryLayer.L3


class Episode(BaseModel):
    """L1 Episodic memory record.

    Structure mirrors the README L1 example exactly.
    """
    episode_id: str = Field(description="Unique episode ID (e.g. ep_20260703_143052_a1b2c3d4).")
    task: str = Field(description="The task description.")
    execution_summary: str = Field(default="", description="Summary of what was executed.")
    evaluation: Evaluation = Field(default_factory=Evaluation)
    reflection: Reflection = Field(default_factory=Reflection)
    timestamp: str = Field(description="ISO 8601 timestamp.")
    task_category: str = Field(default="general", description="Task category for self-model.")
    has_reflection: bool = Field(default=False, description="True if reflection is populated (for density-first search).")
    generalized_hints: list[GeneralizedHint] = Field(
        default_factory=list,
        description="Hints extracted from this episode's reflection (dual-storage).",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata.")

    def mark_reflection_complete(self) -> None:
        """Set has_reflection=True after reflection is populated."""
        self.has_reflection = not self.reflection.is_empty


# ===========================================================================
# L2 — Semantic Memory
# ===========================================================================

class Entity(BaseModel):
    """L2 semantic graph node (entity)."""
    id: str = Field(description="Unique entity ID.")
    entity_type: str = Field(default="concept", description="Entity type (concept, tool, person, etc.).")
    label: str = Field(description="Human-readable label.")
    source_episodes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: str = Field(default="")
    properties: dict[str, Any] = Field(default_factory=dict)


class Relation(BaseModel):
    """L2 semantic graph edge (relation between entities)."""
    source: str = Field(description="Source entity ID.")
    target: str = Field(description="Target entity ID.")
    relation: str = Field(description="Relation type (e.g. 'depends_on', 'is_a', 'causes').")
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    source_episodes: list[str] = Field(default_factory=list)
    created_at: str = Field(default="")
    properties: dict[str, Any] = Field(default_factory=dict)


class KnowledgeNode(BaseModel):
    """L2 knowledge graph node for generalized (general) hints."""
    node_id: str
    hint_text: str
    hint_type: HintType = HintType.GENERAL
    source_episodes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: str = Field(default="")
    connected_entities: list[str] = Field(default_factory=list)


# ===========================================================================
# L3 — Procedural Memory
# ===========================================================================

class SkillMetadata(BaseModel):
    """Metadata for a skill (L3)."""
    status: SkillStatus = SkillStatus.SEED
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    version: str = Field(default="1.0")
    total_executions: int = Field(default=0, ge=0)
    last_executed_at: Optional[str] = Field(default=None, description="ISO 8601 timestamp of last execution.")


class Skill(BaseModel):
    """L3 Procedural memory record — an executable skill.

    Code is stored as a file on disk (``code_path``); the ``code`` field
    is populated at load time by reading the file.  This keeps skill code
    version-controllable and editable without touching the database.

    Structure mirrors the README L3 example.
    """
    skill_id: str = Field(description="Unique skill ID (e.g. extract_pdf_text_a1b2c3d4).")
    name: str = Field(default="", description="Human-readable skill name.")
    code_path: str = Field(default="", description="Path to the skill's Python code file (relative to project root).")
    code: str = Field(default="", description="Executable Python code string (loaded from code_path at runtime).")
    description: str = Field(default="", description="What this skill does.")
    metadata: SkillMetadata = Field(default_factory=SkillMetadata)
    reflection_hints: list[str] = Field(
        default_factory=list,
        description="Tool-specific hints from inner loop reflections (dual-storage L3 target).",
    )
    causal_conditions: list[str] = Field(
        default_factory=list,
        description="Preconditions for this skill to succeed.",
    )
    protected: bool = Field(default=False, description="If True, cannot be archived without HITL approval.")
    created_at: str = Field(default="")
    updated_at: str = Field(default="")


# ===========================================================================
# L4 — Constitution
# ===========================================================================

class Principle(BaseModel):
    """A single constitution principle."""
    id: str
    rule: str
    layer: ConstitutionLayer = ConstitutionLayer.PRINCIPLE
    weight: float = Field(default=1.0, ge=0.0, le=1.0)


class KScenario(BaseModel):
    """A test scenario (K-Scenario) for CIB validation."""
    id: str
    principle: str = Field(description="ID of the principle this scenario tests.")
    description: str
    input: str = Field(default="")
    expected_behavior: str = Field(default="")
    violation_example: str = Field(default="")
    direction_function: str = Field(
        description="Description of how the direction score (0–1) is computed.",
    )


class Constitution(BaseModel):
    """L4 Constitution — loaded from YAML files."""
    version: int = 1
    layers: dict[str, dict[str, str]] = Field(default_factory=dict)
    principles: list[Principle] = Field(default_factory=list)
    k_scenarios: list[KScenario] = Field(default_factory=list)
    cib_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    cib_emergency_threshold: float = Field(default=0.97, ge=0.0, le=1.0)


# ===========================================================================
# L5 — Identity
# ===========================================================================

class SelfModelRecord(BaseModel):
    """M14 Agent Self-Model record.

    Maps to the ``self_model`` SQLite table defined in README Section 8.
    Key metric: calibration_error = |predicted_success - actual_success|.
    """
    record_id: str
    episode_id: str
    task_category: str
    # Predicted values (before execution)
    predicted_success: float = Field(ge=0.0, le=1.0)
    predicted_effort: Optional[float] = Field(default=None, ge=0.0)
    # Actual results (after execution)
    actual_success: float = Field(ge=0.0, le=1.0)
    actual_effort: Optional[float] = Field(default=None, ge=0.0)
    # Calibration (core metric)
    calibration_error: float = Field(ge=0.0, le=1.0)
    calibration_direction: CalibrationDirection
    # Window statistics (last 50 episodes)
    window_avg_calibration: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    window_success_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    window_confidence_margin: Optional[float] = Field(default=None)
    # Coherence index (M17)
    coherence_index: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # Metadata
    timestamp: str
    updated_by: UpdaterSource = UpdaterSource.OUTER_LOOP

    @staticmethod
    def compute_calibration(
        predicted: float,
        actual: float,
        threshold: float = 0.05,
    ) -> tuple[float, CalibrationDirection]:
        """Compute calibration error and direction.

        Args:
            predicted: Predicted success score (0–1).
            actual: Actual success score (0–1).
            threshold: Below this error → 'calibrated'.

        Returns:
            (calibration_error, calibration_direction)
        """
        error = abs(predicted - actual)
        if error < threshold:
            direction = CalibrationDirection.CALIBRATED
        elif predicted > actual:
            direction = CalibrationDirection.OVERCONFIDENT
        else:
            direction = CalibrationDirection.UNDERCONFIDENT
        return round(error, 4), direction


class CapabilityRecord(BaseModel):
    """A single capability record for a task category (L5)."""
    id: str
    label: str
    success_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    effort_estimate: float = Field(default=0.5, ge=0.0, le=1.0)
    total_attempts: int = Field(default=0, ge=0)


# ===========================================================================
# Base — MemoryRecord (generic wrapper for all layers)
# ===========================================================================

class MemoryRecord(BaseModel):
    """Generic memory record wrapper used by MemoryManager for cross-layer operations.

    The ``layer`` field identifies which memory layer this record belongs to.
    The ``data`` field holds the layer-specific model (Episode, Skill, Entity, etc.).
    """
    record_id: str
    layer: MemoryLayer
    data: dict[str, Any] = Field(description="Serialized layer-specific model data.")
    score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Ranking score (importance + recency + relevance). Set by ranking.py.",
    )
    created_at: str = Field(default="")
    tags: list[str] = Field(default_factory=list)