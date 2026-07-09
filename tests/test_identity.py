"""Unit tests for Phase 1.6 — L5 Identity.

Covers:
    - identity_store.py: self_model & capabilities table CRUD
    - self_model.py: calibration computation, window stats, coherence index
    - capability_model.py: YAML init, EMA update, prediction, auto-register
    - updater.py: statistics update (outer loop) vs redesign (meta loop + HITL)
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _make_store(tmp_path):
    from agent.memory.identity.identity_store import IdentityStore

    return IdentityStore(db_path=str(tmp_path / "identity.sqlite3"))


# ===========================================================================
# identity_store.py — Self-model CRUD
# ===========================================================================

class TestIdentityStoreSelfModel:
    def test_table_created(self, tmp_path):
        """self_model table should be created on init."""
        store = _make_store(tmp_path)
        # Insert should work without error
        from agent.memory.schemas import SelfModelRecord, CalibrationDirection, UpdaterSource

        record = SelfModelRecord(
            record_id="sm_001",
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.8,
            actual_success=0.6,
            calibration_error=0.2,
            calibration_direction=CalibrationDirection.OVERCONFIDENT,
            timestamp="2026-01-01T00:00:00Z",
            updated_by=UpdaterSource.OUTER_LOOP,
        )
        store.insert_self_model(record)
        assert store.count_self_model() == 1

    def test_get_self_model(self, tmp_path):
        from agent.memory.schemas import SelfModelRecord, CalibrationDirection, UpdaterSource

        store = _make_store(tmp_path)
        record = SelfModelRecord(
            record_id="sm_001",
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.7,
            actual_success=0.7,
            calibration_error=0.0,
            calibration_direction=CalibrationDirection.CALIBRATED,
            timestamp="2026-01-01T00:00:00Z",
            updated_by=UpdaterSource.OUTER_LOOP,
        )
        store.insert_self_model(record)

        retrieved = store.get_self_model("sm_001")
        assert retrieved is not None
        assert retrieved.episode_id == "ep_001"
        assert retrieved.calibration_direction == CalibrationDirection.CALIBRATED

    def test_get_nonexistent(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.get_self_model("nonexistent") is None

    def test_list_self_model(self, tmp_path):
        from agent.memory.schemas import SelfModelRecord, CalibrationDirection, UpdaterSource

        store = _make_store(tmp_path)
        for i in range(5):
            record = SelfModelRecord(
                record_id=f"sm_{i:03d}",
                episode_id=f"ep_{i:03d}",
                task_category="coding",
                predicted_success=0.7,
                actual_success=0.6,
                calibration_error=0.1,
                calibration_direction=CalibrationDirection.OVERCONFIDENT,
                timestamp=f"2026-01-0{i+1}T00:00:00Z",
                updated_by=UpdaterSource.OUTER_LOOP,
            )
            store.insert_self_model(record)

        all_records = store.list_self_model()
        assert len(all_records) == 5

    def test_list_by_category(self, tmp_path):
        from agent.memory.schemas import SelfModelRecord, CalibrationDirection, UpdaterSource

        store = _make_store(tmp_path)
        for i in range(3):
            store.insert_self_model(SelfModelRecord(
                record_id=f"sm_c{i}",
                episode_id=f"ep_c{i}",
                task_category="coding",
                predicted_success=0.7,
                actual_success=0.6,
                calibration_error=0.1,
                calibration_direction=CalibrationDirection.OVERCONFIDENT,
                timestamp=f"2026-01-0{i+1}T00:00:00Z",
                updated_by=UpdaterSource.OUTER_LOOP,
            ))
        for i in range(2):
            store.insert_self_model(SelfModelRecord(
                record_id=f"sm_t{i}",
                episode_id=f"ep_t{i}",
                task_category="translation",
                predicted_success=0.5,
                actual_success=0.9,
                calibration_error=0.4,
                calibration_direction=CalibrationDirection.UNDERCONFIDENT,
                timestamp=f"2026-01-0{i+1}T00:00:00Z",
                updated_by=UpdaterSource.OUTER_LOOP,
            ))

        coding = store.list_self_model(category="coding")
        assert len(coding) == 3
        translation = store.list_self_model(category="translation")
        assert len(translation) == 2

    def test_get_recent(self, tmp_path):
        from agent.memory.schemas import SelfModelRecord, CalibrationDirection, UpdaterSource

        store = _make_store(tmp_path)
        for i in range(10):
            store.insert_self_model(SelfModelRecord(
                record_id=f"sm_{i:03d}",
                episode_id=f"ep_{i:03d}",
                task_category="coding",
                predicted_success=0.7,
                actual_success=0.6,
                calibration_error=0.1,
                calibration_direction=CalibrationDirection.OVERCONFIDENT,
                timestamp=f"2026-01-{i+1:02d}T00:00:00Z",
                updated_by=UpdaterSource.OUTER_LOOP,
            ))

        recent = store.get_recent_self_model(n=3)
        assert len(recent) == 3
        # Should be ordered oldest-first within the window
        assert recent[0].record_id == "sm_007"
        assert recent[2].record_id == "sm_009"

    def test_count(self, tmp_path):
        from agent.memory.schemas import SelfModelRecord, CalibrationDirection, UpdaterSource

        store = _make_store(tmp_path)
        assert store.count_self_model() == 0

        store.insert_self_model(SelfModelRecord(
            record_id="sm_001",
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.7,
            actual_success=0.6,
            calibration_error=0.1,
            calibration_direction=CalibrationDirection.OVERCONFIDENT,
            timestamp="2026-01-01T00:00:00Z",
            updated_by=UpdaterSource.OUTER_LOOP,
        ))
        assert store.count_self_model() == 1
        assert store.count_self_model(category="coding") == 1
        assert store.count_self_model(category="translation") == 0


# ===========================================================================
# identity_store.py — Capability CRUD
# ===========================================================================

class TestIdentityStoreCapabilities:
    def test_upsert_and_get(self, tmp_path):
        from agent.memory.identity.identity_store import IdentityStore
        from agent.memory.schemas import CapabilityRecord

        store = _make_store(tmp_path)
        cap = CapabilityRecord(id="coding", label="코딩", success_rate=0.6, confidence=0.7)
        store.upsert_capability(cap)

        retrieved = store.get_capability("coding")
        assert retrieved is not None
        assert retrieved.label == "코딩"
        assert retrieved.success_rate == 0.6

    def test_upsert_overwrites(self, tmp_path):
        from agent.memory.schemas import CapabilityRecord

        store = _make_store(tmp_path)
        store.upsert_capability(CapabilityRecord(id="coding", label="코딩", success_rate=0.5))
        store.upsert_capability(CapabilityRecord(id="coding", label="코딩", success_rate=0.8))

        retrieved = store.get_capability("coding")
        assert retrieved.success_rate == 0.8

    def test_list_capabilities(self, tmp_path):
        from agent.memory.schemas import CapabilityRecord

        store = _make_store(tmp_path)
        store.upsert_capability(CapabilityRecord(id="coding", label="코딩"))
        store.upsert_capability(CapabilityRecord(id="writing", label="문서 작성"))

        caps = store.list_capabilities()
        assert len(caps) == 2

    def test_delete_capability(self, tmp_path):
        from agent.memory.schemas import CapabilityRecord

        store = _make_store(tmp_path)
        store.upsert_capability(CapabilityRecord(id="coding", label="코딩"))
        assert store.delete_capability("coding") is True
        assert store.get_capability("coding") is None

    def test_delete_nonexistent(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.delete_capability("nonexistent") is False


# ===========================================================================
# self_model.py — Calibration & window stats
# ===========================================================================

class TestSelfModel:
    def test_record_computes_calibration(self, tmp_path):
        """Recording should auto-compute calibration_error and direction."""
        from agent.memory.identity.self_model import SelfModel

        store = _make_store(tmp_path)
        sm = SelfModel(store=store)

        record = sm.record(
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.8,
            actual_success=0.6,
        )
        assert record.calibration_error == pytest.approx(0.2, abs=0.01)
        assert record.calibration_direction.value == "overconfident"

    def test_record_calibrated(self, tmp_path):
        from agent.memory.identity.self_model import SelfModel
        from agent.memory.schemas import CalibrationDirection

        store = _make_store(tmp_path)
        sm = SelfModel(store=store, calibration_threshold=0.05)

        record = sm.record(
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.7,
            actual_success=0.72,
        )
        assert record.calibration_error < 0.05
        assert record.calibration_direction == CalibrationDirection.CALIBRATED

    def test_record_underconfident(self, tmp_path):
        from agent.memory.identity.self_model import SelfModel
        from agent.memory.schemas import CalibrationDirection

        store = _make_store(tmp_path)
        sm = SelfModel(store=store)

        record = sm.record(
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.3,
            actual_success=0.9,
        )
        assert record.calibration_direction == CalibrationDirection.UNDERCONFIDENT

    def test_window_stats_empty(self, tmp_path):
        from agent.memory.identity.self_model import SelfModel

        store = _make_store(tmp_path)
        sm = SelfModel(store=store)
        stats = sm.compute_window_stats()
        assert stats.avg_calibration_error is None
        assert stats.success_rate is None

    def test_window_stats_with_data(self, tmp_path):
        from agent.memory.identity.self_model import SelfModel

        store = _make_store(tmp_path)
        sm = SelfModel(store=store, window_size=50)

        # Record 5 episodes
        for i in range(5):
            sm.record(
                episode_id=f"ep_{i:03d}",
                task_category="coding",
                predicted_success=0.8,
                actual_success=0.6,
            )

        stats = sm.compute_window_stats(category="coding")
        assert stats.avg_calibration_error is not None
        assert stats.avg_calibration_error == pytest.approx(0.2, abs=0.01)
        assert stats.success_rate == pytest.approx(0.6, abs=0.01)
        assert stats.confidence_margin == pytest.approx(0.2, abs=0.01)
        assert stats.overconfident_ratio == 1.0  # all overconfident

    def test_coherence_index(self, tmp_path):
        from agent.memory.identity.self_model import SelfModel

        store = _make_store(tmp_path)
        sm = SelfModel(store=store)

        # Record some data
        for i in range(5):
            sm.record(
                episode_id=f"ep_{i:03d}",
                task_category="coding",
                predicted_success=0.7,
                actual_success=0.7,
            )

        summary = sm.get_calibration_summary(category="coding")
        assert summary["coherence_index"] is not None
        assert 0.0 <= summary["coherence_index"] <= 1.0

    def test_calibration_summary(self, tmp_path):
        from agent.memory.identity.self_model import SelfModel

        store = _make_store(tmp_path)
        sm = SelfModel(store=store)

        sm.record("ep_001", "coding", 0.8, 0.6)
        sm.record("ep_002", "coding", 0.3, 0.9)

        summary = sm.get_calibration_summary(category="coding")
        assert summary["total_records"] == 2
        assert "overconfident" in summary["direction_counts"]
        assert "underconfident" in summary["direction_counts"]

    def test_get_latest(self, tmp_path):
        from agent.memory.identity.self_model import SelfModel

        store = _make_store(tmp_path)
        sm = SelfModel(store=store)

        sm.record("ep_001", "coding", 0.8, 0.6)
        sm.record("ep_002", "coding", 0.7, 0.7)

        latest = sm.get_latest(category="coding")
        assert latest is not None
        assert latest.episode_id == "ep_002"


# ===========================================================================
# capability_model.py
# ===========================================================================

class TestCapabilityModel:
    def test_get_auto_registers_unknown(self, tmp_path):
        from agent.memory.identity.capability_model import CapabilityModel

        store = _make_store(tmp_path)
        cm = CapabilityModel(store=store)

        cap = cm.get("new_category")
        assert cap.id == "new_category"
        assert cap.success_rate == 0.5  # default
        # Should be persisted
        assert store.get_capability("new_category") is not None

    def test_update_from_episode_ema(self, tmp_path):
        from agent.memory.identity.capability_model import CapabilityModel

        store = _make_store(tmp_path)
        cm = CapabilityModel(store=store, smoothing_factor=0.3)

        # Initial: success_rate=0.5
        cm.get("coding")  # auto-register with defaults

        # Update with actual=0.9
        updated = cm.update_from_episode("coding", actual_success=0.9)
        # EMA: (1-0.3)*0.5 + 0.3*0.9 = 0.35 + 0.27 = 0.62
        assert updated.success_rate == pytest.approx(0.62, abs=0.01)
        assert updated.total_attempts == 1

        # Update again with actual=0.8
        updated = cm.update_from_episode("coding", actual_success=0.8)
        # EMA: (1-0.3)*0.62 + 0.3*0.8 = 0.434 + 0.24 = 0.674
        assert updated.success_rate == pytest.approx(0.674, abs=0.01)
        assert updated.total_attempts == 2

    def test_update_with_effort(self, tmp_path):
        from agent.memory.identity.capability_model import CapabilityModel

        store = _make_store(tmp_path)
        cm = CapabilityModel(store=store, smoothing_factor=0.3)

        cm.get("coding")
        updated = cm.update_from_episode("coding", actual_success=0.8, actual_effort=0.3)
        # EMA: (1-0.3)*0.5 + 0.3*0.3 = 0.35 + 0.09 = 0.44
        assert updated.effort_estimate == pytest.approx(0.44, abs=0.01)

    def test_predict(self, tmp_path):
        from agent.memory.identity.capability_model import CapabilityModel

        store = _make_store(tmp_path)
        cm = CapabilityModel(store=store)

        cm.get("coding")
        prediction = cm.predict("coding")
        assert "predicted_success" in prediction
        assert "confidence" in prediction
        assert "effort_estimate" in prediction
        assert prediction["predicted_success"] == 0.5

    def test_initialize_from_yaml(self, tmp_path):
        from agent.memory.identity.capability_model import CapabilityModel

        # Create a minimal YAML
        yaml_path = tmp_path / "capabilities.yml"
        yaml_path.write_text(
            "version: 1\n"
            "categories:\n"
            "  - id: coding\n"
            "    label: 코딩\n"
            "    success_rate: 0.6\n"
            "    confidence: 0.7\n"
            "    effort_estimate: 0.5\n"
            "    total_attempts: 0\n"
            "  - id: writing\n"
            "    label: 문서 작성\n"
            "    success_rate: 0.5\n"
            "    confidence: 0.7\n"
            "    effort_estimate: 0.5\n"
            "    total_attempts: 0\n",
            encoding="utf-8",
        )

        store = _make_store(tmp_path)
        cm = CapabilityModel(store=store, yaml_path=str(yaml_path))
        inserted = cm.initialize_from_yaml()
        assert inserted == 2

        caps = cm.list_all()
        assert len(caps) == 2
        assert caps[0].id in ("coding", "writing")

    def test_initialize_yaml_skips_existing(self, tmp_path):
        from agent.memory.identity.capability_model import CapabilityModel
        from agent.memory.schemas import CapabilityRecord

        yaml_path = tmp_path / "capabilities.yml"
        yaml_path.write_text(
            "version: 1\n"
            "categories:\n"
            "  - id: coding\n"
            "    label: 코딩\n"
            "    success_rate: 0.6\n"
            "    confidence: 0.7\n"
            "    effort_estimate: 0.5\n"
            "    total_attempts: 0\n",
            encoding="utf-8",
        )

        store = _make_store(tmp_path)
        # Pre-insert coding
        store.upsert_capability(CapabilityRecord(id="coding", label="기존 코딩", success_rate=0.9))

        cm = CapabilityModel(store=store, yaml_path=str(yaml_path))
        inserted = cm.initialize_from_yaml()
        assert inserted == 0  # already exists

        # Existing record should not be overwritten
        cap = store.get_capability("coding")
        assert cap.label == "기존 코딩"
        assert cap.success_rate == 0.9

    def test_update_batch(self, tmp_path):
        from agent.memory.identity.capability_model import CapabilityModel

        store = _make_store(tmp_path)
        cm = CapabilityModel(store=store, smoothing_factor=0.3)

        cm.get("coding")
        cm.get("writing")

        results = cm.update_batch([
            ("coding", 0.9, 0.3),
            ("writing", 0.6, 0.5),
        ])
        assert len(results) == 2
        assert results[0].total_attempts == 1
        assert results[1].total_attempts == 1


# ===========================================================================
# updater.py — Statistics update (outer loop)
# ===========================================================================

class TestUpdaterStatistics:
    def test_update_statistics(self, tmp_path):
        from agent.memory.identity.updater import IdentityUpdater

        store = _make_store(tmp_path)
        updater = IdentityUpdater(store=store)

        result = updater.update_statistics(
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.8,
            actual_success=0.6,
        )
        assert result.capability_updated is True
        assert result.calibration_error == pytest.approx(0.2, abs=0.01)
        assert result.calibration_direction == "overconfident"
        assert result.self_model_record_id is not None

    def test_update_statistics_creates_capability(self, tmp_path):
        from agent.memory.identity.updater import IdentityUpdater

        store = _make_store(tmp_path)
        updater = IdentityUpdater(store=store)

        updater.update_statistics(
            episode_id="ep_001",
            task_category="new_cat",
            predicted_success=0.5,
            actual_success=0.7,
        )

        cap = store.get_capability("new_cat")
        assert cap is not None
        assert cap.total_attempts == 1

    def test_update_statistics_with_effort(self, tmp_path):
        from agent.memory.identity.updater import IdentityUpdater

        store = _make_store(tmp_path)
        updater = IdentityUpdater(store=store)

        result = updater.update_statistics(
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.7,
            actual_success=0.8,
            predicted_effort=0.5,
            actual_effort=0.3,
        )
        assert result.calibration_direction == "underconfident"


# ===========================================================================
# updater.py — Redesign (meta loop + HITL)
# ===========================================================================

class TestUpdaterRedesign:
    def test_redesign_blocked_without_hitl(self, tmp_path):
        from agent.memory.identity.updater import IdentityUpdater

        store = _make_store(tmp_path)
        yaml_path = tmp_path / "identity.yml"
        yaml_path.write_text(
            "version: 1\nname: Gnosis\nautonomy_level:\n  current: L1\n",
            encoding="utf-8",
        )
        updater = IdentityUpdater(store=store, identity_yaml_path=str(yaml_path))

        result = updater.redesign_identity(
            new_config={"autonomy_level": {"current": "L2"}},
            hitl_approved=False,
        )
        assert result.approved is False
        assert "HITL BLOCKED" in result.reason

    def test_redesign_with_hitl(self, tmp_path):
        from agent.memory.identity.updater import IdentityUpdater

        store = _make_store(tmp_path)
        yaml_path = tmp_path / "identity.yml"
        yaml_path.write_text(
            "version: 1\n"
            "name: Gnosis\n"
            "autonomy_level:\n"
            "  current: L1\n"
            "  target: L3\n"
            "version_history:\n"
            "  - version: 1\n"
            "    date: '2026-07-03'\n"
            "    change: 초기 정체성 정의\n"
            "    approved_by: human\n",
            encoding="utf-8",
        )
        updater = IdentityUpdater(store=store, identity_yaml_path=str(yaml_path))

        result = updater.redesign_identity(
            new_config={"autonomy_level": {"current": "L2", "target": "L3"}},
            hitl_approved=True,
        )
        assert result.approved is True
        assert "autonomy_level" in result.changes
        assert result.changes["autonomy_level"]["old"] == "L1"
        assert result.changes["autonomy_level"]["new"] == "L2"

        # YAML should be updated
        import yaml as yaml_lib
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml_lib.safe_load(f)
        assert data["autonomy_level"]["current"] == "L2"
        assert data["version"] == 2  # version incremented

    def test_redesign_updates_identity_core(self, tmp_path):
        from agent.memory.identity.updater import IdentityUpdater

        store = _make_store(tmp_path)
        yaml_path = tmp_path / "identity.yml"
        yaml_path.write_text(
            "version: 1\nname: Gnosis\nversion_history: []\n",
            encoding="utf-8",
        )
        updater = IdentityUpdater(store=store, identity_yaml_path=str(yaml_path))

        new_core = {
            "values": ["새로운 가치"],
            "boundaries": ["새로운 경계"],
            "self_description": "새로운 자기 서술",
        }
        result = updater.redesign_identity(
            new_config={"identity_core": new_core},
            hitl_approved=True,
        )
        assert result.approved is True
        assert "identity_core" in result.changes

    def test_get_identity_state(self, tmp_path):
        from agent.memory.identity.updater import IdentityUpdater

        store = _make_store(tmp_path)
        yaml_path = tmp_path / "identity.yml"
        yaml_path.write_text(
            "version: 1\nname: Gnosis\nautonomy_level:\n  current: L1\n",
            encoding="utf-8",
        )
        updater = IdentityUpdater(store=store, identity_yaml_path=str(yaml_path))

        # Record some data
        updater.update_statistics("ep_001", "coding", 0.8, 0.6)

        state = updater.get_identity_state()
        assert state["name"] == "Gnosis"
        assert state["autonomy_level"]["current"] == "L1"
        assert "self_model" in state
        assert "capabilities" in state
        assert len(state["capabilities"]) >= 1