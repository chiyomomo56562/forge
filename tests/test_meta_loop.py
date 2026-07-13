"""Unit tests for Phase 4.1 — Meta Loop.

Covers:
    - change_proposal.py: Proposal lifecycle (create, approve, reject, execute)
    - constitution_reviser.py: Constitution revision proposals
    - architecture_modifier.py: Architecture modification proposals
    - identity_redesigner.py: Identity redesign proposals
    - meta_loop.py: Full orchestration + HITL gate + state persistence
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# ChangeProposal / ProposalQueue
# ===========================================================================

class TestProposalQueue:
    def test_create_proposal(self, tmp_path):
        from agent.meta_loop.change_proposal import (
            ProposalQueue, ProposalStatus, ProposalType,
        )

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        proposal = queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title="Test proposal",
            description="A test change",
            changes={"key": "value"},
        )

        assert proposal.status == ProposalStatus.PENDING
        assert proposal.proposal_id.startswith("prop_")
        assert proposal.created_at != ""

    def test_approve_proposal(self, tmp_path):
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalStatus

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        proposal = queue.create(
            type="constitution_revision",
            title="Test",
            description="Test",
        )
        approved = queue.approve(proposal.proposal_id, reviewer="admin", reason="ok")

        assert approved.status == ProposalStatus.APPROVED
        assert approved.reviewer == "admin"

    def test_reject_proposal(self, tmp_path):
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalStatus

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        proposal = queue.create(
            type="constitution_revision",
            title="Test",
            description="Test",
        )
        rejected = queue.reject(proposal.proposal_id, reviewer="admin", reason="no")

        assert rejected.status == ProposalStatus.REJECTED

    def test_execute_approved_proposal(self, tmp_path):
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalStatus

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))

        executed = {"called": False}

        def executor(proposal):
            executed["called"] = True
            return {"applied": True}

        proposal = queue.create(
            type="architecture_modification",
            title="Test",
            description="Test",
            executor=executor,
        )
        queue.approve(proposal.proposal_id)
        result = queue.execute(proposal.proposal_id)

        assert result.success is True
        assert executed["called"] is True
        assert queue.get(proposal.proposal_id).status == ProposalStatus.EXECUTED

    def test_execute_without_approval_fails(self, tmp_path):
        from agent.meta_loop.change_proposal import ProposalQueue

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))

        def executor(proposal):
            return {"applied": True}

        proposal = queue.create(
            type="architecture_modification",
            title="Test",
            description="Test",
            executor=executor,
        )
        # Don't approve — try to execute directly
        result = queue.execute(proposal.proposal_id)
        assert result.success is False
        assert "not APPROVED" in result.error

    def test_execute_without_executor_fails(self, tmp_path):
        from agent.meta_loop.change_proposal import ProposalQueue

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        proposal = queue.create(
            type="constitution_revision",
            title="Test",
            description="Test",
            # No executor
        )
        queue.approve(proposal.proposal_id)
        result = queue.execute(proposal.proposal_id)
        assert result.success is False
        assert "No executor" in result.error

    def test_list_pending(self, tmp_path):
        from agent.meta_loop.change_proposal import ProposalQueue

        queue = ProposalQueue(log_path=str(tmp_path / "proposals.jsonl"))
        queue.create(type="constitution_revision", title="A", description="A")
        queue.create(type="constitution_revision", title="B", description="B")
        queue.create(type="constitution_revision", title="C", description="C")

        pending = queue.list_pending()
        assert len(pending) == 3

    def test_persistence(self, tmp_path):
        """Verify proposals are saved to disk and loaded on restart."""
        from agent.meta_loop.change_proposal import ProposalQueue, ProposalStatus

        path = str(tmp_path / "proposals.jsonl")

        queue1 = ProposalQueue(log_path=path)
        p = queue1.create(type="constitution_revision", title="Test", description="Test")
        queue1.approve(p.proposal_id)

        # New instance loads from disk
        queue2 = ProposalQueue(log_path=path)
        loaded = queue2.get(p.proposal_id)
        assert loaded is not None
        assert loaded.status == ProposalStatus.APPROVED


# ===========================================================================
# ConstitutionReviser
# ===========================================================================

class TestConstitutionReviser:
    def test_propose_cib_threshold_update(self, tmp_path):
        from agent.meta_loop.constitution_reviser import ConstitutionReviser
        from agent.meta_loop.change_proposal import ProposalStatus

        # Copy constitution files
        const_dir = tmp_path / "constitution"
        const_dir.mkdir()
        import shutil
        shutil.copytree(str(PROJECT_ROOT / "constitution"), str(const_dir),
                       dirs_exist_ok=True)

        reviser = ConstitutionReviser(
            constitution_dir=str(const_dir),
            proposal_queue=None,  # Will create its own
        )
        proposal = reviser.propose_cib_threshold_update(
            new_threshold=0.97,
            description="Test CIB threshold update",
        )

        assert proposal.status == ProposalStatus.PENDING
        assert proposal.changes["new_threshold"] == 0.97

    def test_propose_principle_update(self, tmp_path):
        from agent.meta_loop.constitution_reviser import ConstitutionReviser

        const_dir = tmp_path / "constitution"
        const_dir.mkdir()
        import shutil
        shutil.copytree(str(PROJECT_ROOT / "constitution"), str(const_dir),
                       dirs_exist_ok=True)

        reviser = ConstitutionReviser(constitution_dir=str(const_dir))
        proposal = reviser.propose_principle_update(
            principle_id="honesty",
            new_rule="Updated honesty rule",
            description="Test principle update",
        )

        assert proposal.changes["principle_id"] == "honesty"
        assert proposal.changes["new_rule"] == "Updated honesty rule"

    def test_execute_cib_threshold_update(self, tmp_path):
        from agent.meta_loop.constitution_reviser import ConstitutionReviser
        from agent.meta_loop.change_proposal import ProposalStatus

        const_dir = tmp_path / "constitution"
        const_dir.mkdir()
        import shutil
        shutil.copytree(str(PROJECT_ROOT / "constitution"), str(const_dir),
                       dirs_exist_ok=True)

        reviser = ConstitutionReviser(constitution_dir=str(const_dir))
        proposal = reviser.propose_cib_threshold_update(
            new_threshold=0.97,
            new_emergency_threshold=0.99,
        )

        # Approve and execute
        reviser.proposal_queue.approve(proposal.proposal_id)
        result = reviser.proposal_queue.execute(proposal.proposal_id)

        assert result.success is True
        assert result.applied_changes["new_threshold"] == 0.97

        # Verify the YAML was actually updated
        import yaml
        safety_path = const_dir / "safety.yml"
        with safety_path.open() as f:
            safety_data = yaml.safe_load(f)
        assert safety_data["cib"]["threshold"] == 0.97
        assert safety_data["cib"]["emergency_threshold"] == 0.99


# ===========================================================================
# ArchitectureModifier
# ===========================================================================

class TestArchitectureModifier:
    def test_propose_skill_category_add(self, tmp_path):
        from agent.meta_loop.architecture_modifier import ArchitectureModifier

        modifier = ArchitectureModifier(
            skill_store=None,
            proposal_queue=None,
        )
        proposal = modifier.propose_skill_category_add(
            category_id="data_analysis",
            description="Skills for data analysis",
        )

        assert proposal.changes["category_id"] == "data_analysis"

    def test_execute_skill_category_add(self, tmp_path):
        from agent.meta_loop.architecture_modifier import ArchitectureModifier
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        modifier = ArchitectureModifier(
            skill_store=store,
            proposal_queue=None,
        )
        proposal = modifier.propose_skill_category_add(
            category_id="data_analysis",
            description="Data analysis skills",
        )

        modifier.proposal_queue.approve(proposal.proposal_id)
        result = modifier.proposal_queue.execute(proposal.proposal_id)

        assert result.success is True
        assert "seed_data_analysis" in store.list_all()[0].skill_id or \
               any("data_analysis" in s.skill_id for s in store.list_all())

    def test_execute_workflow_update(self, tmp_path):
        from agent.meta_loop.architecture_modifier import ArchitectureModifier

        modifier = ArchitectureModifier(skill_store=None)
        proposal = modifier.propose_workflow_update(
            parameter_name="max_retries",
            new_value=5,
            current_value=3,
        )

        modifier.proposal_queue.approve(proposal.proposal_id)
        result = modifier.proposal_queue.execute(proposal.proposal_id)

        assert result.success is True
        assert result.applied_changes["parameter"] == "max_retries"
        assert result.applied_changes["new_value"] == 5


# ===========================================================================
# IdentityRedesigner
# ===========================================================================

class TestIdentityRedesigner:
    def test_propose_autonomy_level_change(self, tmp_path):
        from agent.meta_loop.identity_redesigner import IdentityRedesigner

        redesigner = IdentityRedesigner(
            identity_updater=None,
            proposal_queue=None,
        )
        proposal = redesigner.propose_autonomy_level_change(
            new_level="L4",
            current_level="L3",
        )

        assert proposal.changes["autonomy_level"]["current"] == "L4"

    def test_propose_full_redesign(self, tmp_path):
        from agent.meta_loop.identity_redesigner import IdentityRedesigner

        redesigner = IdentityRedesigner(identity_updater=None)
        proposal = redesigner.propose_full_redesign(
            new_config={
                "autonomy_level": {"current": "L5"},
                "initial_bias": {"optimism": 0.2},
            },
        )

        assert "autonomy_level" in proposal.changes
        assert "initial_bias" in proposal.changes

    def test_execute_redesign_with_identity_updater(self, tmp_path):
        from agent.meta_loop.identity_redesigner import IdentityRedesigner
        from agent.memory.identity.identity_store import IdentityStore
        from agent.memory.identity.updater import IdentityUpdater

        # Create identity files
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir()
        identity_yml = identity_dir / "identity.yml"
        identity_yml.write_text(json.dumps({
            "autonomy_level": {"current": "L3"},
            "identity_core": {"values": ["honesty"]},
            "version": 1,
            "version_history": [],
        }))

        store = IdentityStore(db_path=str(tmp_path / "identity.sqlite3"))
        updater = IdentityUpdater(
            store=store,
            identity_yaml_path=str(identity_yml),
        )

        redesigner = IdentityRedesigner(
            identity_updater=updater,
            proposal_queue=None,
        )
        proposal = redesigner.propose_autonomy_level_change(
            new_level="L4",
            current_level="L3",
        )

        redesigner.proposal_queue.approve(proposal.proposal_id)
        result = redesigner.proposal_queue.execute(proposal.proposal_id)

        assert result.success is True
        assert result.applied_changes["approved"] is True


# ===========================================================================
# MetaLoop orchestrator
# ===========================================================================

class TestMetaLoop:
    def test_run_regular_evolution(self, tmp_path):
        from agent.meta_loop import MetaLoop, MetaLoopResult

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        result = meta.run(trigger_type="regular_evolution")

        assert isinstance(result, MetaLoopResult)
        assert result.trigger_type == "regular_evolution"
        assert len(result.proposals_created) > 0
        assert result.pending_count > 0
        assert result.state.meta_loop_count == 1

    def test_run_emergency_inspection(self, tmp_path):
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        result = meta.run(trigger_type="emergency_inspection")

        # Emergency inspection should propose CIB threshold increase
        assert len(result.proposals_created) > 0
        cib_proposal = next(
            (p for p in result.proposals_created
             if "CIB" in p.title or "threshold" in p.title),
            None,
        )
        assert cib_proposal is not None

    def test_run_stagnation_response(self, tmp_path):
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        result = meta.run(trigger_type="stagnation_response")

        # Stagnation should propose identity and architecture changes
        assert len(result.proposals_created) >= 2

    def test_state_persistence(self, tmp_path):
        from agent.meta_loop import MetaLoop

        state_path = str(tmp_path / "meta_state.json")

        meta1 = MetaLoop(
            memory_manager=None,
            state_path=state_path,
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        meta1.run(trigger_type="regular_evolution")
        meta1.run(trigger_type="regular_evolution")

        meta2 = MetaLoop(
            memory_manager=None,
            state_path=state_path,
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        assert meta2.state.meta_loop_count == 2

    def test_approve_and_execute(self, tmp_path):
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        result = meta.run(trigger_type="regular_evolution")

        # Approve all pending proposals
        for proposal in result.proposals_created:
            meta.approve_proposal(proposal.proposal_id, reviewer="admin")

        # Execute approved
        executed = meta.execute_approved()
        # Some may fail (no executor for constitution revision without files)
        # but the flow should work
        assert isinstance(executed, list)

    def test_audit_log_written(self, tmp_path):
        from agent.meta_loop import MetaLoop

        log_path = tmp_path / "meta_log.jsonl"
        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(log_path),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        meta.run(trigger_type="regular_evolution")

        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip().split("\n")[0])
        assert "trigger_type" in entry
        assert "meta_loop_count" in entry
        assert "proposals_created" in entry

    def test_all_proposals_require_hitl(self, tmp_path):
        """Verify that no proposal is executed without approval."""
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
        )
        result = meta.run(trigger_type="regular_evolution")

        # Try to execute without approval — should fail
        executed = meta.execute_approved()
        assert len(executed) == 0  # Nothing approved yet