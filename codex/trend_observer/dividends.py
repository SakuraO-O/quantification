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
    """Publish the prior report year's implemented cash dividends and yield.

    ``fiscal_year`` must come from the source report period, never from the
    announcement or ex-dividend date. Old events without ``report_period`` are
    deliberately excluded until they have been refetched from that source.
    """
    day = date.fromisoformat(str(trade_date)[:10])
    fiscal_year = day.year - 1
    result = {"dividend_fiscal_year": fiscal_year, "last_year_dividend": None,
              "dividend_yield": None, "dividend_yield_status": "数据不足",
              "dividend_yield_issue": "上一报告年度未找到已实施且已除息的分红"}
    if pd.isna(close) or close <= 0:
        result["dividend_yield_issue"] = "当日收盘价缺失或无效"
        return result
    selected = []
    seen = set()
    for event in events:
        if event.get("event_stage") != "implemented":
            continue
        report_period = event.get("report_period")
        if not report_period or date.fromisoformat(str(report_period)[:10]).year != fiscal_year:
            continue
        ex_date = event.get("ex_date")
        if not ex_date:
            result["dividend_yield_issue"] = "已实施分红缺少除权除息日"
            return result
        if date.fromisoformat(str(ex_date)[:10]) > day:
            continue
        identity = event.get("announcement_id")
        if not identity or identity in seen:
            result["dividend_yield_issue"] = "分红事件标识缺失或重复"
            return result
        seen.add(identity)
        amount = event.get("cash_dividend_per_share")
        if amount is None or pd.isna(amount) or amount < 0:
            result["dividend_yield_issue"] = "已实施分红金额缺失或无效"
            return result
        selected.append(Decimal(str(amount)))
    if not selected:
        return result
    total = sum(selected)
    result["last_year_dividend"] = float(total)
    result["dividend_yield"] = calculate_dividend_yield(float(total), close)
    result["dividend_yield_status"] = "已计算"
    result["dividend_yield_issue"] = None
    return result
