import unittest
from datetime import date, datetime

from codex.trend_observer.freshness import expected_market_date


class FreshnessTest(unittest.TestCase):
    def test_cn_before_at_close_and_weekend(self):
        for stamp, expected in [
            ("2026-09-11T14:59:00+08:00", date(2026, 9, 10)),
            ("2026-09-11T15:00:00+08:00", date(2026, 9, 11)),
            ("2026-09-12T08:00:00+08:00", date(2026, 9, 11)),
        ]:
            with self.subTest(stamp=stamp):
                self.assertEqual(expected_market_date("CN", datetime.fromisoformat(stamp), lambda *args: None), expected)

    def test_calendar_holiday_overrides_weekday(self):
        self.assertEqual(expected_market_date(
            "CN", datetime.fromisoformat("2026-09-11T18:45:00+08:00"),
            lambda market, day: False if day == date(2026, 9, 11) else None,
        ), date(2026, 9, 10))

    def test_us_daylight_saving(self):
        for stamp, expected in [
            ("2026-09-12T04:00:00+08:00", date(2026, 9, 11)),
            ("2026-01-07T04:30:00+08:00", date(2026, 1, 5)),
            ("2026-01-07T05:00:00+08:00", date(2026, 1, 6)),
        ]:
            self.assertEqual(expected_market_date("US", datetime.fromisoformat(stamp), lambda *args: None), expected)

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            expected_market_date("CN", datetime(2026, 9, 11), lambda *args: None)
