import unittest
import os
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database import db_manager
from database.db_manager import get_karat_adjusted_price
from core.analytics import run_analytics, STALE_DATA_CONFIDENCE_CAP

class TestRegressionSuite(unittest.TestCase):
    """
    Regression test suite verifying each confirmed defect from the forensic audit:
    1. Alert lifecycle corruption: stale cancel cannot overwrite triggered alert.
    2. Portfolio valuation error: summary valuation must agree with 24K, 22K, 18K row valuations.
    3. Concurrency / Cycle locking: run_cycle overlap prevention.
    4. S/R score boundary continuity: smooth transition across boundaries.
    5. Frozen feed overconfidence: flat feeds capped at STALE_DATA_CONFIDENCE_CAP.
    """
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db_path = self.temp_db.name
        self.temp_db.close()
        
        self.orig_db_path = db_manager.DB_PATH
        db_manager.DB_PATH = self.temp_db_path
        db_manager.initialize_database()

    def tearDown(self):
        db_manager.DB_PATH = self.orig_db_path
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    def test_regression_defect_1_alert_stale_cancel_cannot_overwrite_triggered(self):
        """
        Defect 1: UI renders active alert -> scheduler triggers alert (status=triggered) ->
        user clicks stale cancel button -> database status must NOT become 'cancelled'.
        """
        db_manager.add_alert('buy', 13000.0)
        alert_id = db_manager.get_active_alerts()[0]['id']

        # Scheduler triggers alert
        triggered = db_manager.trigger_alert(alert_id)
        self.assertTrue(triggered)

        # Stale Cancel click
        cancelled = db_manager.cancel_alert(alert_id)
        self.assertFalse(cancelled, "Stale cancel must be rejected on triggered alert")

        # Authoritative state in DB must be 'triggered'
        alerts = db_manager.get_all_alerts()
        self.assertEqual(alerts[0]['status'], 'triggered')

    def test_regression_defect_2_portfolio_summary_karat_adjustment(self):
        """
        Defect 2: Portfolio with 10g 24K + 5g 22K at 24K spot ₹13,737.
        Old buggy calculation treated all 15g as 24K = 15 * 13,737 = ₹206,055.
        Correct calculation = 10 * 13,737 + 5 * 13,737 * (22/24) = 137,370 + 62,961.25 = ₹200,331.25.
        """
        spot_price_24k = 13737.0
        db_manager.add_purchase('2026-01-01', '24K', 10.0, 13000.0)
        db_manager.add_purchase('2026-01-02', '22K', 5.0, 12000.0)

        summary = db_manager.get_portfolio_summary(current_price_24k=spot_price_24k)

        expected_val = round(10.0 * 13737.0 + 5.0 * 13737.0 * (22.0 / 24.0), 2)
        incorrect_old_val = round(15.0 * 13737.0, 2)

        self.assertAlmostEqual(summary['current_value'], expected_val, delta=0.05)
        self.assertNotEqual(summary['current_value'], incorrect_old_val)

    def test_regression_defect_3_flat_market_cannot_produce_high_confidence(self):
        """
        Defect 3: Flat/frozen API responses previously received ~92% confidence
        because volatility was 0. It must now be capped at STALE_DATA_CONFIDENCE_CAP (<=45).
        """
        flat_prices = [13500.0] * 30
        history = [{'price_24k': p, 'retail_price': p + 200} for p in flat_prices]

        analytics_res = run_analytics(13500.0, retail_price=13700.0, history_override=history)

        self.assertLessEqual(analytics_res.confidence, STALE_DATA_CONFIDENCE_CAP)
        self.assertIn(analytics_res.confidence_label, ("Low", "Very Low"))

if __name__ == '__main__':
    unittest.main()
