import unittest
import sqlite3
import os
import tempfile
import threading
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from database import db_manager

class TestAlertLifecycle(unittest.TestCase):
    def setUp(self):
        # Create a clean temporary database for testing
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.temp_db_path = self.temp_db.name
        self.temp_db.close()
        
        # Patch db_manager.DB_PATH to use the temp db
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

    def test_active_to_cancelled_succeeds(self):
        """Active alert can be successfully cancelled."""
        db_manager.add_alert('buy', 13000.0)
        alerts = db_manager.get_active_alerts()
        self.assertEqual(len(alerts), 1)
        alert_id = alerts[0]['id']

        success = db_manager.cancel_alert(alert_id)
        self.assertTrue(success)

        # Check DB status
        all_alerts = db_manager.get_all_alerts()
        self.assertEqual(all_alerts[0]['status'], 'cancelled')
        self.assertEqual(len(db_manager.get_active_alerts()), 0)

    def test_active_to_triggered_succeeds(self):
        """Active alert can be successfully triggered."""
        db_manager.add_alert('sell', 14000.0)
        alerts = db_manager.get_active_alerts()
        self.assertEqual(len(alerts), 1)
        alert_id = alerts[0]['id']

        success = db_manager.trigger_alert(alert_id)
        self.assertTrue(success)

        # Check DB status
        all_alerts = db_manager.get_all_alerts()
        self.assertEqual(all_alerts[0]['status'], 'triggered')
        self.assertIsNotNone(all_alerts[0]['triggered_at'])
        self.assertEqual(len(db_manager.get_active_alerts()), 0)

    def test_triggered_to_cancelled_is_rejected(self):
        """A triggered alert CANNOT be cancelled (stale cancel defense)."""
        db_manager.add_alert('buy', 13000.0)
        alert_id = db_manager.get_active_alerts()[0]['id']

        # Trigger it first
        trig_success = db_manager.trigger_alert(alert_id)
        self.assertTrue(trig_success)

        # Attempt stale cancel
        cancel_success = db_manager.cancel_alert(alert_id)
        self.assertFalse(cancel_success)

        # Status must remain triggered
        all_alerts = db_manager.get_all_alerts()
        self.assertEqual(all_alerts[0]['status'], 'triggered')

    def test_cancelled_to_cancelled_is_rejected(self):
        """Cancelling an already cancelled alert returns False."""
        db_manager.add_alert('buy', 13000.0)
        alert_id = db_manager.get_active_alerts()[0]['id']

        self.assertTrue(db_manager.cancel_alert(alert_id))
        self.assertFalse(db_manager.cancel_alert(alert_id))

    def test_triggered_to_triggered_is_rejected(self):
        """Triggering an already triggered alert returns False."""
        db_manager.add_alert('sell', 14000.0)
        alert_id = db_manager.get_active_alerts()[0]['id']

        self.assertTrue(db_manager.trigger_alert(alert_id))
        self.assertFalse(db_manager.trigger_alert(alert_id))

    def test_nonexistent_alert_operations_return_false(self):
        """Operations on non-existent alert IDs return False."""
        self.assertFalse(db_manager.cancel_alert(99999))
        self.assertFalse(db_manager.trigger_alert(99999))

    def test_concurrent_trigger_and_cancel_race(self):
        """Race between concurrent trigger and cancel attempts results in exactly one winner."""
        db_manager.add_alert('buy', 13500.0)
        alert_id = db_manager.get_active_alerts()[0]['id']

        results = {'trigger': None, 'cancel': None}

        def do_trigger():
            results['trigger'] = db_manager.trigger_alert(alert_id)

        def do_cancel():
            results['cancel'] = db_manager.cancel_alert(alert_id)

        t1 = threading.Thread(target=do_trigger)
        t2 = threading.Thread(target=do_cancel)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one must have succeeded (True) and the other failed (False)
        self.assertTrue(results['trigger'] ^ results['cancel'], 
                        f"Expected XOR success but got trigger={results['trigger']}, cancel={results['cancel']}")

        # Final state in DB must be valid
        all_alerts = db_manager.get_all_alerts()
        self.assertIn(all_alerts[0]['status'], ('triggered', 'cancelled'))

if __name__ == '__main__':
    unittest.main()
