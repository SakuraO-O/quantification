"""Public, structured fundamentals ingestion for tracked CN stocks.

The adapter stores normalized facts and compact evidence metadata only.  It
never saves a report PDF, HTML page, or provider response body.  A failed
security is isolated: its last confirmed facts stay available and the failure
is recorded in the per-security watermark.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import math
import signal
import threading
from typing import Any, Callable

from .assets import security_id
from .fundamentals import assess_high_dividend_fundamentals
from .ingestion import SyncResult
from .supabase_store import SupabaseStore, payload_hash


FINANCE_URL = "https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code={market}{symbol}&color=b#/cwfx"
DIVIDEND_URL = "https://data.eastmoney.com/yjfp/detail/{symbol}.html"

FINANCE_FIELDS = {
    "TOTALOPERATEREVE": ("revenue", "CNY"),
    "PARENTNETPROFIT": ("net_profit", "CNY"),
    "KCFJCXSYJLR": ("deducted_net_profit", "CNY"),
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
MAX_ANNUAL_REPORTS = 11
MAX_INTERIM_REPORTS = 33


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
    # ``pandas.NaT`` is not a float and still exposes ``date()``/``year``;
    # passing it through produces a NaN fiscal year and breaks event writes.
    if value is None or str(value) in {"NaT", "nan", "None"}:
        return None
    if hasattr(value, "date"):
        result = value.date()
        return None if str(result) in {"NaT", "nan", "None"} else result
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
    cashflow: Callable[[str], Any] | None = None

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
                return ak.stock_fhps_detail_em(symbol=symbol)
        def cashflow(symbol: str):
            with _source_timeout(SOURCE_TIMEOUT_SECONDS):
                return ak.stock_cash_flow_sheet_by_report_em(symbol=symbol)
        return cls(
            finance=finance,
            dividends=dividends,
            cashflow=cashflow,
        )


def _exchange_listing_loader():
    """Load each official exchange directory once, including failed requests."""
    import akshare as ak
    cache = {}
    def load(symbol):
        shanghai = symbol.startswith("6")
        board = "科创板" if symbol.startswith("688") else "主板A股"
        key = board if shanghai else "深圳A股"
        if key not in cache:
            try:
                cache[key] = ak.stock_info_sh_name_code(board) if shanghai else ak.stock_info_sz_name_code()
            except Exception as exc:
                cache[key] = exc
        table = cache[key]
        if isinstance(table, Exception):
            raise table
        code_column, date_column = ("证券代码", "上市日期") if shanghai else ("A股代码", "A股上市日期")
        matches = table[table[code_column].astype(str).str.zfill(6) == symbol]
        if len(matches) != 1:
            raise ValueError("交易所上市名录未唯一匹配证券")
        listed = _date(matches.iloc[0][date_column])
        if listed is None:
            raise ValueError("交易所上市日期缺失")
        return {"item": ["股票代码", "上市时间"], "value": [symbol, listed.strftime("%Y%m%d")]}
    return load


class FundamentalSynchronizer:
    """Fetch financial facts and dividend events without coupling assets together."""

    calculation_version = "fundamentals-v1"

    def __init__(self, store: SupabaseStore, source: FundamentalSource | None = None):
        self.store = store
        self.source = source or FundamentalSource.public()

    @staticmethod
    def dataset_key(asset: dict) -> str:
        return f"fundamentals:CN:{asset['symbol']}:eastmoney"

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

    def _write_finance(self, asset: dict, frame: Any, cashflow: Any = None) -> tuple[int, int]:
        """Persist a compact research window in batches, not one REST call per metric."""

        records = frame.to_dict("records")
        annual = [row for row in records if "年报" in str(row.get("REPORT_TYPE") or "")]
        interim = [row for row in records if "年报" not in str(row.get("REPORT_TYPE") or "")]
        def latest_periods(rows: list[dict], count: int) -> list[dict]:
            ordered = sorted(rows, key=lambda row: (
                _date(row.get("REPORT_DATE")) or date.min,
                _date(row.get("UPDATE_DATE")) or _date(row.get("NOTICE_DATE")) or date.min,
            ), reverse=True)
            selected, seen = [], set()
            for row in ordered:
                period = _date(row.get("REPORT_DATE"))
                if period and period not in seen:
                    selected.append(row)
                    seen.add(period)
                if len(selected) == count:
                    break
            return selected
        selected = latest_periods(annual, MAX_ANNUAL_REPORTS)
        selected += latest_periods(interim, MAX_INTERIM_REPORTS)
        selected = latest_periods(selected, MAX_ANNUAL_REPORTS + MAX_INTERIM_REPORTS)
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
        if cashflow is not None and "NETCASH_OPERATE" in cashflow.columns:
            cash_rows = latest_periods(cashflow.to_dict("records"), 44)
            for raw in cash_rows:
                report_period = _date(raw.get("REPORT_DATE"))
                value = _number(raw.get("NETCASH_OPERATE"))
                if report_period is None or value is None:
                    continue
                notice_date = _date(raw.get("NOTICE_DATE")) or _date(raw.get("UPDATE_DATE"))
                record_id = f"{_stock_code(asset)}:{report_period.isoformat()}:cashflow:{notice_date or 'unknown'}"
                content_hash = payload_hash({"report_period": report_period, "value": value,
                                             "notice_date": notice_date})
                documents.append({"security_id": sid, "source": "eastmoney", "source_record_id": record_id,
                    "title": f"东方财富现金流量表：{asset['name']} {report_period}",
                    "document_type": "structured_cashflow_snapshot", "report_period": report_period,
                    "announcement_date": notice_date,
                    "document_url": FINANCE_URL.format(market=_market_suffix(asset), symbol=_stock_code(asset)),
                    "content_hash": content_hash})
                candidates.append({"report_period": report_period, "metric_code": "operating_cashflow",
                    "value": value, "unit": "CNY",
                    "period_type": "annual" if (report_period.month, report_period.day) == (12, 31) else "year_to_date",
                    "announcement_date": notice_date, "document_key": (record_id, content_hash)})
        saved_documents = self.store.upsert("source_documents", documents, "source,source_record_id,content_hash")
        document_ids = {(row["source_record_id"], row["content_hash"]): row["source_document_id"] for row in saved_documents}
        history = self.store.select("financial_facts", select="financial_fact_id,report_period,metric_code,value,version,source_document_id,verification_status,is_current",
            filters={"security_id": f"eq.{sid}"}, order="report_period.asc,metric_code.asc,version.asc")
        current = {(str(row["report_period"]), row["metric_code"]): row for row in history if row.get("is_current")}
        versions: dict[tuple[str, str], int] = {}
        for row in history:
            identity = (str(row["report_period"]), row["metric_code"])
            versions[identity] = max(versions.get(identity, 0), int(row["version"]))
        changed_rows = []
        replaced = []
        for item in candidates:
            source_document_id = document_ids[item.pop("document_key")]
            identity = (item["report_period"].isoformat(), item["metric_code"])
            prior = current.get(identity)
            # Formal-report values are authoritative; a later provider poll
            # cannot silently replace a reviewed fact or its source.
            if prior and prior.get("verification_status") == "verified":
                continue
            if prior and _number(prior.get("value")) == item["value"] and prior.get("source_document_id") == source_document_id:
                continue
            if prior:
                replaced.append(prior["financial_fact_id"])
            changed_rows.append({"security_id": sid, **item, "source_document_id": source_document_id,
                "version": versions.get(identity, 0) + 1, "is_current": True,
                "verification_status": "candidate"})
        try:
            for fact_id in replaced:
                self.store.patch("financial_facts", {"financial_fact_id": f"eq.{fact_id}"}, {"is_current": False})
            self.store.upsert("financial_facts", changed_rows, "security_id,report_period,metric_code,version")
        except Exception:
            for fact_id in replaced:
                self.store.patch("financial_facts", {"financial_fact_id": f"eq.{fact_id}"}, {"is_current": True})
            raise
        return len(candidates), len(changed_rows)

    def _write_dividends(self, asset: dict, frame: Any) -> tuple[int, int]:
        sid = security_id(asset["symbol"], asset["market"])
        required = {"报告期", "现金分红-现金分红比例", "送转股份-送股比例",
                    "送转股份-转股比例", "方案进度", "除权除息日"}
        if not required.issubset(frame.columns):
            raise ValueError(f"分红来源缺少必要字段: {', '.join(sorted(required - set(frame.columns)))}")
        documents: list[dict] = []
        candidates: list[dict] = []
        for raw in frame.to_dict("records"):
            stage_name = str(raw.get("方案进度") or "")
            cash_per_ten = _number(raw.get("现金分红-现金分红比例"))
            bonus_per_ten = _number(raw.get("送转股份-送股比例"))
            transfer_per_ten = _number(raw.get("送转股份-转股比例"))
            if all(value is None for value in (cash_per_ten, bonus_per_ten, transfer_per_ten)):
                if stage_name.startswith(("实施", "已实施")):
                    raise ValueError("已实施分红缺少金额和送转股比例")
                continue
            if "未实施" in stage_name or "待实施" in stage_name or "预案" in stage_name:
                stage = "proposal"
            elif stage_name.startswith(("实施", "已实施")):
                stage = "implemented"
            elif "通过" in stage_name or "批准" in stage_name:
                stage = "approved"
            else:
                raise ValueError(f"无法识别分红方案进度: {stage_name}")
            if any(value < 0 for value in (cash_per_ten, bonus_per_ten, transfer_per_ten) if value is not None):
                raise ValueError("分红来源存在负数派息或送转股比例")
            report_period = _date(raw.get("报告期"))
            if report_period is None:
                # A partial fiscal-year classification is worse than an
                # explicit sync failure: the publisher could undercount.
                raise ValueError("分红来源存在缺少报告期的记录")
            announcement_date = (_date(raw.get("最新公告日期")) or _date(raw.get("预案公告日"))
                                 or _date(raw.get("业绩披露日期")))
            ex_date = _date(raw.get("除权除息日"))
            fiscal_year = report_period.year
            cash_per_share = float(Decimal(str(cash_per_ten or 0)) / 10)
            code = _stock_code(asset)
            record_id = f"{code}:{report_period.isoformat()}:{ex_date or 'unannounced'}:{stage}"
            content_hash = payload_hash({"code": code, "report_period": report_period,
                "announcement_date": announcement_date, "ex_date": ex_date,
                "stage": stage, "cash_per_ten": cash_per_ten, "bonus_per_ten": bonus_per_ten,
                "transfer_per_ten": transfer_per_ten})
            documents.append({"security_id": sid, "source": "eastmoney", "source_record_id": record_id,
                "title": f"东方财富分红送配：{asset['name']} {report_period}", "document_type": "structured_dividend_event",
                "report_period": report_period, "announcement_date": announcement_date,
                "document_url": DIVIDEND_URL.format(symbol=code), "content_hash": content_hash})
            candidates.append({"security_id": sid, "fiscal_year": fiscal_year, "report_period": report_period,
                "event_stage": stage, "announcement_id": record_id, "is_current": True,
                "verification_status": "candidate",
                "cash_dividend_per_share": cash_per_share, "ex_date": ex_date, "announcement_date": announcement_date,
                "bonus_shares_per_share": float(Decimal(str(bonus_per_ten or 0)) / 10),
                "transfer_shares_per_share": float(Decimal(str(transfer_per_ten or 0)) / 10),
                "document_key": (record_id, content_hash)})
        identities = [row["announcement_id"] for row in candidates]
        if len(identities) != len(set(identities)):
            raise ValueError("分红来源存在无法区分的重复事件")
        existing = {row["announcement_id"]: row for row in self.store.select(
            "dividend_events", select="announcement_id,report_period,fiscal_year,event_stage,is_current,announcement_date,ex_date,bonus_shares_per_share,transfer_shares_per_share,cash_dividend_per_share,source_document_id,verification_status,superseded_by_plan_key",
            filters={"security_id": f"eq.{sid}", "report_period": "not.is.null"})}
        incoming_counts = Counter(str(row["report_period"]) for row in candidates
                                  if row["event_stage"] == "implemented")
        verified_counts = Counter(str(row["report_period"]) for row in existing.values()
                                  if row.get("is_current") and row.get("event_stage") == "implemented"
                                  and row.get("verification_status") != "verified")
        if any(incoming_counts[period] < count for period, count in verified_counts.items()):
            raise ValueError("分红来源按报告期返回的已实施事件少于已核验历史，拒绝缩减")
        saved_documents = self.store.upsert("source_documents", documents, "source,source_record_id,content_hash")
        document_ids = {(row["source_record_id"], row["content_hash"]): row["source_document_id"] for row in saved_documents}
        # Remove the in-memory join key before serializing the event.  Sending
        # it to PostgREST would make it look for a non-existent
        # ``dividend_events.document_key`` column and reject the whole batch.
        rows = []
        for item in candidates:
            payload = dict(item)
            document_key = payload.pop("document_key")
            payload["source_document_id"] = document_ids[document_key]
            rows.append(payload)
        new_rows = [row for row in rows if row["announcement_id"] not in existing]
        updated = 0
        changed_years = {row["fiscal_year"] for row in new_rows}
        for row in rows:
            prior = existing.get(row["announcement_id"])
            if prior is None:
                continue
            if prior.get("verification_status") == "verified" or prior.get("superseded_by_plan_key"):
                continue
            changed = {field: row[field] for field in (
                "report_period", "fiscal_year", "event_stage", "is_current", "announcement_date", "ex_date",
                "bonus_shares_per_share", "transfer_shares_per_share", "cash_dividend_per_share", "source_document_id"
            ) if prior.get(field) != row[field]}
            if changed:
                self.store.patch("dividend_events", {"security_id": f"eq.{sid}", "announcement_id": f"eq.{row['announcement_id']}"}, changed)
                updated += 1
                changed_years.add(row["fiscal_year"])
        # Insert only unseen identities. PostgREST's composite-conflict path
        # currently returns 400 in this project.
        self.store.insert("dividend_events", new_rows)
        stale_ids = set(existing) - set(identities)
        for announcement_id in stale_ids:
            if existing[announcement_id].get("verification_status") == "verified" or existing[announcement_id].get("superseded_by_plan_key"):
                continue
            if existing[announcement_id].get("is_current"):
                self.store.patch("dividend_events", {"security_id": f"eq.{sid}",
                    "announcement_id": f"eq.{announcement_id}"}, {"is_current": False})
                updated += 1
                changed_years.add(existing[announcement_id]["fiscal_year"])
        # A newly seen plan invalidates an earlier "year complete" review
        # until the official announcement is checked and linked.
        for fiscal_year in changed_years:
            self.store.patch("dividend_year_reviews",
                {"security_id": f"eq.{sid}", "fiscal_year": f"eq.{fiscal_year}"},
                {"status": "pending"})
        return len(rows), len(new_rows) + updated

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
            "security_id": f"eq.{sid}", "fiscal_year": f"eq.{latest_period.year}",
            "event_stage": "eq.implemented", "report_period": "not.is.null", "is_current": "eq.true"})
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
            cashflow = None
            if self.source.cashflow:
                try:
                    cashflow = self.source.cashflow(f"{_market_suffix(asset)}{code}")
                except Exception as error:
                    self.store.record_quality_issue(key, "warning", "cashflow_source_unavailable",
                                                    {"asset": asset["symbol"], "error": str(error)[:300]})
            dividends = self.source.dividends(code)
            fact_received, fact_changed = self._write_finance(asset, finance, cashflow)
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

    def sync_listing_dates(self, *, force: bool = False, loader=None) -> list[SyncResult]:
        """One-time metadata backfill; listing dates never come from price-history bounds."""
        exchange_source = loader is None
        loader = loader or _exchange_listing_loader()
        results = []
        rows = self.store.select("securities", filters={"asset_type": "eq.股票", "market": "eq.CN", "is_active": "eq.true"})
        for row in rows:
            if row.get("listing_date") and not force:
                continue
            symbol = row["symbol"]
            try:
                with _source_timeout(SOURCE_TIMEOUT_SECONDS):
                    info = loader(symbol[-6:])
                values = dict(zip(info["item"], info["value"]))
                reported_symbol = str(values.get("股票代码", "")).zfill(6)
                if reported_symbol != symbol[-6:]:
                    raise ValueError("上市信息证券代码不一致")
                raw = str(values.get("上市时间", "")).split(".")[0]
                listed = datetime.strptime(raw, "%Y%m%d").date()
                asset = {"symbol": symbol, "market": "CN", "name": row["name"]}
                source = ("sse" if symbol.startswith("sh") else "szse") if exchange_source else "eastmoney"
                source_url = ("https://www.sse.com.cn/assortment/stock/list/share/" if source == "sse"
                              else "https://www.szse.cn/market/product/stock/list/index.html" if source == "szse"
                              else f"https://quote.eastmoney.com/{symbol}.html")
                document_id = self._source_document(asset, source=source,
                    record_id=f"{symbol}:listing-date", title=f"{row['name']} A股上市日期",
                    document_type="structured_security_metadata", report_period=None, announcement_date=None,
                    url=source_url,
                    evidence={"symbol": symbol, "listing_date": listed.isoformat()})
                self.store.patch("securities", {"security_id": f"eq.{row['security_id']}"},
                                 {"listing_date": listed.isoformat(), "listing_source_document_id": document_id})
                results.append(SyncResult(symbol, f"metadata:CN:{symbol}", "succeeded", rows_changed=1))
            except Exception as exc:
                results.append(SyncResult(symbol, f"metadata:CN:{symbol}", "failed", message=str(exc)))
        return results

    def sync_assets(self, assets: list[dict], *, trigger_type: str = "schedule", today: date | None = None, force: bool = False) -> list[SyncResult]:
        run_id = self.store.start_run("sync_fundamentals", trigger_type)
        results = [self.sync_asset(asset, today=today, force=force, run_id=run_id) for asset in assets if asset.get("asset_type") == "股票" and asset.get("market") == "CN"]
        failed = sum(result.status == "failed" for result in results)
        self.store.finish_run(run_id, "partial" if failed else "succeeded", {"assets": len(results), "failed": failed,
                              "rows_received": sum(result.rows_received for result in results),
                              "rows_changed": sum(result.rows_changed for result in results)})
        return results
