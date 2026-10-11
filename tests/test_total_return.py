import unittest
from datetime import date

import pandas as pd

from codex.trend_observer.total_return import stock_three_year_returns


DATES = [day.date() for day in pd.bdate_range("2023-10-09", "2026-10-09")]
FIRST_EX = date(2025, 6, 9)
SECOND_EX = date(2026, 6, 9)


def bars(raw_and_adjusted):
    result = []
    for day in DATES:
        raw, adjusted = raw_and_adjusted(day)
        result.append({
            "trade_date": day.isoformat(), "raw_close": raw, "close": adjusted,
            "raw_close_source": "eastmoney", "adjustment_method": "qfq",
        })
    return result


def event(day, cash, *, bonus=0.0, transfer=0.0):
    return {
        "event_stage": "implemented", "announcement_date": day.isoformat(),
        "ex_date": day.isoformat(), "cash_dividend_per_share": cash,
        "bonus_shares_per_share": bonus, "transfer_shares_per_share": transfer,
    }


def compute(prices, events=(), *, verified=True):
    return stock_three_year_returns(prices, list(events), DATES[-1], dividend_history_current=verified)


class StockTotalReturnTest(unittest.TestCase):
    def test_no_dividend_equals_price_return(self):
        prices = bars(lambda day: (100, 100) if day < FIRST_EX else (110, 110))
        result = compute(prices)
        self.assertEqual(result["total_return_3y_status"], "可用")
        self.assertAlmostEqual(result["price_return_3y"], .10)
        self.assertAlmostEqual(result["total_return_3y"], .10)
        self.assertEqual(result["total_return_3y_start_date"], "2023-10-09")

    def test_cash_dividend_is_reinvested_once_not_added_to_qfq(self):
        prices = bars(lambda day: (100, 95) if day < FIRST_EX else (95, 95))
        result = compute(prices, [event(FIRST_EX, 5)])
        self.assertAlmostEqual(result["price_return_3y"], -.05)
        self.assertAlmostEqual(result["total_return_3y"], 0)

    def test_multiple_cash_dividends_compound(self):
        prices = bars(lambda day: (100, 90) if day < FIRST_EX else
                      (95, 90) if day < SECOND_EX else (90, 90))
        result = compute(prices, [event(FIRST_EX, 5), event(SECOND_EX, 5)])
        self.assertAlmostEqual(result["price_return_3y"], -.10)
        self.assertAlmostEqual(result["total_return_3y"], 0)

    def test_reinvested_cash_participates_in_later_price_gain(self):
        prices = bars(lambda day: (100, 95) if day < FIRST_EX else
                      (95, 95) if day < SECOND_EX else (105, 105))
        result = compute(prices, [event(FIRST_EX, 5)])
        self.assertAlmostEqual(result["price_return_3y"], .05)
        self.assertAlmostEqual(result["total_return_3y"], 105 / 95 - 1)

    def test_bonus_share_is_a_share_count_change_not_cash(self):
        prices = bars(lambda day: (100, 50) if day < FIRST_EX else (50, 50))
        result = compute(prices, [event(FIRST_EX, 0, bonus=1)])
        self.assertAlmostEqual(result["price_return_3y"], 0)
        self.assertAlmostEqual(result["total_return_3y"], 0)

    def test_unknown_corporate_action_and_missing_ratio_fail_closed(self):
        prices = bars(lambda day: (100, 50) if day < FIRST_EX else (50, 50))
        self.assertEqual(compute(prices)["total_return_3y_status"], "数据不足")
        missing = event(FIRST_EX, 0, bonus=1)
        missing["bonus_shares_per_share"] = None
        self.assertEqual(compute(prices, [missing])["total_return_3y_status"], "数据不足")

    def test_future_event_does_not_enter_current_return(self):
        prices = bars(lambda day: (100, 100))
        result = compute(prices, [event(date(2026, 10, 16), 5)])
        self.assertEqual(result["total_return_3y"], 0)

    def test_short_history_or_unverified_dividends_are_not_zero(self):
        prices = bars(lambda day: (100, 100))
        self.assertIsNone(compute(prices[20:])["total_return_3y"])
        self.assertIsNone(compute(prices, verified=False)["total_return_3y"])

    def test_missing_raw_price_and_duplicate_event_fail_closed(self):
        prices = bars(lambda day: (100, 100))
        prices[-1]["raw_close"] = None
        self.assertIsNone(compute(prices)["total_return_3y"])
        prices[-1]["raw_close"] = 100
        duplicate = event(FIRST_EX, 0)
        self.assertIsNone(compute(prices, [duplicate, duplicate])["total_return_3y"])


if __name__ == "__main__":
    unittest.main()
