"""Unit tests for Phase 4.2 — HITL Gate.

Covers:
    - hitl_gate.py: Approval request creation, approve/reject, audit log
    - hitl_gate.py: Expiry handling, batch operations, notification callback
    - meta_loop.py: HITL gate integration (auto request creation, gate-based approval)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# HITLGate — Basic operations
# ===========================================================================

class TestHITLGateBasic:
    def test_request_approval(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision, HITLSeverity
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        proposal = queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title="Test",
            description="Test proposal",
        )
        request = gate.request_approval(
            proposal=proposal,
            constitution_layer="absolute",
            severity="high",
            impact_summary="Test impact",
            rollback_plan="Revert YAML",
        )

        assert request.decision == HITLDecision.PENDING
        assert request.constitution_layer.value == "absolute"
        assert request.severity == HITLSeverity.HIGH
        assert request.proposal_id == proposal.proposal_id

    def test_approve(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType, ProposalStatus

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        proposal = queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title="Test",
            description="Test",
        )
        gate.request_approval(proposal=proposal, constitution_layer="principle")

        result = gate.approve(proposal.proposal_id, reviewer="admin", reason="ok")

        assert result.allowed is True
        assert result.decision == HITLDecision.APPROVED
        assert queue.get(proposal.proposal_id).status == ProposalStatus.APPROVED

    def test_reject(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType, ProposalStatus

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        proposal = queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title="Test",
            description="Test",
        )
        gate.request_approval(proposal=proposal)

        result = gate.reject(proposal.proposal_id, reviewer="admin", reason="no")

        assert result.allowed is False
        assert result.decision == HITLDecision.REJECTED
        assert queue.get(proposal.proposal_id).status == ProposalStatus.REJECTED

    def test_approve_already_decided(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        proposal = queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title="Test",
            description="Test",
        )
        gate.request_approval(proposal=proposal)
        gate.approve(proposal.proposal_id, reviewer="admin")

        # Try to approve again
        result = gate.approve(proposal.proposal_id, reviewer="admin")
        assert result.allowed is False
        assert "Already decided" in result.reason


# ===========================================================================
# HITLGate — Audit logging
# ===========================================================================

class TestHITLGateAudit:
    def test_audit_log_created(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        audit_path = tmp_path / "hitl_audit.jsonl"
        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(audit_path),
        )

        proposal = queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title="Test",
            description="Test",
        )
        gate.request_approval(proposal=proposal)
        gate.approve(proposal.proposal_id, reviewer="admin", reason="ok")

        assert audit_path.exists()
        lines = audit_path.read_text().strip().split("\n")
        # Should have at least 2 entries: request_created + approved
        assert len(lines) >= 2
        for line in lines:
            entry = json.loads(line)
            assert "action" in entry
            assert "proposal_id" in entry
            assert "timestamp" in entry

    def test_audit_log_rejection(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        audit_path = tmp_path / "hitl_audit.jsonl"
        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(proposal_queue=queue, audit_log_path=str(audit_path))

        proposal = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="T", description="T")
        gate.request_approval(proposal=proposal)
        gate.reject(proposal.proposal_id, reviewer="admin", reason="bad")

        lines = audit_path.read_text().strip().split("\n")
        actions = [json.loads(l)["action"] for l in lines]
        assert "rejected" in actions


# ===========================================================================
# HITLGate — Expiry handling
# ===========================================================================

class TestHITLGateExpiry:
    def test_expired_request(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
            default_expiry_hours=0.01,  # Very short expiry
        )

        proposal = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="T", description="T")
        request = gate.request_approval(proposal=proposal)

        # Manually set created_at to the past
        from datetime import datetime, timedelta, timezone
        request.created_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        # Check expiry
        expired_ids = gate.check_expiry()
        assert proposal.proposal_id in expired_ids

        # Verify the request is marked expired
        updated_request = gate.get_request(proposal.proposal_id)
        assert updated_request.decision == HITLDecision.EXPIRED

    def test_non_expired_request(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
            default_expiry_hours=72.0,  # Long expiry
        )

        proposal = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="T", description="T")
        gate.request_approval(proposal=proposal)

        expired_ids = gate.check_expiry()
        assert len(expired_ids) == 0  # Nothing expired

    def test_never_expires(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
            default_expiry_hours=0,  # Never expires
        )

        proposal = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="T", description="T")
        gate.request_approval(proposal=proposal, expiry_hours=0)

        expired_ids = gate.check_expiry()
        assert len(expired_ids) == 0


# ===========================================================================
# HITLGate — Batch operations
# ===========================================================================

class TestHITLGateBatch:
    def test_approve_batch(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        p1 = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="A", description="A")
        p2 = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="B", description="B")
        gate.request_approval(proposal=p1)
        gate.request_approval(proposal=p2)

        results = gate.approve_batch([p1.proposal_id, p2.proposal_id], reviewer="admin")

        assert len(results) == 2
        assert all(r.allowed for r in results)

    def test_reject_batch(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        p1 = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="A", description="A")
        p2 = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="B", description="B")
        gate.request_approval(proposal=p1)
        gate.request_approval(proposal=p2)

        results = gate.reject_batch([p1.proposal_id, p2.proposal_id], reviewer="admin")

        assert len(results) == 2
        assert all(r.decision == HITLDecision.REJECTED for r in results)


# ===========================================================================
# HITLGate — Notification callback
# ===========================================================================

class TestHITLGateNotification:
    def test_notification_on_request(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        notifications: list = []

        def callback(request):
            notifications.append(request)

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
            notification_callback=callback,
        )

        proposal = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="T", description="T")
        gate.request_approval(proposal=proposal)

        assert len(notifications) == 1  # Notified on request creation

    def test_notification_on_decision(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        notifications: list = []

        def callback(request):
            notifications.append(request)

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
            notification_callback=callback,
        )

        proposal = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="T", description="T")
        gate.request_approval(proposal=proposal)
        gate.approve(proposal.proposal_id, reviewer="admin")

        # Should have 2 notifications: request + approval
        assert len(notifications) == 2


# ===========================================================================
# HITLGate — Query
# ===========================================================================

class TestHITLGateQuery:
    def test_list_pending_requests(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate, HITLDecision
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        p1 = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="A", description="A")
        p2 = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="B", description="B")
        gate.request_approval(proposal=p1)
        gate.request_approval(proposal=p2)
        gate.approve(p1.proposal_id, reviewer="admin")

        pending = gate.list_pending_requests()
        assert len(pending) == 1
        assert pending[0].proposal_id == p2.proposal_id

    def test_get_request(self, tmp_path):
        from agent.meta_loop.hitl_gate import HITLGate
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalType

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        gate = HITLGate(
            proposal_queue=queue,
            audit_log_path=str(tmp_path / "hitl_audit.jsonl"),
        )

        proposal = queue.create(type=ProposalType.CONSTITUTION_REVISION, title="T", description="T")
        gate.request_approval(proposal=proposal, impact_summary="Custom impact")

        request = gate.get_request(proposal.proposal_id)
        assert request is not None
        assert request.impact_summary == "Custom impact"


# ===========================================================================
# MetaLoop integration with HITLGate
# ===========================================================================

class TestMetaLoopHITLIntegration:
    def test_hitl_requests_auto_created(self, tmp_path):
        """Verify that HITL requests are automatically created for all proposals."""
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
        )
        result = meta.run(trigger_type="regular_evolution")

        # All proposals should have HITL requests
        pending_requests = meta.list_pending_hitl_requests()
        assert len(pending_requests) == len(result.proposals_created)

    def test_approve_via_hitl_gate(self, tmp_path):
        """Verify that approval goes through the HITL gate."""
        from agent.meta_loop import MetaLoop
        from agent.meta_loop.change_proposal import ProposalStatus

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
        )
        result = meta.run(trigger_type="regular_evolution")

        # Approve all through the HITL gate
        for proposal in result.proposals_created:
            approved = meta.approve_proposal(proposal.proposal_id, reviewer="admin", reason="ok")
            assert approved is not None
            assert approved.status == ProposalStatus.APPROVED

    def test_reject_via_hitl_gate(self, tmp_path):
        """Verify that rejection goes through the HITL gate."""
        from agent.meta_loop import MetaLoop
        from agent.meta_loop.change_proposal import ProposalStatus

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
        )
        result = meta.run(trigger_type="regular_evolution")

        for proposal in result.proposals_created:
            rejected = meta.reject_proposal(proposal.proposal_id, reviewer="admin", reason="no")
            assert rejected is not None
            assert rejected.status == ProposalStatus.REJECTED

    def test_batch_approve(self, tmp_path):
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
        )
        result = meta.run(trigger_type="stagnation_response")

        proposal_ids = [p.proposal_id for p in result.proposals_created]
        gate_results = meta.approve_batch(proposal_ids, reviewer="admin", reason="batch")

        assert len(gate_results) == len(proposal_ids)
        assert all(r.allowed for r in gate_results)

    def test_hitl_audit_log_written(self, tmp_path):
        from agent.meta_loop import MetaLoop

        hitl_audit_path = tmp_path / "hitl_audit.jsonl"
        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(hitl_audit_path),
        )
        meta.run(trigger_type="regular_evolution")

        assert hitl_audit_path.exists()
        lines = hitl_audit_path.read_text().strip().split("\n")
        for line in lines:
            entry = json.loads(line)
            assert "action" in entry
            assert "constitution_layer" in entry
            assert "severity" in entry

    def test_expiry_check_integration(self, tmp_path):
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
            hitl_expiry_hours=72.0,
        )
        meta.run(trigger_type="regular_evolution")

        # Check expiry (nothing should be expired yet)
        expired = meta.check_hitl_expiry()
        assert len(expired) == 0

    def test_severity_assignment(self, tmp_path):
        """Verify that severity is correctly assigned based on proposal type."""
        from agent.meta_loop import MetaLoop
        from agent.meta_loop.hitl_gate import HITLSeverity

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
        )
        result = meta.run(trigger_type="emergency_inspection")

        # Emergency inspection proposes CIB threshold update → HIGH severity
        for proposal in result.proposals_created:
            request = meta.hitl_gate.get_request(proposal.proposal_id)
            assert request is not None
            assert request.severity == HITLSeverity.HIGH  # CIB threshold = HIGH

    def test_stagnation_response_severity(self, tmp_path):
        """Verify stagnation response assigns correct severity."""
        from agent.meta_loop import MetaLoop
        from agent.meta_loop.hitl_gate import HITLSeverity

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
        )
        result = meta.run(trigger_type="stagnation_response")

        for proposal in result.proposals_created:
            request = meta.hitl_gate.get_request(proposal.proposal_id)
            assert request is not None
            # Identity redesign → CRITICAL, Architecture → MEDIUM
            assert request.severity in (HITLSeverity.CRITICAL, HITLSeverity.MEDIUM)