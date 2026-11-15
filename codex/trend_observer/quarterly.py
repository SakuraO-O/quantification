"""Published quarterly research figures. All arithmetic lives on the server.

Structured provider rows are candidates. Only facts backed by reviewed formal
reports and dividend announcements can enter the published numerical series.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any


METRICS = {
    "net_profit": ("归母净利润", "CNY", "single_quarter"),
    "deducted_net_profit": ("扣非归母净利润", "CNY", "single_quarter"),
    "revenue": ("营业总收入", "CNY", "single_quarter"),
    "operating_cashflow": ("经营现金流净额", "CNY", "single_quarter"),
    "roe": ("加权平均ROE", "percent", "year_to_date"),
    "asset_liability_ratio": ("资产负债率", "percent", "quarter_end"),
}
AMOUNTS = frozenset(("net_profit", "deducted_net_profit", "revenue", "operating_cashflow"))
PERIODS = ((3, 31), (6, 30), (9, 30), (12, 31))


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value)) if value is not None else None
        return number if number is None or number.is_finite() else None
    except (ValueError, ArithmeticError):
        return None


def _year(period: Any) -> int | None:
    try:
        return int(str(period)[:4])
    except (ValueError, TypeError):
        return None


def _quarter(period: Any) -> int | None:
    try:
        parsed = date.fromisoformat(str(period)[:10])
    except (ValueError, TypeError):
        return None
    return next((q for q, (month, day) in enumerate(PERIODS, 1)
                 if (parsed.month, parsed.day) == (month, day)), None)


def _source(fact: dict, documents: dict[str, dict]) -> dict:
    document = documents.get(str(fact.get("source_document_id")), {})
    return {
        "report_period": fact.get("report_period"),
        "source": document.get("source"),
        "announcement_date": fact.get("announcement_date") or document.get("announcement_date"),
        "url": document.get("document_url"),
    }


def _cell(fact: dict | None, previous: dict | None, metric: str, documents: dict[str, dict]) -> dict:
    if not fact:
        return {"value": None, "status": "数据不足", "reason": "报告尚未核验"}
    value = _decimal(fact.get("value"))
    if value is None:
        return {"value": None, "status": "数据不足", "reason": "报告数值缺失"}
    if metric in AMOUNTS and previous is None and _quarter(fact["report_period"]) != 1:
        return {"value": None, "status": "数据不足", "reason": "缺少上一累计报告"}
    if previous:
        prior = _decimal(previous.get("value"))
        if prior is None or previous.get("period_type") not in ("year_to_date", "annual"):
            return {"value": None, "status": "数据不足", "reason": "上一累计报告口径不一致"}
        value -= prior
    return {
        "value": float(value),
        "status": "已核验",
        "basis": METRICS[metric][2],
        "source": _source(fact, documents),
        "minus_source": _source(previous, documents) if previous else None,
    }


def _yoy(current: Decimal | None, previous: Decimal | None) -> dict:
    if current is None or previous is None:
        return {"value": None, "status": "数据不足"}
    if previous <= 0:
        return {"value": None, "status": "—"}
    return {"value": float((current / previous - 1) * 100), "status": "已计算"}


def _dividend_year(year: int, events: list[dict], review: dict | None, profit: Decimal | None) -> dict:
    # The stable plan key, not announcement date or ex-date, identifies a
    # distribution across proposal, approval, implementation and correction.
    plans: dict[str, dict] = {}
    unresolved = False
    pending_approval = False
    for event in events:
        if _year(event.get("report_period")) != year or not event.get("is_current"):
            continue
        key = event.get("plan_key")
        if event.get("verification_status") != "verified":
            # A later candidate invalidates an earlier complete-year review.
            unresolved = True
            continue
        if not key:
            unresolved = True
            continue
        if key in plans:
            unresolved = True
            continue
        plans[key] = event
    implemented = Decimal(0)
    approved = Decimal(0)
    total = Decimal(0)
    has_amount = False
    for event in plans.values():
        stage = event.get("event_stage")
        if stage == "proposal":
            pending_approval = True
            continue
        per_share = _decimal(event.get("cash_dividend_per_share"))
        cash_total = _decimal(event.get("cash_dividend_total"))
        if per_share is None or cash_total is None or (stage == "approved" and not event.get("approval_basis")):
            unresolved = True
            continue
        if stage == "implemented":
            implemented += per_share
        elif stage == "approved":
            approved += per_share
        else:
            unresolved = True
            continue
        has_amount = True
        total += cash_total
    complete = bool(review and review.get("status") == "complete" and review.get("source_document_id"))
    status = ("待核对" if unresolved else "方案待批准" if pending_approval
              else "已核验" if complete
              else "—（尚无具体方案）" if review and review.get("status") == "authorization_only"
              else "待确认")
    payout = None
    if status == "已核验" and profit is not None and profit > 0:
        payout = float(total / profit * 100)
    elif status == "已核验" and (profit is None or profit <= 0):
        status = "数据不足"
    show_amount = (has_amount or (complete and not pending_approval)) and not unresolved
    return {
        "per_share": float(implemented + approved) if show_amount else None,
        "implemented_per_share": float(implemented) if show_amount else None,
        "approved_per_share": float(approved) if show_amount else None,
        "payout_ratio": payout,
        "status": status,
        "unimplemented": approved > 0,
    }


def build_quarterly(
    facts: list[dict], events: list[dict], documents: list[dict],
    year_reviews: list[dict],
) -> dict:
    """Return a versionable, presentation-ready ten-year quarterly contract."""
    sources = {str(row.get("source_document_id")): row for row in documents}
    reported: dict[tuple[int, int, str], dict] = {}
    verified: dict[tuple[int, int, str], dict] = {}
    for fact in facts:
        year, quarter = _year(fact.get("report_period")), _quarter(fact.get("report_period"))
        metric = fact.get("metric_code")
        expected_type = "annual" if quarter == 4 else "year_to_date"
        if (year and quarter and metric in METRICS and fact.get("is_current")
                and fact.get("source_document_id")
                and str(fact["source_document_id"]) in sources
                and fact.get("period_type") == expected_type):
            reported[(year, quarter, metric)] = fact
            if fact.get("verification_status") == "verified":
                verified[(year, quarter, metric)] = fact
    if not reported:
        return {"status": "数据不足", "reason": "尚无已入库的定期报告", "years": [], "metrics": {}, "dividends": {}}
    latest_year, latest_quarter = max((year, q) for year, q, _ in reported)
    years = list(range(latest_year - 9, latest_year + 1))
    reviews = {int(row["fiscal_year"]): row for row in year_reviews
               if str(row.get("source_document_id")) in sources}
    metrics = {}
    for metric, (label, unit, _) in METRICS.items():
        columns = []
        for year in years:
            cells = []
            for q in range(1, 5):
                fact = verified.get((year, q, metric))
                prior = verified.get((year, q - 1, metric)) if q > 1 and metric in AMOUNTS else None
                cells.append(_cell(fact, prior, metric, sources))
            last = max((q for q in range(1, 5) if (year, q, metric) in verified), default=0)
            current = verified.get((year, last, metric)) if last else None
            previous = verified.get((year - 1, last, metric)) if last else None
            yearly = _decimal(current.get("value")) if current else None
            prior_yearly = _decimal(previous.get("value")) if previous else None
            comparison = _yoy(yearly, prior_yearly) if metric in AMOUNTS else {"value": None, "status": "—"}
            columns.append({
                "quarters": cells,
                "year_value": float(yearly) if yearly is not None else None,
                "year_status": "已核验" if yearly is not None else "数据不足",
                "year_quarter": last or None,
                "comparison": comparison,
            })
        metrics[metric] = {"label": label, "unit": unit, "years": columns}
    annual_profit = {
        year: _decimal(verified[(year, 4, "net_profit")]["value"])
        for year in years if (year, 4, "net_profit") in verified
    }
    dividends = {
        str(year): _dividend_year(year, events, reviews.get(year), annual_profit.get(year))
        for year in years
    }
    latest_report = max(
        (fact for (year, q, _), fact in reported.items() if (year, q) == (latest_year, latest_quarter)),
        key=lambda fact: str(fact.get("announcement_date") or ""), default={}
    )
    annual_year = max((year for year, q, metric in reported if q == 4 and metric == "net_profit"), default=None)
    top = {
        "report_period": f"{latest_year}年{('一季度', '半年报', '三季度', '年报')[latest_quarter - 1]}",
        "report_period_date": date(latest_year, *PERIODS[latest_quarter - 1]).isoformat(),
        "announcement_date": latest_report.get("announcement_date"),
        "dividend_year": latest_year,
        "payout_year": annual_year,
        "payout": dividends.get(str(annual_year)) if annual_year else None,
        "dividend": dividends[str(latest_year)],
        "summary": {},
    }
    for metric in ("net_profit", "deducted_net_profit", "revenue"):
        fact = verified.get((latest_year, latest_quarter, metric))
        prior = verified.get((latest_year - 1, latest_quarter, metric))
        top["summary"][metric] = {
            "value": float(fact["value"]) if fact else None,
            "comparison": _yoy(_decimal(fact.get("value")) if fact else None,
                               _decimal(prior.get("value")) if prior else None),
        }
    return {"status": "已计算",
            "reason": None if verified else "已入库报告尚未完成正式公告核验",
            "years": years, "metrics": metrics, "dividends": dividends, "top": top}
