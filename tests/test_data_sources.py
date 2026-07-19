import unittest

import numpy as np
import pandas as pd

from codex.trend_observer.config import ASSETS
from codex.trend_observer.data_sources import fetch_eastmoney_global_index, fetch_fred_close_series, fetch_global_index, fetch_nasdaq_index, fetch_yahoo_index


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
