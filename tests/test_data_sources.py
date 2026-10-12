import unittest
from datetime import datetime

import numpy as np
import pandas as pd

from codex.trend_observer.config import ASSETS, MARKET_TIMEZONE
from codex.trend_observer.data_sources import (
    fetch_eastmoney_global_index,
    fetch_csindex,
    fetch_fred_close_series,
    fetch_global_index,
    fetch_nasdaq_index,
    fetch_unadjusted_stock_history,
    fetch_tencent,
    fetch_yahoo_index,
    trading_weekdays_only,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse(self.payload)


class DataSourcesTest(unittest.TestCase):
    def test_unadjusted_stock_source_requests_raw_prices(self):
        payload = {"data": {"klines": ["2026-10-09,10,9.5,10,9,12345"]}}
        session = FakeSession(payload)
        frame = fetch_unadjusted_stock_history(session, "sh600900")
        self.assertEqual(session.requests[0][1]["params"]["fqt"], "0")
        self.assertEqual(frame.iloc[0]["close"], 9.5)
        self.assertEqual(frame.attrs["adjustment_method"], "raw")

    def test_unadjusted_stock_falls_back_to_independent_tencent_raw_series(self):
        class RawFallbackSession(FakeSession):
            def get(self, url, **kwargs):
                self.requests.append((url, kwargs))
                if "eastmoney" in url:
                    return FakeResponse({"data": {"klines": []}})
                return FakeResponse({"code": 0, "data": {"sh600036": {
                    "day": [["2026-10-09", "10", "9.5", "10", "9", "12345"]]
                }}})

        session = RawFallbackSession({})
        frame = fetch_unadjusted_stock_history(session, "sh600036")
        self.assertEqual(frame.iloc[0]["close"], 9.5)
        self.assertEqual(frame.attrs["source_provider"], "tencent")
        self.assertIn("kline/kline", session.requests[-1][0])
        self.assertNotIn(",qfq", session.requests[-1][1]["params"]["param"])

    def test_tencent_qfq_pages_backwards_with_verified_overlap(self):
        class PagingSession:
            def __init__(self):
                self.requests = []
                today = datetime.now(MARKET_TIMEZONE).date()
                self.days = pd.bdate_range(end=today, periods=1250)

            def get(self, url, **kwargs):
                self.requests.append((url, kwargs))
                _, _, start, end, *_ = kwargs["params"]["param"].split(",")
                selected = [day for day in self.days if start <= day.strftime("%Y-%m-%d") <= end][-640:]
                rows = [[day.strftime("%Y-%m-%d"), "10", str(10 + idx / 100), "11", "9", "100"]
                        for day in selected for idx in [self.days.get_loc(day)]]
                return FakeResponse({"code": 0, "data": {"sh600036": {"qfqday": rows}}})

        session = PagingSession()
        frame = fetch_tencent(session, "sh600036")
        self.assertGreater(len(session.requests), 1)
        self.assertEqual(len(frame), 1250)
        self.assertEqual(frame.attrs["adjustment_method"], "qfq")
        self.assertEqual(frame.iloc[0]["close"], 10)

    def test_tencent_qfq_rejects_inconsistent_page_overlap(self):
        class InconsistentSession:
            def __init__(self):
                self.calls = 0

            def get(self, url, **kwargs):
                self.calls += 1
                dates = ["2024-02-19", "2024-02-20"] if self.calls == 1 else ["2024-02-19", "2024-02-20"]
                close = "10" if self.calls == 1 else "11"
                return FakeResponse({"code": 0, "data": {"sh600036": {
                    "qfqday": [[day, "10", close, "11", "9", "100"] for day in dates]
                }}})

        with self.assertRaisesRegex(ValueError, "口径不一致"):
            fetch_tencent(InconsistentSession(), "sh600036")

    def test_trading_weekdays_only_removes_repeated_weekend_rows(self):
        frame = pd.DataFrame({
            "date": pd.to_datetime(["2026-07-17", "2026-07-18", "2026-07-19", "2026-07-20"]),
            "open": [1, 1, 1, 2], "high": [1, 1, 1, 2], "low": [1, 1, 1, 2],
            "close": [1, 1, 1, 2], "volume": [1, 1, 1, 2], "pe": [np.nan] * 4,
        })
        filtered = trading_weekdays_only(frame)
        self.assertEqual(filtered["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-07-17", "2026-07-20"])

    def test_csindex_adapter_drops_weekend_rows(self):
        payload = {"code": 200, "data": [
            {"tradeDate": "2026-07-17", "open": 1, "high": 2, "low": 1, "close": 2, "tradingVol": 10},
            {"tradeDate": "2026-07-18", "open": 2, "high": 2, "low": 2, "close": 2, "tradingVol": 0},
            {"tradeDate": "2026-07-20", "open": 3, "high": 4, "low": 3, "close": 4, "tradingVol": 12},
        ]}
        history = fetch_csindex(FakeSession(payload), "000300")
        self.assertEqual(history["date"].dt.strftime("%Y-%m-%d").tolist(), ["2026-07-17", "2026-07-20"])

    def test_us_indices_are_configured(self):
        assets_by_symbol = {asset["symbol"]: asset for asset in ASSETS}
        self.assertEqual(assets_by_symbol["NDX100"]["name"], "纳斯达克100")
        self.assertEqual(assets_by_symbol["NDX100"]["provider"], "global_index")
        self.assertEqual(assets_by_symbol["NDX100"]["eastmoney_symbol"], "100.NDX100")
        self.assertEqual(assets_by_symbol["NDX100"]["nasdaq_symbol"], "NDX")
        self.assertEqual(assets_by_symbol["NDX100"]["yahoo_symbol"], "^NDX")
        self.assertEqual(assets_by_symbol["SPX"]["name"], "标普500")
        self.assertEqual(assets_by_symbol["SPX"]["provider"], "global_index")
        self.assertEqual(assets_by_symbol["SPX"]["eastmoney_symbol"], "100.SPX")
        self.assertEqual(assets_by_symbol["SPX"]["yahoo_symbol"], "^GSPC")
        self.assertEqual(assets_by_symbol["SPX"]["fred_series"], "SP500")

    def test_fetch_fred_close_series_normalizes_daily_closes(self):
        class CsvResponse:
            text = """observation_date,SP500
2026-06-19,5980.00
2026-06-22,6001.50
"""

            def raise_for_status(self):
                return None

        class CsvSession:
            def get(self, url, **kwargs):
                self.url, self.kwargs = url, kwargs
                return CsvResponse()

        session = CsvSession()
        history = fetch_fred_close_series(session, "SP500")
        self.assertEqual(len(history), 2)
        self.assertEqual(history.iloc[-1]["close"], 6001.50)
        self.assertEqual(history.iloc[-1]["open"], 6001.50)
        self.assertTrue(np.isnan(history.iloc[-1]["volume"]))
        self.assertEqual(session.kwargs["params"], {"id": "SP500"})

    def test_global_index_uses_fred_when_primary_sources_fail(self):
        fallback = np.nan
        frame = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-19", "2026-06-22"]),
            "open": [5980.0, 6001.5], "high": [5980.0, 6001.5], "low": [5980.0, 6001.5],
            "close": [5980.0, 6001.5], "volume": [fallback, fallback], "pe": [fallback, fallback],
        })
        from unittest.mock import patch
        with (
            patch("codex.trend_observer.data_sources.fetch_eastmoney_global_index", side_effect=RuntimeError("unavailable")),
            patch("codex.trend_observer.data_sources.fetch_yahoo_index", side_effect=RuntimeError("rate limited")),
            patch("codex.trend_observer.data_sources.fetch_fred_close_series", return_value=frame),
        ):
            history = fetch_global_index(
                object(), {"eastmoney_symbol": "100.SPX", "yahoo_symbol": "^GSPC", "fred_series": "SP500"}, start_date=pd.Timestamp("2026-06-19").date()
            )
        self.assertEqual(history.attrs["source_provider"], "fred")

    def test_fetch_eastmoney_global_index_normalizes_klines(self):
        session = FakeSession(
            {
                "data": {
                    "klines": [
                        "2026-06-19,7400.00,7500.00,7510.00,7390.00,123456",
                        "2026-06-22,7500.44,7472.79,7530.01,7460.01,3673570320",
                    ]
                }
            }
        )

        history = fetch_eastmoney_global_index(session, "100.SPX")

        self.assertEqual(len(history), 2)
        self.assertEqual(history.iloc[-1]["date"].strftime("%Y-%m-%d"), "2026-06-22")
        self.assertEqual(history.iloc[-1]["open"], 7500.44)
        self.assertEqual(history.iloc[-1]["close"], 7472.79)
        self.assertEqual(history.iloc[-1]["high"], 7530.01)
        self.assertEqual(history.iloc[-1]["low"], 7460.01)
        self.assertEqual(history.iloc[-1]["volume"], 3673570320)
        self.assertTrue(np.isnan(history.iloc[-1]["pe"]))

        _, kwargs = session.requests[0]
        self.assertEqual(kwargs["params"]["secid"], "100.SPX")
        self.assertEqual(kwargs["params"]["fqt"], "0")
        self.assertEqual(kwargs["headers"]["Referer"], "https://quote.eastmoney.com/")

    def test_fetch_yahoo_index_normalizes_chart_response(self):
        session = FakeSession(
            {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1781875800, 1782135000],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [7400.0, 7500.44],
                                        "close": [7500.0, 7472.79],
                                        "high": [7510.0, 7530.01],
                                        "low": [7390.0, 7460.01],
                                        "volume": [123456, 3673570320],
                                    }
                                ]
                            },
                        }
                    ],
                    "error": None,
                }
            }
        )

        history = fetch_yahoo_index(session, "^GSPC")

        self.assertEqual(len(history), 2)
        self.assertEqual(history.iloc[-1]["date"].strftime("%Y-%m-%d"), "2026-06-22")
        self.assertEqual(history.iloc[-1]["close"], 7472.79)
        self.assertTrue(np.isnan(history.iloc[-1]["pe"]))

        url, kwargs = session.requests[0]
        self.assertIn("%5EGSPC", url)
        self.assertEqual(kwargs["params"]["interval"], "1d")
        self.assertEqual(kwargs["headers"]["Referer"], "https://finance.yahoo.com/")

    def test_fetch_nasdaq_index_normalizes_historical_rows(self):
        session = FakeSession(
            {
                "data": {
                    "tradesTable": {
                        "rows": [
                            {
                                "date": "06/22/2026",
                                "close": "30,347.08",
                                "volume": "--",
                                "open": "30,406.19",
                                "high": "30,642.57",
                                "low": "30,194.25",
                            },
                            {
                                "date": "06/19/2026",
                                "close": "30,406.19",
                                "volume": "1,234",
                                "open": "30,100.00",
                                "high": "30,500.00",
                                "low": "30,000.00",
                            },
                        ]
                    }
                },
                "status": {"rCode": 200},
            }
        )

        history = fetch_nasdaq_index(session, "NDX")

        self.assertEqual(len(history), 2)
        self.assertEqual(history.iloc[0]["date"].strftime("%Y-%m-%d"), "2026-06-19")
        self.assertEqual(history.iloc[-1]["date"].strftime("%Y-%m-%d"), "2026-06-22")
        self.assertEqual(history.iloc[-1]["close"], 30347.08)
        self.assertTrue(np.isnan(history.iloc[-1]["volume"]))

        url, kwargs = session.requests[0]
        self.assertIn("/NDX/historical", url)
        self.assertEqual(kwargs["params"]["assetclass"], "index")
        self.assertEqual(kwargs["headers"]["Origin"], "https://www.nasdaq.com")


if __name__ == "__main__":
    unittest.main()
