import unittest
from contextlib import nullcontext
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from codex.trend_observer.assets import active_assets, security_id
from codex.trend_observer.ingestion import MarketSynchronizer, SyncResult, CALCULATION_VERSION
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
    def test_morning_retry_skips_fresh_previous_session_without_fetch(self):
        from unittest.mock import Mock
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        store = Mock()
        store.calendar_is_trading_day.return_value = None
        store.get_watermark.return_value = {"status": "normal", "database_latest_date": "2026-09-11"}
        store.latest_signal_state.return_value = {"trade_date": "2026-09-11", "calculation_version": CALCULATION_VERSION}
        with patch("codex.trend_observer.ingestion.fetch_history") as fetch:
            result = MarketSynchronizer(store).sync_asset(asset, now=datetime(2026, 9, 12, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertEqual(result.status, "skipped")
        fetch.assert_not_called()

    def test_stale_batch_does_not_write_market_or_success_watermark(self):
        from unittest.mock import Mock
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        store = Mock()
        store.calendar_is_trading_day.return_value = None
        store.get_watermark.return_value = None
        store.latest_signal_state.return_value = None
        frame = pd.DataFrame({"date": pd.to_datetime(["2026-09-10"]), "close": [10.]})
        with patch("codex.trend_observer.ingestion.make_session", return_value=nullcontext(object())), \
             patch("codex.trend_observer.ingestion.fetch_history", return_value=frame):
            result = MarketSynchronizer(store).sync_asset(asset, now=datetime(2026, 9, 12, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertEqual(result.status, "failed")
        self.assertIn("2026-09-11", result.message)
        store.save_market_rows.assert_not_called()
        self.assertNotIn("last_success_at", store.save_watermark.call_args.args[0])

    def test_unchanged_valuation_retries_failed_calculation(self):
        from unittest.mock import Mock
        asset = next(item for item in active_assets() if item["asset_type"] == "指数")
        store = Mock()
        sync = MarketSynchronizer(store)
        with patch.object(sync, "sync_valuation_asset", return_value=SyncResult(asset["symbol"], "valuation", "skipped", 5)), \
             patch.object(sync, "recalculate_valuation_signals", side_effect=ValueError("write failed")) as recalc:
            results = sync.sync_valuations([asset])
        recalc.assert_called_once_with(asset, None)
        self.assertEqual(results[0].status, "failed")
        self.assertIn("重算失败", results[0].message)
        self.assertEqual(store.add_run_item.call_count, 1)
        self.assertEqual(store.add_run_item.call_args.kwargs["status"], "failed")
        self.assertEqual(store.finish_run.call_args.args[1], "partial")

    def test_log_failure_does_not_stop_next_asset(self):
        from unittest.mock import Mock
        assets = active_assets()[:2]
        store = Mock()
        store.add_run_item.side_effect = [ValueError("log failed"), None]
        sync = MarketSynchronizer(store)
        rows = [SyncResult(asset["symbol"], asset["symbol"], "skipped") for asset in assets]
        with patch.object(sync, "sync_asset", side_effect=rows) as fetch:
            result = sync.sync_assets(assets)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([row.status for row in result], ["failed", "skipped"])

    def test_sync_records_skips_and_isolates_preflight_failure(self):
        from unittest.mock import Mock
        assets = active_assets()[:2]
        store = Mock()
        store.start_run.return_value = "run-1"
        synchronizer = MarketSynchronizer(store)
        skipped = SyncResult(assets[1]["symbol"], "second", "skipped", message="当日数据已入库")
        with patch.object(synchronizer, "sync_asset", side_effect=[ValueError("calendar unavailable"), skipped]):
            results = synchronizer.sync_assets(assets)
        self.assertEqual([row.status for row in results], ["failed", "skipped"])
        self.assertEqual(store.add_run_item.call_count, 2)
        self.assertEqual(store.finish_run.call_args.args[1], "partial")

    def test_stale_primary_switches_to_current_fallback(self):
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        old = pd.DataFrame({"date": pd.to_datetime(["2026-09-10"]), "close": [10.]})
        current = pd.DataFrame({"date": pd.to_datetime(["2026-09-11"]), "close": [11.]})
        with patch("codex.trend_observer.data_sources.fetch_eastmoney_stock", return_value=old), \
             patch("codex.trend_observer.data_sources.fetch_tencent", return_value=current) as fallback:
            result = fetch_history(object(), asset, start_date=date(2026, 9, 7), expected_date=date(2026, 9, 11))
        fallback.assert_called_once()
        self.assertEqual(result.attrs["source_provider"], "tencent")
        self.assertIn("2026-09-10", result.attrs["source_diagnostics"][0])

    def test_both_stale_sources_report_dates_instead_of_success(self):
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        old = pd.DataFrame({"date": pd.to_datetime(["2026-09-10"]), "close": [10.]})
        with patch("codex.trend_observer.data_sources.fetch_eastmoney_stock", return_value=old), \
             patch("codex.trend_observer.data_sources.fetch_tencent", return_value=old):
            with self.assertRaisesRegex(ValueError, "eastmoney:.*2026-09-11.*tencent:"):
                fetch_history(object(), asset, start_date=date(2026, 9, 7), expected_date=date(2026, 9, 11))

    def test_partial_future_session_is_removed(self):
        asset = next(item for item in active_assets() if item["asset_type"] == "股票")
        frame = pd.DataFrame({"date": pd.to_datetime(["2026-09-10", "2026-09-11"]), "close": [10., 11.]})
        with patch("codex.trend_observer.data_sources.fetch_eastmoney_stock", return_value=frame):
            result = fetch_history(object(), asset, start_date=date(2026, 9, 7), expected_date=date(2026, 9, 10))
        self.assertEqual(result["date"].dt.date.tolist(), [date(2026, 9, 10)])

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
            result = synchronizer.sync_asset(asset, now=dates[-1].to_pydatetime().replace(hour=18, tzinfo=ZoneInfo("Asia/Shanghai")), force=True)
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
            result = MarketSynchronizer(store).sync_asset(asset, now=dates[-1].to_pydatetime().replace(hour=18, tzinfo=ZoneInfo("Asia/Shanghai")), force=True)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(store.saved_valuations, [])


if __name__ == "__main__":
    unittest.main()
