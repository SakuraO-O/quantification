import unittest
from datetime import date

from codex.trend_observer.dashboard_versions import DashboardPublisher
from codex.trend_observer.dividends import annual_dividend_summary
from codex.trend_observer.ingestion import CALCULATION_VERSION


class DividendYieldV2Test(unittest.TestCase):
    def test_sums_interim_quarterly_and_annual_from_report_year(self):
        events = [
            {"fiscal_year": 2025, "report_period": "2025-03-31", "event_stage": "implemented",
             "announcement_id": "q1", "ex_date": "2025-05-10", "cash_dividend_per_share": 0.2},
            {"fiscal_year": 2025, "report_period": "2025-06-30", "event_stage": "implemented",
             "announcement_id": "interim", "ex_date": "2025-09-10", "cash_dividend_per_share": 0.3},
            {"fiscal_year": 2025, "report_period": "2025-12-31", "event_stage": "implemented",
             "announcement_id": "annual", "ex_date": "2026-06-10", "cash_dividend_per_share": 0.5},
            {"fiscal_year": 2024, "report_period": "2024-12-31", "event_stage": "implemented",
             "announcement_id": "older", "ex_date": "2025-06-10", "cash_dividend_per_share": 10},
        ]
        result = annual_dividend_summary(events, "2026-10-12", 50)
        self.assertEqual(result["dividend_fiscal_year"], 2025)
        self.assertEqual(result["last_year_dividend"], 1)
        self.assertEqual(result["dividend_yield"], 2)
        self.assertEqual(result["dividend_yield_status"], "已计算")

    def test_ignores_proposals_future_events_and_unverified_legacy_years(self):
        events = [
            {"fiscal_year": 2025, "report_period": "2025-12-31", "event_stage": "proposal",
             "announcement_id": "pending", "ex_date": "2026-05-01", "cash_dividend_per_share": 1},
            {"fiscal_year": 2025, "report_period": "2025-12-31", "event_stage": "implemented",
             "announcement_id": "future", "ex_date": "2026-11-01", "cash_dividend_per_share": 1},
            {"fiscal_year": 2025, "report_period": None, "event_stage": "implemented",
             "announcement_id": "legacy", "ex_date": "2026-05-01", "cash_dividend_per_share": 1},
        ]
        result = annual_dividend_summary(events, "2026-10-12", 50)
        self.assertIsNone(result["last_year_dividend"])
        self.assertIsNone(result["dividend_yield"])
        self.assertEqual(result["dividend_yield_status"], "数据不足")

    def test_approved_plan_is_counted_once_and_respects_approval_and_correction_dates(self):
        base = {"report_period": "2025-12-31", "plan_key": "annual", "version": 1,
                "announcement_id": "approved", "announcement_date": "2026-05-01", "approval_date": "2026-05-01",
                "event_stage": "approved", "cash_dividend_per_share": 0.5,
                "approval_basis": "shareholder_resolution", "verification_status": "verified", "is_current": False}
        implementation = dict(base, version=2, event_stage="implemented", announcement_id="implementation",
                              announcement_date="2026-06-01", ex_date="2026-06-10", is_current=True)
        self.assertIsNone(annual_dividend_summary([base, implementation], "2026-04-30", 10)["dividend_yield"])
        approved = annual_dividend_summary([base, implementation], "2026-05-10", 10)
        self.assertEqual(approved["last_year_dividend"], 0.5)
        self.assertEqual(approved["approved_dividend_per_share"], 0.5)
        self.assertEqual(approved["implemented_dividend_per_share"], 0)
        done = annual_dividend_summary([base, implementation], "2026-06-11", 10)
        self.assertEqual(done["last_year_dividend"], 0.5)
        self.assertEqual(done["implemented_dividend_per_share"], 0.5)
        correction = dict(implementation, version=3, cash_dividend_per_share=0.6, announcement_date="2026-07-01")
        self.assertEqual(annual_dividend_summary([base, implementation, correction], "2026-06-11", 10)["last_year_dividend"], 0.5)
        self.assertEqual(annual_dividend_summary([base, implementation, correction], "2026-07-02", 10)["last_year_dividend"], 0.6)

    def test_publisher_replaces_legacy_signal_yield_with_report_year_sum(self):
        class Store:
            def select(self, table, **_kwargs):
                if table == "securities":
                    return [{"security_id": "stock-1", "symbol": "sh601318", "name": "中国平安",
                             "market": "CN", "asset_type": "股票", "is_active": True}]
                if table == "asset_daily_signals":
                    return [{"trade_date": "2026-10-12", "close": 50,
                             "dividend_yield": 99, "calculation_version": CALCULATION_VERSION}]
                if table == "dividend_events":
                    return [{"fiscal_year": 2025, "report_period": "2025-06-30",
                             "event_stage": "implemented", "announcement_id": "interim",
                             "ex_date": "2025-09-10", "cash_dividend_per_share": 0.5},
                            {"fiscal_year": 2025, "report_period": "2025-12-31",
                             "event_stage": "implemented", "announcement_id": "final",
                             "ex_date": "2026-06-10", "cash_dividend_per_share": 1}]
                return []

            def previous_trading_date(self, _market, _today): return date(2026, 10, 12)
            def get_watermark(self, _key):
                return {"status": "normal", "last_success_at": "2026-10-12T19:00:00+08:00"}
            def history(self, _sid): return []
            def rpc(self, _function): return None
            def publish_dashboard_version(self, payload, **kwargs):
                return {"payload": payload} | kwargs

        asset = DashboardPublisher(Store()).publish()["payload"]["assets"][0]
        self.assertEqual(asset["last_year_dividend"], 1.5)
        self.assertEqual(asset["dividend_yield"], 3)
        self.assertEqual(asset["dividend_fiscal_year"], 2025)


if __name__ == "__main__":
    unittest.main()
