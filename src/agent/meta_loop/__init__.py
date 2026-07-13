"""Meta Loop — Phase 4.1: 시스템 헌법 및 구조의 자가 재설계 루프.

The meta loop is the highest level of agent autonomy (L5).  It performs
fundamental system redesign:

    1. **Constitution (L4) Revision** — update absolute/principle/strategy
       layers, adjust CIB threshold
    2. **Architecture Self-Modification** — workflow redesign, skill
       category add/remove
    3. **Organizational Restructuring** — team structure optimization,
       federated learning topology changes
    4. **L5 Identity Redesign** — fundamental self-model re-evaluation

**Critical safety: HITL (Human-in-the-Loop)**
    All meta loop changes require explicit human approval before execution.
    No change is applied without HITL approval.

The meta loop is triggered by the outer loop's :class:`MetaTrigger`:
    - Regular evolution (1,000 episodes accumulated)
    - Emergency inspection (100 outer loop cycles)
    - Stagnation response (coherence index stagnation)

Public API::

    from agent.meta_loop import MetaLoop, MetaLoopResult

    meta = MetaLoop(memory_manager=mm, ...)
    result = meta.run(trigger_type="regular_evolution")
"""

from __future__ import annotations

from .meta_loop import MetaLoop, MetaLoopResult, MetaLoopState
from .change_proposal import (
    ChangeProposal,
    ProposalStatus,
    ProposalQueue,
    ProposalResult,
)
from .hitl_gate import (
    HITLGate,
    ApprovalRequest,
    HITLGateResult,
    HITLDecision,
    HITLSeverity,
)
from .constitution_reviser import ConstitutionReviser, ConstitutionRevisionResult
from .architecture_modifier import ArchitectureModifier, ArchitectureChangeResult
from .identity_redesigner import IdentityRedesigner, IdentityRedesignResult

__all__ = [
    "MetaLoop",
    "MetaLoopResult",
    "MetaLoopState",
    "ChangeProposal",
    "ProposalStatus",
    "ProposalQueue",
    "ProposalResult",
    "HITLGate",
    "ApprovalRequest",
    "HITLGateResult",
    "HITLDecision",
    "HITLSeverity",
    "ConstitutionReviser",
    "ConstitutionRevisionResult",
    "ArchitectureModifier",
    "ArchitectureChangeResult",
    "IdentityRedesigner",
    "IdentityRedesignResult",
]