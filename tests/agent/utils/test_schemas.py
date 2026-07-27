"""Unit tests for Phase 1.1 — schemas, policies, ranking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ===========================================================================
# schemas.py — Enums
# ===========================================================================

class TestEnums:
    def test_memory_layer_values(self):
        from agent.memory.schemas import MemoryLayer

        assert MemoryLayer.L1.value == "L1"
        assert MemoryLayer.L5.value == "L5"

    def test_skill_status_values(self):
        from agent.memory.schemas import SkillStatus

        assert SkillStatus.SEED.value == "Seed"
        assert SkillStatus.ACTIVE.value == "Active"
        assert SkillStatus.DEGRADING.value == "Degrading"
        assert SkillStatus.ARCHIVED.value == "Archived"

    def test_hint_type_values(self):
        from agent.memory.schemas import HintType

        assert HintType.GENERAL.value == "general"
        assert HintType.TOOL_SPECIFIC.value == "tool_specific"

    def test_calibration_direction_values(self):
        from agent.memory.schemas import CalibrationDirection

        assert CalibrationDirection.OVERCONFIDENT.value == "overconfident"
        assert CalibrationDirection.UNDERCONFIDENT.value == "underconfident"
        assert CalibrationDirection.CALIBRATED.value == "calibrated"


# ===========================================================================
# schemas.py — L1 Episode / Evaluation / Reflection
# ===========================================================================

class TestEvaluation:
    def test_default_values(self):
        from agent.memory.schemas import Evaluation, EpisodeStatus

        ev = Evaluation()
        assert ev.status == EpisodeStatus.PENDING
        assert ev.success_score is None
        assert ev.pain_index is None

    def test_compute_pain_index(self):
        from agent.memory.schemas import Evaluation, EpisodeStatus

        ev = Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.9)
        ev.compute_pain_index()
        assert ev.pain_index == 0.1

    def test_compute_pain_index_failure(self):
        from agent.memory.schemas import Evaluation, EpisodeStatus

        ev = Evaluation(status=EpisodeStatus.FAILURE, success_score=0.2)
        ev.compute_pain_index()
        assert ev.pain_index == 0.8

    def test_score_bounds(self):
        from agent.memory.schemas import Evaluation

        with pytest.raises(Exception):
            Evaluation(success_score=1.5)
        with pytest.raises(Exception):
            Evaluation(success_score=-0.1)


class TestReflection:
    def test_default_empty(self):
        from agent.memory.schemas import Reflection

        r = Reflection()
        assert r.is_empty is True

    def test_populated_not_empty(self):
        from agent.memory.schemas import Reflection

        r = Reflection(what_worked="Pandas 사용", what_failed="폰트 설정 누락")
        assert r.is_empty is False

    def test_all_four_fields(self):
        from agent.memory.schemas import Reflection

        r = Reflection(
            what_worked="A",
            what_failed="B",
            next_hint="C",
            causal_condition="D",
        )
        assert r.what_worked == "A"
        assert r.what_failed == "B"
        assert r.next_hint == "C"
        assert r.causal_condition == "D"
        assert r.is_empty is False


class TestEpisode:
    def test_create_episode(self):
        from agent.memory.schemas import Episode

        ep = Episode(
            episode_id="ep_20260703_001",
            task="데이터 시각화",
            execution_summary="Matplotlib 차트 생성",
            timestamp="2026-07-03T10:00:00Z",
        )
        assert ep.episode_id == "ep_20260703_001"
        assert ep.task == "데이터 시각화"
        assert ep.has_reflection is False
        assert ep.task_category == "general"

    def test_mark_reflection_complete(self):
        from agent.memory.schemas import Episode, Reflection

        ep = Episode(
            episode_id="ep_001",
            task="test",
            timestamp="2026-07-03T10:00:00Z",
        )
        ep.reflection = Reflection(what_worked="good")
        ep.mark_reflection_complete()
        assert ep.has_reflection is True

    def test_episode_with_full_evaluation(self):
        from agent.memory.schemas import Episode, EpisodeStatus, Evaluation, Reflection

        ep = Episode(
            episode_id="ep_001",
            task="코드 작성",
            execution_summary="스크립트 실행",
            evaluation=Evaluation(
                status=EpisodeStatus.SUCCESS,
                success_score=0.9,
                cib_score=0.97,
                phoenix_score=0.95,
            ),
            reflection=Reflection(
                what_worked="Pandas 데이터프레임 직접 전달",
                what_failed="한글 폰트 설정 누락",
                next_hint="폰트 캐시 확인 코드 추가",
                causal_condition="한글 포함 시 폰트 설정 필요",
            ),
            timestamp="2026-07-03T10:00:00Z",
        )
        ep.evaluation.compute_pain_index()
        ep.mark_reflection_complete()
        assert ep.evaluation.pain_index == 0.1
        assert ep.has_reflection is True


# ===========================================================================
# schemas.py — GeneralizedHint (dual storage)
# ===========================================================================

class TestGeneralizedHint:
    def test_general_hint_targets_l2(self):
        from agent.memory.schemas import GeneralizedHint, HintType, MemoryLayer

        h = GeneralizedHint(
            hint_id="h_001",
            text="한글 시각화 시 폰트 캐시 확인",
            hint_type=HintType.GENERAL,
        )
        assert h.target_layer == MemoryLayer.L2

    def test_tool_specific_hint_targets_l3(self):
        from agent.memory.schemas import GeneralizedHint, HintType, MemoryLayer

        h = GeneralizedHint(
            hint_id="h_002",
            text="PyPDF2는 이미지 PDF 추출 안 됨, OCR 연동",
            hint_type=HintType.TOOL_SPECIFIC,
        )
        assert h.target_layer == MemoryLayer.L3


# ===========================================================================
# schemas.py — L2 Entity / Relation
# ===========================================================================

class TestEntityRelation:
    def test_entity(self):
        from agent.memory.schemas import Entity

        e = Entity(id="ent_001", label="Matplotlib", entity_type="tool")
        assert e.id == "ent_001"
        assert e.confidence == 0.5

    def test_relation(self):
        from agent.memory.schemas import Relation

        r = Relation(source="ent_001", target="ent_002", relation="depends_on")
        assert r.source == "ent_001"
        assert r.relation == "depends_on"
        assert r.weight == 1.0


# ===========================================================================
# schemas.py — L3 Skill
# ===========================================================================

class TestSkill:
    def test_create_skill(self):
        from agent.memory.schemas import Skill, SkillMetadata, SkillStatus

        s = Skill(
            skill_id="extract_pdf_text_abc123",
            name="PDF Text Extraction",
            code="import PyPDF2\ndef execute(f): ...",
            metadata=SkillMetadata(status=SkillStatus.ACTIVE, success_rate=0.95, version="1.2"),
            reflection_hints=["이미지 PDF는 OCR 연동 필요"],
            protected=False,
        )
        assert s.skill_id == "extract_pdf_text_abc123"
        assert s.metadata.status == SkillStatus.ACTIVE
        assert s.metadata.success_rate == 0.95
        assert len(s.reflection_hints) == 1

    def test_skill_default_status_seed(self):
        from agent.memory.schemas import Skill, SkillStatus

        s = Skill(skill_id="test", code="pass")
        assert s.metadata.status == SkillStatus.SEED
        assert s.metadata.success_rate == 0.0


# ===========================================================================
# schemas.py — L4 Constitution
# ===========================================================================

class TestConstitution:
    def test_principle(self):
        from agent.memory.schemas import ConstitutionLayer, Principle

        p = Principle(id="honesty", rule="불확실한 내용을 확실한 것처럼 말하지 않는다.", layer=ConstitutionLayer.ABSOLUTE)
        assert p.layer == ConstitutionLayer.ABSOLUTE
        assert p.weight == 1.0

    def test_k_scenario(self):
        from agent.memory.schemas import KScenario

        ks = KScenario(
            id="ks_honesty_01",
            principle="honesty",
            description="불확실 정보 단언 상황",
            direction_function="불확실성 표시 시 1.0",
        )
        assert ks.principle == "honesty"
        assert ks.id == "ks_honesty_01"

    def test_constitution_defaults(self):
        from agent.memory.schemas import Constitution

        c = Constitution()
        assert c.cib_threshold == 0.95
        assert c.cib_emergency_threshold == 0.97


# ===========================================================================
# schemas.py — L5 SelfModelRecord
# ===========================================================================

class TestSelfModelRecord:
    def test_compute_calibration_overconfident(self):
        from agent.memory.schemas import CalibrationDirection, SelfModelRecord

        error, direction = SelfModelRecord.compute_calibration(
            predicted=0.9, actual=0.5, threshold=0.05
        )
        assert error == 0.4
        assert direction == CalibrationDirection.OVERCONFIDENT

    def test_compute_calibration_underconfident(self):
        from agent.memory.schemas import CalibrationDirection, SelfModelRecord

        error, direction = SelfModelRecord.compute_calibration(
            predicted=0.3, actual=0.8, threshold=0.05
        )
        assert error == 0.5
        assert direction == CalibrationDirection.UNDERCONFIDENT

    def test_compute_calibration_calibrated(self):
        from agent.memory.schemas import CalibrationDirection, SelfModelRecord

        error, direction = SelfModelRecord.compute_calibration(
            predicted=0.7, actual=0.72, threshold=0.05
        )
        assert error == 0.02
        assert direction == CalibrationDirection.CALIBRATED

    def test_create_self_model_record(self):
        from agent.memory.schemas import CalibrationDirection, SelfModelRecord, UpdaterSource

        r = SelfModelRecord(
            record_id="rec_001",
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.8,
            actual_success=0.6,
            calibration_error=0.2,
            calibration_direction=CalibrationDirection.OVERCONFIDENT,
            timestamp="2026-07-03T10:00:00Z",
            updated_by=UpdaterSource.OUTER_LOOP,
        )
        assert r.calibration_error == 0.2
        assert r.updated_by == UpdaterSource.OUTER_LOOP


# ===========================================================================
# schemas.py — MemoryRecord
# ===========================================================================

class TestMemoryRecord:
    def test_create_memory_record(self):
        from agent.memory.schemas import MemoryLayer, MemoryRecord

        r = MemoryRecord(
            record_id="rec_001",
            layer=MemoryLayer.L1,
            data={"episode_id": "ep_001", "task": "test"},
            created_at="2026-07-03T10:00:00Z",
        )
        assert r.layer == MemoryLayer.L1
        assert r.score is None
        assert r.data["episode_id"] == "ep_001"


# ===========================================================================
# policies.py
# ===========================================================================

class TestMemoryPolicy:
    def test_default_policies(self):
        from agent.memory.policies import MemoryPolicy
        from agent.memory.schemas import MemoryLayer

        p = MemoryPolicy()
        assert p.can_read(MemoryLayer.L1) is True
        assert p.can_write(MemoryLayer.L1) is True
        assert p.can_delete(MemoryLayer.L1) is False
        assert p.can_write(MemoryLayer.L4) is False  # Constitution: meta-loop only
        assert p.can_delete(MemoryLayer.L4) is False

    def test_l2_deletable(self):
        from agent.memory.policies import MemoryPolicy
        from agent.memory.schemas import MemoryLayer

        p = MemoryPolicy()
        assert p.can_delete(MemoryLayer.L2) is True  # Entities can be pruned

    def test_requires_audit_all_layers(self):
        from agent.memory.policies import MemoryPolicy
        from agent.memory.schemas import MemoryLayer

        p = MemoryPolicy()
        for layer in MemoryLayer:
            assert p.requires_audit(layer) is True


class TestSkillLifecyclePolicy:
    def test_seed_to_active(self):
        from agent.memory.policies import SkillLifecyclePolicy
        from agent.memory.schemas import SkillStatus

        p = SkillLifecyclePolicy()
        assert p.determine_status(SkillStatus.SEED, 0.95) == SkillStatus.ACTIVE
        assert p.determine_status(SkillStatus.SEED, 0.85) == SkillStatus.SEED  # Below threshold

    def test_active_to_degrading(self):
        from agent.memory.policies import SkillLifecyclePolicy
        from agent.memory.schemas import SkillStatus

        p = SkillLifecyclePolicy()
        assert p.determine_status(SkillStatus.ACTIVE, 0.3) == SkillStatus.DEGRADING
        assert p.determine_status(SkillStatus.ACTIVE, 0.6) == SkillStatus.ACTIVE

    def test_degrading_to_archived(self):
        from agent.memory.policies import SkillLifecyclePolicy
        from agent.memory.schemas import SkillStatus

        p = SkillLifecyclePolicy()
        assert p.determine_status(SkillStatus.DEGRADING, 0.1) == SkillStatus.ARCHIVED
        assert p.determine_status(SkillStatus.DEGRADING, 0.1, days_idle=35) == SkillStatus.ARCHIVED

    def test_degrading_recovery(self):
        from agent.memory.policies import SkillLifecyclePolicy
        from agent.memory.schemas import SkillStatus

        p = SkillLifecyclePolicy()
        assert p.determine_status(SkillStatus.DEGRADING, 0.8) == SkillStatus.ACTIVE

    def test_archived_stays(self):
        from agent.memory.policies import SkillLifecyclePolicy
        from agent.memory.schemas import SkillStatus

        p = SkillLifecyclePolicy()
        assert p.determine_status(SkillStatus.ARCHIVED, 0.99) == SkillStatus.ARCHIVED


class TestDualStoragePolicy:
    def test_route_general_to_l2(self):
        from agent.memory.policies import DualStoragePolicy
        from agent.memory.schemas import MemoryLayer

        p = DualStoragePolicy()
        assert p.route("general") == MemoryLayer.L2

    def test_route_tool_specific_to_l3(self):
        from agent.memory.policies import DualStoragePolicy
        from agent.memory.schemas import MemoryLayer

        p = DualStoragePolicy()
        assert p.route("tool_specific") == MemoryLayer.L3

    def test_route_invalid(self):
        from agent.memory.policies import DualStoragePolicy

        p = DualStoragePolicy()
        with pytest.raises(ValueError):
            p.route("unknown")


class TestSensitiveDataPolicy:
    def test_detects_openai_key(self):
        from agent.memory.policies import SensitiveDataPolicy

        p = SensitiveDataPolicy()
        assert p.check("my key is sk-abcdefghijklmnopqrstuvwxyz123456") is True

    def test_detects_github_token(self):
        from agent.memory.policies import SensitiveDataPolicy

        p = SensitiveDataPolicy()
        assert p.check("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789AB") is True

    def test_clean_text(self):
        from agent.memory.policies import SensitiveDataPolicy

        p = SensitiveDataPolicy()
        assert p.check("이것은 일반 텍스트입니다.") is False


# ===========================================================================
# ranking.py
# ===========================================================================

class TestRankingWeights:
    def test_default_weights_sum_to_one(self):
        from agent.memory.ranking import RankingWeights

        w = RankingWeights()
        assert abs(w.importance + w.recency + w.relevance - 1.0) < 0.01

    def test_invalid_weights_raise(self):
        from agent.memory.ranking import RankingWeights

        with pytest.raises(ValueError):
            RankingWeights(importance=0.5, recency=0.5, relevance=0.5)


class TestComputeRecency:
    def test_recent_timestamp(self):
        from agent.memory.ranking import compute_recency

        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        score = compute_recency("2026-07-09T10:00:00Z", now=now)
        assert score > 0.9  # 1 day old → high score

    def test_old_timestamp(self):
        from agent.memory.ranking import compute_recency

        now = datetime(2026, 7, 10, tzinfo=timezone.utc)
        score = compute_recency("2026-01-01T00:00:00Z", now=now)
        assert score < 0.1  # ~6 months old → very low

    def test_empty_timestamp(self):
        from agent.memory.ranking import compute_recency

        assert compute_recency("") == 0.0

    def test_invalid_timestamp(self):
        from agent.memory.ranking import compute_recency

        assert compute_recency("not-a-date") == 0.0


class TestComputeImportance:
    def test_protected_is_max(self):
        from agent.memory.ranking import compute_importance

        assert compute_importance(protected=True) == 1.0

    def test_high_pain_index(self):
        from agent.memory.ranking import compute_importance

        score = compute_importance(pain_index=0.9, has_reflection=True)
        assert score > 0.7  # High pain + reflection → important

    def test_low_pain_no_reflection(self):
        from agent.memory.ranking import compute_importance

        score = compute_importance(pain_index=0.1, has_reflection=False)
        assert score < 0.5  # Low pain, no reflection → less important

    def test_low_cib_score(self):
        from agent.memory.ranking import compute_importance

        score = compute_importance(cib_score=0.96, has_reflection=True)
        # CIB near threshold → some importance from near-violation
        assert score > 0.3


class TestRankRecord:
    def test_rank_single_record(self):
        from agent.memory.ranking import rank_record
        from agent.memory.schemas import MemoryLayer, MemoryRecord

        r = MemoryRecord(
            record_id="rec_001",
            layer=MemoryLayer.L1,
            data={"has_reflection": True, "evaluation": {"pain_index": 0.5}},
            created_at="2026-07-08T10:00:00Z",
        )
        score = rank_record(r, query_similarity=0.8)
        assert 0.0 <= score <= 1.0
        assert r.score == score

    def test_rank_records_sorted(self):
        from agent.memory.ranking import rank_records
        from agent.memory.schemas import MemoryLayer, MemoryRecord

        records = [
            MemoryRecord(
                record_id="old",
                layer=MemoryLayer.L1,
                data={"has_reflection": False},
                created_at="2025-01-01T00:00:00Z",
            ),
            MemoryRecord(
                record_id="new",
                layer=MemoryLayer.L1,
                data={"has_reflection": True, "evaluation": {"pain_index": 0.8}},
                created_at="2026-07-08T00:00:00Z",
            ),
        ]
        ranked = rank_records(records, query_similarity=0.7)
        assert ranked[0].record_id == "new"  # Newer + reflection + high pain → higher
        assert ranked[0].score > ranked[1].score

    def test_rank_with_individual_similarities(self):
        from agent.memory.ranking import rank_records_with_similarities
        from agent.memory.schemas import MemoryLayer, MemoryRecord

        records = [
            (MemoryRecord(
                record_id="low_sim",
                layer=MemoryLayer.L1,
                data={"has_reflection": True},
                created_at="2026-07-08T00:00:00Z",
            ), 0.2),
            (MemoryRecord(
                record_id="high_sim",
                layer=MemoryLayer.L1,
                data={"has_reflection": True},
                created_at="2026-07-08T00:00:00Z",
            ), 0.9),
        ]
        ranked = rank_records_with_similarities(records)
        assert ranked[0].record_id == "high_sim"
        assert ranked[0].score > ranked[1].score