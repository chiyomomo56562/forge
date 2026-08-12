from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SkillStatus(StrEnum):
    SEED = "seed"
    DEVELOPING = "developing"
    ACTIVE = "active"
    DEGRADING = "degrading"
    ARCHIVED = "archived"


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


@dataclass(frozen=True)
class SkillExecution:
    skill_id: str
    episode_id: str
    success_score: float
    cib_score: float
    executed_at: datetime
