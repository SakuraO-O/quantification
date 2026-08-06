import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from codex.trend_observer.allocation import calculate_allocation
from codex.trend_observer.analysis import determine_index_investment_advice
from codex.trend_observer.assets import active_assets, provider_symbol_rows, security_rows
from codex.trend_observer.config import ASSETS, PORTFOLIO_CATEGORIES
from codex.trend_observer.dashboard_versions import DashboardPublisher
from codex.trend_observer.feishu import format_dashboard_version_message
from codex.trend_observer.fundamentals import assess_high_dividend_fundamentals
from codex.trend_observer.dispatch import dispatch_morning_report
from codex.trend_observer.style_compass import calculate_style_compass, style_recommendation, style_recommendation_details
from codex.trend_observer.supabase_store import payload_hash


class V2CoreTest(unittest.TestCase):
    def test_active_registry_is_twelve_indices_and_nine_stocks(self):
        assets = active_assets()
        self.assertEqual(sum(asset["asset_type"] == "指数" for asset in assets), 12)
        self.assertEqual(sum(asset["asset_type"] == "股票" for asset in assets), 9)
        self.assertNotIn("sh601919", {asset["symbol"] for asset in assets})
        self.assertEqual(len(security_rows()), 21)
        self.assertGreaterEqual(len(provider_symbol_rows()), 21)
        currencies = {row["market"]: row["currency"] for row in security_rows()}
        self.assertEqual(currencies, {"CN": "CNY", "HK": "HKD", "US": "USD"})

    def test_index_investment_advice_respects_valuation_cap(self):
        self.assertEqual(
            determine_index_investment_advice({"long_trend": "长期上升", "mid_trend": "中期上升", "pe_percentile": 20}),
            "优先新增",
        )
        self.assertEqual(
            determine_index_investment_advice({"long_trend": "长期上升", "mid_trend": "中期修复", "pe_percentile": None}),
            "仅持有",
        )
        self.assertEqual(
            determine_index_investment_advice({"long_trend": "长期上升", "mid_trend": "中期上升", "pe_percentile": 70}),
            "仅持有",
        )
        self.assertEqual(
            determine_index_investment_advice({"long_trend": "长期上升", "mid_trend": "中期上升", "pe_percentile": 95}),
            "仅持有",
        )
        self.assertEqual(
            determine_index_investment_advice({"long_trend": "长期修复", "mid_trend": "中期修复", "pe_percentile": 95}),
            "观察等待",
        )
        self.assertEqual(
            determine_index_investment_advice({"long_trend": "长期下跌", "mid_trend": "中期下跌", "pe_percentile": 10}),
            "暂停参与",
        )

    def test_six_category_allocation(self):
        targets = {"海外": 10, "红利": 30, "成长": 20, "债券": 20, "大宗商品": 10, "现金": 10}
        actuals = {"海外": 100, "红利": 300, "成长": 260, "债券": 140, "大宗商品": 100, "现金": 100}
        rows = {row["category"]: row for row in calculate_allocation(targets, actuals)}
        self.assertEqual(rows["成长"]["deviation_state"], "明显超配")
        self.assertEqual(rows["债券"]["deviation_state"], "明显低配")
        self.assertEqual(rows["海外"]["theoretical_adjustment_amount"], 0.0)

    def test_allocation_deviation_state_thresholds(self):
        targets = {"海外": 20, "红利": 20, "成长": 20, "债券": 20, "大宗商品": 10, "现金": 10}
        actuals = {"海外": 200, "红利": 230, "成长": 140, "债券": 220, "大宗商品": 100, "现金": 100}
        rows = {row["category"]: row for row in calculate_allocation(targets, actuals)}
        self.assertEqual(rows["海外"]["deviation_state"], "均衡")
        self.assertEqual(rows["红利"]["deviation_state"], "关注")
        self.assertEqual(rows["成长"]["deviation_state"], "明显低配")
        self.assertIn("deviation", rows["海外"])
        self.assertNotIn("deviation_percentage_points", rows["海外"])

    def test_style_compass_uses_all_three_periods(self):
        left = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=121), "close": list(range(100, 221))})
        right = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=121), "close": [100 + index * 0.5 for index in range(121)]})
        result = calculate_style_compass(left, right)
        self.assertGreater(result["score"], 20)
        self.assertEqual(result["direction"], "偏左")
        self.assertEqual(style_recommendation("偏左", "可新增", "仅持有", 50, 80), "新增资金优先关注左侧资产")

    def test_style_recommendation_applies_pe_constraint_and_reason(self):
        self.assertEqual(
            style_recommendation_details("偏右", "仅持有", 20, "可新增", 50)["recommendation"],
            "新增资金优先关注右侧资产",
        )
        high_pe = style_recommendation_details("偏右", "仅持有", 20, "可新增", 50.1)
        self.assertEqual(high_pe["recommendation"], "暂不倾斜")
        self.assertIn("高于50%", high_pe["recommendation_reason"])
        missing_pe = style_recommendation_details("偏左", "可新增", None, "仅持有", 20)
        self.assertEqual(missing_pe["recommendation"], "暂不倾斜")
        self.assertIn("PE百分位缺失", missing_pe["recommendation_reason"])
        self.assertEqual(
            style_recommendation_details("数据不足", None, None, None, None)["recommendation"],
            "数据不足",
        )

    def test_bank_assessment_does_not_require_free_cashflow(self):
        assessment = assess_high_dividend_fundamentals(
            {"revenue": 1, "net_profit": 1, "roe": 1, "payout_ratio": 0.3, "capital_ratio": 0.1, "operating_quality_change": 0},
            "bank",
        )
        self.assertEqual(assessment.dividend_safety_status, "稳健")
        self.assertEqual(assessment.cash_reinvestment_status, "稳健")

    def test_version_hash_is_stable(self):
        self.assertEqual(payload_hash({"a": 1, "b": [2]}), payload_hash({"b": [2], "a": 1}))

    def test_dashboard_payload_does_not_embed_publish_time(self):
        root = Path(__file__).resolve().parents[1]
        publisher = (root / "codex/trend_observer/dashboard_versions.py").read_text(encoding="utf-8")
        payload_block = publisher.split("payload = {", 1)[1].split("completeness =", 1)[0]
        self.assertNotIn("generated_at", payload_block)

    def test_enum_migration_normalizes_by_percentile_without_chained_labels(self):
        root = Path(__file__).resolve().parents[1]
        migration = (root / "supabase/migrations/20260726010004_normalize_dashboard_enums.sql").read_text(encoding="utf-8")
        self.assertIn("when pe_percentile >= 90 then '极高估'", migration)
        self.assertIn("when pe_percentile >= 70 then '高估'", migration)
        self.assertNotIn("when '高估' then '极高估'", migration)
        self.assertIn("intentionally safe to rerun", migration)
        self.assertIn("add column if not exists signal_tags text", migration)
        self.assertIn(r"'\1高估'", migration)
        self.assertNotIn(r"'\\1高估'", migration)

    def test_allocation_test_mirror_matches_database_rpc_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        mirror = (root / "codex/trend_observer/allocation.py").read_text(encoding="utf-8")
        rpc = (root / "supabase/migrations/20260726022358_compute_portfolio_allocation.sql").read_text(encoding="utf-8")
        self.assertIn("Test-only mirror", mirror)
        self.assertIn("when abs(deviation) <= 2 then '均衡'", rpc)
        self.assertIn("when abs(deviation) <= 5 then '关注'", rpc)
        self.assertIn("'deviation'", rpc)

    def test_delayed_asset_publishes_dashboard_with_current_fields_blank(self):
        class Store:
            def __init__(self):
                self.assets = [
                    asset | {"security_id": f"{asset['market']}:{asset['symbol']}", "is_active": True}
                    for asset in ASSETS
                ]
                self.published = None

            def select(self, table, filters=None, **kwargs):
                if table == "securities":
                    return self.assets
                if table == "asset_daily_signals":
                    security_id = (filters or {})["security_id"].removeprefix("eq.")
                    symbol = security_id.split(":", 1)[1]
                    trade_date = "2026-07-16" if symbol == "SPX" else "2026-07-17"
                    return [{
                        "trade_date": trade_date, "close": 100, "daily_return": 0.01,
                        "overall_status": "健康上升", "investment_advice": "仅持有",
                        "calculation_version": "trend-v2.1.0",
                    }]
                if table == "portfolio_allocations":
                    rows = []
                    for category in PORTFOLIO_CATEGORIES:
                        rows.append({"allocation_type": "target_ratio", "category": category, "value": 100 / len(PORTFOLIO_CATEGORIES)})
                        rows.append({"allocation_type": "actual_amount", "category": category, "value": 100})
                    return rows
                return []

            def previous_trading_date(self, market, value):
                return datetime(2026, 7, 17).date()

            def history(self, security_id):
                return []

            def rpc(self, function):
                if function != "compute_portfolio_allocation":
                    raise AssertionError(function)
                return None

            def publish_dashboard_version(self, payload, **kwargs):
                self.published = {"payload": payload} | kwargs
                return self.published

        version = DashboardPublisher(Store()).publish()
        spx = next(asset for asset in version["payload"]["assets"] if asset["symbol"] == "SPX")
        self.assertTrue(version["is_complete"])
        self.assertEqual(version["completeness"]["data_status"], "degraded")
        self.assertEqual(spx["data_status"], "delayed")
        self.assertEqual(spx["trade_date"], "2026-07-17")
        self.assertEqual(spx["last_valid_trade_date"], "2026-07-16")
        self.assertIsNone(spx["close"])
        self.assertEqual(len(version["payload"]["style_compass"]), 3)
        self.assertTrue(all(row["direction"] == "数据不足" for row in version["payload"]["style_compass"]))
        self.assertIn("SPX", format_dashboard_version_message(version))

    def test_dashboard_hides_stale_calculation_signals_until_rebuilt(self):
        class Store:
            def __init__(self):
                self.assets = [asset | {"security_id": f"{asset['market']}:{asset['symbol']}", "is_active": True} for asset in ASSETS]
                self.published = None

            def select(self, table, filters=None, **_kwargs):
                if table == "securities":
                    return self.assets
                if table == "asset_daily_signals":
                    return [{"trade_date": "2026-07-17", "calculation_version": "trend-v2.0.0", "investment_advice": "优先新增", "pe_percentile": 10}]
                return []

            def previous_trading_date(self, _market, _value): return datetime(2026, 7, 17).date()
            def history(self, _security_id): return []
            def rpc(self, _function): return None
            def publish_dashboard_version(self, payload, **kwargs):
                self.published = {"payload": payload} | kwargs
                return self.published

        version = DashboardPublisher(Store()).publish()
        self.assertFalse(version["is_complete"])
        first_asset = version["payload"]["assets"][0]
        self.assertEqual(first_asset["data_status"], "delayed")
        self.assertEqual(first_asset["data_issue"], "信号版本待重算")
        self.assertIsNone(first_asset["investment_advice"])
        self.assertTrue(all(item["recommendation"] == "数据不足" for item in version["payload"]["style_compass"]))

    def test_v2_stock_message_includes_dashboard_fields(self):
        message = format_dashboard_version_message(
            {
                "payload": {
                    "latest_market_date": "2026-07-24",
                    "assets": [
                        {
                            "asset_type": "股票", "name": "示例股票", "symbol": "000001", "trade_date": "2026-07-24",
                            "close": 10.2, "daily_return": 0.0123, "short_trend": "短期强势", "mid_trend": "中期上升",
                            "long_trend": "长期上升", "overall_status": "强趋势", "dividend_yield": 5.1,
                        }
                    ],
                }
            }
        )
        self.assertIn("示例股票（000001）｜2026-07-24｜收盘 10.20｜涨幅 +1.23%", message)
        self.assertIn("趋势：短期强势｜中期上升｜长期上升", message)
        self.assertIn("综合：✅强趋势｜股息率：5.10%", message)

    def test_new_secret_key_is_not_sent_as_bearer_jwt(self):
        from codex.trend_observer.supabase_store import SupabaseSettings, SupabaseStore

        new_headers = SupabaseStore(SupabaseSettings("https://example.supabase.co", "sb_secret_example")).headers
        legacy_headers = SupabaseStore(SupabaseSettings("https://example.supabase.co", "legacy.jwt")).headers
        self.assertNotIn("Authorization", new_headers)
        self.assertEqual(legacy_headers["Authorization"], "Bearer legacy.jwt")

    def test_supabase_schema_and_writer_do_not_persist_source_files(self):
        root = Path(__file__).resolve().parents[1]
        migration = (root / "supabase/migrations/20260718091938_trend_observer_core.sql").read_text(encoding="utf-8")
        store = (root / "codex/trend_observer/supabase_store.py").read_text(encoding="utf-8")
        writer = (root / "codex/trend_observer/corporate.py").read_text(encoding="utf-8")
        self.assertIn("ingestion_source_records", migration)
        self.assertNotIn("storage.buckets", migration)
        self.assertNotIn("storage_path", migration)
        self.assertNotIn("upload_raw_evidence", store)
        self.assertNotIn("storage_path", writer)

    def test_dispatch_uses_latest_incomplete_version_for_delay(self):
        class Store:
            def __init__(self):
                self.records = []

            def latest_dashboard_version(self):
                return {"is_complete": False, "completeness": {"missing_asset_signals": [{"symbol": "沪深300"}]}}

            def has_dispatch_key(self, key):
                return False

            def record_dispatch(self, *args, **kwargs):
                self.records.append((args, kwargs))

        store = Store()
        now = datetime(2026, 7, 18, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))  # Saturday
        with patch("codex.trend_observer.dispatch.send_text_message", return_value=200) as send:
            result = dispatch_morning_report(store, now)
        self.assertEqual(result.status, "delayed")
        self.assertIn("沪深300", send.call_args.args[0])

    def test_dispatch_keeps_waiting_before_0930(self):
        class Store:
            def latest_dashboard_version(self): return None

        result = dispatch_morning_report(Store(), datetime(2026, 7, 18, 9, 20, tzinfo=ZoneInfo("Asia/Shanghai")))
        self.assertEqual(result.status, "waiting")
        self.assertIn("09:30", result.message)


if __name__ == "__main__":
    unittest.main()
