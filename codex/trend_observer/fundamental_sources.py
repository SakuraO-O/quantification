"""Public, structured fundamentals ingestion for the nine tracked stocks.

The adapter stores normalized facts and compact evidence metadata only.  It
never saves a report PDF, HTML page, or provider response body.  A failed
security is isolated: its last confirmed facts stay available and the failure
is recorded in the per-security watermark.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
import signal
import threading
from typing import Any, Callable

from .assets import security_id
from .fundamentals import assess_high_dividend_fundamentals
from .ingestion import SyncResult
from .supabase_store import SupabaseStore, payload_hash


FINANCE_URL = "https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code={market}{symbol}&color=b#/cwfx"
DIVIDEND_URL = "https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/{symbol}.phtml"

FINANCE_FIELDS = {
    "TOTALOPERATEREVE": ("revenue", "CNY"),
    "PARENTNETPROFIT": ("net_profit", "CNY"),
    "EPSJB": ("earnings_per_share", "CNY/share"),
    "MGJYXJJE": ("operating_cashflow_per_share", "CNY/share"),
    "FCFF_BACK": ("free_cashflow", "CNY"),
    "ROEJQ": ("roe", "percent"),
    "ZCFZL": ("asset_liability_ratio", "percent"),
    "INTEREST_DEBT_RATIO": ("interest_debt_ratio", "percent"),
    "FIRST_ADEQUACY_RATIO": ("capital_ratio", "percent"),
    "NET_INTEREST_MARGIN": ("net_interest_margin", "percent"),
    "NONPERLOAN": ("non_performing_loan_ratio", "percent"),
    "BLDKBBL": ("provision_coverage", "percent"),
}
SOURCE_TIMEOUT_SECONDS = 20
MAX_ANNUAL_REPORTS = 5
MAX_INTERIM_REPORTS = 4


class SourceTimeoutError(TimeoutError):
    """Raised when a public provider keeps a GitHub Actions runner waiting."""


class _source_timeout:
    """Unix main-thread deadline; GitHub Actions runs on Linux.

    AKShare delegates to providers which do not consistently expose a timeout
    argument.  A signal deadline therefore protects the whole call boundary.
    """

    def __init__(self, seconds: int):
        self.seconds = seconds
        self.previous_handler = None

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
            return self
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(SourceTimeoutError(f"公开来源超过 {self.seconds} 秒未响应")))
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, *_):
        if self.previous_handler is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous_handler)


def _date(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if hasattr(value, "date"):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) or math.isinf(result) else result


def _market_suffix(asset: dict) -> str:
    return "SH" if asset["symbol"].startswith("sh") else "SZ"


def _stock_code(asset: dict) -> str:
    return asset["symbol"][-6:]


def _window_is_due(today: date) -> bool:
    """Daily during disclosure windows, otherwise once a week to save quota."""

    return today.month in {1, 2, 3, 4, 7, 8, 10} or today.weekday() == 0


@dataclass(frozen=True)
class FundamentalSource:
    """Small injectable boundary around AKShare's public providers."""

    finance: Callable[[str], Any]
    dividends: Callable[[str], Any]

    @classmethod
    def public(cls) -> "FundamentalSource":
        try:
            import akshare as ak
        except ImportError as error:  # pragma: no cover - CI installs requirements
            raise RuntimeError("缺少 akshare；请安装 codex/requirements.txt。") from error
        def finance(symbol: str):
            with _source_timeout(SOURCE_TIMEOUT_SECONDS):
                return ak.stock_financial_analysis_indicator_em(symbol=symbol, indicator="按报告期")
        def dividends(symbol: str):
            with _source_timeout(SOURCE_TIMEOUT_SECONDS):
                return ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        return cls(
            finance=finance,
            dividends=dividends,
        )


class FundamentalSynchronizer:
    """Fetch financial facts and dividend events without coupling assets together."""

    calculation_version = "fundamentals-v1"

    def __init__(self, store: SupabaseStore, source: FundamentalSource | None = None):
        self.store = store
        self.source = source or FundamentalSource.public()

    @staticmethod
    def dataset_key(asset: dict) -> str:
        return f"fundamentals:CN:{asset['symbol']}:eastmoney-sina"

    def _source_document(self, asset: dict, *, source: str, record_id: str, title: str, document_type: str,
                         report_period: date | None, announcement_date: date | None, url: str, evidence: dict) -> str:
        rows = self.store.upsert(
            "source_documents",
            {
                "security_id": security_id(asset["symbol"], asset["market"]),
                "source": source,
                "source_record_id": record_id,
                "title": title,
                "document_type": document_type,
                "report_period": report_period,
                "announcement_date": announcement_date,
                "document_url": url,
                "content_hash": payload_hash(evidence),
            },
            "source,source_record_id,content_hash",
        )
        return rows[0]["source_document_id"]

    def _write_finance(self, asset: dict, frame: Any) -> tuple[int, int]:
        """Persist a compact research window in batches, not one REST call per metric."""

        records = frame.to_dict("records")
        annual = [row for row in records if "年报" in str(row.get("REPORT_TYPE") or "")]
        interim = [row for row in records if "年报" not in str(row.get("REPORT_TYPE") or "")]
        newest_first = lambda row: _date(row.get("REPORT_DATE")) or date.min
        selected = sorted(annual, key=newest_first, reverse=True)[:MAX_ANNUAL_REPORTS]
        selected += sorted(interim, key=newest_first, reverse=True)[:MAX_INTERIM_REPORTS]
        sid = security_id(asset["symbol"], asset["market"])
        documents: list[dict] = []
        document_keys: list[tuple[str, str]] = []
        candidates: list[dict] = []
        for raw in selected:
            report_period = _date(raw.get("REPORT_DATE"))
            if not report_period:
                continue
            report_type = str(raw.get("REPORT_TYPE") or "")
            period_type = "annual" if "年报" in report_type else "year_to_date"
            notice_date = _date(raw.get("NOTICE_DATE")) or _date(raw.get("UPDATE_DATE"))
            code = _stock_code(asset)
            record_id = f"{code}:{report_period.isoformat()}:{notice_date or 'unknown'}"
            evidence = {"code": code, "report_period": report_period, "notice_date": notice_date,
                        "report_type": report_type, "values": {field: _number(raw.get(field)) for field in FINANCE_FIELDS}}
            content_hash = payload_hash(evidence)
            documents.append({"security_id": sid, "source": "eastmoney", "source_record_id": record_id,
                "title": f"东方财富财务分析：{asset['name']} {raw.get('REPORT_DATE_NAME') or report_period}",
                "document_type": "structured_financial_snapshot", "report_period": report_period,
                "announcement_date": notice_date, "document_url": FINANCE_URL.format(market=_market_suffix(asset), symbol=code),
                "content_hash": content_hash})
            document_keys.append((record_id, content_hash))
            for field, (metric_code, unit) in FINANCE_FIELDS.items():
                value = _number(raw.get(field))
                if value is None:
                    continue
                candidates.append({"report_period": report_period, "metric_code": metric_code, "value": value,
                    "unit": unit, "period_type": period_type, "announcement_date": notice_date,
                    "document_key": (record_id, content_hash)})
        saved_documents = self.store.upsert("source_documents", documents, "source,source_record_id,content_hash")
        document_ids = {(row["source_record_id"], row["content_hash"]): row["source_document_id"] for row in saved_documents}
        current_rows = self.store.select("financial_facts", select="financial_fact_id,report_period,metric_code,value,version,source_document_id",
            filters={"security_id": f"eq.{sid}", "is_current": "eq.true"})
        current = {(str(row["report_period"]), row["metric_code"]): row for row in current_rows}
        changed_rows = []
        for item in candidates:
            source_document_id = document_ids[item.pop("document_key")]
            identity = (item["report_period"].isoformat(), item["metric_code"])
            prior = current.get(identity)
            if prior and _number(prior.get("value")) == item["value"] and prior.get("source_document_id") == source_document_id:
                continue
            if prior:
                self.store.patch("financial_facts", {"financial_fact_id": f"eq.{prior['financial_fact_id']}"}, {"is_current": False})
            changed_rows.append({"security_id": sid, **item, "source_document_id": source_document_id,
                "version": int(prior["version"]) + 1 if prior else 1, "is_current": True})
        self.store.upsert("financial_facts", changed_rows, "security_id,report_period,metric_code,version")
        return len(candidates), len(changed_rows)

    def _write_dividends(self, asset: dict, frame: Any) -> tuple[int, int]:
        sid = security_id(asset["symbol"], asset["market"])
        documents: list[dict] = []
        candidates: list[dict] = []
        for raw in frame.to_dict("records"):
            announcement_date = _date(raw.get("公告日期"))
            ex_date = _date(raw.get("除权除息日"))
            cash_per_ten = _number(raw.get("派息"))
            stage_name = str(raw.get("进度") or "")
            stage = "implemented" if "实施" in stage_name else "proposal"
            if announcement_date is None or cash_per_ten is None:
                continue
            # In Chinese A-share disclosures, a dividend implemented in year N
            # normally belongs to fiscal year N-1.  The event date is used only
            # for aggregation; each original event remains separately traceable.
            fiscal_year = (ex_date or announcement_date).year - 1 if ex_date else announcement_date.year - 1
            cash_per_share = cash_per_ten / 10.0
            code = _stock_code(asset)
            record_id = f"{code}:{announcement_date.isoformat()}:{ex_date or 'unannounced'}:{stage}:{cash_per_ten}"
            content_hash = payload_hash({"code": code, "announcement_date": announcement_date, "ex_date": ex_date,
                "stage": stage, "cash_per_ten": cash_per_ten})
            documents.append({"security_id": sid, "source": "sina", "source_record_id": record_id,
                "title": f"新浪分红派息：{asset['name']} {announcement_date}", "document_type": "structured_dividend_event",
                "report_period": date(fiscal_year, 12, 31), "announcement_date": announcement_date,
                "document_url": DIVIDEND_URL.format(symbol=code), "content_hash": content_hash})
            candidates.append({"security_id": sid, "fiscal_year": fiscal_year, "event_stage": stage, "announcement_id": record_id,
                "cash_dividend_per_share": cash_per_share, "ex_date": ex_date, "announcement_date": announcement_date,
                "document_key": (record_id, content_hash)})
        saved_documents = self.store.upsert("source_documents", documents, "source,source_record_id,content_hash")
        document_ids = {(row["source_record_id"], row["content_hash"]): row["source_document_id"] for row in saved_documents}
        existing = {row["announcement_id"] for row in self.store.select("dividend_events", select="announcement_id", filters={"security_id": f"eq.{sid}"})}
        rows = [{**item, "source_document_id": document_ids[item.pop("document_key")]} for item in candidates]
        self.store.upsert("dividend_events", rows, "security_id,fiscal_year,event_stage,announcement_id")
        return len(rows), sum(row["announcement_id"] not in existing for row in rows)

    @staticmethod
    def _latest_by_metric(rows: list[dict]) -> tuple[date | None, dict[str, float]]:
        grouped: dict[date, dict[str, float]] = {}
        for row in rows:
            period = _date(row.get("report_period"))
            value = _number(row.get("value"))
            if period and value is not None:
                grouped.setdefault(period, {})[row["metric_code"]] = value
        annual = [(period, values) for period, values in grouped.items() if values]
        return max(annual, default=(None, {}), key=lambda item: item[0] or date.min)

    def _write_assessment(self, asset: dict) -> bool:
        sid = security_id(asset["symbol"], asset["market"])
        rows = self.store.select("financial_facts", select="report_period,metric_code,value", filters={
            "security_id": f"eq.{sid}", "is_current": "eq.true", "period_type": "eq.annual"}, order="report_period.asc")
        latest_period, current = self._latest_by_metric(rows)
        if latest_period is None:
            return False
        prior_periods = sorted({_date(row.get("report_period")) for row in rows if _date(row.get("report_period")) and _date(row.get("report_period")) < latest_period})
        prior = {}
        if prior_periods:
            prior = self._latest_by_metric([row for row in rows if _date(row.get("report_period")) == prior_periods[-1]])[1]
        dividends = self.store.select("dividend_events", select="cash_dividend_per_share", filters={
            "security_id": f"eq.{sid}", "fiscal_year": f"eq.{latest_period.year}", "event_stage": "eq.implemented"})
        dividend_per_share = sum(_number(row.get("cash_dividend_per_share")) or 0 for row in dividends)
        eps = current.get("earnings_per_share")
        ocf_ps = current.get("operating_cashflow_per_share")
        metrics: dict[str, float | None] = dict(current)
        metrics["payout_ratio"] = dividend_per_share / eps if eps and eps > 0 and dividend_per_share > 0 else None
        metrics["dividend_coverage"] = ocf_ps / dividend_per_share if ocf_ps is not None and dividend_per_share > 0 else None
        for key, derived in (("free_cashflow_growth", "free_cashflow"), ("debt_change", "interest_debt_ratio")):
            before, after = prior.get(derived), current.get(derived)
            metrics[key] = (after - before) / abs(before) if before not in (None, 0) and after is not None else None
        comparisons = []
        for key in ("revenue", "net_profit", "roe"):
            if current.get(key) is not None and prior.get(key) is not None:
                comparisons.append(current[key] - prior[key])
        metrics["operating_quality_change"] = 1 if comparisons and sum(value > 0 for value in comparisons) >= 2 else (-1 if comparisons and sum(value < 0 for value in comparisons) >= 2 else 0 if comparisons else None)
        assessment = assess_high_dividend_fundamentals(metrics, asset.get("industry_template"))
        payload = {"security_id": sid, "report_period": latest_period,
                   "dividend_safety_status": assessment.dividend_safety_status,
                   "operating_quality_status": assessment.operating_quality_status,
                   "cash_reinvestment_status": assessment.cash_reinvestment_status,
                   "capital_structure_status": assessment.capital_structure_status,
                   "fundamental_status": assessment.fundamental_status, "evidence": assessment.evidence,
                   "main_risk": assessment.main_risk, "calculation_version": self.calculation_version}
        existing = self.store.select("fundamental_assessments", select="fundamental_assessment_id", filters={
            "security_id": f"eq.{sid}", "report_period": f"eq.{latest_period.isoformat()}",
            "calculation_version": f"eq.{self.calculation_version}"}, limit=1)
        self.store.upsert("fundamental_assessments", payload, "security_id,report_period,calculation_version")
        return not existing

    def sync_asset(self, asset: dict, *, today: date | None = None, force: bool = False, run_id: str | None = None) -> SyncResult:
        today = today or datetime.now().date()
        key = self.dataset_key(asset)
        if not force and not _window_is_due(today):
            result = SyncResult(asset["symbol"], key, "skipped", message="非披露窗口，按周抓取以控制免费额度")
            if run_id:
                self.store.add_run_item(run_id, dataset_key=key, status=result.status, message=result.message)
            return result
        watermark = self.store.get_watermark(key) or {}
        retry_at = watermark.get("next_retry_at")
        if not force and int(watermark.get("consecutive_failures") or 0) >= 3 and retry_at:
            try:
                retry_time = datetime.fromisoformat(str(retry_at).replace("Z", "+00:00"))
            except ValueError:
                retry_time = None
            if retry_time and retry_time > datetime.now(timezone.utc):
                result = SyncResult(asset["symbol"], key, "skipped", message=f"来源连续失败，退避至 {retry_time.isoformat()}")
                if run_id:
                    self.store.add_run_item(run_id, dataset_key=key, status=result.status, message=result.message)
                return result
        try:
            code = _stock_code(asset)
            finance = self.source.finance(f"{code}.{_market_suffix(asset)}")
            dividends = self.source.dividends(code)
            fact_received, fact_changed = self._write_finance(asset, finance)
            dividend_received, dividend_changed = self._write_dividends(asset, dividends)
            assessment_changed = self._write_assessment(asset)
            source_latest = max((_date(row.get("REPORT_DATE")) for row in finance.to_dict("records") if _date(row.get("REPORT_DATE"))), default=None)
            self.store.save_watermark({"dataset_key": key, "last_attempt_at": datetime.now(timezone.utc),
                "last_success_at": datetime.now(timezone.utc), "source_latest_date": source_latest,
                "source_record_id": f"{code}:{source_latest}" if source_latest else None,
                "content_hash": payload_hash({"facts": fact_received, "dividends": dividend_received, "source_latest": source_latest}),
                "status": "normal", "consecutive_failures": 0, "next_retry_at": None, "last_error": None})
            result = SyncResult(asset["symbol"], key, "succeeded", fact_received + dividend_received,
                fact_changed + dividend_changed + int(assessment_changed), str(source_latest) if source_latest else None,
                "结构化财务、分红与四维摘要已更新")
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:1000]
            failures = int(watermark.get("consecutive_failures") or 0) + 1
            next_retry_at = datetime.now(timezone.utc) + timedelta(hours=24) if failures >= 3 else None
            self.store.save_watermark({"dataset_key": key, "last_attempt_at": datetime.now(timezone.utc),
                "status": "backoff" if next_retry_at else "failed", "consecutive_failures": failures,
                "next_retry_at": next_retry_at, "last_error": message})
            self.store.record_quality_issue(key, "warning", "fundamental_source_unavailable", {"asset": asset["symbol"], "error": message})
            result = SyncResult(asset["symbol"], key, "failed", message=message)
        if run_id:
            self.store.add_run_item(run_id, dataset_key=key, status=result.status, rows_received=result.rows_received,
                                    rows_changed=result.rows_changed, first_affected_date=result.first_affected_date, message=result.message)
        return result

    def sync_assets(self, assets: list[dict], *, trigger_type: str = "schedule", today: date | None = None, force: bool = False) -> list[SyncResult]:
        run_id = self.store.start_run("sync_fundamentals", trigger_type)
        results = [self.sync_asset(asset, today=today, force=force, run_id=run_id) for asset in assets if asset.get("asset_type") == "股票" and asset.get("market") == "CN"]
        failed = sum(result.status == "failed" for result in results)
        self.store.finish_run(run_id, "partial" if failed else "succeeded", {"assets": len(results), "failed": failed,
                              "rows_received": sum(result.rows_received for result in results),
                              "rows_changed": sum(result.rows_changed for result in results)})
        return results
