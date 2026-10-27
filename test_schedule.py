import unittest
from datetime import datetime, timezone
from schedule import active_seconds, KST

class ScheduleTests(unittest.TestCase):
    def test_midnight_remains_active(self):
        self.assertEqual(active_seconds(datetime(2026,10,28,0,tzinfo=KST)),3600)
    def test_one_to_seven_completely_stopped(self):
        for h in range(1,7):self.assertEqual(active_seconds(datetime(2026,10,28,h,tzinfo=KST)),0)
        self.assertEqual(active_seconds(datetime(2026,10,28,6,59,59,tzinfo=KST)),0)
    def test_seven_starts_eighteen_hour_session(self):
        self.assertEqual(active_seconds(datetime(2026,10,28,7,tzinfo=KST)),18*3600)
    def test_last_second(self):
        self.assertEqual(active_seconds(datetime(2026,10,28,0,59,59,tzinfo=KST)),1)
    def test_utc_start_and_end(self):
        self.assertEqual(active_seconds(datetime(2026,10,27,22,tzinfo=timezone.utc)),18*3600)
        self.assertEqual(active_seconds(datetime(2026,10,28,15,tzinfo=timezone.utc)),3600)
        self.assertEqual(active_seconds(datetime(2026,10,28,16,tzinfo=timezone.utc)),0)
    def test_year_boundary(self):
        self.assertEqual(active_seconds(datetime(2026,12,31,23,tzinfo=KST)),7200)
