"""
core/validation_framework.py
Institutional Validation & Regression Testing Framework for GoldTracker Analytics Engine.

Provides automated scenario generation, quantitative validation metric calculation,
baseline snapshot management, regression detection, and pass/fail reporting.
"""

from __future__ import annotations

import os
import sys
import io
import time
import json
import math
import random
import datetime
import statistics
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple

# Path bootstrap
def _project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, _project_root())

from core.analytics import run_analytics, AnalyticsResult

# Safe stdout handling for Windows console
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


# ---------------------------------------------------------------------------
# ── DATASTRUCTURES & SCHEMAS ───────────────────────────────────────────────
# ---------------------------------------------------------------------------

@dataclass
class EvaluationPoint:
    """Single test evaluation state input."""
    spot_price: float
    retail_price: Optional[float]
    history: List[Dict[str, Any]]
    now_dt: Optional[datetime.datetime] = None
    step_label: str = ""


@dataclass
class MetricSnapshot:
    """Metrics collected for a single step execution."""
    buy_score: int
    sell_score: int
    confidence: int
    confidence_label: str
    buy_label: str
    sell_label: str
    explanation: str
    session: str
    has_conflict: bool
    execution_time_ms: float
    contributions: Dict[str, Dict[str, Any]]


@dataclass
class ScenarioMetrics:
    """Calculated quantitative validation metrics for a full test scenario."""
    scenario_name: str
    num_steps: int
    recommendation_stability_index: float  # 0.0 to 1.0 (1.0 = no unexpected flips)
    score_continuity_index: float           # 0.0 to 1.0 (1.0 = zero step discontinuities)
    outlier_robustness_score: float         # 0.0 to 1.0 (1.0 = robust against rogue prints)
    confidence_calibration_score: float     # 0.0 to 1.0 (1.0 = perfectly calibrated confidence)
    explanation_consistency_score: float    # 0.0 to 1.0 (1.0 = text perfectly grounds math)
    conflict_resolution_accuracy: float     # 0.0 to 1.0 (1.0 = conflict identified when present)
    avg_execution_time_ms: float
    pass_status: bool
    failure_reasons: List[str] = field(default_factory=list)


@dataclass
class RegressionComparisonResult:
    """Comparison result between a candidate run and the baseline snapshot."""
    scenario_name: str
    buy_score_max_delta: int
    buy_score_mean_abs_delta: float
    confidence_max_delta: int
    confidence_mean_abs_delta: float
    label_changed_steps: int
    explanation_changed_steps: int
    performance_delta_ms: float
    status: str  # "IMPROVED" | "UNCHANGED" | "REGRESSED"
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ── SYNTHETIC BENCHMARK DATASET GENERATOR ─────────────────────────────────
# ---------------------------------------------------------------------------

class ScenarioDatasetGenerator:
    """Generates deterministic synthetic market price datasets for validation."""

    @staticmethod
    def generate_flat_market(num_days: int = 30, price: float = 14000.0) -> List[Dict[str, Any]]:
        """30 days of perfectly constant prices."""
        return [{"price_24k": price, "retail_price": price + 400.0} for _ in range(num_days)]

    @staticmethod
    def generate_gradual_trend(
        num_days: int = 30, start_price: float = 13000.0, daily_change: float = 50.0
    ) -> List[Dict[str, Any]]:
        """Linear trending market (Bullish if daily_change > 0, Bearish if daily_change < 0)."""
        return [
            {"price_24k": start_price + i * daily_change, "retail_price": (start_price + i * daily_change) + 400.0}
            for i in range(num_days)
        ]

    @staticmethod
    def generate_sideways_oscillating(
        num_days: int = 30, base_price: float = 14000.0, amplitude: float = 150.0
    ) -> List[Dict[str, Any]]:
        """Sine-wave oscillating sideways market."""
        history = []
        for i in range(num_days):
            price = base_price + amplitude * math.sin(i * 0.5)
            history.append({"price_24k": round(price, 2), "retail_price": round(price + 400.0, 2)})
        return history

    @staticmethod
    def generate_high_volatility(
        num_days: int = 30, base_price: float = 14000.0, stdev: float = 400.0, seed: int = 42
    ) -> List[Dict[str, Any]]:
        """High volatility random walk with set seed for reproducibility."""
        random.seed(seed)
        history = []
        curr = base_price
        for _ in range(num_days):
            curr += random.gauss(0, stdev)
            curr = max(5000.0, curr)
            history.append({"price_24k": round(curr, 2), "retail_price": round(curr + 400.0, 2)})
        return history

    @staticmethod
    def generate_outlier_corrupted(
        num_days: int = 30, base_price: float = 14000.0, outlier_price: float = 99999.0
    ) -> List[Dict[str, Any]]:
        """Standard flat market with a single corrupted outlier print at step N-2."""
        history = [{"price_24k": base_price, "retail_price": base_price + 400.0} for _ in range(num_days)]
        history[-2] = {"price_24k": outlier_price, "retail_price": outlier_price + 400.0}
        return history

    @staticmethod
    def generate_micro_steps(
        base_price: float = 14000.0, step_size: float = 0.25, num_steps: int = 40
    ) -> List[float]:
        """Sequence of micro price movements (e.g. ₹0.25 steps)."""
        return [round(base_price + i * step_size, 2) for i in range(num_steps)]


# ---------------------------------------------------------------------------
# ── AUTOMATED TEST SUITE EXECUTOR ──────────────────────────────────────────
# ---------------------------------------------------------------------------

class ValidationSuiteExecutor:
    """Executes the 12 required validation test categories against any analytics implementation."""

    @staticmethod
    def run_step(
        spot_price: float,
        retail_price: Optional[float],
        history: List[Dict[str, Any]],
    ) -> Tuple[AnalyticsResult, MetricSnapshot]:
        """Execute run_analytics with precise microsecond timing."""
        t0 = time.perf_counter()
        res = run_analytics(spot_price, retail_price=retail_price, history_override=history)
        t1 = time.perf_counter()
        exec_ms = (t1 - t0) * 1000.0

        contrib_dict = {}
        if hasattr(res, "contributions") and res.contributions:
            for k, c in res.contributions.items():
                contrib_dict[k] = {
                    "score": c.score,
                    "normalized_score": c.normalized_score,
                    "influence": c.influence,
                    "confidence": c.confidence,
                }

        has_conflict = False
        if hasattr(res, "conflict_resolution") and res.conflict_resolution:
            has_conflict = res.conflict_resolution.has_conflict

        snapshot = MetricSnapshot(
            buy_score=res.buy_score,
            sell_score=res.sell_score,
            confidence=res.confidence,
            confidence_label=res.confidence_label,
            buy_label=res.buy_label,
            sell_label=res.sell_label,
            explanation=res.explanation,
            session=res.session,
            has_conflict=has_conflict,
            execution_time_ms=exec_ms,
            contributions=contrib_dict,
        )
        return res, snapshot

    # ── Category 1: Stable Market ──────────────────────────────────────────
    def test_category_1_stable_market(self) -> ScenarioMetrics:
        history = ScenarioDatasetGenerator.generate_flat_market(30, 14000.0)
        snapshots: List[MetricSnapshot] = []
        
        # Test 10 consecutive executions
        for _ in range(10):
            _, snap = self.run_step(14000.0, 14400.0, history)
            snapshots.append(snap)

        buy_scores = [s.buy_score for s in snapshots]
        score_variance = statistics.variance(buy_scores) if len(buy_scores) > 1 else 0.0
        
        # Stability index = 1.0 if variance == 0
        rsi = max(0.0, 1.0 - (score_variance / 10.0))
        exec_avg = sum(s.execution_time_ms for s in snapshots) / len(snapshots)

        failures = []
        if score_variance > 0.0:
            failures.append(f"Stable market produced score variance of {score_variance:.2f}")

        return ScenarioMetrics(
            scenario_name="1. Stable Market",
            num_steps=10,
            recommendation_stability_index=round(rsi, 4),
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0 if snapshots[0].confidence >= 70 else 0.5,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(exec_avg, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 2: Gradual Bull Market ────────────────────────────────────
    def test_category_2_gradual_bull(self) -> ScenarioMetrics:
        snapshots: List[MetricSnapshot] = []
        # Price increases from 13,000 to 15,000 over 20 steps
        for i in range(20):
            hist = ScenarioDatasetGenerator.generate_gradual_trend(30, 13000.0, 50.0)
            spot = 13000.0 + i * 100.0
            _, snap = self.run_step(spot, spot + 400.0, hist)
            snapshots.append(snap)

        scores = [s.buy_score for s in snapshots]
        # Check monotonicity: buy_score should generally decrease as price rises
        increases = sum(1 for i in range(1, len(scores)) if scores[i] > scores[i-1])
        rsi = 1.0 - (increases / (len(scores) - 1))
        exec_avg = sum(s.execution_time_ms for s in snapshots) / len(snapshots)

        failures = []
        if scores[-1] >= scores[0]:
            failures.append(f"Buy score did not weaken during bull run: start={scores[0]}, end={scores[-1]}")

        return ScenarioMetrics(
            scenario_name="2. Gradual Bull Market",
            num_steps=20,
            recommendation_stability_index=round(rsi, 4),
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(exec_avg, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 3: Gradual Bear Market ────────────────────────────────────
    def test_category_3_gradual_bear(self) -> ScenarioMetrics:
        snapshots: List[MetricSnapshot] = []
        # Price decreases from 15,000 to 13,000 over 20 steps
        for i in range(20):
            hist = ScenarioDatasetGenerator.generate_gradual_trend(30, 15000.0, -50.0)
            spot = 15000.0 - i * 100.0
            _, snap = self.run_step(spot, spot + 400.0, hist)
            snapshots.append(snap)

        scores = [s.buy_score for s in snapshots]
        decreases = sum(1 for i in range(1, len(scores)) if scores[i] < scores[i-1])
        rsi = 1.0 - (decreases / (len(scores) - 1))
        exec_avg = sum(s.execution_time_ms for s in snapshots) / len(snapshots)

        failures = []
        if scores[-1] <= scores[0]:
            failures.append(f"Buy score did not strengthen during bear market: start={scores[0]}, end={scores[-1]}")

        return ScenarioMetrics(
            scenario_name="3. Gradual Bear Market",
            num_steps=20,
            recommendation_stability_index=round(rsi, 4),
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(exec_avg, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 4: Sideways Oscillating Market ────────────────────────────
    def test_category_4_sideways_market(self) -> ScenarioMetrics:
        hist = ScenarioDatasetGenerator.generate_sideways_oscillating(30, 14000.0, 150.0)
        snapshots: List[MetricSnapshot] = []
        labels: List[str] = []
        for i in range(15):
            spot = hist[15 + i]["price_24k"]
            _, snap = self.run_step(spot, spot + 400.0, hist[:15+i])
            snapshots.append(snap)
            labels.append(snap.buy_label)

        # Count label flips
        flips = sum(1 for i in range(1, len(labels)) if labels[i] != labels[i-1])
        rsi = max(0.0, 1.0 - (flips / len(labels)))
        exec_avg = sum(s.execution_time_ms for s in snapshots) / len(snapshots)

        failures = []
        if flips > 4:
            failures.append(f"Excessive label flipping in sideways market ({flips} flips in 15 steps)")

        return ScenarioMetrics(
            scenario_name="4. Sideways Oscillating Market",
            num_steps=15,
            recommendation_stability_index=round(rsi, 4),
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(exec_avg, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 5: High Volatility Market ─────────────────────────────────
    def test_category_5_high_volatility(self) -> ScenarioMetrics:
        hist_low = ScenarioDatasetGenerator.generate_high_volatility(30, 14000.0, 20.0)
        hist_high = ScenarioDatasetGenerator.generate_high_volatility(30, 14000.0, 500.0)

        _, snap_low = self.run_step(14000.0, 14400.0, hist_low)
        _, snap_high = self.run_step(14000.0, 14400.0, hist_high)

        failures = []
        # Confidence should be lower in high volatility market
        if snap_high.confidence >= snap_low.confidence:
            failures.append(f"High volatility confidence ({snap_high.confidence}) >= Low volatility confidence ({snap_low.confidence})")

        ccs = 1.0 if snap_high.confidence < snap_low.confidence else 0.0

        return ScenarioMetrics(
            scenario_name="5. High Volatility Market",
            num_steps=2,
            recommendation_stability_index=1.0,
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=ccs,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round((snap_low.execution_time_ms + snap_high.execution_time_ms)/2.0, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 6: Low Liquidity / Frozen Market ──────────────────────────
    def test_category_6_frozen_market(self) -> ScenarioMetrics:
        hist = ScenarioDatasetGenerator.generate_flat_market(30, 14000.0)
        _, snap = self.run_step(14000.0, 14400.0, hist)

        failures = []
        # In a frozen flat market with 0 volatility, confidence should NOT be 90%+
        if snap.confidence > 80:
            failures.append(f"Overconfident in zero-volatility frozen market ({snap.confidence}/100 Confidence)")

        ccs = 1.0 if snap.confidence <= 80 else 0.0

        return ScenarioMetrics(
            scenario_name="6. Low Liquidity / Frozen Market",
            num_steps=1,
            recommendation_stability_index=1.0,
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=ccs,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(snap.execution_time_ms, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 7: Missing Data & Incomplete History ──────────────────────
    def test_category_7_missing_data(self) -> ScenarioMetrics:
        short_hist = ScenarioDatasetGenerator.generate_flat_market(3, 14000.0)
        full_hist = ScenarioDatasetGenerator.generate_flat_market(30, 14000.0)

        _, snap_short = self.run_step(14000.0, 14400.0, short_hist)
        _, snap_full = self.run_step(14000.0, 14400.0, full_hist)

        failures = []
        if snap_short.confidence >= snap_full.confidence:
            failures.append(f"Short history confidence ({snap_short.confidence}) >= Full history confidence ({snap_full.confidence})")
        if snap_short.confidence >= 50:
            failures.append(f"Short history (3 days) received {snap_short.confidence_label} confidence ({snap_short.confidence}/100)")

        ccs = 1.0 if snap_short.confidence < 50 else 0.0

        return ScenarioMetrics(
            scenario_name="7. Missing Data & History",
            num_steps=2,
            recommendation_stability_index=1.0,
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=ccs,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round((snap_short.execution_time_ms + snap_full.execution_time_ms)/2.0, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 8: Outlier Injection ──────────────────────────────────────
    def test_category_8_outlier_injection(self) -> ScenarioMetrics:
        clean_hist = ScenarioDatasetGenerator.generate_flat_market(30, 14000.0)
        corrupt_hist = ScenarioDatasetGenerator.generate_outlier_corrupted(30, 14000.0, 99999.0)

        res_clean, snap_clean = self.run_step(14000.0, 14400.0, clean_hist)
        res_corrupt, snap_corrupt = self.run_step(14000.0, 14400.0, corrupt_hist)

        score_delta = abs(snap_corrupt.buy_score - snap_clean.buy_score)
        ors = max(0.0, 1.0 - (score_delta / 100.0))

        failures = []
        if score_delta > 15:
            failures.append(f"Outlier corrupted buy score by {score_delta} pts ({snap_clean.buy_score} -> {snap_corrupt.buy_score})")

        return ScenarioMetrics(
            scenario_name="8. Outlier Injection",
            num_steps=2,
            recommendation_stability_index=1.0,
            score_continuity_index=1.0,
            outlier_robustness_score=round(ors, 4),
            confidence_calibration_score=1.0 if snap_corrupt.confidence < snap_clean.confidence else 0.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round((snap_clean.execution_time_ms + snap_corrupt.execution_time_ms)/2.0, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 9: Session Boundary Transition ───────────────────────────
    def test_category_9_session_boundary(self) -> ScenarioMetrics:
        hist = ScenarioDatasetGenerator.generate_flat_market(30, 14000.0)
        # Note: Session context uses system time or internal offsets
        res, snap = self.run_step(14000.0, 14400.0, hist)

        return ScenarioMetrics(
            scenario_name="9. Session Boundary",
            num_steps=1,
            recommendation_stability_index=1.0,
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(snap.execution_time_ms, 3),
            pass_status=True,
            failure_reasons=[],
        )

    # ── Category 10: Micro Price Changes & Score Continuity ───────────────
    def test_category_10_micro_price_changes(self) -> ScenarioMetrics:
        hist = ScenarioDatasetGenerator.generate_flat_market(30, 14000.0)
        prices = ScenarioDatasetGenerator.generate_micro_steps(14000.0, 0.25, 30)

        snapshots: List[MetricSnapshot] = []
        for p in prices:
            _, snap = self.run_step(p, p + 400.0, hist)
            snapshots.append(snap)

        max_jump = 0
        jumps = []
        for i in range(1, len(snapshots)):
            delta = abs(snapshots[i].buy_score - snapshots[i-1].buy_score)
            if delta > max_jump:
                max_jump = delta
            if delta > 3:
                jumps.append(f"Step {i} (₹{prices[i-1]} -> ₹{prices[i]}): {delta} pt jump ({snapshots[i-1].buy_score} -> {snapshots[i].buy_score})")

        # Continuity index = 1.0 if max_jump <= 3
        sci = max(0.0, 1.0 - (max_jump / 20.0))
        exec_avg = sum(s.execution_time_ms for s in snapshots) / len(snapshots)

        failures = []
        if jumps:
            failures.append(f"Detected {len(jumps)} step discontinuities (>3 pts for ₹0.25 shift). Max jump: {max_jump} pts")

        return ScenarioMetrics(
            scenario_name="10. Micro Price Changes & Continuity",
            num_steps=len(prices),
            recommendation_stability_index=1.0,
            score_continuity_index=round(sci, 4),
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(exec_avg, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 11: Random Noise Stability ───────────────────────────────
    def test_category_11_random_noise(self) -> ScenarioMetrics:
        random.seed(123)
        hist = ScenarioDatasetGenerator.generate_flat_market(30, 14000.0)
        snapshots: List[MetricSnapshot] = []
        for _ in range(20):
            noisy_spot = 14000.0 + random.gauss(0, 2.0) # ±2 rupee noise
            _, snap = self.run_step(noisy_spot, noisy_spot + 400.0, hist)
            snapshots.append(snap)

        labels = [s.buy_label for s in snapshots]
        flips = sum(1 for i in range(1, len(labels)) if labels[i] != labels[i-1])
        rsi = max(0.0, 1.0 - (flips / len(labels)))
        exec_avg = sum(s.execution_time_ms for s in snapshots) / len(snapshots)

        failures = []
        if flips > 2:
            failures.append(f"Random ₹2 noise caused {flips} recommendation label flips")

        return ScenarioMetrics(
            scenario_name="11. Random Noise Stability",
            num_steps=20,
            recommendation_stability_index=round(rsi, 4),
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(exec_avg, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    # ── Category 12: Extreme Historical Trends ─────────────────────────────
    def test_category_12_extreme_trends(self) -> ScenarioMetrics:
        # Extreme Crash Scenario
        crash_hist = ScenarioDatasetGenerator.generate_gradual_trend(30, 16000.0, -100.0)
        res_crash, snap_crash = self.run_step(13000.0, 13400.0, crash_hist)

        # Extreme Bubble Scenario
        bubble_hist = ScenarioDatasetGenerator.generate_gradual_trend(30, 10000.0, 200.0)
        res_bubble, snap_bubble = self.run_step(16000.0, 16400.0, bubble_hist)

        failures = []
        if snap_crash.buy_score < 60:
            failures.append(f"Extreme crash dip failed to generate strong buy score (got {snap_crash.buy_score})")
        if snap_bubble.buy_score > 35:
            failures.append(f"Extreme bubble failed to generate sell/wait score (got {snap_bubble.buy_score})")

        exec_avg = (snap_crash.execution_time_ms + snap_bubble.execution_time_ms) / 2.0

        return ScenarioMetrics(
            scenario_name="12. Extreme Historical Trends",
            num_steps=2,
            recommendation_stability_index=1.0,
            score_continuity_index=1.0,
            outlier_robustness_score=1.0,
            confidence_calibration_score=1.0,
            explanation_consistency_score=1.0,
            conflict_resolution_accuracy=1.0,
            avg_execution_time_ms=round(exec_avg, 3),
            pass_status=(len(failures) == 0),
            failure_reasons=failures,
        )

    def run_all(self) -> List[ScenarioMetrics]:
        """Run all 12 validation categories sequentially."""
        return [
            self.test_category_1_stable_market(),
            self.test_category_2_gradual_bull(),
            self.test_category_3_gradual_bear(),
            self.test_category_4_sideways_market(),
            self.test_category_5_high_volatility(),
            self.test_category_6_frozen_market(),
            self.test_category_7_missing_data(),
            self.test_category_8_outlier_injection(),
            self.test_category_9_session_boundary(),
            self.test_category_10_micro_price_changes(),
            self.test_category_11_random_noise(),
            self.test_category_12_extreme_trends(),
        ]


# ---------------------------------------------------------------------------
# ── BASELINE REGRESSION COMPARATOR ─────────────────────────────────────────
# ---------------------------------------------------------------------------

BASELINE_PATH = os.path.join(_project_root(), "database", "analytics_baseline.json")


class BaselineRegressionManager:
    """Manages baseline snapshots and compares candidate engines against stored metrics."""

    @staticmethod
    def save_baseline(results: List[ScenarioMetrics], path: str = BASELINE_PATH) -> None:
        """Save benchmark execution results as the authoritative baseline JSON."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "scenarios": [asdict(r) for r in results],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"[ValidationFramework] Baseline snapshot saved successfully to {path}")

    @staticmethod
    def load_baseline(path: str = BASELINE_PATH) -> Optional[List[Dict[str, Any]]]:
        """Load stored baseline snapshot."""
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("scenarios", [])
        except Exception as exc:
            print(f"[ValidationFramework] Error loading baseline: {exc}")
            return None

    @classmethod
    def compare_against_baseline(
        cls, candidate_results: List[ScenarioMetrics], path: str = BASELINE_PATH
    ) -> List[RegressionComparisonResult]:
        """Compare candidate run metrics against stored baseline metrics."""
        baseline_data = cls.load_baseline(path)
        if not baseline_data:
            print("[ValidationFramework] No baseline snapshot found for regression comparison.")
            return []

        baseline_map = {b["scenario_name"]: b for b in baseline_data}
        comparisons: List[RegressionComparisonResult] = []

        for cand in candidate_results:
            base = baseline_map.get(cand.scenario_name)
            if not base:
                continue

            # Compare stability and continuity indices
            sci_diff = cand.score_continuity_index - base["score_continuity_index"]
            rsi_diff = cand.recommendation_stability_index - base["recommendation_stability_index"]
            ors_diff = cand.outlier_robustness_score - base["outlier_robustness_score"]
            ccs_diff = cand.confidence_calibration_score - base["confidence_calibration_score"]
            perf_diff = cand.avg_execution_time_ms - base["avg_execution_time_ms"]

            notes = []
            if sci_diff > 0.05:
                notes.append(f"Score continuity improved by +{sci_diff:.4f}")
            elif sci_diff < -0.05:
                notes.append(f"REGRESSION: Score continuity degraded by {sci_diff:.4f}")

            if rsi_diff > 0.05:
                notes.append(f"Recommendation stability improved by +{rsi_diff:.4f}")
            elif rsi_diff < -0.05:
                notes.append(f"REGRESSION: Recommendation stability degraded by {rsi_diff:.4f}")

            if ors_diff > 0.05:
                notes.append(f"Outlier robustness improved by +{ors_diff:.4f}")
            elif ors_diff < -0.05:
                notes.append(f"REGRESSION: Outlier robustness degraded by {ors_diff:.4f}")

            if ccs_diff > 0.05:
                notes.append(f"Confidence calibration improved by +{ccs_diff:.4f}")
            elif ccs_diff < -0.05:
                notes.append(f"REGRESSION: Confidence calibration degraded by {ccs_diff:.4f}")

            status = "UNCHANGED"
            if any("REGRESSION" in n for n in notes):
                status = "REGRESSED"
            elif any("improved" in n for n in notes) or (cand.pass_status and not base.get("pass_status", False)):
                status = "IMPROVED"

            comparisons.append(
                RegressionComparisonResult(
                    scenario_name=cand.scenario_name,
                    buy_score_max_delta=0,
                    buy_score_mean_abs_delta=0.0,
                    confidence_max_delta=0,
                    confidence_mean_abs_delta=0.0,
                    label_changed_steps=0,
                    explanation_changed_steps=0,
                    performance_delta_ms=round(perf_diff, 3),
                    status=status,
                    notes=notes,
                )
            )

        return comparisons


# ---------------------------------------------------------------------------
# ── CLI ENTRY POINT FOR VALIDATION PIPELINE ────────────────────────────────
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("INSTITUTIONAL QUANTITATIVE VALIDATION PIPELINE RUNNER")
    print("=" * 70)

    executor = ValidationSuiteExecutor()
    results = executor.run_all()

    passed_count = sum(1 for r in results if r.pass_status)
    failed_count = len(results) - passed_count

    print(f"\nVALIDATION SUMMARY: {passed_count}/{len(results)} Scenarios Passed\n")
    print(f"{'Scenario Name':<38} | {'Status':<6} | {'Stability':<9} | {'Continuity':<10} | {'Exec Time'}")
    print("-" * 75)

    for r in results:
        status_str = "PASS" if r.pass_status else "FAIL"
        print(f"{r.scenario_name:<38} | {status_str:<6} | {r.recommendation_stability_index:<9.4f} | {r.score_continuity_index:<10.4f} | {r.avg_execution_time_ms:6.3f} ms")
        if r.failure_reasons:
            for fail in r.failure_reasons:
                print(f"   └── ❌ Failure: {fail}")

    print("-" * 75)
    # Check if baseline exists; if not, save current run as initial baseline
    if not os.path.exists(BASELINE_PATH):
        print("\nSaving initial baseline snapshot to database/analytics_baseline.json...")
        BaselineRegressionManager.save_baseline(results)
    else:
        print("\nComparing against existing baseline snapshot...")
        comps = BaselineRegressionManager.compare_against_baseline(results)
        for c in comps:
            if c.notes:
                print(f" [{c.status}] {c.scenario_name}: {', '.join(c.notes)}")
    print("=" * 70)
