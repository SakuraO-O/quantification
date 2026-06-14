#!/usr/bin/env python3
"""指数/股票三周期均线趋势观察工具。

运行后生成最新截面、历史行情和 Markdown/CSV 报告。
使用 --notify 可将新版趋势观察摘要推送到飞书机器人。
本工具只做趋势观察，不构成投资建议。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests


BASE_DIR = Path(__file__).resolve().parent
CSV_OUTPUT = BASE_DIR / "trend_observer_report.csv"
MARKDOWN_OUTPUT = BASE_DIR / "trend_observer_report.md"
DASHBOARD_OUTPUT = BASE_DIR / "dashboard_data.json"
HISTORY_JSON_OUTPUT = BASE_DIR / "trend_history.json"
HISTORY_CSV_OUTPUT = BASE_DIR / "trend_history.csv"
HISTORY_DIR = BASE_DIR / "history"
HISTORY_MANIFEST_OUTPUT = HISTORY_DIR / "manifest.json"
DIVIDENDS_CONFIG = BASE_DIR / "dividends.json"

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
HTTP_TIMEOUT = (5, 20)
MIN_HISTORY_ROWS = 260
TENCENT_LOOKBACK_ROWS = 1400
TENCENT_LOOKBACK_DAYS = 365 * 5 + 30
PE_MIN_PERIODS = 252
PE_WINDOW_ROWS = 2520
FEISHU_KEYWORD = "妙啊"
DISCLAIMER = "本结果仅用于趋势观察，不构成投资建议。均线信号存在滞后性和假突破风险。"

ASSETS = [
    {"name": "沪深300", "symbol": "000300", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "中证A500", "symbol": "000510", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "中证500", "symbol": "000905", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "创业板100", "symbol": "sz399006", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "科创50", "symbol": "000688", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "恒生指数", "symbol": "hkHSI", "market": "HK", "asset_type": "指数", "provider": "tencent"},
    {"name": "红利低波", "symbol": "H30269", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "国证现金流", "symbol": "980092", "market": "CN", "asset_type": "指数", "provider": "cnindex"},
    {"name": "中证消费", "symbol": "000932", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "全指医药", "symbol": "000991", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "长江电力", "symbol": "sh600900", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "中远海控", "symbol": "sh601919", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "招商银行", "symbol": "sh600036", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "中国神华", "symbol": "sh601088", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "中国海油", "symbol": "sh600938", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "美的集团", "symbol": "sz000333", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "格力电器", "symbol": "sz000651", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "粤高速A", "symbol": "sz000429", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "国电电力", "symbol": "sh600795", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "云铝股份", "symbol": "sz000807", "market": "CN", "asset_type": "股票", "provider": "tencent"},
]

REPORT_COLUMNS = [
    "name",
    "symbol",
    "market",
    "asset_type",
    "date",
    "close",
    "daily_return",
    "return_ytd",
    "return_1w",
    "return_1m",
    "return_1y",
    "return_3y",
    "MA20",
    "MA60",
    "MA120",
    "MA200",
    "ma20_slope_5d",
    "ma60_slope_10d",
    "ma120_slope_20d",
    "ma200_slope_40d",
    "short_trend",
    "mid_trend",
    "long_trend",
    "overall_status",
    "signal_tags",
    "last_year_dividend",
    "dividend_yield",
    "pe",
    "pe_percentile",
    "pe_percentile_period",
    "valuation_status",
    "error",
]


def make_session():
    session = requests.Session()
    session.trust_env = True
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://www.csindex.com.cn/",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


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


def load_dividend_config():
    if not DIVIDENDS_CONFIG.exists():
        return {}
    try:
        payload = json.loads(DIVIDENDS_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{DIVIDENDS_CONFIG} 不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{DIVIDENDS_CONFIG} 顶层必须是股票代码到分红金额的对象。")
    return {
        str(symbol): parse_dividend_value(f"{DIVIDENDS_CONFIG.name} {symbol}", value)
        for symbol, value in payload.items()
    }


def apply_dividend_config():
    config = load_dividend_config()
    stock_assets = {asset["symbol"]: asset for asset in ASSETS if asset.get("asset_type") == "股票"}
    unknown_symbols = sorted(set(config) - set(stock_assets))
    if unknown_symbols:
        raise ValueError(f"{DIVIDENDS_CONFIG.name} 包含未配置的股票代码: {', '.join(unknown_symbols)}")
    for symbol, dividend in config.items():
        stock_assets[symbol]["last_year_dividend"] = None if pd.isna(dividend) else dividend


def calculate_dividend_yield(dividend, close):
    if pd.isna(dividend) or pd.isna(close) or close <= 0:
        return np.nan
    return round(dividend / close * 100, 2)


def normalize_frame(rows):
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "pe"])
    frame["date"] = pd.to_datetime(frame["date"])
    for column in ["open", "high", "low", "close", "volume", "pe"]:
        if column not in frame:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "high", "low", "close"])
    return frame[["date", "open", "high", "low", "close", "volume", "pe"]].sort_values("date").drop_duplicates("date").reset_index(drop=True)


def fetch_tencent(session, symbol):
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = today - timedelta(days=TENCENT_LOOKBACK_DAYS)
    params = {"param": f"{symbol},day,{start:%Y-%m-%d},{today:%Y-%m-%d},{TENCENT_LOOKBACK_ROWS},qfq"}
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    symbol_data = payload.get("data", {}).get(symbol, {})
    raw_rows = symbol_data.get("qfqday") or symbol_data.get("day") or []
    rows = [
        {
            "date": row[0],
            "open": row[1],
            "close": row[2],
            "high": row[3],
            "low": row[4],
            "volume": row[5] if len(row) > 5 else np.nan,
            "pe": np.nan,
        }
        for row in raw_rows
    ]
    return normalize_frame(rows)


def eastmoney_secid(symbol):
    code = symbol[2:] if symbol[:2] in {"sh", "sz"} else symbol
    if symbol.startswith("sh"):
        return f"1.{code}"
    if symbol.startswith("sz"):
        return f"0.{code}"
    raise ValueError(f"东方财富暂不支持该代码: {symbol}")


def fetch_eastmoney_stock(session, symbol):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = today - timedelta(days=TENCENT_LOOKBACK_DAYS)
    params = {
        "secid": eastmoney_secid(symbol),
        "klt": "101",
        "fqt": "1",
        "beg": start.strftime("%Y%m%d"),
        "end": today.strftime("%Y%m%d"),
        "lmt": TENCENT_LOOKBACK_ROWS,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    raw_rows = payload.get("data", {}).get("klines") or []
    rows = []
    for raw in raw_rows:
        parts = raw.split(",")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": parts[1],
                "close": parts[2],
                "high": parts[3],
                "low": parts[4],
                "volume": parts[5],
                "pe": np.nan,
            }
        )
    return normalize_frame(rows)


def fetch_csindex(session, symbol):
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    today = datetime.now(MARKET_TIMEZONE).date()
    start = today - timedelta(days=365 * 11)
    params = {"indexCode": symbol, "startDate": start.strftime("%Y%m%d"), "endDate": today.strftime("%Y%m%d")}
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("code")) != "200":
                raise ValueError(f"中证指数官网返回异常: {payload}")
            rows = payload.get("data") or []
            if not rows:
                raise ValueError(f"中证指数官网未返回行情: {symbol}")
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise last_error
            time.sleep(0.8 * (attempt + 1))
    return normalize_frame(
        [
            {
                "date": row.get("tradeDate"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("tradingVol", np.nan),
                "pe": row.get("peg", np.nan),
            }
            for row in rows
        ]
    )


def fetch_cnindex(session, symbol):
    url = "https://hq.cnindex.com.cn/market/market/getIndexDailyData"
    response = session.get(url, params={"indexCode": symbol}, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise ValueError(f"国证指数官网返回异常: {payload}")
    data = payload.get("data") or {}
    column_index = {name: index for index, name in enumerate(data.get("item") or [])}
    required = {"timestamp", "open", "close", "high", "low"}
    if not required.issubset(column_index):
        raise ValueError(f"国证指数官网返回字段缺失: {symbol}")
    rows = []
    for row in data.get("data") or []:
        rows.append(
            {
                "date": datetime.fromtimestamp(row[column_index["timestamp"]] / 1000, MARKET_TIMEZONE).strftime("%Y-%m-%d"),
                "open": row[column_index["open"]],
                "close": row[column_index["close"]],
                "high": row[column_index["high"]],
                "low": row[column_index["low"]],
                "volume": row[column_index["volume"]] if "volume" in column_index else np.nan,
                "pe": np.nan,
            }
        )
    return normalize_frame(rows)


def fetch_history(session, asset):
    provider = asset["provider"]
    if provider == "tencent" and asset["asset_type"] == "股票":
        try:
            history = fetch_eastmoney_stock(session, asset["symbol"])
            if len(history) >= MIN_HISTORY_ROWS:
                return history
        except Exception:
            pass
        return fetch_tencent(session, asset["symbol"])
    if provider == "tencent":
        return fetch_tencent(session, asset["symbol"])
    if provider == "csindex":
        return fetch_csindex(session, asset["symbol"])
    if provider == "cnindex":
        return fetch_cnindex(session, asset["symbol"])
    raise ValueError(f"不支持的数据源: {provider}")


def percentile_last(values):
    clean = values[~np.isnan(values)]
    if len(clean) < PE_MIN_PERIODS:
        return np.nan
    return float((clean <= clean[-1]).mean() * 100)


def add_indicators(frame):
    data = frame.copy()
    data["daily_return"] = data["close"] / data["close"].shift(1) - 1
    year_start_close = data.groupby(data["date"].dt.year)["close"].transform("first")
    data["return_ytd"] = data["close"] / year_start_close - 1
    data["return_1w"] = data["close"] / data["close"].shift(5) - 1
    data["return_1m"] = data["close"] / data["close"].shift(21) - 1
    data["return_1y"] = data["close"] / data["close"].shift(252) - 1
    data["return_3y"] = data["close"] / data["close"].shift(756) - 1
    for period in [20, 60, 120, 200]:
        data[f"MA{period}"] = data["close"].rolling(period).mean()
    data["ma20_slope_5d"] = data["MA20"] / data["MA20"].shift(5) - 1
    data["ma60_slope_10d"] = data["MA60"] / data["MA60"].shift(10) - 1
    data["ma120_slope_20d"] = data["MA120"] / data["MA120"].shift(20) - 1
    data["ma200_slope_40d"] = data["MA200"] / data["MA200"].shift(40) - 1
    if data["pe"].notna().sum() >= PE_MIN_PERIODS:
        data["pe_percentile"] = data["pe"].rolling(PE_WINDOW_ROWS, min_periods=PE_MIN_PERIODS).apply(percentile_last, raw=True)
    else:
        data["pe_percentile"] = np.nan
    return data


def slope_direction(value, threshold):
    if pd.isna(value):
        return ""
    if value > threshold:
        return "向上"
    if value < -threshold:
        return "向下"
    return "走平"


def determine_short_trend(row):
    if row["close"] > row["MA20"] and row["ma20_slope_5d"] > 0.01:
        return "短期强势"
    if row["close"] < row["MA20"] and row["ma20_slope_5d"] < -0.01:
        return "短期转弱"
    return "短期震荡"


def determine_mid_trend(row):
    if row["close"] < row["MA120"] and row["MA60"] < row["MA120"] and row["ma120_slope_20d"] < -0.005:
        return "中期下跌"
    if row["close"] > row["MA120"] and row["MA60"] > row["MA120"] and row["ma120_slope_20d"] > 0.005:
        return "中期上升"
    if row["close"] > row["MA120"] and (row["MA60"] <= row["MA120"] or abs(row["ma120_slope_20d"]) <= 0.005):
        return "中期修复"
    if row["close"] < row["MA120"] and row["ma120_slope_20d"] <= 0.005:
        return "中期转弱"
    return "中期修复"


def determine_long_trend(row):
    if row["close"] < row["MA200"] and row["MA120"] < row["MA200"] and row["ma200_slope_40d"] < -0.005:
        return "长期下跌"
    if row["close"] > row["MA200"] and row["MA120"] > row["MA200"] and row["ma200_slope_40d"] > 0.005:
        return "长期上升"
    if row["close"] > row["MA200"] and (row["MA120"] <= row["MA200"] or abs(row["ma200_slope_40d"]) <= 0.005):
        return "长期修复"
    if row["close"] < row["MA200"] and row["ma200_slope_40d"] >= -0.005:
        return "长期转弱"
    return "长期修复"


def determine_overall_status(row):
    if row["mid_trend"] == "中期下跌" and row["long_trend"] == "长期下跌":
        return "下跌通道"
    if row["short_trend"] == "短期强势" and row["mid_trend"] == "中期上升" and row["long_trend"] == "长期上升":
        return "强趋势"
    if row["mid_trend"] == "中期上升" and row["long_trend"] == "长期上升":
        return "健康上升"
    if row["mid_trend"] == "中期上升" and row["long_trend"] != "长期上升":
        return "趋势分歧"
    if row["long_trend"] == "长期修复" or row["mid_trend"] == "中期修复":
        return "趋势修复"
    if row["mid_trend"] == "中期转弱" or row["long_trend"] == "长期转弱":
        return "趋势转弱"
    return "趋势修复"


def valuation_status(value):
    if pd.isna(value):
        return "估值数据缺失"
    if value < 15:
        return "极低估"
    if value < 35:
        return "低估"
    if value < 70:
        return "合理"
    if value < 90:
        return "偏高"
    return "高估"


def pe_percentile_period(frame):
    pe_rows = frame.dropna(subset=["pe"])
    if len(pe_rows) < PE_MIN_PERIODS:
        return ""
    days = max((pe_rows.iloc[-1]["date"] - pe_rows.iloc[0]["date"]).days, 1)
    years = days / 365.25
    if len(pe_rows) >= PE_WINDOW_ROWS or years >= 10:
        return "近10年"
    rounded_years = max(1, round(years))
    return f"近{rounded_years}年（历史不足10年）"


def build_signals(row):
    signals = [row["overall_status"]]
    for tag in ["长期下跌", "中期下跌", "长期转弱", "短期强势", "短期转弱"]:
        if tag in {row["long_trend"], row["mid_trend"], row["short_trend"]}:
            signals.append(tag)
    if row["asset_type"] == "指数":
        signals.append(row["valuation_status"])
        if row["valuation_status"] == "估值数据缺失":
            signals.append("估值数据缺失")
    return list(dict.fromkeys(signals))


def enrich_history(frame, asset):
    data = add_indicators(frame)
    required = ["MA20", "MA60", "MA120", "MA200", "ma20_slope_5d", "ma60_slope_10d", "ma120_slope_20d", "ma200_slope_40d"]
    for column in ["short_trend", "mid_trend", "long_trend", "overall_status", "valuation_status", "signal_tags"]:
        data[column] = ""
    data["name"] = asset["name"]
    data["symbol"] = asset["symbol"]
    data["market"] = asset["market"]
    data["asset_type"] = asset["asset_type"]
    data["last_year_dividend"] = asset.get("last_year_dividend", np.nan) if asset["asset_type"] == "股票" else np.nan
    data["dividend_yield"] = data.apply(
        lambda row: calculate_dividend_yield(row["last_year_dividend"], row["close"]),
        axis=1,
    ) if asset["asset_type"] == "股票" else np.nan
    data["pe_percentile_period"] = pe_percentile_period(data) if asset["asset_type"] == "指数" else ""
    ready = ~data[required].isna().any(axis=1)
    for idx in data[ready].index:
        row = data.loc[idx]
        data.at[idx, "short_trend"] = determine_short_trend(row)
        data.at[idx, "mid_trend"] = determine_mid_trend(row)
        data.at[idx, "long_trend"] = determine_long_trend(row)
        data.at[idx, "overall_status"] = determine_overall_status(data.loc[idx])
        data.at[idx, "valuation_status"] = valuation_status(row["pe_percentile"]) if asset["asset_type"] == "指数" else ""
        data.at[idx, "signal_tags"] = ", ".join(build_signals(data.loc[idx]))
    data.loc[~ready, "overall_status"] = "数据预热"
    if asset["asset_type"] == "指数":
        data.loc[~ready, "valuation_status"] = data.loc[~ready, "pe_percentile"].map(valuation_status)
    return data


def empty_result(asset, error):
    return {column: np.nan for column in REPORT_COLUMNS} | {
        "name": asset["name"],
        "symbol": asset["symbol"],
        "market": asset["market"],
        "asset_type": asset["asset_type"],
        "date": "",
        "short_trend": "数据不足",
        "mid_trend": "数据不足",
        "long_trend": "数据不足",
        "overall_status": "数据不足",
        "signal_tags": f"数据不足, {error}",
        "valuation_status": "估值数据缺失" if asset["asset_type"] == "指数" else "",
        "error": error,
    }


def latest_result(asset, history):
    if len(history) < MIN_HISTORY_ROWS:
        return empty_result(asset, f"仅获取到 {len(history)} 个交易日，至少需要 {MIN_HISTORY_ROWS} 个交易日。")
    current = history.iloc[-1]
    if current["overall_status"] in ("", "数据预热"):
        return empty_result(asset, "均线或斜率计算所需历史数据不足。")
    result = {column: current.get(column, np.nan) for column in REPORT_COLUMNS}
    result.update({"error": ""})
    return result


def round_output(frame):
    data = frame.copy()
    for column in ["close", "MA20", "MA60", "MA120", "MA200", "last_year_dividend", "dividend_yield", "pe", "pe_percentile"]:
        if column in data:
            data[column] = data[column].round(2)
    for column in [
        "daily_return",
        "return_ytd",
        "return_1w",
        "return_1m",
        "return_1y",
        "return_3y",
        "ma20_slope_5d",
        "ma60_slope_10d",
        "ma120_slope_20d",
        "ma200_slope_40d",
    ]:
        if column in data:
            data[column] = (data[column] * 100).round(2)
    return data


def clean_value(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, np.generic):
        return value.item()
    return value


def export_dashboard_data(results):
    records = []
    for row in results.to_dict("records"):
        signals = [tag.strip() for tag in str(row["signal_tags"]).split(",") if tag.strip()]
        records.append({key: clean_value(value) for key, value in row.items()} | {"signals": signals})
    DASHBOARD_OUTPUT.write_text(
        json.dumps({"generated_at": datetime.now(MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M"), "assets": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def export_history_data(history):
    output = round_output(history)
    output.to_csv(HISTORY_CSV_OUTPUT, index=False, encoding="utf-8-sig")
    records = [{key: clean_value(value) for key, value in row.items()} for row in output.to_dict("records")]
    generated_at = datetime.now(MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M")
    HISTORY_JSON_OUTPUT.write_text(
        json.dumps({"generated_at": generated_at, "history": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if HISTORY_DIR.exists():
        shutil.rmtree(HISTORY_DIR)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    manifest_assets = []
    if records:
        for symbol, group in output.groupby("symbol", sort=False):
            asset_records = [{key: clean_value(value) for key, value in row.items()} for row in group.to_dict("records")]
            file_name = f"{symbol}.json"
            (HISTORY_DIR / file_name).write_text(
                json.dumps({"generated_at": generated_at, "symbol": symbol, "history": asset_records}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            first = group.iloc[0]
            manifest_assets.append(
                {
                    "name": clean_value(first.get("name")),
                    "symbol": clean_value(symbol),
                    "asset_type": clean_value(first.get("asset_type")),
                    "path": file_name,
                    "rows": int(len(group)),
                    "start_date": clean_value(group.iloc[0].get("date")),
                    "end_date": clean_value(group.iloc[-1].get("date")),
                }
            )
    HISTORY_MANIFEST_OUTPUT.write_text(
        json.dumps({"generated_at": generated_at, "assets": manifest_assets}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_markdown(results):
    table = results.copy()
    for column in ["close", "MA20", "MA60", "MA120", "MA200", "last_year_dividend", "dividend_yield", "pe", "pe_percentile"]:
        table[column] = table[column].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    for column in [
        "daily_return",
        "return_ytd",
        "return_1w",
        "return_1m",
        "return_1y",
        "return_3y",
        "ma20_slope_5d",
        "ma60_slope_10d",
        "ma120_slope_20d",
        "ma200_slope_40d",
    ]:
        table[column] = table[column].map(lambda value: "" if pd.isna(value) else f"{value:.2f}%")
    return (
        "# 三周期均线趋势观察报告\n\n"
        f"生成时间：{datetime.now(MARKET_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}（Asia/Shanghai）\n\n"
        + table[REPORT_COLUMNS].to_markdown(index=False)
        + "\n\n"
        + DISCLAIMER
        + "\n"
    )


def format_feishu_message(results):
    generated_at = datetime.now(MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M")
    lines = [f"{FEISHU_KEYWORD} | 三周期趋势观察报告 | {generated_at}", ""]
    for row in results.to_dict("records"):
        close = "--" if pd.isna(row["close"]) else f"{row['close']:.2f}"
        lines.append(f"{row['name']} ({row['symbol']}) | {row['date'] or '--'} | 收盘 {close}")
        lines.append(f"趋势：{row['short_trend']} / {row['mid_trend']} / {row['long_trend']} | 综合：{row['overall_status']}")
        if row["asset_type"] == "指数":
            pe = "--" if pd.isna(row["pe"]) else f"{row['pe']:.2f}"
            pe_pct = "--" if pd.isna(row["pe_percentile"]) else f"{row['pe_percentile']:.2f}%"
            period = row["pe_percentile_period"] or "--"
            lines.append(f"估值：PE {pe} | 百分位 {pe_pct} | {row['valuation_status']} | 区间 {period}")
        if row["asset_type"] == "股票":
            dividend = "--" if pd.isna(row["last_year_dividend"]) else f"{row['last_year_dividend']:.5f}".rstrip("0").rstrip(".")
            dividend_yield = "--" if pd.isna(row["dividend_yield"]) else f"{row['dividend_yield']:.2f}%"
            lines.append(f"股息：上一年每股分红 {dividend} | 股息率 {dividend_yield}")
        lines.append(f"信号：{row['signal_tags'] or '--'}")
        if row["error"]:
            lines.append(f"异常：{row['error']}")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def post_feishu_message(results):
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("未设置 FEISHU_WEBHOOK_URL，无法发送飞书通知。")
    payload = {"msg_type": "text", "content": {"text": format_feishu_message(results)}}
    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        key = f"{timestamp}\n{secret}".encode("utf-8")
        payload.update({"timestamp": timestamp, "sign": base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode("utf-8")})
    response = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if result.get("code", result.get("StatusCode", 0)) != 0:
        raise RuntimeError(f"飞书通知发送失败: {result}")
    print("飞书通知已发送。")


def parse_args():
    parser = argparse.ArgumentParser(description="生成三周期均线趋势观察报告。")
    parser.add_argument("--notify", action="store_true", help="生成报告后发送飞书摘要通知。")
    return parser.parse_args()


def run(notify=False):
    apply_dividend_config()
    latest_rows = []
    history_frames = []
    with make_session() as session:
        for number, asset in enumerate(ASSETS, start=1):
            print(f"[{number:02d}/{len(ASSETS)}] 正在分析 {asset['name']} ({asset['symbol']})...", flush=True)
            try:
                raw_history = fetch_history(session, asset)
                history = enrich_history(raw_history, asset)
                history_frames.append(history)
                latest_rows.append(latest_result(asset, history))
            except Exception as exc:
                latest_rows.append(empty_result(asset, str(exc)))
                print(f"  获取失败: {exc}", flush=True)

    results = round_output(pd.DataFrame(latest_rows)[REPORT_COLUMNS])
    history_output = pd.concat(history_frames, ignore_index=True) if history_frames else pd.DataFrame()
    results.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")
    MARKDOWN_OUTPUT.write_text(format_markdown(results), encoding="utf-8")
    export_dashboard_data(results)
    export_history_data(history_output)
    print(f"\n已导出 CSV: {CSV_OUTPUT}")
    print(f"已导出 Markdown: {MARKDOWN_OUTPUT}")
    print(f"已导出网页数据: {DASHBOARD_OUTPUT}")
    print(f"已导出历史 JSON: {HISTORY_JSON_OUTPUT}")
    print(f"已导出拆分历史目录: {HISTORY_DIR}")
    print(f"已导出历史 CSV: {HISTORY_CSV_OUTPUT}\n")
    print(format_markdown(results))
    if notify:
        post_feishu_message(results)


if __name__ == "__main__":
    try:
        run(parse_args().notify)
    except KeyboardInterrupt:
        print("\n已取消趋势观察。", flush=True)
