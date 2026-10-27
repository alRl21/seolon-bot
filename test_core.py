import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from core import Store


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.db')

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_and_persistence(self):
        self.assertEqual(self.store.attend(1, 2, date(2026, 1, 1)), (30, 1))
        self.assertIsNone(self.store.attend(1, 2, date(2026, 1, 1)))
        self.assertEqual(Store(self.store.path).profile(1, 2), (30, 1))

    def test_bonus_and_gap(self):
        start = date(2026, 1, 1)
        for n in range(30):
            result = self.store.attend(1, 2, start + timedelta(days=n))
            if n == 6:
                self.assertEqual(result, (130, 7))
        self.assertEqual(result, (530, 30))
        self.assertEqual(self.store.profile(1, 2), (1800, 30))
        self.assertEqual(self.store.attend(1, 2, start + timedelta(days=31)), (30, 1))

    def test_parallel_duplicate(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.attend(1, 2, date(2026, 1, 1)), range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.store.profile(1, 2), (30, 1))

    def test_users_are_separate(self):
        self.store.attend(1, 2)
        self.assertEqual(self.store.profile(1, 3), (0, 0))
        self.assertEqual(self.store.profile(2, 2), (0, 0))


if __name__ == '__main__':
    unittest.main()
