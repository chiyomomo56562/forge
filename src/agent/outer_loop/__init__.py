"""Outer Loop — Phase 3.1: 7-step system health check process.

The outer loop runs every *N* episodes (adaptive N) and performs a
7-step health check:

    1. **Data Aggregation** — recent N episodes: success rate, phoenix avg
    2. **Metrics Recording** — CIB validation + coherence index (M17) + BC
    3. **Cache Refresh** — update memory caches
    4. **Self-Model Recalculation** — M14 window stats (last 50 episodes)
    5. **Independent Audit** — performer self-eval vs Phoenix score deviation
    6. **Growth Rate Regulation** — M16 crash/stagnation/overgrowth detection
    7. **Meta Loop Trigger** — regular evolution (1,000 episodes) or
       emergency inspection (100 outer loop cycles)

Public API::

    from agent.outer_loop import OuterLoop, OuterLoopResult

    outer = OuterLoop(memory_manager=mm, ...)
    result = outer.run()
"""

from __future__ import annotations

from .outer_loop import OuterLoop, OuterLoopResult, OuterLoopState
from .aggregator import DataAggregator, AggregationResult
from .metrics import MetricsRecorder, MetricsResult
from .cache_refresher import CacheRefresher
from .self_model_recalculator import SelfModelRecalculator, RecalculationResult
from .auditor import IndependentAuditor, AuditResult
from .growth_regulator import GrowthRateRegulator, GrowthSignal, GrowthRegulationResult
from .growth_actions import GrowthActionExecutor, ActionResult
from .coherence_index import CoherenceIndexTracker, CoherenceRecord, CoherenceTrend, TrendAnalysis
from .adaptive_n import AdaptiveNCalculator, AdaptiveNResult, RISK_N_MAP
from .meta_trigger import MetaTrigger, TriggerResult, TriggerType

__all__ = [
    "OuterLoop",
    "OuterLoopResult",
    "OuterLoopState",
    "DataAggregator",
    "AggregationResult",
    "MetricsRecorder",
    "MetricsResult",
    "CacheRefresher",
    "SelfModelRecalculator",
    "RecalculationResult",
    "IndependentAuditor",
    "AuditResult",
    "GrowthRateRegulator",
    "GrowthSignal",
    "GrowthRegulationResult",
    "GrowthActionExecutor",
    "ActionResult",
    "CoherenceIndexTracker",
    "CoherenceRecord",
    "CoherenceTrend",
    "TrendAnalysis",
    "AdaptiveNCalculator",
    "AdaptiveNResult",
    "RISK_N_MAP",
    "MetaTrigger",
    "TriggerResult",
    "TriggerType",
]