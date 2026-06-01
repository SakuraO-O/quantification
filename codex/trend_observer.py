#!/usr/bin/env python3
"""中长期均线趋势观察工具。

维护 ASSETS 后直接运行本文件，程序将输出 Markdown 报告和 CSV 明细。
使用 --notify 可将不含均线数值的摘要推送到飞书机器人。
本工具只做趋势观察，不构成投资建议。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests


# ------------------------------ 用户配置区 ------------------------------ #
# provider:
#   tencent: symbol 示例 sh000300, sz399102, hkHSI, hkHSTECH
#   eastmoney: symbol 示例 2.930955, 0.980092, 2.931250
#   csindex: symbol 示例 H30269, 931250
#   cnindex: symbol 示例 980092
#   xueqiu:  symbol 示例 CSI932000, CSI931250
#   yfinance: 需自行安装 yfinance，symbol 示例 QQQ, 000300.SS
ASSETS = [
    {"name": "沪深300", "symbol": "sh000300", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "中证A500", "symbol": "sh000510", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "中证500", "symbol": "sh000905", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "创业板100", "symbol": "sz399006", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "科创50", "symbol": "sh000688", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "恒生指数", "symbol": "hkHSI", "market": "HK", "asset_type": "指数", "provider": "tencent"},
    {"name": "红利低波", "symbol": "H30269", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "国证现金流", "symbol": "980092", "market": "CN", "asset_type": "指数", "provider": "cnindex"},
    {"name": "中证消费", "symbol": "sh000932", "market": "CN", "asset_type": "指数", "provider": "tencent"},
    {"name": "港股通创新药", "symbol": "931250", "market": "CN", "asset_type": "指数", "provider": "csindex"},
    {"name": "长江电力", "symbol": "sh600900", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "中国神华", "symbol": "sh601088", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "中国海油", "symbol": "sh600938", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "招商银行", "symbol": "sh600036", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "国电电力", "symbol": "sh600795", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "格力电器", "symbol": "sz000651", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "美的集团", "symbol": "sz000333", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "粤高速A", "symbol": "sz000429", "market": "CN", "asset_type": "股票", "provider": "tencent"},
    {"name": "云铝股份", "symbol": "sz000807", "market": "CN", "asset_type": "股票", "provider": "tencent"},
]

BASE_DIR = Path(__file__).resolve().parent
CSV_OUTPUT = BASE_DIR / "trend_observer_report.csv"
MARKDOWN_OUTPUT = BASE_DIR / "trend_observer_report.md"
DASHBOARD_OUTPUT = BASE_DIR / "dashboard_data.json"
LOOKBACK_ROWS = 300
MIN_HISTORY_ROWS = 250
SLOPE_DAYS = 20
HTTP_TIMEOUT = (5, 10)
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
DISCLAIMER = "本结果仅用于趋势观察，不构成投资建议。均线信号存在滞后性和假突破风险。"
FEISHU_KEYWORD = "妙啊"

SIGNAL_PRIORITY = [
    "跌破年线",
    "🆘死亡交叉",
    "均线空头排列",
    "突破年线",
    "💚黄金交叉",
    "均线多头排列",
    "回踩年线",
    "回踩中期均线",
    "高位乖离",
    "短期过热",
    "震荡观察",
]

ACTION_HINTS = {
    "继续持有": "长期趋势向上且未出现破坏信号，可继续持有，适合定投或等待回踩。",
    "谨慎加仓": "长期趋势向上且回踩关键均线，可作为分批加仓观察点。",
    "暂不追高": "趋势向上但乖离或短期热度偏高，不宜一次性加仓。",
    "减仓观察": "长期趋势出现破坏信号，建议控制仓位并观察是否修复。",
    "暂不操作": "趋势不清晰或仍偏弱，避免重仓操作。",
}

REPORT_COLUMNS = [
    "name",
    "symbol",
    "market",
    "asset_type",
    "date",
    "close",
    "return_day",
    "return_ytd",
    "return_6m",
    "return_1y",
    "trend_status",
    "signal_tags",
    "action_category",
    "action_hint",
    "MA20",
    "MA50",
    "MA60",
    "MA120",
    "MA200",
]

MARKDOWN_COLUMNS = [
    "name",
    "symbol",
    "date",
    "close",
    "return_day",
    "return_ytd",
    "return_6m",
    "return_1y",
    "trend_status",
    "signal_tags",
    "action_category",
    "action_hint",
    "MA20",
    "MA50",
    "MA60",
    "MA120",
    "MA200",
]


def make_session():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


def normalize_frame(rows):
    frame = pd.DataFrame(rows, columns=["date", "close", "high", "low", "volume"])
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"])
    numeric_columns = ["close", "high", "low", "volume"]
    frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    frame = frame.dropna(subset=["date", "close", "high", "low"])
    return frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def fetch_tencent(session, symbol):
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,,,{LOOKBACK_ROWS},qfq"}
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    symbol_data = payload.get("data", {}).get(symbol, {})
    raw_rows = symbol_data.get("day") or symbol_data.get("qfqday") or []
    rows = [
        {
            "date": row[0],
            "close": row[2],
            "high": row[3],
            "low": row[4],
            "volume": row[5] if len(row) > 5 else np.nan,
        }
        for row in raw_rows
    ]
    return normalize_frame(rows)


def fetch_xueqiu(session, symbol):
    page = session.get(f"https://xueqiu.com/S/{symbol}", timeout=HTTP_TIMEOUT)
    page.raise_for_status()
    url = "https://stock.xueqiu.com/v5/stock/chart/kline.json"
    params = {
        "symbol": symbol,
        "begin": int(datetime.now(timezone.utc).timestamp() * 1000),
        "period": "day",
        "type": "before",
        "count": f"-{LOOKBACK_ROWS}",
        "indicator": "kline",
    }
    response = session.get(
        url,
        params=params,
        headers={"Referer": f"https://xueqiu.com/S/{symbol}"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json().get("data", {})
    column_index = {name: index for index, name in enumerate(data.get("column", []))}
    required = {"timestamp", "close", "high", "low"}
    if not required.issubset(column_index):
        raise ValueError(f"雪球返回字段缺失: {symbol}")
    rows = []
    for row in data.get("item", []):
        rows.append(
            {
                "date": datetime.fromtimestamp(
                    row[column_index["timestamp"]] / 1000, MARKET_TIMEZONE
                ).strftime("%Y-%m-%d"),
                "close": row[column_index["close"]],
                "high": row[column_index["high"]],
                "low": row[column_index["low"]],
                "volume": row[column_index["volume"]] if "volume" in column_index else np.nan,
            }
        )
    return normalize_frame(rows)


def fetch_eastmoney(session, symbol):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": symbol,
        "klt": "101",
        "fqt": "1",
        "lmt": str(LOOKBACK_ROWS),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
    }
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    raw_rows = (response.json().get("data") or {}).get("klines") or []
    rows = []
    for raw_row in raw_rows:
        row = raw_row.split(",")
        rows.append(
            {
                "date": row[0],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
            }
        )
    return normalize_frame(rows)


def fetch_csindex(session, symbol):
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    today = datetime.now(MARKET_TIMEZONE).date()
    params = {
        "indexCode": symbol,
        "startDate": (today - timedelta(days=600)).strftime("%Y%m%d"),
        "endDate": today.strftime("%Y%m%d"),
    }
    response = session.get(url, params=params, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "200":
        raise ValueError(f"中证指数官网返回异常: {payload}")
    rows = [
        {
            "date": row["tradeDate"],
            "close": row["close"],
            "high": row["high"],
            "low": row["low"],
            "volume": row.get("tradingVol", np.nan),
        }
        for row in payload.get("data") or []
    ]
    return normalize_frame(rows).tail(LOOKBACK_ROWS).reset_index(drop=True)


def fetch_cnindex(session, symbol):
    url = "https://hq.cnindex.com.cn/market/market/getIndexDailyData"
    response = session.get(url, params={"indexCode": symbol}, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise ValueError(f"国证指数官网返回异常: {payload}")
    data = payload.get("data") or {}
    column_index = {name: index for index, name in enumerate(data.get("item") or [])}
    required = {"timestamp", "close", "high", "low"}
    if not required.issubset(column_index):
        raise ValueError(f"国证指数官网返回字段缺失: {symbol}")
    rows = [
        {
            "date": datetime.fromtimestamp(
                row[column_index["timestamp"]] / 1000, MARKET_TIMEZONE
            ).strftime("%Y-%m-%d"),
            "close": row[column_index["close"]],
            "high": row[column_index["high"]],
            "low": row[column_index["low"]],
            "volume": row[column_index["volume"]] if "volume" in column_index else np.nan,
        }
        for row in data.get("data") or []
    ]
    return normalize_frame(rows).tail(LOOKBACK_ROWS).reset_index(drop=True)


def fetch_yfinance(symbol):
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("未安装 yfinance，请执行 pip install yfinance 后重试。") from exc

    frame = yf.download(symbol, period="18mo", interval="1d", progress=False, auto_adjust=False)
    if frame.empty:
        return frame
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.reset_index().rename(
        columns={"Date": "date", "Close": "close", "High": "high", "Low": "low", "Volume": "volume"}
    )
    return normalize_frame(frame[["date", "close", "high", "low", "volume"]].to_dict("records"))


def fetch_history(session, asset):
    provider = asset.get("provider", "tencent").lower()
    symbol = asset.get("provider_symbol", asset["symbol"])
    if provider == "tencent":
        return fetch_tencent(session, symbol)
    if provider == "xueqiu":
        return fetch_xueqiu(session, symbol)
    if provider == "eastmoney":
        return fetch_eastmoney(session, symbol)
    if provider == "csindex":
        return fetch_csindex(session, symbol)
    if provider == "cnindex":
        return fetch_cnindex(session, symbol)
    if provider == "yfinance":
        return fetch_yfinance(symbol)
    raise ValueError(f"不支持的 provider: {provider}")


def calculate_indicators(frame):
    data = frame.copy()
    for period in [20, 50, 60, 120, 200]:
        data[f"MA{period}"] = data["close"].rolling(period).mean()
    # MA50 回踩同样采用最近 20 个交易日方向，和 MA200 斜率口径保持一致。
    data["MA50_slope"] = data["MA50"] - data["MA50"].shift(SLOPE_DAYS)
    data["MA200_slope"] = data["MA200"] - data["MA200"].shift(SLOPE_DAYS)
    return data


def determine_trend(today):
    if today["close"] > today["MA200"] and today["MA50"] > today["MA200"] and today["MA200_slope"] > 0:
        return "✅长期趋势向上"
    if today["close"] < today["MA200"] and today["MA50"] < today["MA200"] and today["MA200_slope"] < 0:
        return "❌长期趋势向下"
    return "震荡观察"


def find_signals(today, yesterday, trend_status):
    signals = set()
    if yesterday["close"] <= yesterday["MA200"] and today["close"] > today["MA200"]:
        signals.add("突破年线")
    if yesterday["MA50"] <= yesterday["MA200"] and today["MA50"] > today["MA200"]:
        signals.add("💚黄金交叉")
    if today["MA20"] > today["MA50"] > today["MA120"] > today["MA200"] and today["close"] > today["MA20"]:
        signals.add("均线多头排列")
    if yesterday["close"] >= yesterday["MA200"] and today["close"] < today["MA200"]:
        signals.add("跌破年线")
    if yesterday["MA50"] >= yesterday["MA200"] and today["MA50"] < today["MA200"]:
        signals.add("🆘死亡交叉")
    if today["MA20"] < today["MA50"] < today["MA120"] < today["MA200"] and today["close"] < today["MA20"]:
        signals.add("均线空头排列")
    if (
        today["close"] > today["MA200"]
        and today["MA200"] * 0.98 <= today["low"] <= today["MA200"] * 1.01
        and today["MA200_slope"] >= 0
    ):
        signals.add("回踩年线")
    if today["close"] > today["MA50"] and today["low"] <= today["MA50"] * 1.01 and today["MA50_slope"] > 0:
        signals.add("回踩中期均线")
    if today["close"] / today["MA200"] - 1 > 0.25:
        signals.add("高位乖离")
    if today["close"] > today["MA20"] and today["MA20"] > today["MA50"] and today["close"] / today["MA20"] - 1 > 0.08:
        signals.add("短期过热")
    if trend_status == "震荡观察":
        signals.add("震荡观察")
    return [tag for tag in SIGNAL_PRIORITY if tag in signals]


def determine_action(trend_status, signals):
    tags = set(signals)
    if "跌破年线" in tags or "🆘死亡交叉" in tags:
        return "减仓观察"
    if trend_status == "✅长期趋势向上" and ({"回踩年线", "回踩中期均线"} & tags) and "高位乖离" not in tags:
        return "谨慎加仓"
    if trend_status == "✅长期趋势向上" and ({"高位乖离", "短期过热"} & tags):
        return "暂不追高"
    if trend_status == "✅长期趋势向上":
        return "继续持有"
    return "暂不操作"


def determine_action_hint(trend_status, action_category):
    if trend_status == "❌长期趋势向下" and action_category == "暂不操作":
        return "长期趋势向下，谨慎参与，原则上不主动加仓。"
    return ACTION_HINTS[action_category]


def calculate_returns(frame):
    today = frame.iloc[-1]
    today_date = today["date"]

    def return_since(reference_rows):
        if reference_rows.empty:
            return np.nan
        return round((today["close"] / reference_rows.iloc[-1]["close"] - 1) * 100, 2)

    year_start = pd.Timestamp(year=today_date.year, month=1, day=1)
    return {
        "return_day": return_since(frame.iloc[:-1]),
        "return_ytd": return_since(frame[frame["date"] < year_start]),
        "return_6m": return_since(frame[frame["date"] <= today_date - pd.DateOffset(months=6)]),
        "return_1y": return_since(frame[frame["date"] <= today_date - pd.DateOffset(years=1)]),
    }


def empty_result(asset, status, detail):
    return {
        "name": asset["name"],
        "symbol": asset["symbol"],
        "market": asset.get("market", ""),
        "asset_type": asset.get("asset_type", ""),
        "date": "",
        "close": np.nan,
        "return_day": np.nan,
        "return_ytd": np.nan,
        "return_6m": np.nan,
        "return_1y": np.nan,
        "MA20": np.nan,
        "MA50": np.nan,
        "MA60": np.nan,
        "MA120": np.nan,
        "MA200": np.nan,
        "trend_status": status,
        "signal_tags": status,
        "action_category": "暂不操作",
        "action_hint": detail,
    }


def analyze_asset(session, asset):
    frame = fetch_history(session, asset)
    if len(frame) < MIN_HISTORY_ROWS:
        return empty_result(
            asset,
            "数据不足，无法判断长期趋势",
            f"仅获取到 {len(frame)} 个交易日，至少需要 {MIN_HISTORY_ROWS} 个交易日。",
        )
    indicators = calculate_indicators(frame)
    today = indicators.iloc[-1]
    yesterday = indicators.iloc[-2]
    required = ["MA20", "MA50", "MA60", "MA120", "MA200", "MA50_slope", "MA200_slope"]
    if today[required].isna().any() or yesterday[["MA50", "MA200"]].isna().any():
        return empty_result(asset, "数据不足，无法判断长期趋势", "均线或斜率计算所需历史数据不足。")

    trend_status = determine_trend(today)
    signals = find_signals(today, yesterday, trend_status)
    action_category = determine_action(trend_status, signals)
    returns = calculate_returns(frame)
    return {
        "name": asset["name"],
        "symbol": asset["symbol"],
        "market": asset.get("market", ""),
        "asset_type": asset.get("asset_type", ""),
        "date": today["date"].strftime("%Y-%m-%d"),
        "close": round(today["close"], 2),
        **returns,
        "MA20": round(today["MA20"], 2),
        "MA50": round(today["MA50"], 2),
        "MA60": round(today["MA60"], 2),
        "MA120": round(today["MA120"], 2),
        "MA200": round(today["MA200"], 2),
        "trend_status": trend_status,
        "signal_tags": ", ".join(signals) if signals else "无新增信号",
        "action_category": action_category,
        "action_hint": determine_action_hint(trend_status, action_category),
    }


def format_markdown(results):
    table = results.copy()
    numeric = ["close", "MA20", "MA50", "MA60", "MA120", "MA200"]
    for column in numeric:
        table[column] = table[column].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    percentages = ["return_day", "return_ytd", "return_6m", "return_1y"]
    for column in percentages:
        table[column] = table[column].map(lambda value: "" if pd.isna(value) else f"{value:.2f}%")
    report_date = datetime.now(MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    return (
        "# 均线趋势观察报告\n\n"
        f"生成时间：{report_date}（Asia/Shanghai）\n\n"
        + table[MARKDOWN_COLUMNS].to_markdown(index=False)
        + "\n\n"
        + DISCLAIMER
        + "\n"
    )


def format_feishu_message(results):
    generated_at = datetime.now(MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M")
    lines = [f"{FEISHU_KEYWORD} | 均线趋势观察报告 | {generated_at}", ""]
    for row in results.to_dict("records"):
        close = "--" if pd.isna(row["close"]) else f"{row['close']:.2f}"
        lines.extend(
            [
                f"{row['name']} ({row['symbol']}) | {row['date'] or '--'} | 收盘 {close}",
                f"趋势：{row['trend_status']} | 信号：{row['signal_tags']} | 建议：{row['action_category']}",
                f"提示：{row['action_hint']}",
                "",
            ]
        )
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def export_dashboard_data(results):
    records = []
    for row in results.to_dict("records"):
        records.append(
            {
                "name": row["name"],
                "symbol": row["symbol"],
                "asset_type": row["asset_type"],
                "date": row["date"],
                "close": None if pd.isna(row["close"]) else row["close"],
                "return_day": None if pd.isna(row["return_day"]) else row["return_day"],
                "return_ytd": None if pd.isna(row["return_ytd"]) else row["return_ytd"],
                "return_6m": None if pd.isna(row["return_6m"]) else row["return_6m"],
                "return_1y": None if pd.isna(row["return_1y"]) else row["return_1y"],
                "trend": row["trend_status"],
                "signals": [tag.strip() for tag in row["signal_tags"].split(",")],
                "action": row["action_category"],
                "hint": row["action_hint"],
                "MA20": None if pd.isna(row["MA20"]) else row["MA20"],
                "MA50": None if pd.isna(row["MA50"]) else row["MA50"],
                "MA60": None if pd.isna(row["MA60"]) else row["MA60"],
                "MA120": None if pd.isna(row["MA120"]) else row["MA120"],
                "MA200": None if pd.isna(row["MA200"]) else row["MA200"],
            }
        )
    payload = {
        "generated_at": datetime.now(MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M"),
        "assets": records,
    }
    DASHBOARD_OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def post_feishu_message(results):
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise RuntimeError("未设置 FEISHU_WEBHOOK_URL，无法发送飞书通知。")

    payload = {
        "msg_type": "text",
        "content": {"text": format_feishu_message(results)},
    }
    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        key = f"{timestamp}\n{secret}".encode("utf-8")
        signature = base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode("utf-8")
        payload.update({"timestamp": timestamp, "sign": signature})

    response = requests.post(webhook_url, json=payload, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    result = response.json()
    if result.get("code", result.get("StatusCode", 0)) != 0:
        raise RuntimeError(f"飞书通知发送失败: {result}")
    print("飞书通知已发送。")


def parse_args():
    parser = argparse.ArgumentParser(description="生成中长期均线趋势观察报告。")
    parser.add_argument("--notify", action="store_true", help="生成报告后发送飞书摘要通知。")
    return parser.parse_args()


def run(notify=False):
    results = []
    with make_session() as session:
        for number, asset in enumerate(ASSETS, start=1):
            print(f"[{number:02d}/{len(ASSETS)}] 正在分析 {asset['name']} ({asset['symbol']})...", flush=True)
            try:
                result = analyze_asset(session, asset)
            except Exception as exc:
                result = empty_result(asset, "行情获取失败", str(exc))
                print(f"  获取失败: {exc}", flush=True)
            results.append(result)

    output = pd.DataFrame(results)[REPORT_COLUMNS]
    output.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")
    MARKDOWN_OUTPUT.write_text(format_markdown(output), encoding="utf-8")
    export_dashboard_data(output)
    print(f"\n已导出 CSV: {CSV_OUTPUT}")
    print(f"已导出 Markdown: {MARKDOWN_OUTPUT}\n")
    print(f"已导出网页数据: {DASHBOARD_OUTPUT}\n")
    print(format_markdown(output))
    if notify:
        post_feishu_message(output[output["asset_type"] == "指数"])


if __name__ == "__main__":
    try:
        run(parse_args().notify)
    except KeyboardInterrupt:
        print("\n已取消趋势观察。", flush=True)
