import unittest
import os
import tempfile
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database import db_manager
from database.db_manager import get_karat_adjusted_price

class TestPortfolioValuation(unittest.TestCase):
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

    def test_get_karat_adjusted_price_factors(self):
        """Karat adjusted price helper produces exact mathematical proportions."""
        price_24k = 12000.0

        self.assertAlmostEqual(get_karat_adjusted_price(price_24k, '24K'), 12000.0)
        self.assertAlmostEqual(get_karat_adjusted_price(price_24k, '24'), 12000.0)
        self.assertAlmostEqual(get_karat_adjusted_price(price_24k, '22K'), 12000.0 * (22 / 24))
        self.assertAlmostEqual(get_karat_adjusted_price(price_24k, '22'), 12000.0 * (22 / 24))
        self.assertAlmostEqual(get_karat_adjusted_price(price_24k, '18K'), 12000.0 * (18 / 24))
        self.assertAlmostEqual(get_karat_adjusted_price(price_24k, '18'), 12000.0 * (18 / 24))

        # None/Zero safety
        self.assertEqual(get_karat_adjusted_price(0, '24K'), 0.0)
        self.assertEqual(get_karat_adjusted_price(None, '24K'), 0.0)

        # Unsupported karat raises ValueError
        with self.assertRaises(ValueError):
            get_karat_adjusted_price(price_24k, '14K')

    def test_portfolio_summary_single_24k(self):
        """Portfolio with 24K gold only."""
        current_price_24k = 13737.0
        db_manager.add_purchase('2026-01-15', '24K', 10.0, 13000.0, '24K bar')

        summary = db_manager.get_portfolio_summary(current_price_24k=current_price_24k)
        self.assertAlmostEqual(summary['total_grams'], 10.0)
        self.assertAlmostEqual(summary['total_invested'], 130000.0)
        self.assertAlmostEqual(summary['current_value'], round(10.0 * 13737.0, 2))
        self.assertAlmostEqual(summary['pnl'], round(137370.0 - 130000.0, 2))

    def test_portfolio_summary_single_22k(self):
        """Portfolio with 22K gold only is correctly valued at 22/24 of 24K price."""
        current_price_24k = 13737.0
        db_manager.add_purchase('2026-01-15', '22K', 5.0, 12000.0, '22K ornament')

        summary = db_manager.get_portfolio_summary(current_price_24k=current_price_24k)
        expected_current_val = round(5.0 * 13737.0 * (22 / 24), 2)
        self.assertAlmostEqual(summary['total_grams'], 5.0)
        self.assertAlmostEqual(summary['total_invested'], 60000.0)
        self.assertAlmostEqual(summary['current_value'], expected_current_val)

    def test_portfolio_summary_mixed_holdings_sum_invariant(self):
        """
        Core Invariant: Summary current value MUST equal the sum of row current values
        for mixed portfolios containing 24K, 22K, and 18K gold.
        """
        current_price_24k = 13737.0
        db_manager.add_purchase('2026-01-10', '24K', 10.0, 13500.0, 'Coin')
        db_manager.add_purchase('2026-01-15', '22K', 5.0, 12500.0, 'Bangles')
        db_manager.add_purchase('2026-01-20', '18K', 8.0, 10000.0, 'Ring')

        purchases = db_manager.get_portfolio()
        self.assertEqual(len(purchases), 3)

        # Compute individual row valuations
        row_valuations = []
        for p in purchases:
            adj_price = get_karat_adjusted_price(current_price_24k, p['karat'])
            row_val = round(p['grams'] * adj_price, 2)
            row_valuations.append(row_val)

        sum_of_rows = sum(row_valuations)

        summary = db_manager.get_portfolio_summary(current_price_24k=current_price_24k)

        # Verify summary equals sum of rows within rounding tolerance (1 cent)
        self.assertAlmostEqual(summary['current_value'], sum_of_rows, delta=0.05)
        
        # Verify specific values:
        # 24K: 10 * 13737 = 137370.0
        # 22K: 5 * 13737 * (22/24) = 62961.25
        # 18K: 8 * 13737 * (18/24) = 82422.0
        expected_total = 137370.0 + 62961.25 + 82422.0
        self.assertAlmostEqual(summary['current_value'], round(expected_total, 2), delta=0.05)

    def test_empty_portfolio_is_safe(self):
        """Empty portfolio returns zeroed safe dictionary without errors."""
        summary = db_manager.get_portfolio_summary(current_price_24k=13737.0)
        self.assertEqual(summary['total_grams'], 0.0)
        self.assertEqual(summary['total_invested'], 0.0)
        self.assertEqual(summary['current_value'], 0.0)
        self.assertEqual(summary['pnl'], 0.0)
        self.assertEqual(summary['pnl_pct'], 0.0)
        self.assertIsNone(summary['avg_buy_price'])

    def test_missing_current_price_is_safe(self):
        """Missing or None current price is safe and leaves current_value at 0."""
        db_manager.add_purchase('2026-01-10', '24K', 10.0, 13500.0)
        summary = db_manager.get_portfolio_summary(current_price_24k=None)
        self.assertEqual(summary['total_grams'], 10.0)
        self.assertEqual(summary['total_invested'], 135000.0)
        self.assertEqual(summary['current_value'], 0.0)

if __name__ == '__main__':
    unittest.main()
