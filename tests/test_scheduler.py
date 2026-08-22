import unittest
import threading
import time
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.scheduler import GoldScheduler, _CYCLE_LOCK

class TestSchedulerConcurrency(unittest.TestCase):
    def test_start_is_idempotent(self):
        """Calling start() multiple times on a scheduler does not spawn multiple threads."""
        sched = GoldScheduler()
        sched.interval_secs = 3600  # long interval

        sched.start(check_gaps=False)
        self.assertTrue(sched.running)
        first_thread = sched.thread

        # Start again
        sched.start(check_gaps=False)
        self.assertTrue(sched.running)
        self.assertEqual(sched.thread, first_thread)

        sched.stop()
        self.assertFalse(sched.running)

    def test_stop_is_idempotent(self):
        """Calling stop() multiple times is safe and does not error."""
        sched = GoldScheduler()
        sched.stop()
        self.assertFalse(sched.running)
        sched.stop()
        self.assertFalse(sched.running)

    def test_cycle_lock_prevents_overlap(self):
        """When a cycle is running, another concurrent trigger skips rather than blocks or duplicates."""
        sched = GoldScheduler()

        # Simulate a long-running cycle holding the lock
        acquired = _CYCLE_LOCK.acquire(blocking=False)
        self.assertTrue(acquired, "Failed to acquire _CYCLE_LOCK for simulation")

        try:
            # While lock is held, run_now() must return None (skipped)
            result = sched.run_now()
            self.assertIsNone(result)
        finally:
            _CYCLE_LOCK.release()

    def test_scheduler_listener_registration(self):
        """Adding and removing listeners works properly without duplication."""
        sched = GoldScheduler()
        received = []

        def cb1(data):
            received.append(('cb1', data))

        def cb2(data):
            received.append(('cb2', data))

        sched.add_listener(cb1)
        sched.add_listener(cb2)
        sched.add_listener(cb1)  # duplicate add ignored
        self.assertEqual(len(sched.listeners), 2)

        sched._notify_listeners({'test': 123})
        self.assertEqual(len(received), 2)

        sched.remove_listener(cb1)
        self.assertEqual(len(sched.listeners), 1)

        received.clear()
        sched._notify_listeners({'test': 456})
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], 'cb2')

if __name__ == '__main__':
    unittest.main()
