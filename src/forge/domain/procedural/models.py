from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SkillStatus(StrEnum):
    SEED = "seed"
    DEVELOPING = "developing"
    ACTIVE = "active"
    DEGRADING = "degrading"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class SkillStep:
    """A reviewed, executable tool invocation belonging to an L3 skill."""

    step_id: str
    tool_name: str
    tool_arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillStepDraft:
    """A non-executable procedure candidate grounded in an observed reflection."""

    draft_id: str
    source_episode_id: str
    hint: str
    tool_name: str


@dataclass(frozen=True)
class ProceduralSkill:
    skill_id: str
    source_l2_id: str
    procedure: tuple[str, ...]
    reflection_hints: tuple[str, ...]
    status: SkillStatus
    success_rate: float
    total_executions: int
    updated_at: datetime
    executable_steps: tuple[SkillStep, ...] = ()
    step_drafts: tuple[SkillStepDraft, ...] = ()


@dataclass(frozen=True)
class SkillExecution:
    skill_id: str
    episode_id: str
    success_score: float
    cib_score: float
    executed_at: datetime
