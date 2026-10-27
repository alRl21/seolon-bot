import unittest
from datetime import datetime, timezone
from schedule import active_seconds, KST


class ScheduleTests(unittest.TestCase):
    def test_midnight_off(self):
        self.assertEqual(active_seconds(datetime(2026, 10, 27, 0, tzinfo=KST)), 0)

    def test_before_seven_off(self):
        self.assertEqual(active_seconds(datetime(2026, 10, 27, 6, 59, 59, tzinfo=KST)), 0)

    def test_seven_starts_seventeen_hours(self):
        self.assertEqual(active_seconds(datetime(2026, 10, 27, 7, tzinfo=KST)), 17 * 3600)

    def test_last_second(self):
        self.assertEqual(active_seconds(datetime(2026, 10, 27, 23, 59, 59, tzinfo=KST)), 1)

    def test_utc_start_and_end(self):
        self.assertEqual(active_seconds(datetime(2026, 10, 26, 22, tzinfo=timezone.utc)), 17 * 3600)
        self.assertEqual(active_seconds(datetime(2026, 10, 27, 15, tzinfo=timezone.utc)), 0)

    def test_year_boundary(self):
        self.assertEqual(active_seconds(datetime(2026, 12, 31, 23, tzinfo=KST)), 3600)
