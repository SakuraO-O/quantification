import unittest
from datetime import date
import time

import pandas as pd

from codex.trend_observer.assets import active_assets, security_id
from codex.trend_observer.fundamental_sources import FundamentalSource, FundamentalSynchronizer, SourceTimeoutError, _source_timeout


class MemoryStore:
    def __init__(self):
        self.tables = {name: [] for name in ("source_documents", "financial_facts", "dividend_events", "fundamental_assessments")}
        self.watermarks = []
        self.issues = []
        self.items = []

    def upsert(self, table, row, on_conflict):
        rows = row if isinstance(row, list) else [row]
        result = []
        for item in rows:
            item = dict(item)
            if table == "source_documents":
                existing = next((value for value in self.tables[table] if value["source"] == item["source"] and value["source_record_id"] == item["source_record_id"] and value["content_hash"] == item["content_hash"]), None)
                if not existing:
                    item["source_document_id"] = f"doc-{len(self.tables[table]) + 1}"
                    self.tables[table].append(item)
                    existing = item
                result.append(existing)
            elif table == "financial_facts":
                existing = next((value for value in self.tables[table] if value["security_id"] == item["security_id"] and value["report_period"] == item["report_period"] and value["metric_code"] == item["metric_code"] and value["version"] == item["version"]), None)
                if existing: existing.update(item)
                else: self.tables[table].append(item); existing = item
                result.append(existing)
            elif table == "dividend_events":
                existing = next((value for value in self.tables[table] if value["announcement_id"] == item["announcement_id"]), None)
                if existing: existing.update(item)
                else: self.tables[table].append(item); existing = item
                result.append(existing)
            elif table == "fundamental_assessments":
                existing = next((value for value in self.tables[table] if value["security_id"] == item["security_id"] and value["report_period"] == item["report_period"] and value["calculation_version"] == item["calculation_version"]), None)
                if existing: existing.update(item)
                else: self.tables[table].append(item); existing = item
                result.append(existing)
        return result

    def select(self, table, select="*", filters=None, order=None, limit=None):
        rows = list(self.tables.get(table, []))
        for key, value in (filters or {}).items():
            expected = value.removeprefix("eq.")
            rows = [row for row in rows if str(row.get(key)).lower() == expected.lower()]
        if order:
            column, _, direction = order.partition(".")
            rows.sort(key=lambda row: row.get(column), reverse=direction == "desc")
        return rows[:limit] if limit is not None else rows

    def patch(self, table, filters, row):
        for item in self.select(table, filters=filters): item.update(row)
        return []

    def save_watermark(self, row): self.watermarks.append(row)
    def insert(self, table, rows):
        return self.upsert(table, rows, "")
    def get_watermark(self, key):
        return next((row for row in reversed(self.watermarks) if row["dataset_key"] == key), None)
    def record_quality_issue(self, *args): self.issues.append(args)
    def start_run(self, *args): return "run-1"
    def finish_run(self, *args): self.finished = args
    def add_run_item(self, *args, **kwargs): self.items.append((args, kwargs))


def finance_frame(*, bank=False):
    data = [
        {"REPORT_DATE": "2024-12-31", "REPORT_TYPE": "年报", "REPORT_DATE_NAME": "2024年报", "NOTICE_DATE": "2025-03-28",
         "TOTALOPERATEREVE": 100, "PARENTNETPROFIT": 20, "EPSJB": 2, "MGJYXJJE": 3, "FCFF_BACK": 10,
         "ROEJQ": 12, "ZCFZL": 60, "INTEREST_DEBT_RATIO": 35},
        {"REPORT_DATE": "2025-12-31", "REPORT_TYPE": "年报", "REPORT_DATE_NAME": "2025年报", "NOTICE_DATE": "2026-03-28",
         "TOTALOPERATEREVE": 110, "PARENTNETPROFIT": 22, "EPSJB": 2.2, "MGJYXJJE": 3.3, "FCFF_BACK": 12,
         "ROEJQ": 13, "ZCFZL": 59, "INTEREST_DEBT_RATIO": 34},
    ]
    if bank:
        for row in data: row.update({"FIRST_ADEQUACY_RATIO": 12, "NET_INTEREST_MARGIN": 1.8, "NONPERLOAN": 1, "BLDKBBL": 300})
    return pd.DataFrame(data)


def dividend_frame():
    return pd.DataFrame([{"公告日期": "2026-06-01", "派息": 10, "进度": "实施", "除权除息日": "2026-06-10"}])


class FundamentalSourceTest(unittest.TestCase):
    def test_normalizes_facts_and_keeps_only_compact_provenance(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: dividend_frame()))
        result = sync.sync_asset(asset, today=date(2026, 7, 19), force=True)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(store.tables["financial_facts"]), 16)
        self.assertTrue(all("content" not in row and "response" not in row for row in store.tables["source_documents"]))
        event = store.tables["dividend_events"][0]
        self.assertEqual(event["fiscal_year"], 2025)
        self.assertEqual(event["cash_dividend_per_share"], 1)
        self.assertNotIn("document_key", event)
        assessment = store.tables["fundamental_assessments"][0]
        self.assertEqual(assessment["report_period"], date(2025, 12, 31))
        self.assertEqual(assessment["dividend_safety_status"], "稳健")

    def test_bank_uses_capital_metric_and_not_free_cashflow_requirement(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600036")
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(bank=True), lambda _: dividend_frame()))
        result = sync.sync_asset(asset, today=date(2026, 7, 19), force=True)
        self.assertEqual(result.status, "succeeded")
        assessment = store.tables["fundamental_assessments"][0]
        self.assertEqual(assessment["cash_reinvestment_status"], "稳健")
        self.assertEqual(assessment["capital_structure_status"], "稳健")

    def test_one_failure_does_not_stop_other_assets(self):
        good = next(item for item in active_assets() if item["symbol"] == "sh600900")
        bad = next(item for item in active_assets() if item["symbol"] == "sh600036")
        store = MemoryStore()
        def finance(symbol):
            if symbol.startswith("600036"): raise RuntimeError("temporary source failure")
            return finance_frame()
        sync = FundamentalSynchronizer(store, FundamentalSource(finance, lambda _: dividend_frame()))
        results = [sync.sync_asset(asset, today=date(2026, 7, 19), force=True) for asset in (good, bad)]
        self.assertEqual([result.status for result in results], ["succeeded", "failed"])
        self.assertTrue(store.tables["fundamental_assessments"])
        self.assertTrue(store.issues)

    def test_skips_non_disclosure_day_without_network_request(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: self.fail("should not fetch"), lambda _: self.fail("should not fetch")))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 6, 16).replace(day=16), force=False).status, "skipped")

    def test_three_failures_enter_backoff_without_another_request(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        store = MemoryStore()
        calls = []
        def broken(_):
            calls.append(True)
            raise RuntimeError("source down")
        sync = FundamentalSynchronizer(store, FundamentalSource(broken, broken))
        for _ in range(3):
            self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "failed")
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=False).status, "skipped")
        self.assertEqual(len(calls), 3)

    def test_provider_deadline_interrupts_a_stalled_call(self):
        with self.assertRaises(SourceTimeoutError):
            with _source_timeout(0.02):
                time.sleep(0.1)

    def test_nat_dividend_date_is_treated_as_missing_not_a_float_year(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        dividends = pd.DataFrame([{"公告日期": "2026-06-01", "派息": 10, "进度": "预案", "除权除息日": pd.NaT}])
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: dividends))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        event = store.tables["dividend_events"][0]
        self.assertEqual(event["fiscal_year"], 2025)
        self.assertIsNone(event["ex_date"])

    def test_all_failed_run_is_marked_partial_for_cli_to_surface(self):
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(
            lambda _: (_ for _ in ()).throw(RuntimeError("source down")),
            lambda _: pd.DataFrame(),
        ))
        results = sync.sync_assets(active_assets(), today=date(2026, 7, 19), force=True)
        self.assertTrue(results)
        self.assertTrue(all(item.status == "failed" for item in results))
        self.assertEqual(store.finished[1], "partial")


if __name__ == "__main__":
    unittest.main()
