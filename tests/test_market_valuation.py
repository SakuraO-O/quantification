import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from codex.trend_observer.market_valuation import (
    CHINABOND_URL,
    CHINEXT_PE_URL,
    DIVIDEND_LOW_VOLATILITY_URL,
    HS300_DIVIDEND_URL,
    HS300_EQUITY_BOND_SPREAD_URL,
    HS300_PE_URL,
    NASDAQ_PE_URL,
    SP500_PE_URL,
    collect_market_valuation_snapshot,
    parse_baifenwei,
    parse_chinabond,
    parse_lixinger_dividend,
    parse_lixinger_pe,
    parse_worldperatio,
    write_market_valuation_snapshot,
)


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def worldperatio_html(months=120):
    points = []
    year, month = 2016, 8
    for index in range(months):
        value = 40.0 if index == months - 1 else 20.0
        points.append(f"[Date.UTC({year}, {month - 1}, 1),{value:.2f}]")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return fixture("worldperatio.html").replace("SERIES", ",".join(points))


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeSession:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **_kwargs):
        page = self.pages[url]
        return page if isinstance(page, FakeResponse) else FakeResponse(page)


class MarketValuationTest(unittest.TestCase):
    def test_chinabond_uses_named_ten_year_column(self):
        value, value_date = parse_chinabond(fixture("chinabond.html"))
        self.assertEqual(value, 1.739)
        self.assertEqual(value_date, "2026-07-03")

    def test_lixinger_requires_explicit_ten_year_percentile(self):
        value, percentile, value_date = parse_lixinger_pe(fixture("lixinger_pe.html"), "沪深300")
        self.assertEqual((value, percentile, value_date), (14.32, 85.0, "2026-07-03"))

        default_range = fixture("lixinger_pe.html").replace('data-range="10y"', "")
        _, percentile, _ = parse_lixinger_pe(default_range, "沪深300")
        self.assertIsNone(percentile)

    def test_lixinger_dividend_uses_dynamic_current_value(self):
        value, value_date = parse_lixinger_dividend(fixture("lixinger_dividend.html"), "沪深300")
        self.assertEqual(value, 2.73)
        self.assertEqual(value_date, "2026-07-03")

    def test_worldperatio_requires_120_consecutive_months(self):
        result = parse_worldperatio(worldperatio_html())
        self.assertEqual(result["pe"], 32.74)
        self.assertEqual(result["date"], "2026-07-02")
        self.assertEqual(result["history_start"], "2016-08-01")
        self.assertEqual(result["history_end"], "2026-07-02")
        self.assertEqual(result["history_count"], 120)
        self.assertEqual(result["percentile_10y"], 100.0)
        self.assertEqual(result["history"][-1], {"date": "2026-07-02", "value": 32.74})

        with self.assertRaisesRegex(ValueError, "不足120个月"):
            parse_worldperatio(worldperatio_html(119))

    def test_worldperatio_accepts_sp500_heading(self):
        sp500_html = worldperatio_html().replace("Nasdaq 100", "S&P 500")
        result = parse_worldperatio(sp500_html, index_name="标普500", index_pattern=r"S\s*&\s*P\s*500")
        self.assertEqual(result["pe"], 32.74)
        self.assertEqual(result["history_count"], 120)
        self.assertEqual(SP500_PE_URL, "https://worldperatio.com/index/sp-500/")

    def test_worldperatio_rejects_a_page_for_another_index(self):
        with self.assertRaisesRegex(ValueError, "与预期指数不匹配"):
            parse_worldperatio(worldperatio_html(), index_name="标普500", index_pattern=r"S\s*&\s*P\s*500")

    def test_baifenwei_reads_published_ten_year_column(self):
        result = parse_baifenwei(fixture("baifenwei.html"))
        self.assertEqual(result, (5.28, 55.3, 14.24, "2026-07-02"))

    @patch("codex.trend_observer.market_valuation.time.sleep", return_value=None)
    def test_collection_isolates_source_failures_and_preserves_metadata(self, _sleep):
        hs300_pe = fixture("lixinger_pe.html")
        chinext_pe = hs300_pe.replace("沪深300(000300)", "创业板指(399006)").replace("14.32", "51.19").replace("85.0", "70.7")
        low_vol = fixture("lixinger_dividend.html").replace("沪深300", "红利低波").replace("2.73", "4.77")
        pages = {
            CHINABOND_URL: fixture("chinabond.html"),
            HS300_PE_URL: hs300_pe,
            HS300_DIVIDEND_URL: fixture("lixinger_dividend.html"),
            CHINEXT_PE_URL: FakeResponse("blocked", 429),
            NASDAQ_PE_URL: worldperatio_html(),
            DIVIDEND_LOW_VOLATILITY_URL: low_vol,
            HS300_EQUITY_BOND_SPREAD_URL: fixture("baifenwei.html"),
        }
        now = datetime(2026, 7, 4, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        snapshot = collect_market_valuation_snapshot(FakeSession(pages), now=now)

        self.assertEqual(snapshot["schema_version"], "1.0")
        self.assertEqual(snapshot["generated_at"], "2026-07-04T09:30:00+08:00")
        self.assertEqual(len(snapshot["indicators"]), 7)
        self.assertEqual(snapshot["indicators"]["china_10y_bond"]["source"], "ChinaBond")
        self.assertEqual(snapshot["indicators"]["hs300_valuation"]["hs300_pe_ttm"], 14.32)
        failed = snapshot["indicators"]["chinext_100_valuation"]
        self.assertIsNone(failed["chinext_100_pe_ttm"])
        self.assertIn("HTTP 429", failed["error"])
        spread = snapshot["indicators"]["hs300_equity_bond_spread"]
        self.assertEqual(spread["hs300_equity_bond_spread_pe_ttm"], 14.24)
        self.assertNotIn("hs300_pe_ttm", spread)

    def test_snapshot_write_is_valid_json(self):
        payload = {"schema_version": "1.0", "generated_at": "2026-07-04T09:30:00+08:00", "indicators": {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            write_market_valuation_snapshot(payload, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
