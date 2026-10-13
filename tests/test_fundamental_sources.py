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
            if value == "not.is.null":
                rows = [row for row in rows if row.get(key) is not None]
                continue
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
    return pd.DataFrame([{"报告期": "2025-12-31", "最新公告日期": "2026-06-01",
                          "现金分红-现金分红比例": 10, "送转股份-送股比例": 0,
                          "送转股份-转股比例": 0, "方案进度": "实施分配",
                          "除权除息日": "2026-06-10"}])


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
        self.assertEqual(event["report_period"], date(2025, 12, 31))
        self.assertEqual(event["cash_dividend_per_share"], 1)
        self.assertEqual(event["bonus_shares_per_share"], 0)
        self.assertEqual(event["transfer_shares_per_share"], 0)
        self.assertNotIn("document_key", event)
        assessment = store.tables["fundamental_assessments"][0]
        self.assertEqual(assessment["report_period"], date(2025, 12, 31))
        self.assertEqual(assessment["dividend_safety_status"], "稳健")

    def test_existing_dividend_is_backfilled_with_share_distribution_ratios(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        store = MemoryStore()
        initial = dividend_frame()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: initial))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        revised = dividend_frame()
        revised.loc[0, "送转股份-送股比例"] = 2
        revised.loc[0, "送转股份-转股比例"] = 1
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: revised))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        self.assertEqual(len(store.tables["dividend_events"]), 1)
        event = store.tables["dividend_events"][0]
        self.assertAlmostEqual(event["bonus_shares_per_share"], .2)
        self.assertAlmostEqual(event["transfer_shares_per_share"], .1)

    def test_revised_ex_date_retires_previous_event(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        store = MemoryStore()
        original = dividend_frame()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: original))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        revised = dividend_frame()
        revised.loc[0, "除权除息日"] = "2026-06-11"
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: revised))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        events = store.tables["dividend_events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(sum(row["is_current"] for row in events), 1)
        self.assertEqual(next(row["ex_date"] for row in events if row["is_current"]), date(2026, 6, 11))

    def test_short_source_response_keeps_existing_event_current(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        store = MemoryStore()
        original = dividend_frame()
        second = original.copy()
        second.loc[0, "报告期"] = "2025-06-30"
        second.loc[0, "除权除息日"] = "2025-09-10"
        complete = pd.concat([original, second], ignore_index=True)
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: complete))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: original))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "failed")
        self.assertEqual(sum(row["is_current"] for row in store.tables["dividend_events"]), 2)

    def test_duplicate_source_identity_fails_without_writing_events(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        duplicated = pd.concat([dividend_frame(), dividend_frame()], ignore_index=True)
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: duplicated))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "failed")
        self.assertEqual(store.tables["dividend_events"], [])

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
        dividends = dividend_frame()
        dividends.loc[0, "方案进度"] = "预案"
        dividends.loc[0, "除权除息日"] = pd.NaT
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: dividends))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        event = store.tables["dividend_events"][0]
        self.assertEqual(event["fiscal_year"], 2025)
        self.assertIsNone(event["ex_date"])

    def test_interim_report_year_does_not_follow_ex_date_year(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        dividends = dividend_frame()
        interim = dividends.iloc[0].copy()
        interim["报告期"] = "2025-06-30"
        interim["最新公告日期"] = "2025-09-01"
        interim["除权除息日"] = "2025-09-10"
        interim["现金分红-现金分红比例"] = 5
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(
            lambda _: finance_frame(), lambda _: pd.concat([dividends, interim.to_frame().T], ignore_index=True)))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        self.assertEqual(len(store.tables["dividend_events"]), 2)
        self.assertEqual({row["fiscal_year"] for row in store.tables["dividend_events"]}, {2025})

    def test_missing_report_period_fails_instead_of_guessing_from_ex_date(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        dividends = dividend_frame()
        dividends.loc[0, "报告期"] = pd.NaT
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: dividends))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "failed")
        self.assertEqual(store.tables["dividend_events"], [])

    def test_unimplemented_plan_is_not_treated_as_paid(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        dividends = dividend_frame()
        dividends.loc[0, "方案进度"] = "尚未实施"
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: dividends))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "succeeded")
        self.assertEqual(store.tables["dividend_events"][0]["event_stage"], "proposal")

    def test_implemented_event_without_amount_fails(self):
        asset = next(item for item in active_assets() if item["symbol"] == "sh600900")
        dividends = dividend_frame()
        for column in ("现金分红-现金分红比例", "送转股份-送股比例", "送转股份-转股比例"):
            dividends.loc[0, column] = float("nan")
        store = MemoryStore()
        sync = FundamentalSynchronizer(store, FundamentalSource(lambda _: finance_frame(), lambda _: dividends))
        self.assertEqual(sync.sync_asset(asset, today=date(2026, 7, 19), force=True).status, "failed")
        self.assertEqual(store.tables["dividend_events"], [])

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
