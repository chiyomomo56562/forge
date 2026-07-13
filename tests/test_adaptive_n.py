"""Unit tests for Phase 3.4 — Adaptive N (어댑티브 N).

Covers:
    - adaptive_n.py: AdaptiveNCalculator (volatility-based N computation)
    - adaptive_n.py: Change logging to JSONL
    - adaptive_n.py: Risk level → base N mapping
    - adaptive_n.py: History tracking and analysis
    - outer_loop.py: Integration with AdaptiveNCalculator
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ===========================================================================
# AdaptiveNCalculator — Basic computation
# ===========================================================================

class TestAdaptiveNCalculatorBasic:
    def test_disabled_returns_base_n(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            enabled=False,
        )
        result = calc.compute(base_N=50, current_N=50)
        assert result.new_N == 50
        assert result.direction == "unchanged"

    def test_no_scores_returns_base_n(self, tmp_path):
        """With no score data, volatility = 0 → low volatility → expand."""
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
        )
        result = calc.compute(base_N=50, current_N=50)
        # No scores → volatility = 0 → low → expand
        assert result.new_N == 100  # 50 * 2
        assert result.direction == "expanded"

    def test_high_volatility_shrinks_n(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            high_volatility_threshold=0.15,
        )
        # Highly volatile CIB scores
        cib_scores = [0.5, 0.9, 0.4, 0.95, 0.3, 0.85, 0.45, 0.92, 0.35, 0.88,
                       0.42, 0.97, 0.38, 0.9, 0.5, 0.93, 0.33, 0.87, 0.48, 0.95]
        result = calc.compute(
            base_N=50,
            current_N=50,
            cib_scores=cib_scores,
            phoenix_scores=[0.8] * 20,
        )
        assert result.direction == "shrunk"
        assert result.new_N == 25  # max(50 * 0.5, 10) = 25
        assert result.combined_volatility > 0.15

    def test_low_volatility_expands_n(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            low_volatility_threshold=0.03,
        )
        # Very stable scores
        cib_scores = [0.97, 0.971, 0.969, 0.97, 0.972, 0.968, 0.97, 0.971, 0.969, 0.97,
                       0.971, 0.969, 0.97, 0.972, 0.968, 0.97, 0.971, 0.969, 0.97, 0.971]
        phoenix_scores = [0.85, 0.851, 0.849, 0.85, 0.852, 0.848, 0.85, 0.851, 0.849, 0.85,
                          0.851, 0.849, 0.85, 0.852, 0.848, 0.85, 0.851, 0.849, 0.85, 0.851]
        result = calc.compute(
            base_N=50,
            current_N=50,
            cib_scores=cib_scores,
            phoenix_scores=phoenix_scores,
        )
        assert result.direction == "expanded"
        assert result.new_N == 100  # min(50 * 2, 200) = 100
        assert result.combined_volatility < 0.03

    def test_normal_volatility_keeps_base_n(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            high_volatility_threshold=0.15,
            low_volatility_threshold=0.03,
        )
        # Moderately volatile scores (combined volatility between 0.03 and 0.15)
        # cib_vol ~ 0.08, phoenix_vol = 0 → combined = 0.6 * 0.08 = 0.048
        cib_scores = [0.7, 0.85, 0.65, 0.8, 0.75, 0.9, 0.7, 0.85, 0.65, 0.8,
                       0.75, 0.9, 0.7, 0.85, 0.65, 0.8, 0.75, 0.9, 0.7, 0.85]
        result = calc.compute(
            base_N=50,
            current_N=50,
            cib_scores=cib_scores,
            phoenix_scores=[0.8] * 20,
        )
        assert result.direction == "unchanged"
        assert result.new_N == 50


# ===========================================================================
# AdaptiveNCalculator — Safety constraints
# ===========================================================================

class TestAdaptiveNCalculatorSafety:
    def test_absolute_min_enforced(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            absolute_min=10,
            min_multiplier=0.5,
        )
        # base_N=15, high volatility → max(15*0.5, 10) = 10
        cib_scores = [0.3, 0.9, 0.2, 0.95, 0.1, 0.85, 0.25, 0.92, 0.15, 0.88,
                       0.22, 0.97, 0.18, 0.9, 0.3, 0.93, 0.13, 0.87, 0.28, 0.95]
        result = calc.compute(
            base_N=15,
            current_N=15,
            cib_scores=cib_scores,
        )
        assert result.new_N >= 10

    def test_absolute_max_enforced(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            absolute_max=200,
            max_multiplier=2.0,
        )
        # base_N=150, low volatility → min(150*2, 200) = 200
        result = calc.compute(
            base_N=150,
            current_N=150,
            cib_scores=[0.97] * 20,
            phoenix_scores=[0.85] * 20,
        )
        assert result.new_N <= 200

    def test_n_never_below_absolute_min(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            absolute_min=10,
            min_multiplier=0.5,
        )
        # Even with base_N=10, shrunk N should be at least 10
        cib_scores = [0.1, 0.9, 0.2, 0.95, 0.1, 0.85, 0.25, 0.92, 0.15, 0.88,
                       0.22, 0.97, 0.18, 0.9, 0.3, 0.93, 0.13, 0.87, 0.28, 0.95]
        result = calc.compute(
            base_N=10,
            current_N=10,
            cib_scores=cib_scores,
        )
        assert result.new_N >= 10


# ===========================================================================
# AdaptiveNCalculator — Risk level mapping
# ===========================================================================

class TestAdaptiveNCalculatorRiskLevel:
    def test_get_base_n_low(self):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        assert AdaptiveNCalculator.get_base_n("low") == 100

    def test_get_base_n_medium(self):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        assert AdaptiveNCalculator.get_base_n("medium") == 50

    def test_get_base_n_high(self):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        assert AdaptiveNCalculator.get_base_n("high") == 20

    def test_get_base_n_critical(self):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        assert AdaptiveNCalculator.get_base_n("critical") == 10

    def test_get_base_n_unknown_defaults_medium(self):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        assert AdaptiveNCalculator.get_base_n("unknown") == 50


# ===========================================================================
# AdaptiveNCalculator — Logging and history
# ===========================================================================

class TestAdaptiveNCalculatorLogging:
    def test_change_logged_to_jsonl(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        log_path = tmp_path / "adaptive_N.jsonl"
        calc = AdaptiveNCalculator(log_path=str(log_path))

        calc.compute(
            base_N=50,
            current_N=50,
            cib_scores=[0.97] * 20,
            phoenix_scores=[0.85] * 20,
            outer_loop_count=1,
        )

        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert "new_N" in entry
        assert "old_N" in entry
        assert "combined_volatility" in entry
        assert "direction" in entry
        assert "reason" in entry
        assert entry["outer_loop_count"] == 1

    def test_history_loaded_on_restart(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        log_path = str(tmp_path / "adaptive_N.jsonl")

        calc1 = AdaptiveNCalculator(log_path=log_path)
        calc1.compute(base_N=50, current_N=50, outer_loop_count=1)
        calc1.compute(base_N=50, current_N=100, outer_loop_count=2)
        assert len(calc1.history) == 2

        calc2 = AdaptiveNCalculator(log_path=log_path)
        assert len(calc2.history) == 2

    def test_n_value_history(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(log_path=str(tmp_path / "adaptive_N.jsonl"))
        calc.compute(base_N=50, current_N=50, outer_loop_count=1)
        calc.compute(base_N=50, current_N=100, outer_loop_count=2)
        calc.compute(base_N=50, current_N=50, outer_loop_count=3)

        n_values = calc.get_n_value_history(n=3)
        assert len(n_values) == 3

    def test_volatility_trend(self, tmp_path):
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(log_path=str(tmp_path / "adaptive_N.jsonl"))
        calc.compute(base_N=50, current_N=50, cib_scores=[0.97] * 20, outer_loop_count=1)
        calc.compute(base_N=50, current_N=100, cib_scores=[0.5, 0.9] * 10, outer_loop_count=2)

        volatilities = calc.get_volatility_trend(n=2)
        assert len(volatilities) == 2


# ===========================================================================
# AdaptiveNCalculator — Volatility computation
# ===========================================================================

class TestAdaptiveNCalculatorVolatility:
    def test_volatility_with_few_scores(self, tmp_path):
        """With fewer than 2 scores, volatility should be 0."""
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(log_path=str(tmp_path / "adaptive_N.jsonl"))
        result = calc.compute(
            base_N=50,
            current_N=50,
            cib_scores=[0.9],
            phoenix_scores=[0.8],
        )
        assert result.cib_volatility == 0.0
        assert result.phoenix_volatility == 0.0

    def test_volatility_window_limit(self, tmp_path):
        """Verify that only the last 20 scores are used for volatility."""
        from agent.outer_loop.adaptive_n import AdaptiveNCalculator

        calc = AdaptiveNCalculator(
            log_path=str(tmp_path / "adaptive_N.jsonl"),
            volatility_window=20,
        )
        # 30 stable scores + 10 volatile scores
        # The last 20 should be volatile → high volatility
        cib_scores = [0.97] * 30 + [0.3, 0.9, 0.2, 0.95, 0.1, 0.85, 0.25, 0.92, 0.15, 0.88]
        result = calc.compute(
            base_N=50,
            current_N=50,
            cib_scores=cib_scores,
        )
        assert result.cib_volatility > 0.1  # should be high


# ===========================================================================
# Outer Loop integration
# ===========================================================================

class TestOuterLoopAdaptiveNIntegration:
    def test_adaptive_n_calculator_initialized(self, tmp_path):
        from agent.outer_loop import OuterLoop

        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        assert hasattr(outer, "adaptive_n_calculator")

    def test_adaptive_n_log_created(self, tmp_path):
        from agent.outer_loop import OuterLoop

        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        outer.run()

        # The adaptive N log should be created
        n_log_path = tmp_path / "adaptive_N_log.jsonl"
        assert n_log_path.exists()

        entry = json.loads(n_log_path.read_text().strip().split("\n")[0])
        assert "new_N" in entry
        assert "old_N" in entry
        assert "combined_volatility" in entry

    def test_adaptive_n_history_persisted(self, tmp_path):
        from agent.outer_loop import OuterLoop

        state_path = str(tmp_path / "state.json")
        audit_path = str(tmp_path / "audit.jsonl")

        outer1 = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=audit_path,
        )
        outer1.run()
        outer1.run()
        assert len(outer1.adaptive_n_calculator.history) == 2

        # New instance should load history
        outer2 = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=audit_path,
        )
        assert len(outer2.adaptive_n_calculator.history) == 2

    def test_risk_level_affects_base_n(self, tmp_path):
        from agent.outer_loop import OuterLoop

        outer_low = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state_low.json"),
            audit_log_path=str(tmp_path / "audit_low.jsonl"),
            risk_level="low",
        )
        assert outer_low.state.base_N == 100
        assert outer_low.state.current_N == 100

        outer_critical = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state_critical.json"),
            audit_log_path=str(tmp_path / "audit_critical.jsonl"),
            risk_level="critical",
        )
        assert outer_critical.state.base_N == 10
        assert outer_critical.state.current_N == 10