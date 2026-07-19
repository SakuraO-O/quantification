import unittest
from contextlib import nullcontext
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from codex.trend_observer.ingestion import _merge_valuation_history
from codex.trend_observer.ingestion import MarketSynchronizer
from codex.trend_observer.valuation_sources import ValuationBatch, parse_cnindex_current_pe


class ValuationSourcesTest(unittest.TestCase):
    def test_cnindex_parser_uses_rolling_pe_for_matching_symbol(self):
        html = """
        <p>全部指数 2026-07-17</p>
        <table><tr><th>指数代码</th><th>指数简称</th><th>PE(滚动)</th></tr>
        <tr><td>399006</td><td>创业板指</td><td>45.39</td></tr>
        <tr><td>980092</td><td>国证自由现金流</td><td>10.62</td></tr></table>
        """
        self.assertEqual(parse_cnindex_current_pe(html, "sz399006"), {"trade_date": "2026-07-17", "value": 45.39})
        self.assertEqual(parse_cnindex_current_pe(html, "980092"), {"trade_date": "2026-07-17", "value": 10.62})

    def test_monthly_source_percentile_uses_observations_not_daily_copies(self):
        market = pd.DataFrame({
            "date": pd.date_range("2016-08-01", periods=2600, freq="B"),
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
        })
        valuation = []
        for index, value in enumerate(pd.date_range("2016-08-01", periods=120, freq="MS")):
            valuation.append({
                "trade_date": value.date().isoformat(), "value": float(index + 1),
                "source": "worldperatio", "methodology": "estimated_pe_monthly_10y",
            })
        merged = _merge_valuation_history(market, valuation)
        latest = merged.dropna(subset=["pe_percentile_override"]).iloc[-1]
        self.assertEqual(latest["pe_percentile_override"], 100.0)
        self.assertEqual(latest["pe_percentile_period_override"], "近10年")

    def test_current_only_source_has_no_false_percentile(self):
        market = pd.DataFrame({
            "date": pd.to_datetime(["2026-07-17"]), "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1],
        })
        merged = _merge_valuation_history(market, [{
            "trade_date": "2026-07-17", "value": 10.62, "source": "cnindex", "methodology": "official_rolling_pe_current",
        }])
        self.assertEqual(merged.iloc[0]["pe"], 10.62)
        self.assertNotIn("pe_percentile_override", merged.columns)

    def test_valuation_failure_is_isolated_when_quality_issue_write_fails(self):
        class Store:
            def get_watermark(self, _key): return None
            def record_quality_issue(self, *_args, **_kwargs):
                raise RuntimeError("database temporarily unavailable")
            def save_watermark(self, _row): raise RuntimeError("database temporarily unavailable")

        asset = {"name": "标普500", "symbol": "SPX", "market": "US", "asset_type": "指数"}
        with patch("codex.trend_observer.ingestion.make_session", side_effect=RuntimeError("network unavailable")):
            result = MarketSynchronizer(Store()).sync_valuation_asset(
                asset, now=datetime(2026, 7, 19, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            )
        self.assertEqual(result.status, "failed")
        self.assertIn("network unavailable", result.message)

    def test_valuation_sync_keeps_compact_source_metadata_not_response_content(self):
        class Store:
            def __init__(self): self.record = None; self.rows = []
            def get_watermark(self, _key): return None
            def save_ingestion_source_record(self, row): self.record = row; return "source-record-id"
            def valuation_history(self, _sid): return []
            def save_valuation_rows(self, rows): self.rows.extend(rows)
            def save_watermark(self, _row): pass

        store = Store()
        asset = {"name": "纳斯达克100", "symbol": "NDX100", "market": "US", "asset_type": "指数"}
        batch = ValuationBatch("worldperatio", "https://example.test/pe", "estimated_pe_monthly_10y", [{"trade_date": "2026-07-01", "value": 30.1}])
        with patch("codex.trend_observer.ingestion.make_session", return_value=nullcontext(object())), \
             patch("codex.trend_observer.ingestion.fetch_valuation_batch", return_value=batch):
            result = MarketSynchronizer(store).sync_valuation_asset(asset)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(store.record["metadata"], {"source_url": "https://example.test/pe", "methodology": "estimated_pe_monthly_10y", "observation_count": 1})
        self.assertNotIn("response", store.record["metadata"])
        self.assertEqual(store.rows[0]["ingestion_source_record_id"], "source-record-id")

    def test_source_switch_refuses_to_mix_pe_histories(self):
        class Store:
            def get_watermark(self, _key): return None
            def valuation_history(self, _sid): return [{"trade_date": "2026-07-01", "value": 25, "source": "old-provider"}]
            def record_quality_issue(self, *_args, **_kwargs): pass

        asset = {"name": "纳斯达克100", "symbol": "NDX100", "market": "US", "asset_type": "指数"}
        batch = ValuationBatch("worldperatio", "https://example.test/pe", "estimated_pe_monthly_10y", [{"trade_date": "2026-07-02", "value": 30.1}])
        with patch("codex.trend_observer.ingestion.make_session", return_value=nullcontext(object())), \
             patch("codex.trend_observer.ingestion.fetch_valuation_batch", return_value=batch):
            result = MarketSynchronizer(Store()).sync_valuation_asset(asset)
        self.assertEqual(result.status, "failed")
        self.assertIn("来源切换待人工迁移", result.message)


if __name__ == "__main__":
    unittest.main()
