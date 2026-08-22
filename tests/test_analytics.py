import unittest
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.analytics import (
    compute_support_resistance,
    run_analytics,
    STALE_DATA_CONFIDENCE_CAP,
)

class TestAnalyticsCorrectness(unittest.TestCase):
    def test_sr_boundary_continuity(self):
        """
        Support/Resistance score must behave continuously around boundary levels.
        Small price movements (e.g. ₹0.50) must produce small, progressive changes.
        """
        # Generate synthetic history with clear support at 13000 and resistance at 14000
        history_prices = [13000.0] * 10 + [13500.0] * 10 + [14000.0] * 10
        support_level = 13000.0

        test_offsets = [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]
        results = []

        for offset in test_offsets:
            p = support_level + offset
            sr_res = compute_support_resistance(history_prices, p)
            results.append((p, sr_res.modifier))

        # Verify that modifiers change smoothly without sharp jumps
        for i in range(1, len(results)):
            p_prev, mod_prev = results[i-1]
            p_curr, mod_curr = results[i]
            diff = abs(mod_curr - mod_prev)
            # A price step of 0.5 to 1.0 INR must never cause a jump greater than 0.5 pts
            self.assertLess(diff, 0.5, 
                            f"Discontinuity detected between ₹{p_prev} ({mod_prev}) and ₹{p_curr} ({mod_curr}): jump of {diff:.3f}")

    def test_sr_resistance_boundary_continuity(self):
        """Verify continuity around the overhead resistance level."""
        history_prices = [13000.0] * 10 + [13500.0] * 10 + [14000.0] * 10
        resist_level = 14000.0

        test_offsets = [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]
        results = []

        for offset in test_offsets:
            p = resist_level + offset
            sr_res = compute_support_resistance(history_prices, p)
            results.append((p, sr_res.modifier))

        for i in range(1, len(results)):
            p_prev, mod_prev = results[i-1]
            p_curr, mod_curr = results[i]
            diff = abs(mod_curr - mod_prev)
            self.assertLess(diff, 0.5,
                            f"Discontinuity detected between ₹{p_prev} ({mod_prev}) and ₹{p_curr} ({mod_curr}): jump of {diff:.3f}")

    def test_frozen_market_confidence_capped(self):
        """
        A static/frozen feed (all identical prices, zero volatility) MUST have its confidence capped
        at STALE_DATA_CONFIDENCE_CAP (<= 45) rather than receiving falsely high certainty (e.g. 92%).
        """
        flat_price = 13737.0
        # 30 days of identical flat readings
        flat_history = [{'price_24k': flat_price, 'retail_price': flat_price + 200} for _ in range(30)]

        res = run_analytics(flat_price, retail_price=flat_price + 200, history_override=flat_history)

        self.assertLessEqual(res.confidence, STALE_DATA_CONFIDENCE_CAP)
        self.assertIn(res.confidence_label, ("Low", "Very Low"))
        
        # Verify quality notes identify the frozen feed
        has_frozen_note = any("static/frozen" in note for note in res.data_quality_notes)
        self.assertTrue(has_frozen_note, "Expected static/frozen note in data_quality_notes")

    def test_fresh_active_market_produces_healthy_confidence(self):
        """
        Fresh data with healthy, natural price action produces High or Medium confidence.
        """
        import numpy as np
        # 30 days of realistic gently fluctuating prices around 13700
        np.random.seed(42)
        prices = [13500 + i * 10 + float(np.random.normal(0, 15)) for i in range(30)]
        history = [{'price_24k': round(p, 2), 'retail_price': round(p + 350, 2)} for p in prices]

        res = run_analytics(round(prices[-1], 2), retail_price=round(prices[-1] + 350, 2), history_override=history)

        self.assertGreaterEqual(res.confidence, 60)
        self.assertIn(res.confidence_label, ("High", "Very High", "Medium"))

    def test_insufficient_history_caps_confidence(self):
        """When history is short (<14 observations), confidence is capped."""
        short_history = [{'price_24k': 13500.0 + i * 10, 'retail_price': 13800.0} for i in range(5)]
        res = run_analytics(13550.0, history_override=short_history)

        self.assertLessEqual(res.confidence, 45)

if __name__ == '__main__':
    unittest.main()
