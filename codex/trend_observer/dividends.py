"""Dividend yield and prior report-year aggregation."""

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd


def calculate_dividend_yield(dividend, close):
    if pd.isna(dividend) or pd.isna(close) or close <= 0:
        return np.nan
    return round(dividend / close * 100, 2)


def annual_dividend_summary(events: list[dict], trade_date: str | date, close) -> dict:
    """Prior report-year dividends approved by the market date, counted once per plan."""
    day = date.fromisoformat(str(trade_date)[:10])
    fiscal_year = day.year - 1
    result = {"dividend_fiscal_year": fiscal_year, "last_year_dividend": None,
              "dividend_yield": None, "dividend_yield_status": "数据不足",
              "dividend_yield_issue": "上一报告年度未找到已实施或已批准的分红",
              "implemented_dividend_per_share": None, "approved_dividend_per_share": None}
    if pd.isna(close) or close <= 0:
        result["dividend_yield_issue"] = "当日收盘价缺失或无效"
        return result
    reviewed_periods = {str(event.get("report_period")) for event in events
                        if str(event.get("report_period", ""))[:4] == str(fiscal_year)
                        and event.get("verification_status") == "verified" and event.get("plan_key")
                        and event.get("is_current") is not False}
    ambiguous = any(str(event.get("report_period")) in reviewed_periods
                    and event.get("verification_status") != "verified"
                    and event.get("is_current") is not False
                    and not event.get("superseded_by_plan_key") for event in events)
    if ambiguous:
        result["dividend_yield_issue"] = "同一报告期候选与已核验方案关联待核对"
        return result
    plans = {}
    for event in events:
        period = event.get("report_period")
        if not period or date.fromisoformat(str(period)[:10]).year != fiscal_year:
            continue
        stage = event.get("event_stage")
        if stage not in ("implemented", "approved"):
            continue
        approval = event.get("approval_date")
        ex_date = event.get("ex_date")
        implemented = stage == "implemented" and ex_date and str(ex_date)[:10] <= day.isoformat()
        approved = (approval and str(approval)[:10] <= day.isoformat()
                    and event.get("verification_status") == "verified"
                    and event.get("approval_basis") in ("shareholder_resolution", "shareholder_authorized_board"))
        if not implemented and not approved:
            continue
        # Candidates replaced by a reviewed plan must never be counted again.
        if event.get("superseded_by_plan_key") or (not event.get("plan_key") and event.get("is_current") is False):
            continue
        identity = event.get("plan_key") or event.get("announcement_id")
        if not identity:
            result["dividend_yield_issue"] = "分红方案标识缺失"
            return result
        effective = str(event.get("announcement_date") or approval or ex_date or "")[:10]
        if effective > day.isoformat():
            continue
        version = int(event.get("version") or 1)
        prior = plans.get(identity)
        if prior and prior[0] == version:
            result["dividend_yield_issue"] = "分红方案版本重复"
            return result
        if prior and prior[0] > version:
            continue
        amount = event.get("cash_dividend_per_share")
        if amount is None or pd.isna(amount) or not Decimal(str(amount)).is_finite() or Decimal(str(amount)) < 0:
            result["dividend_yield_issue"] = "分红金额缺失或无效"
            return result
        plans[identity] = (version, Decimal(str(amount)), bool(implemented))
    if not plans:
        return result
    implemented = sum((item[1] for item in plans.values() if item[2]), Decimal(0))
    approved = sum((item[1] for item in plans.values() if not item[2]), Decimal(0))
    total = implemented + approved
    result.update(last_year_dividend=float(total), dividend_yield=calculate_dividend_yield(float(total), close),
                  implemented_dividend_per_share=float(implemented), approved_dividend_per_share=float(approved),
                  dividend_yield_status="已计算", dividend_yield_issue=None)
    return result
