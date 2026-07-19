import unittest
from contextlib import nullcontext
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from codex.trend_observer.assets import active_assets, security_id
from codex.trend_observer.ingestion import MarketSynchronizer
from codex.trend_observer.dividends import apply_dividend_config
from codex.trend_observer.data_sources import fetch_history
from codex.trend_observer.supabase_store import SupabaseSettings, SupabaseStore, payload_hash


class PaginationStore(SupabaseStore):
    def __init__(self, rows):
        super().__init__(SupabaseSettings("https://example.supabase.co", "sb_secret_example"))
        self.rows = rows
        self.ranges = []

    def _request(self, method, path, *, params=None, data=None, headers=None):
        start, end = map(int, headers["Range"].split("-"))
        self.ranges.append((start, end))
        return self.rows[start:end + 1]


class RepairStore:
    def __init__(self, asset, incoming):
        self.asset = asset
        self.sid = security_id(asset["symbol"], asset["market"])
        self.incoming = incoming
        self.saved_signals = []
        self.saved_market = []
        self.saved_valuations = []
        self.watermarks = []

    def calendar_is_trading_day(self, market, value): return True
    def latest_signal_state(self, sid): return None
    def get_watermark(self, key):
        latest = self.incoming.iloc[-1]["date"].date().isoformat()
        return {"status": "normal", "database_latest_date": latest, "source_latest_date": latest,
                "content_hash": payload_hash(self.incoming.to_dict("records")), "consecutive_failures": 0}
    def history(self, sid):
        return [{"security_id": sid, "trade_date": row.date.date().isoformat(), "open": row.open,
                 "high": row.high, "low": row.low, "close": row.close, "volume": row.volume,
                 "source": "eastmoney"} for row in self.incoming.itertuples(index=False)]
    def valuation_history(self, sid): return []
    def save_market_rows(self, rows): self.saved_market.extend(rows); return rows
    def save_valuation_rows(self, rows): self.saved_valuations.extend(rows); return rows
    def save_signal_rows(self, rows): self.saved_signals.extend(rows); return rows
    def save_watermark(self, row): self.watermarks.append(row)
    def add_run_item(self, *args, **kwargs): pass


class V2RegressionTest(unittest.TestCase):
    def test_single_asset_sync_can_apply_its_dividend(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        result = apply_dividend_config([asset], allow_subset=True)
        self.assertEqual(result[0]["last_year_dividend"], 1)

    def test_incremental_stock_fetch_keeps_eastmoney_for_short_overlap(self):
        frame = pd.DataFrame({
            "date": pd.to_datetime(["2026-07-16", "2026-07-17"]),
            "open": [1, 2], "high": [2, 3], "low": [0.5, 1.5],
            "close": [1.5, 2.5], "volume": [100, 200], "pe": [float("nan"), float("nan")],
        })
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        with patch("codex.trend_observer.data_sources.fetch_eastmoney_stock", return_value=frame), \
             patch("codex.trend_observer.data_sources.fetch_tencent") as tencent:
            result = fetch_history(object(), asset, start_date=date(2026, 7, 16))
        self.assertEqual(result.attrs["source_provider"], "eastmoney")
        tencent.assert_not_called()

    def test_incremental_stock_fetch_falls_back_when_eastmoney_is_empty(self):
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "pe"])
        fallback = pd.DataFrame({
            "date": pd.to_datetime(["2026-07-17"]), "open": [1], "high": [2], "low": [0.5],
            "close": [1.5], "volume": [100], "pe": [float("nan")],
        })
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        with patch("codex.trend_observer.data_sources.fetch_eastmoney_stock", return_value=empty), \
             patch("codex.trend_observer.data_sources.fetch_tencent", return_value=fallback):
            result = fetch_history(object(), asset, start_date=date(2026, 7, 16))
        self.assertEqual(result.attrs["source_provider"], "tencent")

    def test_supabase_select_paginates_past_project_max_rows(self):
        store = PaginationStore([{"id": index} for index in range(2005)])
        rows = store.select("market_daily", order="trade_date.asc")
        self.assertEqual(len(rows), 2005)
        self.assertEqual(store.ranges, [(0, 999), (1000, 1999), (2000, 2999)])

    def test_unchanged_facts_repair_missing_signals_and_preserve_actual_source(self):
        dates = pd.date_range("2025-01-01", periods=280, freq="B")
        incoming = pd.DataFrame({
            "date": dates,
            "open": range(100, 380), "high": range(101, 381), "low": range(99, 379),
            "close": range(100, 380), "volume": range(1000, 1280), "pe": [float("nan")] * 280,
        })
        incoming.attrs["source_provider"] = "eastmoney"
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        store = RepairStore(asset, incoming)
        synchronizer = MarketSynchronizer(store)
        with patch("codex.trend_observer.ingestion.make_session", return_value=nullcontext(object())), \
             patch("codex.trend_observer.ingestion.fetch_history", return_value=incoming):
            result = synchronizer.sync_asset(asset, now=datetime(2026, 7, 18, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")), force=True)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.rows_changed, 0)
        self.assertEqual(len(store.saved_signals), 280)

    def test_market_sync_never_persists_a_price_adapter_pe(self):
        dates = pd.date_range("2025-01-01", periods=280, freq="B")
        incoming = pd.DataFrame({
            "date": dates, "open": range(100, 380), "high": range(101, 381), "low": range(99, 379),
            "close": range(100, 380), "volume": range(1000, 1280), "pe": [20.0] * 280,
        })
        incoming.attrs["source_provider"] = "unverified-price-provider"
        asset = next(item for item in active_assets() if item["asset_type"] == "指数")
        store = RepairStore(asset, incoming)
        with patch("codex.trend_observer.ingestion.make_session", return_value=nullcontext(object())), \
             patch("codex.trend_observer.ingestion.fetch_history", return_value=incoming):
            result = MarketSynchronizer(store).sync_asset(asset, now=datetime(2026, 7, 18, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")), force=True)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(store.saved_valuations, [])


if __name__ == "__main__":
    unittest.main()
