import unittest

import numpy as np
import pandas as pd

from codex.trend_observer.models import clean_value
from codex.trend_observer.outputs import records_from_results


class OutputsTest(unittest.TestCase):
    def test_clean_value_serializes_common_types(self):
        self.assertEqual(clean_value(pd.Timestamp("2026-06-20")), "2026-06-20")
        self.assertEqual(clean_value(np.float64(1.25)), 1.25)
        self.assertIsNone(clean_value(np.nan))

    def test_records_from_results_adds_signals(self):
        frame = pd.DataFrame(
            [
                {
                    "name": "沪深300",
                    "symbol": "000300",
                    "signal_tags": "健康上升, 偏高",
                    "date": pd.Timestamp("2026-06-20"),
                    "close": np.float64(1.25),
                }
            ]
        )
        records = records_from_results(frame)
        self.assertEqual(records[0]["date"], "2026-06-20")
        self.assertEqual(records[0]["close"], 1.25)
        self.assertEqual(records[0]["signals"], ["健康上升", "偏高"])


if __name__ == "__main__":
    unittest.main()

