import hashlib
import unittest

from codex.trend_observer.assets import security_id
from codex.trend_observer.quarterly import build_quarterly
from codex.trend_observer.quarterly_evidence import import_quarterly_evidence


def fact(period, metric, value, *, verified=True, version=1):
    return {
        "report_period": period, "metric_code": metric, "value": value,
        "period_type": "annual" if period.endswith("12-31") else "year_to_date",
        "announcement_date": "2026-10-28", "source_document_id": "report",
        "is_current": True, "verification_status": "verified" if verified else "candidate",
        "version": version,
    }


def event(year, plan, stage, per_share, total, *, basis=None):
    return {
        "report_period": f"{year}-06-30", "fiscal_year": year, "plan_key": plan,
        "event_stage": stage, "cash_dividend_per_share": per_share,
        "cash_dividend_total": total, "approval_basis": basis,
        "is_current": True, "verification_status": "verified",
    }


class QuarterlyTest(unittest.TestCase):
    def setUp(self):
        self.docs = [{"source_document_id": "report", "source": "cninfo",
                      "announcement_date": "2026-10-28", "document_url": "https://static.cninfo.com.cn/report.pdf"}]

    def test_quarter_difference_yoy_and_source_pair(self):
        facts = [
            fact("2025-03-31", "net_profit", 100), fact("2025-06-30", "net_profit", 300),
            fact("2025-09-30", "net_profit", 450), fact("2025-12-31", "net_profit", 600),
            fact("2026-03-31", "net_profit", 120), fact("2026-06-30", "net_profit", 320),
            fact("2026-09-30", "net_profit", 500),
            fact("2026-06-30", "roe", 12), fact("2026-09-30", "roe", 15),
            fact("2026-06-30", "asset_liability_ratio", 30),
        ]
        result = build_quarterly(facts, [], self.docs, [])
        self.assertEqual(result["top"]["report_period"], "2026年三季度")
        self.assertAlmostEqual(result["top"]["summary"]["net_profit"]["comparison"]["value"], 500 / 450 * 100 - 100)
        profit = result["metrics"]["net_profit"]["years"][-1]
        self.assertEqual([cell["value"] for cell in profit["quarters"][:3]], [120, 200, 180])
        self.assertEqual(profit["quarters"][1]["minus_source"]["report_period"], "2026-03-31")
        self.assertIsNone(profit["quarters"][3]["value"])
        self.assertEqual(result["metrics"]["roe"]["years"][-1]["quarters"][2]["value"], 15)
        self.assertEqual(result["metrics"]["asset_liability_ratio"]["years"][-1]["quarters"][1]["value"], 30)
        self.assertEqual(result["top"]["payout_year"], 2025)

    def test_missing_prior_and_nonpositive_yoy_are_not_invented(self):
        facts = [
            fact("2025-06-30", "net_profit", 0),
            fact("2026-03-31", "net_profit", -10),
            fact("2026-06-30", "net_profit", 20),
        ]
        result = build_quarterly(facts, [], self.docs, [])
        current = result["metrics"]["net_profit"]["years"][-1]
        self.assertEqual(current["quarters"][1]["value"], 30)
        self.assertEqual(current["comparison"]["status"], "—")
        self.assertEqual(result["top"]["summary"]["net_profit"]["comparison"]["status"], "—")
        without_q1 = build_quarterly([facts[0], facts[2]], [], self.docs, [])
        self.assertEqual(without_q1["metrics"]["net_profit"]["years"][-1]["quarters"][1]["reason"], "缺少上一累计报告")
        candidate = build_quarterly([fact("2026-06-30", "net_profit", 20, verified=False)], [], self.docs, [])
        self.assertIsNone(candidate["top"]["summary"]["net_profit"]["value"])
        self.assertEqual(candidate["metrics"]["net_profit"]["years"][-1]["quarters"][1]["status"], "数据不足")

    def test_year_filter_requires_listing_date_and_preserves_prelisting_disclosures(self):
        facts = [fact("2021-12-31", "net_profit", 100), fact("2026-06-30", "net_profit", 200)]
        listed = build_quarterly(facts, [], self.docs, [], listing_date="2022-04-21")
        self.assertEqual(listed["years"], [2021, 2022, 2023, 2024, 2025, 2026])
        self.assertEqual(listed["metrics"]["net_profit"]["years"][-1]["quarters"][3]["status"], "未披露")
        self.assertEqual(listed["metrics"]["roe"]["years"][-1]["quarters"][1]["reason"], "报告未提供该指标")
        unknown = build_quarterly(facts, [], self.docs, [])
        self.assertEqual(len(unknown["years"]), 10)

    def test_report_document_preserves_year_even_when_selected_metrics_are_missing(self):
        documents = self.docs + [{"source_document_id":"legacy-report", "report_period":"2018-06-30",
                                  "document_type":"structured_financial_snapshot"}]
        result = build_quarterly([fact("2026-03-31", "net_profit", 100)], [], documents, [], listing_date="2022-04-21")
        self.assertIn(2018, result["years"])
        self.assertNotIn(2017, result["years"])
        year = result["metrics"]["net_profit"]["years"][result["years"].index(2018)]
        self.assertEqual(year["quarters"][1]["status"], "数据不足")
        self.assertEqual(year["quarters"][1]["reason"], "报告未提供该指标")

    def test_approved_amount_and_complete_year_payout(self):
        facts = [fact("2025-12-31", "net_profit", 1000),
                 fact("2026-03-31", "net_profit", 100)]
        events = [
            event(2025, "interim", "implemented", "0.2", "200"),
            event(2025, "annual", "approved", "0.3", "300", basis="shareholder_authorized_board"),
        ]
        reviews = [{"fiscal_year": 2025, "status": "complete", "source_document_id": "report"}]
        result = build_quarterly(facts, events, self.docs, reviews)
        year = result["dividends"]["2025"]
        self.assertEqual(year["per_share"], 0.5)
        self.assertTrue(year["unimplemented"])
        self.assertEqual(year["payout_ratio"], 50)
        self.assertEqual(result["top"]["dividend_year"], 2026)
        self.assertEqual(result["top"]["payout_year"], 2025)
        self.assertEqual(result["top"]["payout"]["payout_ratio"], 50)
        events.append(event(2025, "extra", "proposal", "0.1", "100"))
        pending = build_quarterly(facts, events, self.docs, reviews)["dividends"]["2025"]
        self.assertEqual(pending["per_share"], 0.5)
        self.assertIsNone(pending["payout_ratio"])
        self.assertEqual(pending["status"], "方案待批准")
        events[-1]["verification_status"] = "candidate"
        candidate = build_quarterly(facts, events, self.docs, [])["dividends"]["2025"]
        self.assertEqual(candidate["status"], "待核对")
        reviewed_candidate = build_quarterly(facts, events, self.docs, reviews)["dividends"]["2025"]
        self.assertEqual(reviewed_candidate["status"], "待核对")
        self.assertIsNone(reviewed_candidate["payout_ratio"])

    def test_zero_requires_explicit_completed_review(self):
        facts = [fact("2025-12-31", "net_profit", 100)]
        pending = build_quarterly(facts, [], self.docs, [])["dividends"]["2025"]
        self.assertIsNone(pending["per_share"])
        self.assertIsNone(pending["payout_ratio"])
        review = [{"fiscal_year": 2025, "status": "complete", "source_document_id": "report"}]
        complete = build_quarterly(facts, [], self.docs, review)["dividends"]["2025"]
        self.assertEqual(complete["per_share"], 0)
        self.assertEqual(complete["payout_ratio"], 0)

    def test_new_unverified_annual_report_switches_year_without_inventing_ratio(self):
        facts = [fact("2025-12-31", "net_profit", 100),
                 fact("2026-12-31", "net_profit", 200, verified=False)]
        reviews = [{"fiscal_year": 2025, "status": "complete", "source_document_id": "report"}]
        result = build_quarterly(facts, [], self.docs, reviews)
        self.assertEqual(result["top"]["payout_year"], 2026)
        self.assertIsNone(result["top"]["payout"]["payout_ratio"])
        self.assertEqual(result["top"]["summary"]["net_profit"]["value"], None)

    def test_manifest_rejects_unofficial_or_changed_document_before_writes(self):
        class Store:
            def upsert(self, *_args, **_kwargs):
                raise AssertionError("invalid manifest must not write")
        payload = b"formal report"
        document = {
            "key": "fy", "url": "https://static.cninfo.com.cn/file.pdf",
            "sha256": hashlib.sha256(payload).hexdigest(), "announcement_id": "AN1",
            "title": "年报", "document_type": "annual_report",
            "report_period": "2025-12-31", "announcement_date": "2026-03-20",
        }
        manifest = {"symbol": "sz000807", "market": "CN", "reviewed_by": "reviewer", "documents": [document]}
        with self.assertRaisesRegex(ValueError, "哈希不符"):
            import_quarterly_evidence(Store(), manifest, fetch_document=lambda _url: b"changed")
        document["url"] = "https://not-official.example/file.pdf"
        with self.assertRaisesRegex(ValueError, "非正式公告"):
            import_quarterly_evidence(Store(), manifest, fetch_document=lambda _url: payload)

    def test_manifest_rejects_unknown_candidate_before_writing(self):
        class Store:
            def select(self, *_args, **_kwargs):
                return []

            def upsert(self, *_args, **_kwargs):
                raise AssertionError("candidate validation must precede writes")

        raw = b"approved plan"
        manifest = {
            "symbol": "sz000807", "market": "CN", "reviewed_by": "reviewer",
            "documents": [{"key": "approval", "url": "https://static.cninfo.com.cn/approval.pdf",
                           "sha256": hashlib.sha256(raw).hexdigest(), "announcement_id": "A1",
                           "title": "approved plan", "document_type": "resolution",
                           "report_period": "2025-12-31", "announcement_date": "2026-03-20"}],
            "dividends": [{"plan_key": "2025-annual", "fiscal_year": 2025,
                           "report_period": "2025-12-31", "event_stage": "approved",
                           "cash_dividend_per_share": "0.5", "cash_dividend_total": "500",
                           "approval_basis": "shareholder_resolution",
                           "supersedes_candidates": ["missing"],
                           "documents": [{"key": "approval", "role": "approval"}]}],
        }
        with self.assertRaisesRegex(ValueError, "待关联候选分红不存在"):
            import_quarterly_evidence(Store(), manifest, fetch_document=lambda _url: raw)

    def test_reviewed_plan_keeps_one_current_version_across_stages(self):
        class Store:
            def __init__(self):
                self.tables = {name: [] for name in ("source_documents", "financial_facts", "dividend_events",
                                                     "dividend_event_sources", "dividend_year_reviews")}

            def select(self, table, select="*", filters=None, order=None, limit=None):
                rows = self.tables[table]
                for key, condition in (filters or {}).items():
                    rows = [row for row in rows if str(row.get(key)).lower() == condition[3:].lower()]
                return rows[:limit] if limit else rows

            def insert(self, table, rows):
                rows = rows if isinstance(rows, list) else [rows]
                result = []
                for row in rows:
                    value = dict(row)
                    if table == "financial_facts":
                        value["financial_fact_id"] = f"fact-{len(self.tables[table]) + 1}"
                    if table == "dividend_events":
                        value["dividend_event_id"] = f"event-{len(self.tables[table]) + 1}"
                    self.tables[table].append(value)
                    result.append(value)
                return result

            def upsert(self, table, rows, _key):
                rows = rows if isinstance(rows, list) else [rows]
                result = []
                for row in rows:
                    if table == "source_documents":
                        prior = next((item for item in self.tables[table]
                                      if item["source_record_id"] == row["source_record_id"]), None)
                        if prior:
                            result.append(prior)
                            continue
                        row = dict(row, source_document_id=f"doc-{len(self.tables[table]) + 1}")
                    if table == "dividend_year_reviews":
                        self.tables[table] = [old for old in self.tables[table]
                                              if old["fiscal_year"] != row["fiscal_year"]]
                    self.tables[table].append(dict(row))
                    result.append(row)
                return result

            def patch(self, table, filters, row):
                for item in self.select(table, filters=filters):
                    item.update(row)
                return []

        raw = b"reviewed announcement"
        digest = hashlib.sha256(raw).hexdigest()
        def document(key, role):
            return {"key": key, "url": f"https://static.cninfo.com.cn/{key}.pdf", "sha256": digest,
                    "announcement_id": key, "title": key, "document_type": role,
                    "report_period": "2025-12-31", "announcement_date": "2026-03-20"}
        base = {"symbol": "sz000807", "market": "CN", "reviewed_by": "reviewer",
                "documents": [document("report", "annual_report"), document("approval", "resolution"),
                              document("implementation", "implementation")],
                "facts": [{"report_period": "2025-12-31", "metric_code": "net_profit",
                           "value": "1000.00", "period_type": "annual", "document": "report"}],
                "year_reviews": [{"fiscal_year": 2025, "status": "complete", "document": "report"}]}
        def plan(stage, key):
            return {"plan_key": "2025-annual", "fiscal_year": 2025, "report_period": "2025-12-31",
                    "event_stage": stage, "cash_dividend_per_share": "0.50", "cash_dividend_total": "500",
                    "approval_basis": "shareholder_resolution",
                    **({"ex_date": "2026-06-20", "bonus_shares_per_share": "0",
                        "transfer_shares_per_share": "0"} if stage == "implemented" else {}),
                    "supersedes_candidates": ["candidate-2025"],
                    "documents": [{"key": key, "role": "approval" if stage == "approved" else "implementation"}]}
        store = Store()
        store.tables["dividend_events"].append({
            "dividend_event_id": "candidate-1", "security_id": security_id("sz000807", "CN"),
            "announcement_id": "candidate-2025", "fiscal_year": 2025,
            "verification_status": "candidate", "is_current": True,
        })
        approved = dict(base, dividends=[plan("approved", "approval")])
        self.assertEqual(import_quarterly_evidence(store, approved, fetch_document=lambda _url: raw)["dividends"], 1)
        self.assertEqual(import_quarterly_evidence(store, approved, fetch_document=lambda _url: raw)["dividends"], 0)
        implemented = dict(base, dividends=[plan("implemented", "implementation")])
        self.assertEqual(import_quarterly_evidence(store, implemented, fetch_document=lambda _url: raw)["dividends"], 1)
        self.assertEqual([row["version"] for row in store.tables["dividend_events"] if row["verification_status"] == "verified"], [1, 2])
        self.assertEqual(store.tables["dividend_events"][0]["superseded_by_plan_key"], "2025-annual")
        self.assertFalse(store.tables["dividend_events"][0]["is_current"])
        current = [row for row in store.tables["dividend_events"] if row["is_current"]]
        self.assertEqual(len(current), 1)
        result = build_quarterly(store.tables["financial_facts"], store.tables["dividend_events"],
                                 store.tables["source_documents"], store.tables["dividend_year_reviews"])
        self.assertEqual(result["dividends"]["2025"]["payout_ratio"], 50)


if __name__ == "__main__":
    unittest.main()
