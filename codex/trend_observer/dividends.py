"""Dividend configuration and yield helpers."""

import json
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
