"""Dividend configuration and yield helpers."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation

import numpy as np
import pandas as pd

from .config import DIVIDENDS_CONFIG


def parse_dividend_value(label, value):
    if value in (None, "") or pd.isna(value):
        return np.nan
    try:
        dividend = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} 必须为非负数。") from exc
    if not dividend.is_finite() or dividend < 0:
        raise ValueError(f"{label} 必须为非负数。")
    if abs(dividend.as_tuple().exponent) > 5:
        raise ValueError(f"{label} 最多只能录入 5 位小数。")
    return float(dividend)


def load_dividend_config(path=DIVIDENDS_CONFIG):
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 顶层必须是股票代码到分红金额的对象。")
    return {
        str(symbol): parse_dividend_value(f"{path.name} {symbol}", value)
        for symbol, value in payload.items()
    }


def apply_dividend_config(assets, path=DIVIDENDS_CONFIG, *, allow_subset=False):
    config = load_dividend_config(path)
    stock_assets = {asset["symbol"]: asset for asset in assets if asset.get("asset_type") == "股票"}
    unknown_symbols = sorted(set(config) - set(stock_assets))
    if unknown_symbols and not allow_subset:
        raise ValueError(f"{path.name} 包含未配置的股票代码: {', '.join(unknown_symbols)}")
    for symbol, dividend in config.items():
        if symbol in stock_assets:
            stock_assets[symbol]["last_year_dividend"] = None if pd.isna(dividend) else dividend
    return assets


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
