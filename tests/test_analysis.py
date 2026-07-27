import unittest

import numpy as np
import pandas as pd

from codex.trend_observer.analysis import determine_overall_status, determine_short_trend, valuation_status
from codex.trend_observer.dividends import calculate_dividend_yield


class AnalysisTest(unittest.TestCase):
    def test_overall_statuses(self):
        cases = [
            ({"short_trend": "短期强势", "mid_trend": "中期上升", "long_trend": "长期上升"}, "强趋势"),
            ({"short_trend": "短期震荡", "mid_trend": "中期上升", "long_trend": "长期上升"}, "健康上升"),
            ({"short_trend": "短期震荡", "mid_trend": "中期修复", "long_trend": "长期上升"}, "趋势修复"),
            ({"short_trend": "短期震荡", "mid_trend": "中期上升", "long_trend": "长期修复"}, "趋势分歧"),
            ({"short_trend": "短期震荡", "mid_trend": "中期转弱", "long_trend": "长期上升"}, "趋势转弱"),
            ({"short_trend": "短期下跌", "mid_trend": "中期下跌", "long_trend": "长期下跌"}, "下跌通道"),
        ]
        for row, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(determine_overall_status(pd.Series(row)), expected)

    def test_valuation_boundaries(self):
        cases = [
            (np.nan, "估值数据缺失"),
            (14.99, "极低估"),
            (15, "低估"),
            (34.99, "低估"),
            (35, "合理"),
            (69.99, "合理"),
            (70, "高估"),
            (89.99, "高估"),
            (90, "极高估"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(valuation_status(value), expected)

    def test_short_trend_uses_dashboard_label(self):
        row = pd.Series({"close": 9.9, "MA20": 10.0, "ma20_slope_5d": -0.0101})
        self.assertEqual(determine_short_trend(row), "短期下跌")

    def test_dividend_yield(self):
        self.assertTrue(np.isnan(calculate_dividend_yield(np.nan, 10)))
        self.assertTrue(np.isnan(calculate_dividend_yield(1, 0)))
        self.assertEqual(calculate_dividend_yield(0.3, 10), 3.0)
        self.assertEqual(calculate_dividend_yield(0.51, 10), 5.1)


if __name__ == "__main__":
    unittest.main()
