"""Unit tests for Phase 1.3 — L3 Procedural Memory.

Code is stored as files in ``scripts/skills/`` (or a temp dir for tests);
SQLite stores metadata + ``code_path`` only.

Covers:
    - skill_store.py: CRUD (save / get / delete / list / count)
    - skill_store.py: Field-level updates (status, success_rate, reflection_hints, causal_conditions, protected)
    - skill_store.py: Code file I/O (write to file, read from file, update_code)
    - skill_evaluator.py: State machine transitions (Seed → Active → Degrading → Archived, recovery)
    - skill_evaluator.py: success_rate update logic
    - skill_loader.py: Load + cache + code file reading
    - skill_executor.py: Code execution (sandbox, timeout, output capture)
"""

from __future__ import annotations

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _make_skill(
    skill_id: str = "skill_001",
    name: str = "PDF Text Extractor",
    code: str = "result = 'hello'",
    status: str = "Seed",
    success_rate: float = 0.0,
    total_executions: int = 0,
    reflection_hints: list[str] | None = None,
    causal_conditions: list[str] | None = None,
    protected: bool = False,
):
    from agent.memory.schemas import Skill, SkillMetadata, SkillStatus

    return Skill(
        skill_id=skill_id,
        name=name,
        code=code,
        description="Extracts text from PDF files.",
        metadata=SkillMetadata(
            status=SkillStatus(status),
            success_rate=success_rate,
            total_executions=total_executions,
        ),
        reflection_hints=reflection_hints or [],
        causal_conditions=causal_conditions or [],
        protected=protected,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


# ===========================================================================
# skill_store.py — CRUD
# ===========================================================================

class TestSkillStoreCRUD:
    def test_upsert_and_get(self, tmp_path):
        """Upsert writes code to file + metadata to DB; get reads both back."""
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        skill = _make_skill(code="result = 'hello'")
        store.upsert(skill)

        # Code file should exist on disk
        code_file = tmp_path / "skills" / "skill_001.py"
        assert code_file.exists()
        assert "result = 'hello'" in code_file.read_text()

        # get() should return skill with code loaded from file
        retrieved = store.get("skill_001")
        assert retrieved is not None
        assert retrieved.skill_id == "skill_001"
        assert retrieved.code == "result = 'hello'"
        assert retrieved.code_path == str(code_file)
        assert retrieved.name == "PDF Text Extractor"

    def test_get_nonexistent(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        assert store.get("nonexistent") is None

    def test_upsert_overwrites_code_file(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        skill = _make_skill(code="result = 'v1'")
        store.upsert(skill)

        skill.code = "result = 'v2'"
        store.upsert(skill)

        retrieved = store.get("skill_001")
        assert retrieved is not None
        assert retrieved.code == "result = 'v2'"

    def test_delete(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        skill = _make_skill()
        store.upsert(skill)
        assert store.count() == 1

        code_file = tmp_path / "skills" / "skill_001.py"
        assert code_file.exists()

        assert store.delete("skill_001") is True
        assert store.get("skill_001") is None
        assert store.count() == 0
        # Code file should also be deleted
        assert not code_file.exists()

    def test_delete_keeps_code_file(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill())
        code_file = tmp_path / "skills" / "skill_001.py"

        assert store.delete("skill_001", delete_code_file=False) is True
        assert not store.get("skill_001")
        # Code file should still exist
        assert code_file.exists()

    def test_delete_nonexistent(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        assert store.delete("nonexistent") is False

    def test_count(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        assert store.count() == 0

        store.upsert(_make_skill("s1"))
        store.upsert(_make_skill("s2"))
        store.upsert(_make_skill("s3"))
        assert store.count() == 3

    def test_list_all(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", name="Skill 1"))
        store.upsert(_make_skill("s2", name="Skill 2"))

        skills = store.list_all()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"Skill 1", "Skill 2"}

    def test_list_by_status(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Seed"))
        store.upsert(_make_skill("s2", status="Active"))
        store.upsert(_make_skill("s3", status="Active"))

        active = store.list_by_status(SkillStatus.ACTIVE)
        assert len(active) == 2
        assert all(s.metadata.status == SkillStatus.ACTIVE for s in active)

        seeds = store.list_by_status(SkillStatus.SEED)
        assert len(seeds) == 1

    def test_persistence_across_connections(self, tmp_path):
        """Data should survive closing and reopening the store."""
        from agent.memory.procedural.skill_store import SkillStore

        db_path = str(tmp_path / "skills.sqlite3")
        skills_dir = str(tmp_path / "skills")
        store1 = SkillStore(db_path=db_path, skills_dir=skills_dir)
        store1.upsert(_make_skill("persist_001", code="result = 42"))
        store1.close()

        store2 = SkillStore(db_path=db_path, skills_dir=skills_dir)
        retrieved = store2.get("persist_001")
        assert retrieved is not None
        assert retrieved.skill_id == "persist_001"
        assert retrieved.code == "result = 42"

    def test_get_metadata_only(self, tmp_path):
        """get_metadata should return skill without loading code from file."""
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", code="result = 42"))

        meta = store.get_metadata("s1")
        assert meta is not None
        assert meta.skill_id == "s1"
        assert meta.code == ""  # code not loaded
        assert meta.code_path != ""  # path is populated


# ===========================================================================
# skill_store.py — Field-level updates
# ===========================================================================

class TestSkillStoreFieldUpdates:
    def test_update_status(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Seed"))

        assert store.update_status("s1", SkillStatus.ACTIVE) is True
        skill = store.get("s1")
        assert skill.metadata.status == SkillStatus.ACTIVE

    def test_update_status_nonexistent(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        assert store.update_status("nonexistent", SkillStatus.ACTIVE) is False

    def test_update_success_rate(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))

        assert store.update_success_rate("s1", 0.85) is True
        skill = store.get("s1")
        assert skill.metadata.success_rate == 0.85

    def test_update_success_rate_with_executions(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))

        assert store.update_success_rate("s1", 0.9, total_executions=10, last_executed_at="2026-01-01T00:00:00Z") is True
        skill = store.get("s1")
        assert skill.metadata.success_rate == 0.9
        assert skill.metadata.total_executions == 10
        assert skill.metadata.last_executed_at == "2026-01-01T00:00:00Z"

    def test_update_reflection_hints(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))

        hints = ["PyPDF2로는 이미지 PDF 텍스트 추출 안 됨", "OCR 스킬 연동 필요"]
        assert store.update_reflection_hints("s1", hints) is True
        skill = store.get("s1")
        assert skill.reflection_hints == hints

    def test_update_reflection_hints_nonexistent(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        assert store.update_reflection_hints("nonexistent", ["hint"]) is False

    def test_update_causal_conditions(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))

        conditions = ["PDF가 텍스트 기반이어야 함", "파일 크기 < 100MB"]
        assert store.update_causal_conditions("s1", conditions) is True
        skill = store.get("s1")
        assert skill.causal_conditions == conditions

    def test_set_protected(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", protected=False))

        assert store.set_protected("s1", True) is True
        skill = store.get("s1")
        assert skill.protected is True

    def test_set_protected_nonexistent(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        assert store.set_protected("nonexistent", True) is False

    def test_update_code(self, tmp_path):
        """update_code should rewrite the code file on disk."""
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", code="result = 1"))

        assert store.update_code("s1", "result = 2") is True
        retrieved = store.get("s1")
        assert retrieved.code == "result = 2"

    def test_update_code_nonexistent(self, tmp_path):
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        assert store.update_code("nonexistent", "result = 1") is False


# ===========================================================================
# skill_loader.py
# ===========================================================================

class TestSkillLoader:
    def test_load_from_store(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", code="result = 42"))

        loader = SkillLoader(store=store)
        skill = loader.load("s1")
        assert skill is not None
        assert skill.skill_id == "s1"
        assert skill.code == "result = 42"
        assert skill.code_path != ""

    def test_load_nonexistent(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        loader = SkillLoader(store=store)
        assert loader.load("nonexistent") is None

    def test_load_caches(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))
        loader = SkillLoader(store=store)

        loader.load("s1")
        assert loader.is_cached("s1")

        skill = loader.load("s1")
        assert skill is not None

    def test_load_code_compiles(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", code="result = 1 + 2"))
        loader = SkillLoader(store=store)

        compiled = loader.load_code("s1")
        assert compiled is not None

    def test_load_code_invalid_syntax(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", code="def broken("))
        loader = SkillLoader(store=store)

        assert loader.load_code("s1") is None

    def test_invalidate_cache(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))
        loader = SkillLoader(store=store)

        loader.load("s1")
        assert loader.is_cached("s1")

        loader.invalidate("s1")
        assert not loader.is_cached("s1")

    def test_clear_cache(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))
        store.upsert(_make_skill("s2"))
        loader = SkillLoader(store=store)

        loader.load("s1")
        loader.load("s2")
        loader.clear_cache()
        assert not loader.is_cached("s1")
        assert not loader.is_cached("s2")

    def test_list_active(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Seed"))
        store.upsert(_make_skill("s2", status="Active"))
        loader = SkillLoader(store=store)

        active = loader.list_active()
        assert len(active) == 1
        assert active[0].skill_id == "s2"

    def test_reload_code_after_file_edit(self, tmp_path):
        """reload_code should pick up changes made to the code file on disk."""
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", code="result = 1"))
        loader = SkillLoader(store=store)

        # Load and cache
        loader.load_code("s1")

        # Edit the code file directly on disk
        store.update_code("s1", "result = 999")

        # reload_code should pick up the new code
        code = loader.reload_code("s1")
        assert code == "result = 999"

    def test_load_metadata_only(self, tmp_path):
        from agent.memory.procedural.skill_loader import SkillLoader
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", code="result = 42"))
        loader = SkillLoader(store=store)

        meta = loader.load_metadata("s1")
        assert meta is not None
        assert meta.code == ""  # code not loaded from file


# ===========================================================================
# skill_executor.py
# ===========================================================================

class TestSkillExecutor:
    def test_execute_simple(self):
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=5)
        result = executor.execute_code("result = 1 + 2", skill_id="test_001")

        assert result.success is True
        assert result.result_value == 3
        assert result.timed_out is False

    def test_execute_with_inputs(self):
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=5)
        result = executor.execute_code(
            "result = a * b",
            skill_id="test_002",
            inputs={"a": 3, "b": 4},
        )
        assert result.success is True
        assert result.result_value == 12

    def test_execute_captures_stdout(self):
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=5)
        result = executor.execute_code(
            "print('hello world')\nresult = 'done'",
            skill_id="test_003",
        )
        assert result.success is True
        assert "hello world" in result.output

    def test_execute_captures_error(self):
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=5)
        result = executor.execute_code(
            "raise ValueError('test error')",
            skill_id="test_004",
        )
        assert result.success is False
        assert "test error" in result.traceback_str

    def test_execute_timeout(self):
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=0.5, allow_imports=True)
        result = executor.execute_code(
            "import time as _t\n_t.sleep(5)\nresult = 'done'",
            skill_id="test_005",
        )
        assert result.success is False
        assert result.timed_out is True

    def test_execute_sandbox_blocks_open(self):
        """Sandbox should not allow opening files."""
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=5, allow_imports=False)
        result = executor.execute_code(
            "open('/etc/passwd', 'r')",
            skill_id="test_006",
        )
        assert result.success is False
        assert "NameError" in result.traceback_str or "not defined" in result.traceback_str

    def test_execute_sandbox_blocks_exec(self):
        """Sandbox should not allow calling exec()."""
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=5)
        result = executor.execute_code(
            "exec('result = 1')",
            skill_id="test_007",
        )
        assert result.success is False

    def test_execute_skill_object(self, tmp_path):
        """execute() should work with a Skill loaded from the store."""
        from agent.memory.procedural.skill_executor import SkillExecutor
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("exec_001", code="result = [1, 2, 3]"))
        skill = store.get("exec_001")

        executor = SkillExecutor(default_timeout=5)
        result = executor.execute(skill)
        assert result.success is True
        assert result.result_value == [1, 2, 3]

    def test_execute_duration(self):
        from agent.memory.procedural.skill_executor import SkillExecutor

        executor = SkillExecutor(default_timeout=5)
        result = executor.execute_code("result = 1", skill_id="test_008")
        assert result.success is True
        assert result.duration_seconds >= 0.0


# ===========================================================================
# skill_evaluator.py — State machine
# ===========================================================================

class TestSkillEvaluatorStateMachine:
    def test_seed_to_active(self, tmp_path):
        """Seed → Active when success_rate > 0.9 (last 5)."""
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Seed"))
        evaluator = SkillEvaluator(store=store)

        # 5 successes → rate = 1.0 > 0.9
        for _ in range(5):
            status = evaluator.evaluate("s1", success=True)

        assert status == SkillStatus.ACTIVE
        skill = store.get("s1")
        assert skill.metadata.status == SkillStatus.ACTIVE

    def test_seed_stays_seed_with_low_rate(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Seed"))
        evaluator = SkillEvaluator(store=store)

        # 5 executions: 3 successes, 2 failures → rate = 0.6 < 0.9
        evaluator.evaluate("s1", success=True)
        evaluator.evaluate("s1", success=True)
        evaluator.evaluate("s1", success=True)
        evaluator.evaluate("s1", success=False)
        status = evaluator.evaluate("s1", success=False)

        assert status == SkillStatus.SEED

    def test_active_to_degrading(self, tmp_path):
        """Active → Degrading when success_rate < 0.5 (last 10)."""
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Active", success_rate=0.9))
        evaluator = SkillEvaluator(store=store)

        # 6 failures, 4 successes = 10 total → rate = 0.4 < 0.5
        for _ in range(6):
            evaluator.evaluate("s1", success=False)
        for _ in range(3):
            evaluator.evaluate("s1", success=True)
        status = evaluator.evaluate("s1", success=True)

        assert status == SkillStatus.DEGRADING

    def test_degrading_to_archived(self, tmp_path):
        """Degrading → Archived when success_rate < 0.2."""
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Degrading", success_rate=0.1))
        evaluator = SkillEvaluator(store=store)

        # 9 failures, 1 success → rate = 0.1 < 0.2
        for _ in range(9):
            evaluator.evaluate("s1", success=False)
        status = evaluator.evaluate("s1", success=True)

        assert status == SkillStatus.ARCHIVED

    def test_degrading_recovery_to_active(self, tmp_path):
        """Degrading → Active when success_rate > 0.7 (last 5)."""
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Degrading", success_rate=0.3))
        evaluator = SkillEvaluator(store=store)

        # 5 successes → rate = 1.0 > 0.7
        for _ in range(5):
            status = evaluator.evaluate("s1", success=True)

        assert status == SkillStatus.ACTIVE

    def test_archived_stays_archived(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Archived"))
        evaluator = SkillEvaluator(store=store)

        status = evaluator.evaluate("s1", success=True)
        assert status == SkillStatus.ARCHIVED


# ===========================================================================
# skill_evaluator.py — success_rate & history
# ===========================================================================

class TestSkillEvaluatorHistory:
    def test_record_execution(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))
        evaluator = SkillEvaluator(store=store)

        evaluator.record_execution("s1", True)
        evaluator.record_execution("s1", False)
        evaluator.record_execution("s1", True)

        rate = evaluator.get_recent_success_rate("s1", window=3)
        assert rate == pytest.approx(2 / 3)

    def test_recent_success_rate_empty(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        evaluator = SkillEvaluator(store=store)

        assert evaluator.get_recent_success_rate("s1", window=5) == 0.0

    def test_evaluate_updates_success_rate(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))
        evaluator = SkillEvaluator(store=store)

        evaluator.evaluate("s1", success=True)
        evaluator.evaluate("s1", success=True)
        evaluator.evaluate("s1", success=False)

        skill = store.get("s1")
        assert skill.metadata.success_rate == pytest.approx(2 / 3, abs=0.01)
        assert skill.metadata.total_executions == 3
        assert skill.metadata.last_executed_at is not None

    def test_evaluate_nonexistent_skill(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        evaluator = SkillEvaluator(store=store)

        status = evaluator.evaluate("nonexistent", success=True)
        assert status == SkillStatus.SEED

    def test_reset_history(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1"))
        evaluator = SkillEvaluator(store=store)

        evaluator.record_execution("s1", True)
        evaluator.record_execution("s1", False)
        assert evaluator.get_recent_success_rate("s1", 2) == 0.5

        evaluator.reset_history("s1")
        assert evaluator.get_recent_success_rate("s1", 2) == 0.0

    def test_evaluate_batch(self, tmp_path):
        from agent.memory.procedural.skill_evaluator import SkillEvaluator
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        store.upsert(_make_skill("s1", status="Seed"))
        store.upsert(_make_skill("s2", status="Seed"))
        evaluator = SkillEvaluator(store=store)

        results = [("s1", True)] * 5 + [("s2", False)] * 5
        statuses = evaluator.evaluate_batch(results)

        assert statuses["s1"] == SkillStatus.ACTIVE
        assert statuses["s2"] == SkillStatus.SEED